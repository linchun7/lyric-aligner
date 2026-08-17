import hashlib
import unittest

from lyric_aligner.evidence.fusion import (
    EvidenceFusionConfig,
    EvidenceFusionError,
    build_evidence_fusion,
)


def timeline(text: str = "canonical line") -> dict:
    return {
        "result": {
            "occurrence_id": "occ-1",
            "track_id": "track-1",
            "ordinal": 1,
            "language_profile": "en",
            "canonical_selection_sha256": "a" * 64,
            "lines": [
                {
                    "canonical_line_index": 0,
                    "text": text,
                    "source_start_ms": 1000,
                    "source_end_ms": 2200,
                    "mix_start_ms": 5000,
                    "mix_end_ms": 6200,
                }
            ],
        }
    }


def editor(text: str = "canonical line", *, onset: int = 100, offset: int = 100) -> dict:
    return {
        "mode": "shadow_only",
        "authority": {"automatic_timing_change_allowed": False},
        "occurrences": [
            {
                "occurrence_id": "occ-1",
                "lines": [
                    {
                        "canonical_line_index": 0,
                        "canonical_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "best_editor_cue_number": 7,
                        "suggested_onset_delta_ms": onset,
                        "suggested_offset_delta_ms": offset,
                        "best_candidate_margin_uncalibrated": 0.2,
                        "candidates": [{"timing_support_score": 0.9}],
                    }
                ],
            }
        ],
    }


def asr(*, start: int = 5120, end: int = 6310) -> dict:
    return {
        "backend": "faster_whisper",
        "jobs": [
            {
                "job_id": "job-1",
                "occurrence_id": "occ-1",
                "canonical_line_index": 0,
                "canonical_text_support_score": 0.9,
                "language_probability": 0.95,
                "segments": [{"start_ms": start, "end_ms": end}],
            }
        ],
    }


class V4EvidenceFusionTests(unittest.TestCase):
    def test_source_only_is_low_and_never_release_eligible(self):
        result = build_evidence_fusion(timeline_payloads=[timeline()])
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "LOW")
        self.assertFalse(row["shadow_level_calibrated"])
        self.assertFalse(row["release_gate_eligible"])
        self.assertFalse(row["automatic_timing_change_allowed"])
        self.assertFalse(result["release_gate_eligible"])

    def test_one_auxiliary_family_is_medium(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()], editor_evidence=editor()
        )
        self.assertEqual(result["lines"][0]["shadow_level"], "MEDIUM")
        self.assertEqual(result["lines"][0]["auxiliary_boundary_family_count"], 1)

    def test_editor_and_asr_agreement_is_high_but_still_shadow_only(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()],
            editor_evidence=editor(onset=100, offset=100),
            asr_evidence=asr(start=5120, end=6310),
            config=EvidenceFusionConfig(conflict_boundary_ms=500),
        )
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "HIGH")
        self.assertLessEqual(row["editor_asr_boundary_disagreement_ms"], 500)
        self.assertFalse(row["release_gate_eligible"])
        self.assertFalse(result["policy_calibrated"])

    def test_editor_and_asr_disagreement_is_conflict(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()],
            editor_evidence=editor(onset=0, offset=0),
            asr_evidence=asr(start=5900, end=7100),
            config=EvidenceFusionConfig(conflict_boundary_ms=500),
        )
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "CONFLICT")
        self.assertGreater(row["editor_asr_boundary_disagreement_ms"], 500)
        self.assertFalse(row["release_gate_eligible"])

    def test_editor_text_identity_mismatch_fails_closed(self):
        with self.assertRaises(EvidenceFusionError):
            build_evidence_fusion(
                timeline_payloads=[timeline("truth")],
                editor_evidence=editor("different"),
            )

    def test_unknown_auxiliary_line_fails_closed(self):
        payload = asr()
        payload["jobs"][0]["canonical_line_index"] = 99
        with self.assertRaises(EvidenceFusionError):
            build_evidence_fusion(
                timeline_payloads=[timeline()], asr_evidence=payload
            )


if __name__ == "__main__":
    unittest.main()
