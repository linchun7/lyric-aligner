from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner.doctor import DoctorError
from lyric_aligner.doctor_partial import build_doctor_report_with_partial_repair


def base_report(*, task_passed: bool = True) -> dict:
    return {
        "schema_version": "1.1",
        "mode": "v4_doctor",
        "stages": {},
        "artifacts": {},
        "lineage": {},
        "dataset": {},
        "backends": {},
        "requirements": {
            "passed": task_passed,
            "results": {"task": task_passed},
        },
        "recommended_next_action": {"action": "render"},
    }


def partial_report(
    *,
    lineage: bool = True,
    lock: bool = True,
    actionable: bool = True,
    decisions: bool = True,
) -> dict:
    return {
        "status": "proposal_inputs_ready" if decisions else "calibrated_decisions_required",
        "lineage": {"valid": lineage},
        "trust_lock": {"valid": lock, "actionable": actionable},
        "decisions": {"valid": decisions},
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
    }


class DoctorPartialRepairExtensionTests(unittest.TestCase):
    def test_partial_requirements_are_evaluated_without_changing_base_doctor(self):
        with patch(
            "lyric_aligner.doctor_partial.build_doctor_report",
            return_value=base_report(),
        ) as base, patch(
            "lyric_aligner.doctor_partial.inspect_partial_timeline_repair_readiness",
            return_value=partial_report(),
        ) as inspect:
            report = build_doctor_report_with_partial_repair(
                task_manifest=Path("task.json"),
                run=Path("run.json"),
                run_artifact=Path("run.artifact.json"),
                fusion=Path("fusion.json"),
                fusion_artifact=Path("fusion.artifact.json"),
                partial_trust_lock=Path("trust.lock.json"),
                partial_trust_decisions=Path("decisions.json"),
                partial_trust_decisions_artifact=Path("decisions.artifact.json"),
                inspect_backend_status=False,
                requirements=(
                    "task",
                    "partial_repair:lineage",
                    "partial_repair:trust_lock",
                    "partial_repair:actionable_scope",
                    "partial_repair:decisions",
                    "partial_repair:proposal_inputs",
                ),
            )
        self.assertEqual(
            base.call_args.kwargs["requirements"],
            ["task"],
        )
        self.assertEqual(
            inspect.call_args.kwargs["trust_lock_path"],
            Path("trust.lock.json"),
        )
        self.assertTrue(report["requirements"]["passed"])
        self.assertTrue(report["requirements"]["results"]["task"])
        self.assertTrue(
            report["requirements"]["results"]["partial_repair:proposal_inputs"]
        )
        self.assertIs(report["partial_timeline_repair"], inspect.return_value)

    def test_failed_partial_requirement_makes_combined_requirements_fail(self):
        with patch(
            "lyric_aligner.doctor_partial.build_doctor_report",
            return_value=base_report(),
        ), patch(
            "lyric_aligner.doctor_partial.inspect_partial_timeline_repair_readiness",
            return_value=partial_report(decisions=False),
        ):
            report = build_doctor_report_with_partial_repair(
                inspect_backend_status=False,
                requirements=("task", "partial_repair:decisions"),
            )
        self.assertFalse(report["requirements"]["passed"])
        self.assertFalse(
            report["requirements"]["results"]["partial_repair:decisions"]
        )

    def test_existing_base_requirement_failure_remains_failure(self):
        with patch(
            "lyric_aligner.doctor_partial.build_doctor_report",
            return_value=base_report(task_passed=False),
        ), patch(
            "lyric_aligner.doctor_partial.inspect_partial_timeline_repair_readiness",
            return_value=partial_report(),
        ):
            report = build_doctor_report_with_partial_repair(
                inspect_backend_status=False,
                requirements=("task", "partial_repair:lineage"),
            )
        self.assertFalse(report["requirements"]["passed"])
        self.assertFalse(report["requirements"]["results"]["task"])
        self.assertTrue(
            report["requirements"]["results"]["partial_repair:lineage"]
        )

    def test_unknown_partial_requirement_fails_closed(self):
        with self.assertRaisesRegex(
            DoctorError,
            "unknown doctor requirement partial_repair:unknown",
        ):
            build_doctor_report_with_partial_repair(
                inspect_backend_status=False,
                requirements=("partial_repair:unknown",),
            )


if __name__ == "__main__":
    unittest.main()
