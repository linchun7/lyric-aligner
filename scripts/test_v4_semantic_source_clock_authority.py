from __future__ import annotations

import unittest

from lyric_aligner.qa.semantic_sync import audit_independent_audio_sync
from lyric_aligner.qa.source_clock_authority import (
    SOURCE_CLOCK_SEMANTIC_AUTHORITY_POLICY_ID,
    SourceClockAuthorityError,
    build_verified_source_clock_authority,
)


SHA_MAP = "a" * 64
SHA_ANALYSIS = "b" * 64
SHA_SELECTION = "c" * 64
SHA_PROTOCOL = "d" * 64
SHA_CANDIDATE = "9" * 64
SHA_SOURCE = "e" * 64
SHA_LYRIC = "f" * 64
SHA_CANON = "1" * 64
FINGERPRINT = "2" * 64
ALGORITHM = "4.0.0a20"


def source_clock_map() -> dict:
    return {
        "schema_version": "source-clock-map-1.0",
        "policy_id": "bpm-fixed-source-clock-1.0",
        "task_fingerprint_sha256": FINGERPRINT,
        "algorithm_version": ALGORITHM,
        "automatic_timing_change_allowed": True,
        "final_mix_promotion_selection_sha256": SHA_SELECTION,
        "final_mix_promotion_protocol_sha256": SHA_PROTOCOL,
        "final_mix_promotion_analysis_sha256": SHA_ANALYSIS,
        "tracks": [
            {
                "ordinal": 1,
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "source_audio_sha256": SHA_SOURCE,
                "canonical_lyric_sha256": SHA_LYRIC,
                "canonical_selection_sha256": SHA_CANON,
                "policy_id": "bpm-fixed-source-clock-1.0",
                "rate": 0.95,
                "offset_ms": 123.0,
                "provenance_sha256": SHA_ANALYSIS,
            }
        ],
    }


def promotion_analysis() -> dict:
    return {
        "schema_version": "source-clock-final-mix-promotion-v2-analysis-1.0",
        "purpose": "fresh unseen final-mix holdout verdict for source-clock production promotion",
        "selection_lock_sha256": SHA_SELECTION,
        "promotion_protocol_sha256": SHA_PROTOCOL,
        "human_used_for_clock_estimation": False,
        "human_used_only_as_final_nonregression_check": True,
        "promoted_count": 1,
        "promoted_ordinals": [1],
        "tracks": [
            {
                "ordinal": 1,
                "testable": True,
                "promoted": True,
                "metric_pass": True,
                "risk_pass": True,
                "human_pass": True,
                "valid_final_mix_anchor_count": 3,
                "source_clock": {
                    "policy_id": "bpm-fixed-source-clock-1.0",
                    "rate": 0.95,
                    "offset_ms": 123.0,
                    "provenance_sha256": SHA_CANDIDATE,
                },
                "new_metrics": {
                    "count": 3,
                    "median_abs_error_ms": 200.0,
                    "p90_abs_error_ms": 280.0,
                    "worst_abs_error_ms": 300.0,
                },
                "old_metrics": {
                    "count": 3,
                    "median_abs_error_ms": 1100.0,
                    "p90_abs_error_ms": 1260.0,
                    "worst_abs_error_ms": 1300.0,
                },
                "old_risk_score": 2055.0,
                "new_risk_score": 415.0,
                "risk_improvement_ms": 1640.0,
                "ledger": [
                    {"canonical_line_index": 0, "valid": True, "asr_start_ms": 10100, "old_projection_start_ms": 9000, "new_projection_start_ms": 10000, "support": 1.0, "mean_word_probability": 0.9, "ambiguous": False, "old_signed_error_ms": -1100, "new_signed_error_ms": -100},
                    {"canonical_line_index": 1, "valid": True, "asr_start_ms": 19800, "old_projection_start_ms": 19000, "new_projection_start_ms": 20000, "support": 1.0, "mean_word_probability": 0.9, "ambiguous": False, "old_signed_error_ms": -800, "new_signed_error_ms": 200},
                    {"canonical_line_index": 2, "valid": True, "asr_start_ms": 30300, "old_projection_start_ms": 29000, "new_projection_start_ms": 30000, "support": 1.0, "mean_word_probability": 0.9, "ambiguous": False, "old_signed_error_ms": -1300, "new_signed_error_ms": -300},
                ],
            }
        ],
    }


