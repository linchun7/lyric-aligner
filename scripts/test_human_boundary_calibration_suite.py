import tempfile
import unittest
from pathlib import Path

from lyric_aligner.alignment.production_backend_profiles import ProductionBoundaryBackendProfile
from lyric_aligner.text.alignment_lexical import ALIGNMENT_LEXICAL_ID, ALIGNMENT_LEXICAL_REVISION
from scripts.test_human_boundary_gold_pack import HumanBoundaryGoldPackTests
from scripts.v4_ingest_human_boundary_gold import sha256_json
from scripts.v4_run_human_boundary_calibration_suite import run_calibration_suite


class HumanBoundaryCalibrationSuiteTests(unittest.TestCase):
    def pack_helper(self):
        return HumanBoundaryGoldPackTests()

    def make_profiles(self, root: Path):
        profiles = {}
        definitions = (
            ("profile-a", "backend-a", "final_mix_singing_alignment", "group-a"),
            ("profile-b", "backend-b", "final_mix_forced_alignment", "group-b"),
        )
        for profile_id, backend_id, family, group in definitions:
            sidecar = root / "private" / profile_id
            runtime = sidecar / "runtime" / ".venv" / "Scripts"
            runtime.mkdir(parents=True)
            (runtime / "python.exe").write_bytes(b"fake-python")
            (sidecar / "adapter.py").write_text("pass\n", encoding="utf-8")
            (sidecar / "model.onnx").write_bytes((profile_id + "-model").encode("ascii"))
            profiles[profile_id] = ProductionBoundaryBackendProfile(
                profile_id=profile_id,
                backend_id=backend_id,
                backend_version="fixture-adapter-1",
                family=family,
                correlation_group=group,
                model_id=profile_id + "-model",
                supported_languages=("fixture",),
                sidecar_root_relpath=f"private/{profile_id}",
                adapter_relpath=f"private/{profile_id}/adapter.py",
                model_relpath=f"private/{profile_id}/model.onnx",
            )
        return profiles

    @staticmethod
    def perfect_prediction_executor(**kwargs):
        human_gold = kwargs["human_gold_artifact"]
        boundary_kind = kwargs["boundary_kind"]
        config = kwargs["config"]
        scoped = [
            row for row in human_gold["records"]
            if row["boundary_kind"] == boundary_kind
        ]
        rows = [
            {"id": row["id"], "predicted_ms": int(row["gold_ms"])}
            for row in scoped
        ]
        scope = {
            "boundary_kind": boundary_kind,
            "audio_basis": config.audio_basis,
            "language": config.language,
            "backend_version": config.backend_version,
            "backend_profile_id": config.profile_id,
            "window_policy_id": config.window_policy_id,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "implementation_revision": config.implementation_revision,
            "adapter_contract_revision": config.adapter_contract_revision,
            "lexical_id": ALIGNMENT_LEXICAL_ID,
            "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        }
        artifact = {
            "schema_version": "human-boundary-backend-predictions-1.1",
            "selection_lock_sha256": human_gold["selection_lock_sha256"],
            "backend_id": config.backend_id,
            "family": config.family,
            "correlation_group": config.correlation_group,
            "scope": scope,
            "record_count": 30,
            "records": rows,
            "predictions_sha256": sha256_json(rows),
        }
        artifact["artifact_sha256"] = sha256_json(artifact)
        return artifact

    def test_blank_audits_fail_before_profiles_or_models_and_leave_no_failed_dir(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self.pack_helper().build_pack(root)
            destination = root / "suite"
            calls = []

            def forbidden_profile_getter(profile_id):
                calls.append(profile_id)
                raise AssertionError("profile resolution must not happen before human audit ingest")

            with self.assertRaises(ValueError):
                run_calibration_suite(
                    pack_dir=pack,
                    out_dir=destination,
                    profile_ids=("profile-a", "profile-b"),
                    repository_root=root,
                    profile_getter=forbidden_profile_getter,
                    prediction_executor=lambda **kwargs: (_ for _ in ()).throw(
                        AssertionError("backend must not run before human audit ingest")
                    ),
                )
            self.assertEqual(calls, [])
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name("suite.staging").exists())
            self.assertFalse(destination.with_name("suite.failed").exists())

    def test_six_scope_suite_atomically_completes_when_both_backends_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = self.pack_helper()
            pack = helper.build_pack(root)
            helper.fill_audits(pack)
            profiles = self.make_profiles(root)
            destination = root / "suite"
            suite = run_calibration_suite(
                pack_dir=pack,
                out_dir=destination,
                profile_ids=("profile-a", "profile-b"),
                repository_root=root,
                profile_getter=profiles.__getitem__,
                prediction_executor=self.perfect_prediction_executor,
            )
            self.assertTrue(suite["production_authority_ready"])
            self.assertEqual(suite["scope_count"], 6)
            self.assertTrue(destination.is_dir())
            self.assertFalse(destination.with_name("suite.staging").exists())
            self.assertFalse(destination.with_name("suite.failed").exists())
            self.assertTrue((destination / "human_gold.json").is_file())
            self.assertTrue((destination / "suite.summary.json").is_file())
            self.assertEqual(
                {(row["profile_id"], row["boundary_kind"]) for row in suite["scopes"]},
                {
                    (profile, kind)
                    for profile in ("profile-a", "profile-b")
                    for kind in ("start", "end", "internal")
                },
            )
            self.assertTrue(all(row["production_authoritative"] for row in suite["scopes"]))

    def test_runtime_failure_preserves_failed_evidence_and_never_materializes_ready_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = self.pack_helper()
            pack = helper.build_pack(root)
            helper.fill_audits(pack)
            profiles = self.make_profiles(root)
            destination = root / "suite"
            calls = {"count": 0}

            def fail_after_one(**kwargs):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise RuntimeError("fixture backend crash")
                return self.perfect_prediction_executor(**kwargs)

            with self.assertRaises(RuntimeError):
                run_calibration_suite(
                    pack_dir=pack,
                    out_dir=destination,
                    profile_ids=("profile-a", "profile-b"),
                    repository_root=root,
                    profile_getter=profiles.__getitem__,
                    prediction_executor=fail_after_one,
                )
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name("suite.staging").exists())
            failed = destination.with_name("suite.failed")
            self.assertTrue(failed.is_dir())
            self.assertTrue((failed / "SUITE.FAILED.json").is_file())
            self.assertFalse((failed / "suite.summary.json").exists())


if __name__ == "__main__":
    unittest.main()
