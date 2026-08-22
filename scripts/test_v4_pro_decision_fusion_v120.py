from __future__ import annotations

import unittest

from lyric_aligner.alignment.selective_fusion import build_pro_decisions


def _timing(cue: int, *, proposal: int | None) -> dict:
    return {
        "cue_ordinal": cue,
        "action": "review",
        "reason": "synthetic_timing_review",
        "old_start_ms": cue * 10_000,
        "old_end_ms": cue * 10_000 + 1_000,
        "proposed_start_ms": proposal,
        "proposed_end_ms": None if proposal is None else proposal + 1_000,
    }


def _job(cue: int, *, text_review: bool = False) -> dict:
    reasons = ["smart_timing_review:synthetic_timing_review"]
    capabilities = ["source_local_acoustic_match"]
    if text_review:
        reasons.append("smart_text_review:synthetic_text_review")
        capabilities.extend(["mix_asr", "word_timestamps"])
    return {
        "job_id": f"job-{cue}",
        "cue_ordinal": cue,
        "source_ordinal": 0,
        "requested_capabilities": capabilities,
        "reasons": reasons,
        "shadow_evidence_only": False,
    }


def _acoustic(
    cue: int,
    *,
    shift_ms: int,
    passed: bool = True,
    boundary_hit: bool = False,
) -> dict:
    editor = cue * 10_000
    return {
        "job_id": f"job-{cue}",
        "cue_ordinal": cue,
        "editor_start_residual_ms": -shift_ms,
        "predicted_mix_start_ms": editor + shift_ms,
        "local_match_gate_passed": passed,
        "reliable_local_match": passed,
        "slope_search_min": 0.94,
        "slope_search_max": 1.06,
        "slope_search_boundary_hit": boundary_hit,
        "timing_fusion_evidence_eligible": passed and not boundary_hit,
        "shadow_evidence_only": False,
    }