def promotion_selection() -> dict:
    return {
        "schema_version": "source-clock-final-mix-promotion-v2-selection-lock-1.0",
        "purpose": "fresh final-mix holdout selection excluding previously observed canonical identities",
        "expanded_candidate_sha256": SHA_CANDIDATE,
        "promotion_protocol_sha256": SHA_PROTOCOL,
        "final_mix_asr_result_read": False,
        "human_result_used_for_selection": False,
        "candidate_count": 1,
        "tracks": [
            {
                "ordinal": 1,
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "source_audio_sha256": SHA_SOURCE,
                "canonical_lyric_sha256": SHA_LYRIC,
                "canonical_selection_sha256": SHA_CANON,
                "source_clock": {
                    "policy_id": "bpm-fixed-source-clock-1.0",
                    "rate": 0.95,
                    "offset_ms": 123.0,
                    "provenance_sha256": SHA_CANDIDATE,
                },
                "testable": True,
                "selected_probe_count": 3,
                "probes": [
                    {"canonical_line_index": 0, "old_projection_start_ms": 9000, "new_projection_start_ms": 10000},
                    {"canonical_line_index": 1, "old_projection_start_ms": 19000, "new_projection_start_ms": 20000},
                    {"canonical_line_index": 2, "old_projection_start_ms": 29000, "new_projection_start_ms": 30000},
                ],
            }
        ],
    }


def promotion_protocol() -> dict:
    return {
        "schema_version": "source-clock-final-mix-promotion-protocol-2.0",
        "purpose": "fresh final-mix holdout promotion for BPM-fixed source-clock candidates",
        "valid_final_mix_anchor": {
            "canonical_start_covered": True,
            "canonical_match_ambiguous": False,
            "minimum_canonical_match_support_score": 0.85,
            "minimum_mean_word_probability": 0.70,
        },
        "minimum_valid_final_mix_anchors": 3,
        "new_projection_thresholds_ms": {
            "maximum_median_abs_error": 750,
            "maximum_p90_abs_error": 900,
            "maximum_worst_abs_error": 1200,
        },
        "risk_score": "median_abs_error + 0.5*p90_abs_error + 0.25*worst_abs_error",
        "minimum_risk_score_improvement_ms": 300,
        "human_check_if_present": {
            "maximum_new_abs_error_ms": 1000,
            "must_not_regress_vs_old_projection": True,
            "human_result_never_changes_rate_or_offset": True,
        },
    }


def run() -> dict:
    return {
        "task_fingerprint_sha256": FINGERPRINT,
        "algorithm_version": ALGORITHM,
        "occurrences": [
            {
                "ordinal": 1,
                "occurrence_id": "occ-1",
                "source_clock": {
                    "policy_id": "bpm-fixed-source-clock-1.0",
                    "rate": 0.95,
                    "offset_ms": 123.0,
                    "provenance_sha256": SHA_ANALYSIS,
                },
            }
        ],
    }


def timeline() -> dict:
    return {
        "task_fingerprint_sha256": FINGERPRINT,
        "algorithm_version": ALGORITHM,
        "source_clock_map_sha256": SHA_MAP,
        "occurrence_id": "occ-1",
        "track_id": "track-1",
        "result": {"canonical_selection_sha256": SHA_CANON},
    }


def editor_audit(*, reliable: bool = False) -> dict:
    return {
        "tracks": [
            {
                "ordinal": 1,
                "eligible_source_cue_count": 10,
                "match_count": 10 if reliable else 0,
                "match_fraction": 1.0 if reliable else 0.0,
                "passed": reliable,
            }
        ]
    }


def fusion(*, asr_shift_ms: int | None = None) -> dict:
    lines = []
    for index, start in enumerate((10_000, 20_000, 30_000)):
        families = []
        if asr_shift_ms is not None:
            families.append(
                {
                    "family": "asr",
                    "canonical_onset": {
                        "start_ms": start + asr_shift_ms,
                        "support_score": 0.99,
                        "basis": "canonical_prefix_word_span",
                        "canonical_start_covered": True,
                        "canonical_match_ambiguous": False,
                    },
                }
            )
        lines.append(
            {
                "ordinal": 1,
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "canonical_line_index": index,
                "source_timeline_boundary_ms": [start, start + 1000],
                "families": families,
            }
        )
    return {"mode": "shadow_only", "lines": lines}


