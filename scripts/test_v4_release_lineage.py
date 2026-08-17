import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from v4_validate_release import (
    _load_upstream_artifacts,
    _validate_final_render_binding,
)


FINGERPRINT = "c" * 64


class V4ReleaseLineageTests(unittest.TestCase):
    def artifact(
        self,
        root: Path,
        *,
        stage: str,
        profile_id: str,
        profile_version: str = "bootstrap-test",
        algorithm_version: str = __version__,
        outputs=None,
        name: str | None = None,
    ) -> Path:
        if outputs is None:
            output = root / f"{name or stage}.json"
            output.write_text("{}", encoding="utf-8")
            outputs = ((stage, output),)
        payload = build_artifact_manifest(
            task_fingerprint_sha256=FINGERPRINT,
            stage=stage,
            algorithm_version=algorithm_version,
            outputs=outputs,
            normalized_config={
                "calibration_profile_id": profile_id,
                "calibration_profile_version": profile_version,
            },
        )
        path = root / f"{name or stage}.artifact.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def final_files(self, root: Path):
        final_srt = root / "FINAL.srt"
        report = root / "FINAL.csv"
        qa = root / "FINAL.qa.json"
        final_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nline\n",
            encoding="utf-8",
        )
        report.write_text("start_ms,end_ms,text\n1000,2000,line\n", encoding="utf-8")
        qa.write_text("{}", encoding="utf-8")
        return final_srt, report, qa

    def final_render_artifact(
        self,
        root: Path,
        *,
        name="render",
        algorithm_version=__version__,
    ):
        final_srt, report, qa = self.final_files(root)
        artifact = self.artifact(
            root,
            stage="final_render",
            profile_id="same",
            profile_version="profile-v1",
            algorithm_version=algorithm_version,
            name=name,
            outputs=(
                ("final_srt", final_srt),
                ("audit_csv", report),
                ("qa_json", qa),
            ),
        )
        return artifact, final_srt, report, qa

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

    def test_final_render_binding_accepts_exact_materialized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(root)
            artifact_id = _validate_final_render_binding(
                [artifact],
                fingerprint=FINGERPRINT,
                algorithm_version=__version__,
                final_srt=final_srt,
                report=report,
                qa_json=qa,
            )
            self.assertTrue(artifact_id)

    def test_final_render_binding_rejects_modified_srt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(root)
            final_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nchanged\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "final_srt .*mismatch"):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_requires_exactly_one_render_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(root, name="one")
            second = self.artifact(
                root,
                stage="final_render",
                profile_id="same",
                profile_version="profile-v1",
                name="two",
                outputs=(
                    ("final_srt", final_srt),
                    ("audit_csv", report),
                    ("qa_json", qa),
                ),
            )
            with self.assertRaisesRegex(ValueError, "exactly one final_render"):
                _validate_final_render_binding(
                    [artifact, second],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_rejects_wrong_algorithm_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root, algorithm_version="4.0.0a3"
            )
            with self.assertRaisesRegex(ValueError, "algorithm version mismatch"):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )


if __name__ == "__main__":
    unittest.main()
