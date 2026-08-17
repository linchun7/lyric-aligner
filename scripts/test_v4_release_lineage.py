import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.contracts.artifacts import build_artifact_manifest
from v4_validate_release import _load_upstream_artifacts


FINGERPRINT = "c" * 64


class V4ReleaseLineageTests(unittest.TestCase):
    def artifact(
        self,
        root: Path,
        *,
        stage: str,
        profile_id: str,
        profile_version: str = "bootstrap-test",
    ) -> Path:
        output = root / f"{stage}.json"
        output.write_text("{}", encoding="utf-8")
        payload = build_artifact_manifest(
            task_fingerprint_sha256=FINGERPRINT,
            stage=stage,
            algorithm_version="4.0.0a2",
            outputs=((stage, output),),
            normalized_config={
                "calibration_profile_id": profile_id,
                "calibration_profile_version": profile_version,
            },
        )
        path = root / f"{stage}.artifact.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_release_collects_upstream_ids_and_one_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = self.artifact(root, stage="asset_resolution", profile_id="same")
            coarse = self.artifact(root, stage="coarse_audio_alignment", profile_id="same")
            ids, metadata = _load_upstream_artifacts(
                [asset, coarse], fingerprint=FINGERPRINT
            )
            self.assertEqual(len(ids), 2)
            self.assertEqual(metadata["calibration_profile_id"], "same")
            self.assertEqual(
                metadata["upstream_stages"],
                ["asset_resolution", "coarse_audio_alignment"],
            )

    def test_release_blocks_mixed_calibration_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = self.artifact(root, stage="asset_resolution", profile_id="left")
            coarse = self.artifact(root, stage="coarse_audio_alignment", profile_id="right")
            with self.assertRaisesRegex(ValueError, "different calibration_profile_id"):
                _load_upstream_artifacts([asset, coarse], fingerprint=FINGERPRINT)

    def test_release_blocks_cross_task_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = self.artifact(root, stage="asset_resolution", profile_id="same")
            with self.assertRaisesRegex(ValueError, "task fingerprint"):
                _load_upstream_artifacts([artifact], fingerprint="d" * 64)


if __name__ == "__main__":
    unittest.main()
