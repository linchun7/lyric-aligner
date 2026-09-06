import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import v4_run_wav2vec2_ctc_observer as runner


class Wav2Vec2CTCObserverCliTests(unittest.TestCase):
    def _manifest(self, root: Path) -> tuple[Path, dict]:
        clip = root / "private" / "fixture.wav"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"fixture")
        payload = {
            "schema_version": runner.MANIFEST_SCHEMA_VERSION,
            "partition": "calibration",
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

    def _runtime_identity(self) -> dict:
        return {
            "python_version": "test",
            "python_implementation": "CPython",
            "packages": {name: "test" for name in runner.INFERENCE_RUNTIME_PACKAGES},
            "runtime_revision": "d" * 64,
        }

    def test_valid_calibration_manifest_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, payload = self._manifest(root)
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                loaded = runner.load_evaluation_manifest(path)
            self.assertEqual(loaded["manifest_sha256"], payload["manifest_sha256"])
            self.assertEqual(loaded["partition"], "calibration")

    def test_manifest_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, payload = self._manifest(root)
            payload["records"][0]["canonical_text"] = "被修改"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                with self.assertRaisesRegex(runner.Wav2Vec2CTCRunError, "hash is invalid"):
                    runner.load_evaluation_manifest(path)

    def test_locked_clip_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, _ = self._manifest(root)
            (root / "private" / "fixture.wav").write_bytes(b"replaced-after-lock")
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                with self.assertRaisesRegex(runner.Wav2Vec2CTCRunError, "clip SHA mismatch"):
                    runner.load_evaluation_manifest(path)

    def test_clip_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            outside = root.parent / "outside.wav"
            outside.write_bytes(b"fixture")
            path, payload = self._manifest(root)
            payload["records"][0]["clip_path"] = "../outside.wav"
            payload["manifest_sha256"] = runner._sha256_json(
                {key: value for key, value in payload.items() if key != "manifest_sha256"}
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                with self.assertRaisesRegex(runner.Wav2Vec2CTCRunError, "escapes"):
                    runner.load_evaluation_manifest(path)

    def test_holdout_is_supported_but_mixed_partition_is_not_representable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, payload = self._manifest(root)
            payload["partition"] = "holdout"
            payload["manifest_sha256"] = runner._sha256_json(
                {key: value for key, value in payload.items() if key != "manifest_sha256"}
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                loaded = runner.load_evaluation_manifest(path)
            self.assertEqual(loaded["partition"], "holdout")
            self.assertTrue(all("partition" not in row for row in loaded["records"]))

    def test_model_snapshot_identity_binds_only_required_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in runner.MODEL_RUNTIME_FILES:
                (root / name).write_bytes(name.encode("utf-8"))
            metadata = root / ".cache" / "huggingface" / "download" / "meta.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text("first", encoding="utf-8")
            first = runner._model_snapshot_identity(root)
            metadata.write_text("second", encoding="utf-8")
            unchanged = runner._model_snapshot_identity(root)
            (root / "pytorch_model.bin").write_bytes(b"changed-weights")
            changed = runner._model_snapshot_identity(root)
            self.assertEqual(first["file_count"], len(runner.MODEL_RUNTIME_FILES))
            self.assertEqual(first["snapshot_revision"], unchanged["snapshot_revision"])
            self.assertNotEqual(first["snapshot_revision"], changed["snapshot_revision"])

    def test_model_snapshot_identity_requires_every_runtime_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in runner.MODEL_RUNTIME_FILES[:-1]:
                (root / name).write_bytes(name.encode("utf-8"))
            with self.assertRaisesRegex(FileNotFoundError, "missing required runtime file"):
                runner._model_snapshot_identity(root)

    def test_batch_loads_backend_once_and_reuses_it_for_all_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._two_record_manifest(root)
            fake_evidence = SimpleNamespace(
                start=SimpleNamespace(hypotheses=[SimpleNamespace(boundary_ms=1111)]),
                end=SimpleNamespace(hypotheses=[SimpleNamespace(boundary_ms=1444)]),
                to_dict=lambda: {"fixture": True},
            )
            with (
                mock.patch.object(runner, "REPOSITORY_ROOT", root),
                mock.patch.object(
                    runner,
                    "_implementation_identity",
                    return_value={"files": [], "implementation_revision": "a" * 64},
                ),
                mock.patch.object(
                    runner,
                    "_model_snapshot_identity",
                    return_value={"files": [], "file_count": 5, "snapshot_revision": "b" * 64},
                ),
                mock.patch.object(
                    runner,
                    "_runtime_identity",
                    return_value=self._runtime_identity(),
                ),
                mock.patch.object(runner, "LocalWav2Vec2CTCBackend") as backend_class,
                mock.patch.object(runner, "build_outer_evidence", return_value=fake_evidence),
            ):
                backend = backend_class.return_value
                backend.infer.return_value = object()
                artifact = runner.run_observer(
                    manifest_path=manifest,
                    model_dir=root / "model",
                    model_revision="c" * 40,
                )
            self.assertEqual(backend_class.call_count, 1)
            self.assertEqual(backend.infer.call_count, 2)
            self.assertEqual(artifact["aligned_count"], 2)
            self.assertEqual(artifact["unavailable_count"], 0)
            self.assertEqual(artifact["prediction_coverage"], 1.0)
            self.assertEqual(len(artifact["runtime"]["runtime_revision"]), 64)
            self.assertIn("torch", artifact["runtime"]["packages"])

    def test_item_local_ctc_failure_becomes_unavailable_instead_of_aborting_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self._manifest(root)
            with (
                mock.patch.object(runner, "REPOSITORY_ROOT", root),
                mock.patch.object(
                    runner,
                    "_implementation_identity",
                    return_value={"files": [], "implementation_revision": "a" * 64},
                ),
                mock.patch.object(
                    runner,
                    "_model_snapshot_identity",
                    return_value={"files": [], "file_count": 5, "snapshot_revision": "b" * 64},
                ),
                mock.patch.object(
                    runner,
                    "_runtime_identity",
                    return_value=self._runtime_identity(),
                ),
                mock.patch.object(runner, "LocalWav2Vec2CTCBackend") as backend_class,
                mock.patch.object(runner, "build_outer_evidence") as build_evidence,
            ):
                backend_class.return_value.infer.side_effect = runner.Wav2Vec2CTCObserverError(
                    "fixture lexical failure"
                )
                artifact = runner.run_observer(
                    manifest_path=manifest,
                    model_dir=root / "model",
                    model_revision="c" * 40,
                )
            build_evidence.assert_not_called()
            self.assertEqual(artifact["aligned_count"], 0)
            self.assertEqual(artifact["unavailable_count"], 1)
            self.assertEqual(artifact["prediction_coverage"], 0.0)
            self.assertEqual(artifact["records"][0]["status"], "unavailable")
            self.assertIsNone(artifact["records"][0]["predicted_start_ms"])
            self.assertFalse(artifact["automatic_mutation_allowed"])


if __name__ == "__main__":
    unittest.main()