class SourceClockAuthorityReplayTests(unittest.TestCase):
    def authority(self) -> dict[int, dict]:
        return build_verified_source_clock_authority(
            source_clock_map=source_clock_map(),
            promotion_analysis=promotion_analysis(),
            promotion_selection=promotion_selection(),
            promotion_protocol=promotion_protocol(),
            run=run(),
            timelines_by_ordinal={1: timeline()},
            source_clock_map_sha256=SHA_MAP,
            promotion_analysis_sha256=SHA_ANALYSIS,
            promotion_selection_sha256=SHA_SELECTION,
            promotion_protocol_sha256=SHA_PROTOCOL,
        )

    def test_exact_v2_replay_grants_only_explicit_authority(self) -> None:
        authority = self.authority()
        self.assertEqual(set(authority), {1})
        self.assertEqual(authority[1]["policy_id"], SOURCE_CLOCK_SEMANTIC_AUTHORITY_POLICY_ID)
        self.assertEqual(authority[1]["holdout_signed_errors_ms"], [-100, 200, -300])

    def test_timeline_map_hash_mismatch_fails_closed(self) -> None:
        bad = timeline()
        bad["source_clock_map_sha256"] = "3" * 64
        with self.assertRaisesRegex(SourceClockAuthorityError, "timeline map SHA mismatch"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=promotion_analysis(),
                promotion_selection=promotion_selection(),
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: bad},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256=SHA_SELECTION,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )

    def test_legacy_overlap_missing_map_sha_can_replay_exact_source_clock_numerically(self) -> None:
        legacy = timeline()
        legacy.pop("source_clock_map_sha256")
        legacy["mapping_source"] = "confirmed_overlap_primary_timeline_extension"
        legacy["result"]["source_clock"] = {
            "policy_id": "bpm-fixed-source-clock-1.0",
            "rate": 0.95,
            "offset_ms": 123.0,
            "provenance_sha256": SHA_ANALYSIS,
        }
        legacy["result"]["lines"] = [
            {
                "canonical_clock_start_ms": 1000,
                "canonical_clock_end_ms": 1500,
                "source_start_ms": 1073,
                "source_end_ms": 1548,
            },
            {
                "canonical_clock_start_ms": 2000,
                "canonical_clock_end_ms": 2500,
                "source_start_ms": 2023,
                "source_end_ms": 2498,
            },
            {
                "canonical_clock_start_ms": 3000,
                "canonical_clock_end_ms": 3500,
                "source_start_ms": 2973,
                "source_end_ms": 3448,
            },
            {
                "canonical_clock_start_ms": 4000,
                "canonical_clock_end_ms": None,
                "source_start_ms": 3923,
                "source_end_ms": None,
            },
        ]
        authority = build_verified_source_clock_authority(
            source_clock_map=source_clock_map(),
            promotion_analysis=promotion_analysis(),
            promotion_selection=promotion_selection(),
            promotion_protocol=promotion_protocol(),
            run=run(),
            timelines_by_ordinal={1: legacy},
            source_clock_map_sha256=SHA_MAP,
            promotion_analysis_sha256=SHA_ANALYSIS,
            promotion_selection_sha256=SHA_SELECTION,
            promotion_protocol_sha256=SHA_PROTOCOL,
        )
        self.assertEqual(authority[1]["timeline_map_binding"], "legacy_overlap_numeric_replay")

    def test_missing_map_sha_without_exact_overlap_numeric_replay_fails_closed(self) -> None:
        legacy = timeline()
        legacy.pop("source_clock_map_sha256")
        with self.assertRaisesRegex(SourceClockAuthorityError, "legacy overlap clock replay failed"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=promotion_analysis(),
                promotion_selection=promotion_selection(),
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: legacy},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256=SHA_SELECTION,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )

    def test_legacy_overlap_numeric_replay_rejects_wrong_source_onset(self) -> None:
        legacy = timeline()
        legacy.pop("source_clock_map_sha256")
        legacy["mapping_source"] = "confirmed_overlap_primary_timeline_extension"
        legacy["result"]["source_clock"] = {
            "policy_id": "bpm-fixed-source-clock-1.0",
            "rate": 0.95,
            "offset_ms": 123.0,
            "provenance_sha256": SHA_ANALYSIS,
        }
        legacy["result"]["lines"] = [
            {
                "canonical_clock_start_ms": 1000,
                "canonical_clock_end_ms": 1500,
                "source_start_ms": 9999,
                "source_end_ms": 1548,
            },
        ]
        with self.assertRaisesRegex(SourceClockAuthorityError, "legacy overlap clock replay failed"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=promotion_analysis(),
                promotion_selection=promotion_selection(),
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: legacy},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256=SHA_SELECTION,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )

    def test_unpromoted_or_underpowered_holdout_cannot_gain_authority(self) -> None:
        analysis = promotion_analysis()
        analysis["tracks"][0]["valid_final_mix_anchor_count"] = 2
        with self.assertRaisesRegex(SourceClockAuthorityError, "holdout ledger/count mismatch"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=analysis,
                promotion_selection=promotion_selection(),
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: timeline()},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256=SHA_SELECTION,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )

    def test_selection_identity_is_bound_to_the_source_clock_map(self) -> None:
        selection = promotion_selection()
        selection["tracks"][0]["source_audio_sha256"] = "0" * 64
        with self.assertRaisesRegex(SourceClockAuthorityError, "source_audio_sha256 differs"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=promotion_analysis(),
                promotion_selection=selection,
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: timeline()},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256=SHA_SELECTION,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )

    def test_actual_selection_sha_must_match_the_map_and_analysis_binding(self) -> None:
        with self.assertRaisesRegex(SourceClockAuthorityError, "map/promotion selection SHA mismatch"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=promotion_analysis(),
                promotion_selection=promotion_selection(),
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: timeline()},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256="0" * 64,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )

    def test_analysis_metric_pass_is_recomputed_from_the_ledger(self) -> None:
        analysis = promotion_analysis()
        analysis["tracks"][0]["new_metrics"]["median_abs_error_ms"] = 1.0
        with self.assertRaisesRegex(SourceClockAuthorityError, "new_metrics median_abs_error_ms does not replay"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=analysis,
                promotion_selection=promotion_selection(),
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: timeline()},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256=SHA_SELECTION,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )

    def test_valid_ledger_row_must_meet_frozen_anchor_thresholds(self) -> None:
        analysis = promotion_analysis()
        analysis["tracks"][0]["ledger"][0]["support"] = 0.1
        with self.assertRaisesRegex(SourceClockAuthorityError, "valid ledger row violates frozen anchor policy"):
            build_verified_source_clock_authority(
                source_clock_map=source_clock_map(),
                promotion_analysis=analysis,
                promotion_selection=promotion_selection(),
                promotion_protocol=promotion_protocol(),
                run=run(),
                timelines_by_ordinal={1: timeline()},
                source_clock_map_sha256=SHA_MAP,
                promotion_analysis_sha256=SHA_ANALYSIS,
                promotion_selection_sha256=SHA_SELECTION,
                promotion_protocol_sha256=SHA_PROTOCOL,
            )


