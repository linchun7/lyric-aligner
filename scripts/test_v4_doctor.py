from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.doctor import build_doctor_report


class V4DoctorTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_missing_required_task_fails_without_throwing(self) -> None:
        report = build_doctor_report(requirements=["task"], inspect_backend_status=False)
        self.assertFalse(report["requirements"]["passed"])
        self.assertEqual(report["recommended_next_action"]["action"], "supply_task_manifest")
        self.assertNotIn("lyric", json.dumps(report).lower())

    def test_resume_recommends_projection_then_fusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._write(root, "task.json", {"tracks": [{"id": "t1"}]})
            run = self._write(
                root,
                "run.json",
                {"stage": "reconstruction", "status": "ready_for_render"},
            )
            forced = self._write(
                root,
                "forced.json",
                {"backend": "external_forced_aligner", "jobs": []},
            )
            report = build_doctor_report(
                task_manifest=task,
                run=run,
                forced_evidence=forced,
                requirements=["task", "run", "forced_source"],
                inspect_backend_status=False,
            )
        self.assertTrue(report["requirements"]["passed"])
        actions = [row["action"] for row in report["next_actions"]]
        self.assertIn("plan_auxiliary_evidence", actions)
        self.assertIn("project_forced_evidence_to_mix", actions)
        self.assertIn("render_authoritative_timeline", actions)

    def test_valid_forced_mix_and_fusion_remain_shadow_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._write(root, "task.json", {"tracks": [{"id": "t1"}]})
            run = self._write(root, "run.json", {"stage": "x", "status": "ready_for_render"})
            forced_mix = self._write(
                root,
                "forced_mix.json",
                {
                    "mode": "forced_alignment_mix_projection",
                    "primary_timing_authority": "source_to_mix_only",
                    "jobs": [
                        {"projection_status": "projected"},
                        {"projection_status": "unprojectable"},
                    ],
                },
            )
            fusion = self._write(
                root,
                "fusion.json",
                {
                    "mode": "shadow_only",
                    "policy_calibrated": False,
                    "release_gate_eligible": False,
                    "lines": [{"shadow_level": "CONFLICT"}],
                },
            )
            report = build_doctor_report(
                task_manifest=task,
                run=run,
                forced_mix_evidence=forced_mix,
                fusion=fusion,
                requirements=["forced_mix", "fusion"],
                inspect_backend_status=False,
            )
        self.assertTrue(report["requirements"]["passed"])
        self.assertIn("projected=1;unprojectable=1", report["stages"]["forced_mix"]["detail"])
        self.assertEqual(report["authority"]["primary_timing"], "source_to_mix_only")

    def test_calibrated_fusion_is_rejected_by_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fusion = self._write(
                root,
                "fusion.json",
                {
                    "mode": "shadow_only",
                    "policy_calibrated": True,
                    "release_gate_eligible": False,
                    "lines": [],
                },
            )
            report = build_doctor_report(
                fusion=fusion,
                requirements=["fusion"],
                inspect_backend_status=False,
            )
        self.assertFalse(report["requirements"]["passed"])
        self.assertEqual(report["stages"]["fusion"]["detail"], "fusion_policy_must_remain_uncalibrated")

    def test_backend_report_does_not_emit_full_command_or_resolved_path(self) -> None:
        secret_command = "definitely-not-installed --private /Users/example/song.wav"
        report = build_doctor_report(
            external_forced_aligner_command=secret_command,
            inspect_backend_status=True,
        )
        rendered = json.dumps(report)
        self.assertNotIn(secret_command, rendered)
        self.assertNotIn("/Users/example", rendered)
        self.assertIn("external_forced_aligner", rendered)


if __name__ == "__main__":
    unittest.main()
