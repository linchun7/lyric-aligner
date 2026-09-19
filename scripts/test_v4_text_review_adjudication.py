import unittest

from lyric_aligner.review.text_review_adjudication import (
    _max_witness, _neighbor_witness, _pro_support, normalize, presentation_equivalent,
)


class TextReviewAdjudicationUnitTests(unittest.TestCase):
    def test_presentation_equivalent(self):
        self.assertTrue(presentation_equivalent(" Don't  Stop! ", "don't stop"))
        self.assertFalse(presentation_equivalent("don't stop", "don't go"))

    def test_normalize_is_case_and_layout_insensitive(self):
        self.assertEqual(normalize("Hello, WORLD"), "helloworld")

    def test_smart_self_reference_does_not_witness(self):
        row = {"cue_ordinal": 10, "canonical_span": [20, 21]}
        decisions = {10: row}
        self.assertEqual(_neighbor_witness(row, decisions), (False, "neighbors_not_resolved"))

    def test_review_or_unknown_neighbor_action_does_not_witness(self):
        row = {"cue_ordinal": 10, "canonical_span": [20, 21]}
        decisions = {9: {"action": "replace", "canonical_span": [19, 20]},
                     10: row, 11: {"action": "unknown", "canonical_span": [21, 22]}}
        self.assertEqual(_neighbor_witness(row, decisions), (False, "neighbors_not_resolved"))

    def test_fake_pro_job_presence_is_not_support(self):
        row = {"cue_ordinal": 10}
        pro = [{"cue_ordinal": 10, "job_id": "j", "text_state": "text_review_unvalidated"}]
        asr = [{"job_id": "j", "canonical_start_covered": True, "canonical_end_covered": True}]
        self.assertEqual(_pro_support(pro, asr, row), (False, "pro_not_independent_support"))

    def test_real_pro_support_requires_both_ends_and_non_ambiguous(self):
        row = {"cue_ordinal": 10}
        pro = [{"cue_ordinal": 10, "job_id": "j", "text_state": "canonical_text_supported", "asr_canonical_support_score": .8}]
        asr = [{"job_id": "j", "canonical_start_covered": True, "canonical_end_covered": True,
                "canonical_match_ambiguous": False, "canonical_match_support_score": .8}]
        self.assertEqual(_pro_support(pro, asr, row), (True, "pro_canonical_asr_support"))

    def test_max_transition_risk_fails_closed(self):
        payload = {"status": "ready", "occurrences": [{"primary_interval": [0, 100]}],
                   "transitions": [{"status": "review_required", "nominal_boundary": 50}]}
        self.assertEqual(_max_witness(payload, {"start_ms": 49000, "end_ms": 51000}), (False, "max_transition_risk"))


if __name__ == "__main__":
    unittest.main()
