import copy
import unittest

from lyric_aligner.review.transition_lyric_resolution import (
    TransitionLyricResolutionError,
    authorize_transition_lyrics,
)


RUN_SHA = "a" * 64
SOURCE_SHA = "b" * 64
ARTIFACT_ID = "c" * 64
TASK = "d" * 64


def issue(index: int, left: str, right: str):
    return {
        "kind": "transition_ambiguity",
        "code": "ambiguous_source_occurrence",
        "candidate_id": f"candidate-{index}",
        "left_occurrence_id": left,
        "right_occurrence_id": right,
        "interval_start": float(index * 10),
        "interval_end": float(index * 10 + 8),
        "status": "review",
        "reason": "high audio score but ambiguous source occurrence",
    }


def lexical_row(index: int, left: str, right: str, *, clear=True, gap=1000):
    return {
        "issue_candidate_id": f"candidate-{index}",
        "left_occurrence_id": left,
        "right_occurrence_id": right,
        "identity_support": {
            "left_unique_cue_count": 2,
            "right_unique_cue_count": 2,
            "left_unique_normalized_chars": 20,
            "right_unique_normalized_chars": 20,
            "ambiguous_cue_count": 0,
            "unmatched_cue_count": 0,
            "enough_identity": True,
        },
        "order_support": {
            "last_left_editor_end_ms": 10000,
            "first_right_editor_start_ms": 10000 + gap,
            "sequential_gap_ms": gap,
            "lexical_order_clear": clear,
        },
        "recommendation": "clear_candidate" if clear else "review",
        "automatic_authority_granted": False,
        "confirmed_overlap_authority_granted": False,
        "timing_mutation_performed": False,
    }


def fixture():
    transitions = [
        {"left_occurrence_id": "a", "right_occurrence_id": "b", "nominal_boundary": 14.0},
        {"left_occurrence_id": "b", "right_occurrence_id": "c", "nominal_boundary": 24.0},
    ]
    issues = [issue(1, "a", "b"), issue(2, "b", "c")]
    run = {
        "algorithm_version": "4.0.0a19",
        "task_fingerprint_sha256": TASK,
        "status": "review_required",
        "legacy_fallback_used": False,
        "issues": issues,
        "transitions": transitions,
    }
    lexical = {
        "schema_version": "adjacent-transition-editor-lexical-evidence-1.0",
        "task_fingerprint_sha256": TASK,
        "source_srt_sha256": SOURCE_SHA,
        "run_sha256": RUN_SHA,
        "run_artifact_id": ARTIFACT_ID,
        "automatic_authority_granted": False,
        "timing_mutation_performed": False,
        "transitions": [
            lexical_row(1, "a", "b", clear=True),
            lexical_row(2, "b", "c", clear=False),
        ],
    }
    positional = {
        "schema_version": "adjacent-transition-positional-v2-report-1",
        "task_fingerprint_sha256": TASK,
        "run_sha256": RUN_SHA,
        "run_artifact_id": ARTIFACT_ID,
        "authority": {
            "shadow_only": True,
            "automatic_review_decision": False,
            "timing_mutation_performed": False,
        },
        "evidence": [
            {
                "transition_index": 1,
                "nominal_boundary": 14.0,
                "recommendation": {"action": "overlap_candidate_advisory"},
            },
            {
                "transition_index": 2,
                "nominal_boundary": 24.0,
                "recommendation": {"action": "unresolved"},
            },
        ],
    }
    return run, lexical, positional


class TransitionLyricResolutionTest(unittest.TestCase):
    def call(self, run, lexical, positional):
        return authorize_transition_lyrics(
            run=run,
            base_run_artifact_id=ARTIFACT_ID,
            run_sha256=RUN_SHA,
            source_srt_sha256=SOURCE_SHA,
            lexical=lexical,
            positional=positional,
        )

    def test_lexical_clear_resolves_occurrence_even_when_music_overlap_is_possible(self):
        run, lexical, positional = fixture()
        decisions, report = self.call(run, lexical, positional)
        self.assertEqual(report["resolved_clear_count"], 1)
        self.assertEqual(report["remaining_unresolved_count"], 1)
        first = decisions["review_items"][0]
        self.assertEqual(first["decision"]["action"], "resolved_clear")
        self.assertIn("instrumental crossfade", first["decision"]["rationale"])
        self.assertIsNone(decisions["review_items"][1]["decision"])
        self.assertTrue(report["decisions"][0]["source_audio_overlap_possible"])
        self.assertFalse(report["confirmed_overlap_authority_granted"])

    def test_negative_or_inconsistent_lexical_gap_cannot_clear(self):
        run, lexical, positional = fixture()
        lexical["transitions"][0]["order_support"] = {
            "last_left_editor_end_ms": 11000,
            "first_right_editor_start_ms": 10000,
            "sequential_gap_ms": -1000,
            "lexical_order_clear": True,
        }
        decisions, report = self.call(run, lexical, positional)
        self.assertIsNone(decisions["review_items"][0]["decision"])
        self.assertEqual(report["resolved_clear_count"], 0)

    def test_short_or_ambiguous_identity_cannot_clear(self):
        run, lexical, positional = fixture()
        lexical["transitions"][0]["identity_support"]["left_unique_normalized_chars"] = 8
        decisions, report = self.call(run, lexical, positional)
        self.assertIsNone(decisions["review_items"][0]["decision"])
        self.assertEqual(report["decisions"][0]["lexical_reason"], "lexical_side_identity_too_short")

    def test_stale_lexical_run_hash_rejected(self):
        run, lexical, positional = fixture()
        lexical["run_sha256"] = "e" * 64
        with self.assertRaisesRegex(TransitionLyricResolutionError, "another Max run"):
            self.call(run, lexical, positional)

    def test_duplicate_or_missing_positional_population_rejected(self):
        run, lexical, positional = fixture()
        positional["evidence"] = [copy.deepcopy(positional["evidence"][0])] * 2
        with self.assertRaisesRegex(TransitionLyricResolutionError, "duplicate"):
            self.call(run, lexical, positional)

    def test_candidate_identity_mismatch_rejected(self):
        run, lexical, positional = fixture()
        lexical["transitions"][0]["issue_candidate_id"] = "wrong"
        with self.assertRaisesRegex(TransitionLyricResolutionError, "candidate identity mismatch"):
            self.call(run, lexical, positional)


if __name__ == "__main__":
    unittest.main()