class ProDecisionFusionV120Tests(unittest.TestCase):
    def test_fusion_separates_support_rebuttal_tolerance_and_unvalidated(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.8",
            "timing_decisions": [
                _timing(0, proposal=None),
                _timing(1, proposal=11_200),
                _timing(2, proposal=14_000),
                _timing(3, proposal=30_400),
                _timing(4, proposal=None),
                _timing(5, proposal=50_400),
            ],
            "text_decisions": [
                {"cue_ordinal": cue, "action": "unchanged"}
                for cue in range(6)
            ],
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.3",
            "jobs": [_job(cue) for cue in range(6)],
        }
        acoustic = {
            "schema_version": "1.3",
            "jobs": [
                _acoustic(0, shift_ms=-1_200),
                _acoustic(1, shift_ms=650),
                _acoustic(2, shift_ms=-650),
                _acoustic(3, shift_ms=500),
                _acoustic(5, shift_ms=1_400),
            ],
        }

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )
        by_cue = {row["cue_ordinal"]: row for row in result["decisions"]}

        self.assertEqual(by_cue[0]["timing_state"], "pro_detected_anomaly")
        self.assertEqual(by_cue[1]["timing_state"], "smart_candidate_supported")
        self.assertEqual(by_cue[2]["timing_state"], "smart_candidate_rebutted")
        self.assertEqual(by_cue[3]["timing_state"], "editor_within_display_tolerance")
        self.assertEqual(by_cue[4]["timing_state"], "unvalidated_no_actionable_evidence")
        self.assertEqual(by_cue[5]["timing_state"], "smart_pro_conflict")
        self.assertEqual(result["summary"]["high_priority_manual_review_count"], 0)
        self.assertEqual(by_cue[1]["manual_review_priority"], "medium")
        self.assertFalse(by_cue[1]["independent_vocal_onset_evidence_used"])
        self.assertFalse(result["automatic_timing_change_allowed"])
        self.assertFalse(result["automatic_text_change_allowed"])
        self.assertFalse(result["timing_mutation_performed"])

    def test_slope_boundary_match_cannot_support_or_rebut_smart(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.8",
            "timing_decisions": [
                _timing(1, proposal=11_200),
                _timing(2, proposal=24_000),
            ],
            "text_decisions": [
                {"cue_ordinal": 1, "action": "unchanged"},
                {"cue_ordinal": 2, "action": "unchanged"},
            ],
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.3",
            "jobs": [_job(1), _job(2)],
        }
        acoustic = {
            "schema_version": "1.3",
            "jobs": [
                _acoustic(1, shift_ms=650, boundary_hit=True),
                _acoustic(2, shift_ms=-50, boundary_hit=True),
            ],
        }

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )
        by_cue = {row["cue_ordinal"]: row for row in result["decisions"]}
        self.assertEqual(by_cue[1]["timing_state"], "smart_candidate_unverified")
        self.assertEqual(by_cue[2]["timing_state"], "smart_candidate_unverified")
        for row in by_cue.values():
            self.assertTrue(row["local_match_gate_passed"])
            self.assertFalse(row["timing_fusion_evidence_eligible"])
            self.assertEqual(
                row["timing_evidence_semantics"],
                "diagnostic_only_slope_boundary_limited",
            )
            self.assertNotIn(
                row["timing_state"],
                {"smart_candidate_supported", "smart_candidate_rebutted"},
            )

    def test_text_and_timing_axes_are_independent(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.8",
            "timing_decisions": [_timing(0, proposal=None)],
            "text_decisions": [
                {
                    "cue_ordinal": 0,
                    "action": "review",
                    "reason": "synthetic_text_review",
                }
            ],
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.3",
            "jobs": [_job(0, text_review=True)],
        }
        asr = {
            "schema_version": "1.0",
            "jobs": [
                {"job_id": "job-0", "canonical_text_support_score": 0.91}
            ],
        }

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            asr_evidence=asr,
        )
        row = result["decisions"][0]
        self.assertEqual(row["text_state"], "canonical_text_supported")
        self.assertEqual(row["timing_state"], "unvalidated_no_actionable_evidence")
        self.assertTrue(row["partial_evidence"])

    def test_supported_acoustic_occurrence_can_support_cross_script_text_identity(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.8",
            "timing_decisions": [_timing(1, proposal=11_200)],
            "text_decisions": [
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 7,
                    "canonical_span": [7, 8],
                    "action": "review",
                    "reason": "synthetic_cross_script_review",
                }
            ],
        }
        job = _job(1, text_review=True)
        job["canonical_line_index"] = 7
        job["canonical_text_sha256"] = "synthetic-hash"
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.3",
            "jobs": [job],
        }
        acoustic = {"schema_version": "1.3", "jobs": [_acoustic(1, shift_ms=650)]}

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )

        row = result["decisions"][0]
        self.assertEqual(row["timing_state"], "smart_candidate_supported")
        self.assertEqual(
            row["text_state"],
            "canonical_occurrence_supported_by_acoustic",
        )
        self.assertFalse(row["automatic_text_change_allowed"])
        self.assertEqual(row["canonical_text_sha256"], "synthetic-hash")
        self.assertEqual(row["manual_review_priority"], "high")

    def test_smart_cross_script_recovery_retains_high_timing_value(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.8",
            "timing_decisions": [_timing(1, proposal=11_200)],
            "text_decisions": [
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 7,
                    "action": "replace",
                    "reason": "preceding_canonical_anchor_confirms_cross_script_vocalization",
                }
            ],
        }
        job = _job(1, text_review=False)
        job["canonical_line_index"] = 7
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.3",
            "jobs": [job],
        }
        acoustic = {"schema_version": "1.3", "jobs": [_acoustic(1, shift_ms=650)]}

        row = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )["decisions"][0]

        self.assertEqual(
            row["text_state"],
            "cross_script_vocalization_resolved_by_smart",
        )
        self.assertEqual(row["manual_review_priority"], "high")


if __name__ == "__main__":
    unittest.main()
