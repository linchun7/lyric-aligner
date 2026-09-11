import unittest

from lyric_aligner.review.transition_lexical import (
    TransitionLexicalEvidenceError,
    TransitionLexicalPolicy,
    build_transition_lexical_evidence,
)
from lyric_aligner.text_repair import SubtitleCue, _normalize_for_match


def cue(ordinal: int, start: str, end: str, text: str) -> SubtitleCue:
    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"{start} --> {end}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=ordinal,
    )


def timeline(occurrence_id: str, lines: list[tuple[int, str]]) -> dict:
    return {
        "result": {
            "occurrence_id": occurrence_id,
            "lines": [
                {
                    "canonical_line_index": index,
                    "mix_start_ms": start,
                    "mix_end_ms": None,
                    "text": text,
                }
                for index, (start, text) in enumerate(lines)
            ],
        }
    }


class TestTransitionLexicalEvidence(unittest.TestCase):
    def base(self):
        issue = {
            "kind": "transition_ambiguity",
            "code": "ambiguous_source_occurrence",
            "candidate_id": "candidate",
            "left_occurrence_id": "left",
            "right_occurrence_id": "right",
        }
        transition = {
            "left_occurrence_id": "left",
            "right_occurrence_id": "right",
            "nominal_boundary": 10.0,
        }
        left_occurrence = {"occurrence_id": "left", "mapping_blocked": False}
        right_occurrence = {"occurrence_id": "right", "mapping_blocked": False}
        left = timeline("left", [(6000, "unique left lyric ending")])
        right = timeline("right", [(10500, "unique right lyric beginning")])
        return issue, transition, left_occurrence, right_occurrence, left, right

    def test_unique_sequential_editor_lyrics_are_shadow_clear_candidate(self):
        issue, transition, left_occ, right_occ, left, right = self.base()
        editor = [
            cue(0, "00:00:07,000", "00:00:09,000", "unique left lyric ending"),
            cue(1, "00:00:11,000", "00:00:13,000", "unique right lyric beginning"),
        ]
        result = build_transition_lexical_evidence(
            issue=issue,
            transition=transition,
            left_occurrence=left_occ,
            right_occurrence=right_occ,
            left_timeline=left,
            right_timeline=right,
            editor_cues=editor,
        )
        self.assertEqual(result["recommendation"], "clear_candidate")
        self.assertTrue(result["order_support"]["lexical_order_clear"])
        self.assertFalse(result["automatic_authority_granted"])
        self.assertFalse(result["confirmed_overlap_authority_granted"])
        self.assertFalse(result["timing_mutation_performed"])

    def test_cross_side_editor_overlap_stays_review(self):
        issue, transition, left_occ, right_occ, left, right = self.base()
        editor = [
            cue(0, "00:00:08,000", "00:00:11,000", "unique left lyric ending"),
            cue(1, "00:00:10,000", "00:00:12,000", "unique right lyric beginning"),
        ]
        result = build_transition_lexical_evidence(
            issue=issue,
            transition=transition,
            left_occurrence=left_occ,
            right_occurrence=right_occ,
            left_timeline=left,
            right_timeline=right,
            editor_cues=editor,
        )
        self.assertEqual(result["recommendation"], "review")
        self.assertLess(result["order_support"]["sequential_gap_ms"], 0)

    def test_same_lyric_on_both_sides_cannot_prove_identity(self):
        issue, transition, left_occ, right_occ, left, _ = self.base()
        right = timeline("right", [(10500, "unique left lyric ending")])
        editor = [
            cue(0, "00:00:09,000", "00:00:11,000", "unique left lyric ending")
        ]
        result = build_transition_lexical_evidence(
            issue=issue,
            transition=transition,
            left_occurrence=left_occ,
            right_occurrence=right_occ,
            left_timeline=left,
            right_timeline=right,
            editor_cues=editor,
        )
        self.assertEqual(result["recommendation"], "review")
        self.assertEqual(result["identity_support"]["ambiguous_cue_count"], 1)

    def test_no_nearby_canonical_line_stays_review_without_crashing(self):
        issue, transition, left_occ, right_occ, _, right = self.base()
        left = timeline("left", [(-100_000, "very old left lyric")])
        result = build_transition_lexical_evidence(
            issue=issue,
            transition=transition,
            left_occurrence=left_occ,
            right_occurrence=right_occ,
            left_timeline=left,
            right_timeline=right,
            editor_cues=[],
        )
        self.assertEqual(result["recommendation"], "review")
        self.assertEqual(result["canonical_local"]["left_line_count"], 0)

    def test_blocked_mapping_fails_closed(self):
        issue, transition, left_occ, right_occ, left, right = self.base()
        left_occ["mapping_blocked"] = True
        with self.assertRaises(TransitionLexicalEvidenceError):
            build_transition_lexical_evidence(
                issue=issue,
                transition=transition,
                left_occurrence=left_occ,
                right_occurrence=right_occ,
                left_timeline=left,
                right_timeline=right,
                editor_cues=[],
            )

    def test_pair_mismatch_fails_closed(self):
        issue, transition, left_occ, right_occ, left, right = self.base()
        issue["right_occurrence_id"] = "other"
        with self.assertRaises(TransitionLexicalEvidenceError):
            build_transition_lexical_evidence(
                issue=issue,
                transition=transition,
                left_occurrence=left_occ,
                right_occurrence=right_occ,
                left_timeline=left,
                right_timeline=right,
                editor_cues=[],
            )

    def test_policy_rejects_invalid_values(self):
        with self.assertRaises(TransitionLexicalEvidenceError):
            TransitionLexicalPolicy(editor_context_ms=0).validate()


if __name__ == "__main__":
    unittest.main()
