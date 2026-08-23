import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from v4_validate_release import (
    _load_upstream_artifacts,
    _require_v4_production_editor_reconciliation_materializer,
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
        extra_config: dict | None = None,
        evidence: dict | None = None,
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
                **(extra_config or {}),
            },
            evidence=evidence,
        )
        path = root / f"{name or stage}.artifact.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def final_files(
        self,
        root: Path,
        *,
        qa_segmentation_authority: str = "editor_reconciled",
        qa_publish_ready: bool = True,
        qa_release_blocked_reason: str = "",
    ):
        final_srt = root / "FINAL.srt"
        report = root / "FINAL.csv"
        qa = root / "FINAL.qa.json"
        final_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nline\n",
            encoding="utf-8",
        )
        report.write_text("start_ms,end_ms,text\n1000,2000,line\n", encoding="utf-8")
        qa.write_text(
            json.dumps(
                {
                    "segmentation_authority": qa_segmentation_authority,
                    "publish_ready": qa_publish_ready,
                    "release_blocked_reason": qa_release_blocked_reason,
                }
            ),
            encoding="utf-8",
        )
        return final_srt, report, qa

    def final_render_artifact(
        self,
        root: Path,
        *,
        name="render",
        algorithm_version=__version__,
        segmentation_authority="editor_reconciled",
        evidence_segmentation_authority: str | None = None,
        evidence_publish_ready: bool = True,
        evidence_release_blocked_reason: str = "",
        qa_segmentation_authority: str = "editor_reconciled",
        qa_publish_ready: bool = True,
        qa_release_blocked_reason: str = "",
    ):
        final_srt, report, qa = self.final_files(
            root,
            qa_segmentation_authority=qa_segmentation_authority,
            qa_publish_ready=qa_publish_ready,
            qa_release_blocked_reason=qa_release_blocked_reason,
        )
        extra_config = {}
        if segmentation_authority is not None:
            extra_config["segmentation_authority"] = segmentation_authority
        evidence_authority = (
            segmentation_authority
            if evidence_segmentation_authority is None
            else evidence_segmentation_authority
        )
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
            extra_config=extra_config,
            evidence={
                "segmentation_authority": evidence_authority,
                "publish_ready": evidence_publish_ready,
                "release_blocked_reason": evidence_release_blocked_reason,
            },
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

    def test_final_render_binding_accepts_consistent_production_authority_fields(self):
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

    def test_v4_release_stays_closed_without_production_reconciliation_materializer(self):
        with self.assertRaisesRegex(
            ValueError,
            "production editor reconciliation materializer contract is not implemented",
        ):
            _require_v4_production_editor_reconciliation_materializer()

    def test_final_render_binding_rejects_canonical_only_segmentation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root,
                segmentation_authority=None,
            )
            with self.assertRaisesRegex(
                ValueError,
                "no editor-reconciled segmentation authority",
            ):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_rejects_unknown_segmentation_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root,
                segmentation_authority="canonical_line",
            )
            with self.assertRaisesRegex(
                ValueError,
                "canonical-line rendering is evaluation-only",
            ):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_rejects_evidence_authority_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root,
                evidence_segmentation_authority="canonical_line_evaluation_only",
            )
            with self.assertRaisesRegex(ValueError, "artifact evidence does not confirm"):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_rejects_evidence_not_publish_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root,
                evidence_publish_ready=False,
            )
            with self.assertRaisesRegex(ValueError, "artifact evidence is not publish_ready"):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_rejects_qa_authority_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root,
                qa_segmentation_authority="canonical_line_evaluation_only",
            )
            with self.assertRaisesRegex(ValueError, "final render QA does not confirm"):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_rejects_qa_not_publish_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root,
                qa_publish_ready=False,
            )
            with self.assertRaisesRegex(ValueError, "final render QA is not publish_ready"):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

    def test_final_render_binding_rejects_remaining_release_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, final_srt, report, qa = self.final_render_artifact(
                root,
                evidence_release_blocked_reason="still_blocked",
            )
            with self.assertRaisesRegex(ValueError, "still records a release_blocked_reason"):
                _validate_final_render_binding(
                    [artifact],
                    fingerprint=FINGERPRINT,
                    algorithm_version=__version__,
                    final_srt=final_srt,
                    report=report,
                    qa_json=qa,
                )

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
                extra_config={"segmentation_authority": "editor_reconciled"},
                evidence={
                    "segmentation_authority": "editor_reconciled",
                    "publish_ready": True,
                    "release_blocked_reason": "",
                },
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
