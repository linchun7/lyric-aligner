from __future__ import annotations

import unittest

from lyric_aligner.qa.semantic_sync import (
    TrackWindow,
    audit_independent_audio_sync,
    audit_semantic_sync,
    match_track_semantics,
)
from lyric_aligner.srt import Cue


class SemanticSyncAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = [TrackWindow(1, "Artist - Track", 0, 120_000)]
        self.source = [
            Cue(1, 10_000, 13_000, "alpha beta gamma"),
            Cue(2, 20_000, 23_000, "delta epsilon"),
            Cue(3, 30_000, 33_000, "zeta eta"),
            Cue(4, 40_000, 43_000, "theta iota"),
            Cue(5, 50_000, 53_000, "kappa lambda"),
        ]

    def test_resegmented_candidate_with_same_onsets_passes(self) -> None:
        candidate = [
            Cue(1, 10_120, 11_000, "alpha"),
            Cue(2, 11_000, 13_000, "beta gamma"),
            Cue(3, 19_850, 23_000, "delta epsilon"),
            Cue(4, 30_250, 33_000, "zeta eta"),
            Cue(5, 39_900, 43_000, "theta iota"),
            Cue(6, 50_300, 53_000, "kappa lambda"),
        ]
        result = audit_semantic_sync(self.source, candidate, self.window)
        self.assertTrue(result["passed"], result["errors"])
        track = result["tracks"][0]
        self.assertEqual(track["match_count"], 5)
        self.assertLess(track["median_abs_delta_ms"], 500)

    def test_systematic_twelve_second_shift_fails_closed(self) -> None:
        candidate = [
            Cue(index, cue.start_ms + 12_000, cue.end_ms + 12_000, cue.text)
            for index, cue in enumerate(self.source, 1)
        ]
        result = audit_semantic_sync(self.source, candidate, self.window)
        self.assertFalse(result["passed"])
        track = result["tracks"][0]
        self.assertEqual(track["median_abs_delta_ms"], 12_000.0)
        self.assertEqual(track["large_error_fraction"], 1.0)
        self.assertIn("median_semantic_timing_error_exceeded", track["errors"])
        self.assertIn("large_semantic_timing_error_fraction_exceeded", track["errors"])

    def test_repeated_later_chorus_cannot_replace_nearby_match(self) -> None:
        source = [
            Cue(1, 10_000, 13_000, "same chorus words"),
            Cue(2, 20_000, 23_000, "unique middle words"),
        ]
        candidate = [
            Cue(1, 10_200, 13_000, "same chorus words"),
            Cue(2, 20_100, 23_000, "unique middle words"),
            Cue(3, 70_000, 73_000, "same chorus words"),
        ]
        matches = match_track_semantics(source, candidate)
        self.assertEqual(matches[0]["candidate_start_ms"], 10_200)
        self.assertEqual(matches[1]["candidate_start_ms"], 20_100)

    def test_low_anchor_coverage_blocks_release_quality(self) -> None:
        candidate = [Cue(1, 10_000, 13_000, "unrelated text")]
        result = audit_semantic_sync(self.source, candidate, self.window)
        self.assertFalse(result["passed"])
        self.assertIn(
            "insufficient_semantic_anchor_coverage",
            result["tracks"][0]["errors"],
        )


class IndependentAudioSemanticSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.starts = [10_000, 20_000, 30_000, 40_000, 50_000]
        self.texts = [
            "alpha beta gamma",
            "delta epsilon words",
            "zeta eta words",
            "theta iota words",
            "kappa lambda words",
        ]
        self.window = [TrackWindow(1, "Artist - Track", 0, 120_000)]

    def cues(self, *, shift_ms: int = 0, texts: list[str] | None = None) -> list[Cue]:
        values = self.texts if texts is None else texts
        return [
            Cue(index + 1, start + shift_ms, start + shift_ms + 1500, values[index])
            for index, start in enumerate(self.starts)
        ]

    def final_rows(self, *, shift_ms: int = 0) -> list[dict]:
        return [
            {
                "occurrence_id": "occ-1",
                "canonical_line_index": index,
                "start_ms": start + shift_ms,
            }
            for index, start in enumerate(self.starts)
        ]

    def fusion(
        self,
        *,
        projection_shift_ms: int = 0,
        forced: bool = True,
        asr: bool = False,
        asr_shift_ms: int = 0,
    ) -> dict:
        lines = []
        for index, start in enumerate(self.starts):
            families = [
                {
                    "family": "source_timeline",
                    "available": True,
                    "boundary_ms": [start + projection_shift_ms, start + projection_shift_ms + 1500],
                }
            ]
            if forced:
                families.append(
                    {
                        "family": "forced_alignment",
                        "available": True,
                        "boundary_ms": [start, start + 1500],
                        "line_confidence": 0.95,
                    }
                )
            if asr:
                families.append(
                    {
                        "family": "asr",
                        "available": True,
                        "boundary_ms": [start + asr_shift_ms, start + asr_shift_ms + 1500],
                        "canonical_match_support_score": 0.95,
                        "boundary_basis": "canonical_word_span",
                    }
                )
            lines.append(
                {
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "canonical_line_index": index,
                    "source_timeline_boundary_ms": [
                        start + projection_shift_ms,
                        start + projection_shift_ms + 1500,
                    ],
                    "families": families,
                }
            )
        return {"mode": "shadow_only", "lines": lines}

    def editor_audits(
        self,
        projection_cues: list[Cue],
        final_cues: list[Cue],
        *,
        source_texts: list[str] | None = None,
    ) -> tuple[dict, dict]:
        source = self.cues(texts=source_texts)
        return (
            audit_semantic_sync(source, projection_cues, self.window),
            audit_semantic_sync(source, final_cues, self.window),
        )

    def test_forced_alignment_catches_twelve_second_projection_and_final_shift(self) -> None:
        projection = self.cues(shift_ms=12_000)
        final = self.cues(shift_ms=12_000)
        editor_projection, editor_final = self.editor_audits(projection, final)
        result = audit_independent_audio_sync(
            self.fusion(projection_shift_ms=12_000, forced=True),
            self.final_rows(shift_ms=12_000),
            editor_projection_audit=editor_projection,
            editor_final_audit=editor_final,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["projection_sync"]["tracks"][0]["median_abs_delta_ms"],
            12_000.0,
        )
        self.assertEqual(
            result["final_sync"]["tracks"][0]["median_abs_delta_ms"],
            12_000.0,
        )
        self.assertEqual(
            result["projection_sync"]["tracks"][0]["evidence_basis"],
            "forced_alignment",
        )

    def test_poor_editor_recognition_does_not_block_good_forced_alignment(self) -> None:
        projection = self.cues()
        final = self.cues()
        garbage = ["totally unrelated recognition"] * len(self.texts)
        editor_projection, editor_final = self.editor_audits(
            projection,
            final,
            source_texts=garbage,
        )
        result = audit_independent_audio_sync(
            self.fusion(forced=True),
            self.final_rows(),
            editor_projection_audit=editor_projection,
            editor_final_audit=editor_final,
        )
        self.assertTrue(result["passed"], result)
        track = result["projection_sync"]["tracks"][0]
        self.assertFalse(track["editor_witness_reliable"])
        self.assertEqual(track["evidence_basis"], "forced_alignment")

    def test_poor_editor_recognition_cannot_authorize_asr_only_release(self) -> None:
        projection = self.cues()
        final = self.cues()
        garbage = ["totally unrelated recognition"] * len(self.texts)
        editor_projection, editor_final = self.editor_audits(
            projection,
            final,
            source_texts=garbage,
        )
        result = audit_independent_audio_sync(
            self.fusion(forced=False, asr=True),
            self.final_rows(),
            editor_projection_audit=editor_projection,
            editor_final_audit=editor_final,
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "insufficient_independent_audio_semantic_evidence",
            result["projection_sync"]["tracks"][0]["errors"],
        )

    def test_reliable_editor_plus_asr_can_supply_fallback_evidence(self) -> None:
        projection = self.cues()
        final = self.cues()
        editor_projection, editor_final = self.editor_audits(projection, final)
        result = audit_independent_audio_sync(
            self.fusion(forced=False, asr=True),
            self.final_rows(),
            editor_projection_audit=editor_projection,
            editor_final_audit=editor_final,
        )
        self.assertTrue(result["passed"], result)
        self.assertEqual(
            result["projection_sync"]["tracks"][0]["evidence_basis"],
            "asr_plus_reliable_editor",
        )

    def test_forced_and_asr_conflict_blocks_release(self) -> None:
        projection = self.cues()
        final = self.cues()
        editor_projection, editor_final = self.editor_audits(projection, final)
        result = audit_independent_audio_sync(
            self.fusion(forced=True, asr=True, asr_shift_ms=4_000),
            self.final_rows(),
            editor_projection_audit=editor_projection,
            editor_final_audit=editor_final,
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "independent_audio_family_conflict",
            result["projection_sync"]["tracks"][0]["errors"],
        )


if __name__ == "__main__":
    unittest.main()
