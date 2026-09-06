import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import v4_run_mandarin_lyricalignment_observer as runner


class MandarinLyricAlignmentObserverCliTests(unittest.TestCase):
    def _manifest(self, root: Path, *, partition: str = "calibration") -> tuple[Path, dict]:
        clip = root / "private" / "fixture.wav"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"fixture")
        payload = {
            "schema_version": runner.MANIFEST_SCHEMA_VERSION,
            "partition": partition,
            "selection_lock_sha256": "1" * 64,
            "final_audio_sha256": "2" * 64,
            "outer_audit_csv_sha256": "3" * 64,
            "audio_basis": "locked_final_mix_clip",
            "language": "zh",
            "record_count": 1,
            "records": [
                {
                    "case_id": "4" * 64,
                    "clip_path": "private/fixture.wav",
                    "clip_sha256": runner._sha256_file(clip),
                    "window_start_ms": 1000,
                    "canonical_text": "测试歌词",
                }
            ],
        }
        payload["manifest_sha256"] = runner._sha256_json(payload)
        path = root / "manifest.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path, payload

    def _two_record_manifest(self, root: Path) -> Path:
        path, payload = self._manifest(root)
        second_clip = root / "private" / "fixture2.wav"
        second_clip.write_bytes(b"fixture2")
        payload["records"].append(
            {
                "case_id": "5" * 64,
                "clip_path": "private/fixture2.wav",
                "clip_sha256": runner._sha256_file(second_clip),
                "window_start_ms": 2000,
                "canonical_text": "第二句歌词",
            }
        )
        payload["record_count"] = 2
        payload["manifest_sha256"] = runner._sha256_json(
            {key: value for key, value in payload.items() if key != "manifest_sha256"}
        )
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_valid_calibration_manifest_is_accepted_only_when_expected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, payload = self._manifest(root)
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                loaded = runner.load_evaluation_manifest(
                    path,
                    expected_partition="calibration",
                )
            self.assertEqual(loaded["manifest_sha256"], payload["manifest_sha256"])
            self.assertEqual(loaded["partition"], "calibration")

    def test_holdout_manifest_fails_closed_when_calibration_expected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, _ = self._manifest(root, partition="holdout")
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                with self.assertRaisesRegex(runner.MandarinLyricAlignmentRunError, "partition mismatch"):
                    runner.load_evaluation_manifest(
                        path,
                        expected_partition="calibration",
                    )

    def test_locked_clip_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, _ = self._manifest(root)
            (root / "private" / "fixture.wav").write_bytes(b"changed")
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                with self.assertRaisesRegex(runner.MandarinLyricAlignmentRunError, "clip SHA mismatch"):
                    runner.load_evaluation_manifest(
                        path,
                        expected_partition="calibration",
                    )

    def test_batch_loads_backend_once_and_reuses_for_all_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._two_record_manifest(root)
            fake_evidence = SimpleNamespace(
                start=SimpleNamespace(hypotheses=[SimpleNamespace(boundary_ms=1111)]),
                end=SimpleNamespace(hypotheses=[SimpleNamespace(boundary_ms=1444)]),
                to_dict=lambda: {"fixture": True},
            )
            fake_runtime = {"runtime_revision": "r" * 64, "packages": {"torch": "x"}}
            fake_model = {
                "files": [],
                "file_count": 3,
                "snapshot_revision": "m" * 64,
                "architecture_fingerprint_sha256": runner.ARCHITECTURE_FINGERPRINT_SHA256,
            }
            fake_lexical = {"lexical_revision": "l" * 64}
            with (
                mock.patch.object(runner, "REPOSITORY_ROOT", root),
                mock.patch.object(
                    runner,
                    "_implementation_identity",
                    return_value={"files": [], "implementation_revision": "i" * 64},
                ),
                mock.patch.object(runner, "_runtime_identity", return_value=fake_runtime),
                mock.patch.object(runner, "_model_snapshot_identity", return_value=fake_model),
                mock.patch.object(runner, "_lexical_identity", return_value=fake_lexical),
                mock.patch.object(runner, "LocalMandarinLyricAlignmentBackend") as backend_class,
                mock.patch.object(runner, "build_outer_evidence", return_value=fake_evidence),
            ):
                backend = backend_class.return_value
                backend.infer.return_value = object()
                artifact = runner.run_observer(
                    manifest_path=manifest,
                    expected_partition="calibration",
                    model_dir=root / "model",
                    vocab_path=root / "vocab.txt",
                    pronunciation_table_path=root / "pron.json",
                )
            self.assertEqual(backend_class.call_count, 1)
            self.assertEqual(backend.infer.call_count, 2)
            self.assertEqual(artifact["aligned_count"], 2)
            self.assertEqual(artifact["prediction_coverage"], 1.0)
            self.assertFalse(artifact["automatic_mutation_allowed"])
            self.assertFalse(artifact["subtitle_mutation_performed"])

    def test_item_local_lexical_failure_becomes_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self._manifest(root)
            with (
                mock.patch.object(runner, "REPOSITORY_ROOT", root),
                mock.patch.object(
                    runner,
                    "_implementation_identity",
                    return_value={"files": [], "implementation_revision": "i" * 64},
                ),
                mock.patch.object(
                    runner,
                    "_runtime_identity",
                    return_value={"runtime_revision": "r" * 64, "packages": {}},
                ),
                mock.patch.object(
                    runner,
                    "_model_snapshot_identity",
                    return_value={"snapshot_revision": "m" * 64},
                ),
                mock.patch.object(
                    runner,
                    "_lexical_identity",
                    return_value={"lexical_revision": "l" * 64},
                ),
                mock.patch.object(runner, "LocalMandarinLyricAlignmentBackend") as backend_class,
                mock.patch.object(runner, "build_outer_evidence") as build_evidence,
            ):
                backend_class.return_value.infer.side_effect = runner.MandarinLyricAlignmentObserverError(
                    "fixture lexical failure"
                )
                artifact = runner.run_observer(
                    manifest_path=manifest,
                    expected_partition="calibration",
                    model_dir=root / "model",
                    vocab_path=root / "vocab.txt",
                    pronunciation_table_path=root / "pron.json",
                )
            build_evidence.assert_not_called()
            self.assertEqual(artifact["aligned_count"], 0)
            self.assertEqual(artifact["unavailable_count"], 1)
            self.assertEqual(artifact["prediction_coverage"], 0.0)
            self.assertEqual(artifact["records"][0]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
