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
                "canonical_match_support_score": 0.9,
                "canonical_match_start_ms": start,
                "canonical_match_end_ms": end,
                "canonical_start_covered": True,
                "canonical_end_covered": True,
                "language_probability": 0.95,
                "segments": [{"start_ms": start, "end_ms": end}],
            }
        ],
    }


class V4EvidenceFusionTests(unittest.TestCase):
    def test_legacy_missing_coverage_is_readable_but_not_new_boundary_evidence(self):
        payload=asr();job=payload['jobs'][0]
        del job['canonical_start_covered'];del job['canonical_end_covered']
        row=build_evidence_fusion(timeline_payloads=[timeline()],asr_evidence=payload)['lines'][0]
        self.assertEqual(row['auxiliary_boundary_family_count'],0)
        family=next(f for f in row['families'] if f['family']=='asr')
        self.assertFalse(family['available']);self.assertIsNone(family['canonical_onset'])

    def test_coverage_flags_cannot_replace_missing_word_endpoints_with_segments(self):
        payload=asr();payload['jobs'][0]['canonical_match_end_ms']=None
        row=build_evidence_fusion(timeline_payloads=[timeline()],asr_evidence=payload)['lines'][0]
        self.assertEqual(row['auxiliary_boundary_family_count'],0)

    def test_partial_end_preserves_onset_without_inventing_interval_for_each_backend(self):
        from lyric_aligner.qa.semantic_sync import _line_audio_anchor
        for backend in ('faster_whisper','qwen3_asr'):
            with self.subTest(backend=backend):
                payload=asr();payload['backend']=backend
                payload['jobs'][0].update(canonical_start_covered=True,canonical_end_covered=False)
                row=build_evidence_fusion(timeline_payloads=[timeline()],asr_evidence=payload)['lines'][0]
                self.assertEqual(row['auxiliary_boundary_family_count'],0)
                anchor=_line_audio_anchor(row,min_forced_confidence=.8,min_asr_support_score=.8,conflict_ms=1000)
                self.assertTrue(anchor['available'])
                self.assertEqual(anchor['start_ms'],5120)
                self.assertFalse(row['automatic_timing_change_allowed'])

    def test_missing_prefix_or_ambiguity_never_supplies_onset(self):
        from lyric_aligner.qa.semantic_sync import _line_audio_anchor
        for flags in (dict(canonical_start_covered=False,canonical_end_covered=True),
                      dict(canonical_start_covered=True,canonical_end_covered=False,canonical_match_ambiguous=True)):
            payload=asr();payload['jobs'][0].update(flags)
            row=build_evidence_fusion(timeline_payloads=[timeline()],asr_evidence=payload)['lines'][0]
            anchor=_line_audio_anchor(row,min_forced_confidence=.8,min_asr_support_score=.8,conflict_ms=1000)
            self.assertFalse(anchor['available'])

    def test_qwen_is_one_asr_family_with_explicit_backend_identity(self):
        payload=asr();payload['backend']='qwen3_asr'
        payload['jobs'][0].update(canonical_start_covered=True,canonical_end_covered=True,language_probability=None)
        result=build_evidence_fusion(timeline_payloads=[timeline()],asr_evidence=payload)
        row=result['lines'][0]
        self.assertEqual(row['auxiliary_boundary_family_count'],1)
        family=next(f for f in row['families'] if f['family']=='asr')
        self.assertEqual(family['backend'],'qwen3_asr')
        self.assertIsNone(family['language_probability'])
        self.assertFalse(row['automatic_timing_change_allowed'])

    def test_qwen_partial_onset_cannot_fall_back_to_segment_envelope(self):
        payload=asr();payload['backend']='qwen3_asr'
        payload['jobs'][0]['canonical_start_covered']=False
        result=build_evidence_fusion(timeline_payloads=[timeline()],asr_evidence=payload)
        self.assertEqual(result['lines'][0]['auxiliary_boundary_family_count'],0)

    def test_qwen_partial_end_cannot_supply_whole_line_interval(self):
        payload=asr();payload['backend']='qwen3_asr'
        payload['jobs'][0].update(canonical_start_covered=True,canonical_end_covered=False)
        result=build_evidence_fusion(timeline_payloads=[timeline()],asr_evidence=payload)
        self.assertEqual(result['lines'][0]['auxiliary_boundary_family_count'],0)

    def test_source_only_is_low_and_never_release_eligible(self):
        result = build_evidence_fusion(timeline_payloads=[timeline()])
        row = result["lines"][0]
        self.assertEqual(row["shadow_level"], "LOW")
        self.assertFalse(row["shadow_level_calibrated"])
        self.assertFalse(row["release_gate_eligible"])
        self.assertFalse(row["automatic_timing_change_allowed"])
        self.assertFalse(result["release_gate_eligible"])

    def test_ambiguous_asr_cannot_fall_back_to_segment_envelope(self):
        payload = asr()
        payload['jobs'][0]['canonical_match_ambiguous'] = True
        payload['jobs'][0]['canonical_match_start_ms'] = None
        payload['jobs'][0]['canonical_match_end_ms'] = None
        result = build_evidence_fusion(timeline_payloads=[timeline()], asr_evidence=payload)
        self.assertEqual(result['lines'][0]['auxiliary_boundary_family_count'], 0)
        self.assertEqual(result['lines'][0]['shadow_level'], 'LOW')

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
