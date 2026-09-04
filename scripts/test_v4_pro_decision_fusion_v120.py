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
    source_boundary_hit: bool = False,
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
        "source_search_boundary_hit": source_boundary_hit,
        "timing_fusion_evidence_eligible": (
            passed and not boundary_hit and not source_boundary_hit
        ),
        "shadow_evidence_only": False,
    }


class ProDecisionFusionV120Tests(unittest.TestCase):
    def test_fusion_separates_support_rebuttal_tolerance_and_unvalidated(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
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
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [_job(cue) for cue in range(6)],
        }
        acoustic = {
            "schema_version": "1.4",
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
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
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
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [_job(1), _job(2)],
        }
        acoustic = {
            "schema_version": "1.4",
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
                "diagnostic_only_search_boundary_limited",
            )
            self.assertNotIn(
                row["timing_state"],
                {"smart_candidate_supported", "smart_candidate_rebutted"},
            )

    def test_source_search_boundary_match_cannot_adjudicate_timing(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
            "timing_decisions": [_timing(1, proposal=11_200)],
            "text_decisions": [{"cue_ordinal": 1, "action": "unchanged"}],
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [_job(1)],
        }
        acoustic = {
            "schema_version": "1.4",
            "jobs": [
                _acoustic(1, shift_ms=650, source_boundary_hit=True),
            ],
        }

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )
        row = result["decisions"][0]
        self.assertEqual(row["timing_state"], "smart_candidate_unverified")
        self.assertTrue(row["local_match_gate_passed"])
        self.assertFalse(row["timing_fusion_evidence_eligible"])
        self.assertTrue(row["source_search_boundary_hit"])
        self.assertEqual(
            row["timing_evidence_semantics"],
            "diagnostic_only_search_boundary_limited",
        )

    def test_legacy_acoustic_without_source_boundary_field_fails_closed(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
            "timing_decisions": [_timing(1, proposal=11_200)],
            "text_decisions": [{"cue_ordinal": 1, "action": "unchanged"}],
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [_job(1)],
        }
        legacy_row = _acoustic(1, shift_ms=650)
        legacy_row.pop("source_search_boundary_hit")
        acoustic = {"schema_version": "1.3", "jobs": [legacy_row]}

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )
        row = result["decisions"][0]
        self.assertEqual(row["timing_state"], "smart_candidate_unverified")
        self.assertFalse(row["timing_fusion_evidence_eligible"])

    def test_text_and_timing_axes_are_independent(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
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
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
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
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
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
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [job],
        }
        acoustic = {"schema_version": "1.4", "jobs": [_acoustic(1, shift_ms=650)]}

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
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
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
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [job],
        }
        acoustic = {"schema_version": "1.4", "jobs": [_acoustic(1, shift_ms=650)]}

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


    def test_v127_segmentation_timing_review_stays_investigative(self) -> None:
        timing = _timing(1, proposal=None)
        timing["reason"] = "segmentation_internal_boundary_unvalidated"
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
            "timing_decisions": [timing],
            "text_decisions": [{"cue_ordinal": 1, "action": "unchanged"}],
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [_job(1)],
        }
        acoustic = {"schema_version": "1.4", "jobs": [_acoustic(1, shift_ms=80)]}

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )
        row = result["decisions"][0]
        self.assertEqual(result["product_version"], "1.2.7")
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["authority"], "automatic_adjudication_no_srt_mutation")
        self.assertTrue(result["automatic_adjudication_allowed"])
        self.assertFalse(result["automatic_review_resolution_allowed"])
        self.assertEqual(row["timing_state"], "editor_supported_by_acoustic")
        self.assertEqual(row["timing_resolution"], "manual_review_required")
        self.assertEqual(row["resolution"], "manual_review_required")
        self.assertFalse(row["automatic_adjudication_performed"])
        self.assertEqual(row["manual_review_mode"], "investigate")
        self.assertTrue(row["manual_review_required"])
        self.assertIsNone(row["timing_recommendation"])
        self.assertFalse(row["automatic_review_resolution_performed"])
        self.assertFalse(row["automatic_timing_change_allowed"])
        self.assertFalse(row["automatic_text_change_allowed"])
        self.assertFalse(row["timing_mutation_performed"])

    def test_v127_supported_text_is_advisory_not_auto_resolved(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
            "timing_decisions": [{"cue_ordinal": 3, "action": "unchanged"}],
            "text_decisions": [
                {
                    "cue_ordinal": 3,
                    "canonical_ordinal": 7,
                    "canonical_span": [7, 8],
                    "action": "review",
                    "reason": "low_or_structurally_unsafe_similarity",
                }
            ],
        }
        job = {
            "job_id": "job-3",
            "cue_ordinal": 3,
            "source_ordinal": 0,
            "canonical_line_index": 7,
            "canonical_text_sha256": "canonical-hash",
            "requested_capabilities": ["mix_asr", "word_timestamps"],
            "reasons": ["smart_text_review:low_or_structurally_unsafe_similarity"],
            "shadow_evidence_only": False,
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [job],
        }
        asr = {
            "schema_version": "1.0",
            "jobs": [{"job_id": "job-3", "canonical_text_support_score": 0.95}],
        }

        result = build_pro_decisions(smart_report=smart, plan=plan, asr_evidence=asr)
        row = result["decisions"][0]
        self.assertEqual(row["text_state"], "canonical_text_supported")
        self.assertEqual(row["text_resolution"], "canonical_text_supported_advisory")
        self.assertEqual(row["resolution"], "manual_review_required")
        self.assertTrue(row["manual_review_required"])
        self.assertTrue(row["automatic_adjudication_performed"])
        self.assertEqual(row["manual_review_mode"], "investigate")
        self.assertFalse(row["automatic_review_resolution_performed"])
        self.assertFalse(row["automatic_text_change_allowed"])
        self.assertEqual(result["summary"]["text_supported_advisory_count"], 1)
        self.assertEqual(result["summary"]["automatic_review_resolution_count"], 0)

    def test_v127_supported_timing_becomes_confirm_only_advisory(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
            "timing_decisions": [_timing(1, proposal=11_200)],
            "text_decisions": [{"cue_ordinal": 1, "action": "unchanged"}],
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [_job(1)],
        }
        acoustic = {"schema_version": "1.4", "jobs": [_acoustic(1, shift_ms=650)]}

        result = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            acoustic_evidence=acoustic,
        )
        row = result["decisions"][0]
        self.assertEqual(row["timing_state"], "smart_candidate_supported")
        self.assertEqual(row["timing_resolution"], "candidate_confirmed_advisory")
        self.assertEqual(row["recommended_start_ms"], 11_200)
        self.assertEqual(
            row["timing_recommendation"],
            "apply_smart_candidate_after_manual_confirmation",
        )
        self.assertTrue(row["manual_review_required"])
        self.assertTrue(row["automatic_adjudication_performed"])
        self.assertEqual(row["manual_review_mode"], "confirm_recommendation")
        self.assertTrue(row["recommendation_requires_manual_confirmation"])
        self.assertFalse(row["automatic_review_resolution_performed"])
        self.assertFalse(row["independent_vocal_onset_evidence_used"])
        self.assertFalse(row["automatic_timing_change_allowed"])
        self.assertEqual(result["summary"]["confirm_only_manual_review_count"], 1)

    def test_v127_insufficient_asr_stays_investigative(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
            "timing_decisions": [{"cue_ordinal": 4, "action": "unchanged"}],
            "text_decisions": [
                {
                    "cue_ordinal": 4,
                    "canonical_ordinal": 9,
                    "canonical_span": [9, 10],
                    "action": "review",
                    "reason": "low_or_structurally_unsafe_similarity",
                }
            ],
        }
        job = {
            "job_id": "job-4",
            "cue_ordinal": 4,
            "source_ordinal": 0,
            "canonical_line_index": 9,
            "canonical_text_sha256": "canonical-hash-9",
            "requested_capabilities": ["mix_asr", "word_timestamps"],
            "reasons": ["smart_text_review:low_or_structurally_unsafe_similarity"],
            "shadow_evidence_only": False,
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [job],
        }
        asr = {
            "schema_version": "1.0",
            "jobs": [{"job_id": "job-4", "canonical_text_support_score": 0.71}],
        }

        row = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            asr_evidence=asr,
        )["decisions"][0]
        self.assertEqual(row["text_state"], "text_review_asr_insufficient")
        self.assertEqual(row["text_resolution"], "manual_review_required")
        self.assertTrue(row["manual_review_required"])
        self.assertFalse(row["automatic_adjudication_performed"])
        self.assertEqual(row["manual_review_mode"], "investigate")

    def test_v127_ambiguous_text_support_never_auto_resolves(self) -> None:
        smart = {
            "schema_version": "smart-1.1",
            "policy_id": "smart-validation-policy-2026-08-22-v1.2.10",
            "timing_decisions": [{"cue_ordinal": 5, "action": "unchanged"}],
            "text_decisions": [
                {
                    "cue_ordinal": 5,
                    "canonical_ordinal": 11,
                    "canonical_span": [11, 12],
                    "action": "review",
                    "reason": "ambiguous_nearby_canonical_match",
                }
            ],
        }
        job = {
            "job_id": "job-5",
            "cue_ordinal": 5,
            "source_ordinal": 0,
            "canonical_line_index": 11,
            "canonical_text_sha256": "ambiguous-canonical-hash",
            "requested_capabilities": ["mix_asr", "word_timestamps"],
            "reasons": ["smart_text_review:ambiguous_nearby_canonical_match"],
            "shadow_evidence_only": False,
        }
        plan = {
            "schema_version": "1.1",
            "policy_id": "smart-to-pro-reason-aware-2026-08-22-v1.2.6",
            "jobs": [job],
        }
        asr = {
            "schema_version": "1.0",
            "jobs": [{"job_id": "job-5", "canonical_text_support_score": 0.95}],
        }

        row = build_pro_decisions(
            smart_report=smart,
            plan=plan,
            asr_evidence=asr,
        )["decisions"][0]
        self.assertEqual(row["text_state"], "canonical_text_supported")
        self.assertEqual(row["text_resolution"], "canonical_text_supported_advisory")
        self.assertTrue(row["manual_review_required"])
        self.assertTrue(row["automatic_adjudication_performed"])
        self.assertEqual(row["manual_review_mode"], "investigate")
        self.assertFalse(row["automatic_review_resolution_performed"])


if __name__ == "__main__":
    unittest.main()
