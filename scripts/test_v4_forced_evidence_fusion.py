import hashlib
import unittest

from lyric_aligner.evidence.fusion import (
    EvidenceFusionConfig,
    EvidenceFusionError,
    build_evidence_fusion,
)


TEXT = "canonical line"
TEXT_SHA = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()


def timeline() -> dict:
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
                    "text": TEXT,
                    "source_start_ms": 1000,
                    "source_end_ms": 2200,
                    "mix_start_ms": 5000,
                    "mix_end_ms": 6200,
                }
            ],
        }
    }


def editor(*, start_delta: int = 100, end_delta: int = 100) -> dict:
    return {
        "mode": "shadow_only",
        "authority": {"automatic_timing_change_allowed": False},
        "occurrences": [
            {
                "occurrence_id": "occ-1",
                "lines": [
                    {
                        "canonical_line_index": 0,
                        "canonical_text_sha256": TEXT_SHA,
                        "best_editor_cue_number": 1,
                        "suggested_onset_delta_ms": start_delta,
                        "suggested_offset_delta_ms": end_delta,
                        "candidates": [{"timing_support_score": 0.9}],
                    }
                ],
            }
        ],
    }


def asr(*, start: int = 5120, end: int = 6320) -> dict:
    return {
        "backend": "faster_whisper",
        "jobs": [
            {
                "job_id": "asr-1",
                "occurrence_id": "occ-1",
                "canonical_line_index": 0,
                "canonical_text_support_score": 0.9,
                "segments": [{"start_ms": start, "end_ms": end}],
            }
        ],
    }


def forced(
    *,
    start: int | None = 5110,
    end: int | None = 6310,
    status: str = "projected",
    reason: str | None = None,
) -> dict:
    return {
        "mode": "forced_alignment_mix_projection",
        "source_evidence_backend": "external_forced_aligner",
        "primary_timing_authority": "source_to_mix_only",
        "forced_alignment_authority": "auxiliary_acoustic_evidence_only",
        "jobs": [
            {
                "job_id": "forced-1",
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "canonical_line_index": 0,
                "canonical_text_sha256": TEXT_SHA,
                "projection_status": status,
                "projection_reason": reason,
                "mix_start_ms": start,
                "mix_end_ms": end,
                "line_confidence": 0.88,
                "backend_id": "test-aligner",
                "backend_version": "1",
                "model_id": "test-model",
                "model_revision": "r1",
            }
        ],
    }


class V4ForcedEvidenceFusionTests(unittest.TestCase):
    def test_forced_only_is_medium_shadow(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()], forced_mix_evidence=forced()
        )
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "MEDIUM")
        self.assertEqual(row["auxiliary_boundary_family_count"], 1)
        family = next(f for f in row["families"] if f["family"] == "forced_alignment")
        self.assertTrue(family["available"])
        self.assertEqual(family["boundary_ms"], [5110, 6310])
        self.assertFalse(result["release_gate_eligible"])

    def test_editor_and_forced_agreement_is_high(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()],
            editor_evidence=editor(),
            forced_mix_evidence=forced(),
            config=EvidenceFusionConfig(conflict_boundary_ms=500),
        )
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "HIGH")
        self.assertEqual(row["editor_forced_boundary_disagreement_ms"], 10)
        self.assertEqual(row["max_auxiliary_boundary_disagreement_ms"], 10)

    def test_asr_and_forced_disagreement_is_conflict(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()],
            asr_evidence=asr(start=5100, end=6300),
            forced_mix_evidence=forced(start=5900, end=7100),
            config=EvidenceFusionConfig(conflict_boundary_ms=500),
        )
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "CONFLICT")
        self.assertGreater(row["asr_forced_boundary_disagreement_ms"], 500)
        self.assertFalse(row["automatic_timing_change_allowed"])

    def test_any_outlier_blocks_three_family_high(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()],
            editor_evidence=editor(),
            asr_evidence=asr(),
            forced_mix_evidence=forced(start=5900, end=7100),
            config=EvidenceFusionConfig(conflict_boundary_ms=500),
        )
        row = result["lines"][0]
        self.assertEqual(row["auxiliary_boundary_family_count"], 3)
        self.assertEqual(row["shadow_level"], "CONFLICT")
        self.assertGreater(row["max_auxiliary_boundary_disagreement_ms"], 500)

    def test_unprojectable_forced_is_reported_but_not_counted(self):
        result = build_evidence_fusion(
            timeline_payloads=[timeline()],
            forced_mix_evidence=forced(
                start=None,
                end=None,
                status="unprojectable",
                reason="source_interval_crosses_confirmed_cut",
            ),
        )
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "LOW")
        self.assertEqual(row["auxiliary_boundary_family_count"], 0)
        family = next(f for f in row["families"] if f["family"] == "forced_alignment")
        self.assertFalse(family["available"])
        self.assertEqual(family["reason"], "source_interval_crosses_confirmed_cut")
        self.assertEqual(
            result["summary"]["forced_alignment_line_counts"],
            {"projected": 0, "unprojectable": 1, "absent": 0},
        )

    def test_forced_canonical_identity_mismatch_fails_closed(self):
        payload = forced()
        payload["jobs"][0]["canonical_text_sha256"] = "0" * 64
        with self.assertRaises(EvidenceFusionError):
            build_evidence_fusion(
                timeline_payloads=[timeline()], forced_mix_evidence=payload
            )

    def test_unknown_forced_line_fails_closed(self):
        payload = forced()
        payload["jobs"][0]["canonical_line_index"] = 99
        with self.assertRaises(EvidenceFusionError):
            build_evidence_fusion(
                timeline_payloads=[timeline()], forced_mix_evidence=payload
            )

    def test_unprojectable_payload_cannot_smuggle_mix_boundary(self):
        payload = forced(
            start=5100,
            end=6300,
            status="unprojectable",
            reason="source_interval_crosses_confirmed_cut",
        )
        with self.assertRaises(EvidenceFusionError):
            build_evidence_fusion(
                timeline_payloads=[timeline()], forced_mix_evidence=payload
            )


if __name__ == "__main__":
    unittest.main()