class SourceClockSemanticGateTests(unittest.TestCase):
    def authority(self) -> dict[int, dict]:
        return SourceClockAuthorityReplayTests().authority()

    def final_rows(self, shift_ms: int = 0) -> list[dict]:
        return [
            {
                "occurrence_id": "occ-1",
                "canonical_line_index": index,
                "start_ms": start + shift_ms,
            }
            for index, start in enumerate((10_000, 20_000, 30_000))
        ]

    def test_verified_source_clock_can_replace_editor_timing_as_independent_basis(self) -> None:
        unreliable = editor_audit(reliable=False)
        result = audit_independent_audio_sync(
            fusion(),
            self.final_rows(),
            editor_projection_audit=unreliable,
            editor_final_audit=unreliable,
            verified_source_clock_authority_by_ordinal=self.authority(),
        )
        self.assertTrue(result["passed"], result)
        track = result["final_sync"]["tracks"][0]
        self.assertEqual(track["evidence_basis"], "verified_source_clock_final_mix_holdout")
        self.assertTrue(track["verified_source_clock_authority"])
        self.assertEqual(track["median_abs_delta_ms"], 0.0)

    def test_verified_projection_does_not_hide_final_product_regression(self) -> None:
        unreliable = editor_audit(reliable=False)
        result = audit_independent_audio_sync(
            fusion(),
            self.final_rows(shift_ms=4_000),
            editor_projection_audit=unreliable,
            editor_final_audit=unreliable,
            verified_source_clock_authority_by_ordinal=self.authority(),
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "median_final_audio_timing_error_exceeded",
            result["final_sync"]["tracks"][0]["errors"],
        )

    def test_current_independent_audio_can_rebut_old_promoted_source_clock(self) -> None:
        unreliable = editor_audit(reliable=False)
        result = audit_independent_audio_sync(
            fusion(asr_shift_ms=4_000),
            self.final_rows(),
            editor_projection_audit=unreliable,
            editor_final_audit=unreliable,
            verified_source_clock_authority_by_ordinal=self.authority(),
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "verified_source_clock_current_audio_rebuttal",
            result["final_sync"]["tracks"][0]["errors"],
        )

    def test_plain_asr_only_behavior_is_unchanged_without_verified_authority(self) -> None:
        unreliable = editor_audit(reliable=False)
        result = audit_independent_audio_sync(
            fusion(asr_shift_ms=0),
            self.final_rows(),
            editor_projection_audit=unreliable,
            editor_final_audit=unreliable,
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "insufficient_independent_audio_semantic_evidence",
            result["final_sync"]["tracks"][0]["errors"],
        )


if __name__ == "__main__":
    unittest.main()
