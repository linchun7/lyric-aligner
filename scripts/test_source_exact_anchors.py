"""Hermetic contract tests for experimental exact source anchors."""
from __future__ import annotations

from copy import deepcopy
import unittest

from lyric_aligner.alignment.source_exact_anchors import (
    SOURCE_EXACT_ANCHORS_POLICY_ID,
    build_exact_source_anchors,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _word(text, start, end, probability=0.95):
    return {"text": text, "start_ms": start, "end_ms": end, "probability": probability}


def _legacy(index, start, end, label):
    return {"canonical_line_index": index, "source_interval_ms": [start, end],
            "packet_cache_key_sha256": "p" + label, "candidate_id": "c" + label,
            "qualification": "selected_full_context"}


class SourceExactAnchorsTests(unittest.TestCase):
    def _base(self):
        canonical = [
            {"canonical_line_index": 10, "text": "intro"},
            {"canonical_line_index": 20, "text": "one two three four five"},
            {"canonical_line_index": 30, "text": "outro"},
        ]
        words = [_word("one", 250, 280), _word("two", 290, 320), _word("three", 330, 360),
                 _word("four", 370, 400), _word("five", 410, 440)]
        old = [_legacy(10, 100, 200, "left"), _legacy(30, 500, 600, "right")]
        return canonical, words, old

    def _run(self, canonical=None, words=None, old=None):
        base_canonical, base_words, base_old = self._base()
        return build_exact_source_anchors(canonical or base_canonical, words or base_words,
                                          source_observation_sha256=SHA_A, source_audio_sha256=SHA_B,
                                          source_domain_ms=[0, 1000], qualified_anchors=old or base_old)

    def test_qualifies_unique_complete_line_with_legacy_time_bracket_and_identity(self):
        result = self._run()
        self.assertEqual(result["policy_id"], SOURCE_EXACT_ANCHORS_POLICY_ID)
        self.assertEqual(result["counts"], {"canonical_line_denominator": 3,
                                             "qualified_anchor_count": 1, "rejected_anchor_count": 2})
        anchor = result["anchors"][0]
        self.assertEqual(anchor["canonical_line_index"], 20)
        self.assertEqual(anchor["source_interval_ms"], [250, 440])
        self.assertEqual(anchor["observed_word_indices"], [0, 1, 2, 3, 4])
        self.assertEqual(anchor["lexical_units"], ["one", "two", "three", "four", "five"])
        self.assertEqual(anchor["observed_word_evidence"], [
            {"observed_word_index": 0, "text": "one", "start_ms": 250, "end_ms": 280, "probability": .95},
            {"observed_word_index": 1, "text": "two", "start_ms": 290, "end_ms": 320, "probability": .95},
            {"observed_word_index": 2, "text": "three", "start_ms": 330, "end_ms": 360, "probability": .95},
            {"observed_word_index": 3, "text": "four", "start_ms": 370, "end_ms": 400, "probability": .95},
            {"observed_word_index": 4, "text": "five", "start_ms": 410, "end_ms": 440, "probability": .95},
        ])
        self.assertEqual(anchor["legacy_bracket"]["left"]["proof_ids"], [
            "packet_cache_key_sha256:pleft", "candidate_id:cleft", "qualification:selected_full_context"])
        self.assertEqual(anchor["legacy_bracket"]["right"]["canonical_line_index"], 30)
        self.assertEqual(len(anchor["anchor_identity_sha256"]), 64)
        self.assertEqual(result["ledger"][1]["status"], "qualified")

    def test_repeated_canonical_sequence_is_rejected_before_source_filtering(self):
        canonical, words, old = self._base()
        canonical.append({"canonical_line_index": 40, "text": "one two three four five"})
        old.append(_legacy(40, 700, 800, "later"))
        result = self._run(canonical, words, old)
        entries = {item["canonical_line_index"]: item for item in result["ledger"]}
        self.assertEqual(entries[20]["reason"], "canonical_sequence_not_unique_in_full_canonical")
        self.assertEqual(entries[40]["reason"], "canonical_sequence_not_unique_in_full_canonical")
        self.assertEqual(entries[20]["canonical_sequence_match_count"], 2)
        self.assertEqual(result["anchors"], [])

    def test_repeated_source_sequence_is_rejected_even_when_one_occurrence_is_low_probability(self):
        canonical, words, old = self._base()
        words.extend([_word("one", 650, 680, 0.01), _word("two", 690, 720, 0.01),
                      _word("three", 730, 760, 0.01), _word("four", 770, 800, 0.01),
                      _word("five", 810, 840, 0.01)])
        result = self._run(canonical, words, old)
        entry = result["ledger"][1]
        self.assertEqual(entry["reason"], "observed_sequence_not_unique_in_full_observed_stream")
        self.assertEqual(entry["observed_sequence_match_count"], 2)

    def test_invalid_time_probability_and_gap_are_each_rejected(self):
        canonical, words, old = self._base()
        bad_probability = deepcopy(words)
        bad_probability[2]["probability"] = float("nan")
        self.assertEqual(self._run(canonical, bad_probability, old)["ledger"][1]["reason"],
                         "observed_probability_below_threshold_or_nonfinite")
        over_probability = deepcopy(words)
        over_probability[2]["probability"] = 1.5
        self.assertEqual(self._run(canonical, over_probability, old)["ledger"][1]["reason"],
                         "observed_probability_below_threshold_or_nonfinite")
        bad_time = deepcopy(words)
        bad_time[2]["end_ms"] = bad_time[2]["start_ms"]
        self.assertEqual(self._run(canonical, bad_time, old)["ledger"][1]["reason"], "observed_word_time_invalid")
        large_gap = deepcopy(words)
        large_gap[3]["start_ms"], large_gap[3]["end_ms"] = 2000, 2030
        self.assertEqual(self._run(canonical, large_gap, old)["ledger"][1]["reason"],
                         "observed_word_outside_source_domain")
        result = build_exact_source_anchors(canonical, large_gap, source_observation_sha256=SHA_A,
                                            source_audio_sha256=SHA_B, source_domain_ms=[0, 3000],
                                            qualified_anchors=old)
        self.assertEqual(result["ledger"][1]["reason"], "observed_word_adjacent_gap_exceeded")

    def test_non_single_or_nonenglish_observed_item_is_barrier_not_deleted(self):
        canonical, words, old = self._base()
        words.insert(2, _word("discard this", 321, 329))
        result = self._run(canonical, words, old)
        self.assertEqual(result["ledger"][1]["reason"], "observed_sequence_not_unique_in_full_observed_stream")
        self.assertEqual(result["ledger"][1]["observed_sequence_match_count"], 0)
        nonenglish = deepcopy(self._base()[1])
        nonenglish.insert(2, _word("舞", 321, 329))
        result = self._run(canonical, nonenglish, old)
        self.assertEqual(result["ledger"][1]["reason"], "observed_sequence_not_unique_in_full_observed_stream")
        self.assertEqual(result["ledger"][1]["observed_sequence_match_count"], 0)

    def test_cross_line_conflict_rejects_both_independent_candidates(self):
        canonical = [
            {"canonical_line_index": 10, "text": "intro"},
            {"canonical_line_index": 20, "text": "one two three four five"},
            {"canonical_line_index": 30, "text": "six seven eight nine ten"},
            {"canonical_line_index": 40, "text": "outro"},
        ]
        # The later canonical line is earlier in source time.  Both otherwise
        # qualify under the same legacy bracket; neither may win greedily.
        words = [_word("six", 250, 270), _word("seven", 275, 295), _word("eight", 300, 320),
                 _word("nine", 325, 345), _word("ten", 350, 370),
                 _word("one", 500, 520), _word("two", 525, 545), _word("three", 550, 570),
                 _word("four", 575, 595), _word("five", 600, 620)]
        old = [_legacy(10, 100, 200, "left"), _legacy(40, 700, 800, "right")]
        result = self._run(canonical, words, old)
        entries = {item["canonical_line_index"]: item for item in result["ledger"]}
        self.assertEqual(entries[20]["reason"], "cross_line_source_order_or_overlap_conflict")
        self.assertEqual(entries[30]["reason"], "cross_line_source_order_or_overlap_conflict")
        self.assertEqual(result["anchors"], [])

    def test_requires_old_qualified_canonical_and_time_brackets_and_is_deterministic(self):
        canonical, words, old = self._base()
        wrong_time = deepcopy(old)
        wrong_time[1]["source_interval_ms"] = [300, 600]
        self.assertEqual(self._run(canonical, words, wrong_time)["ledger"][1]["reason"],
                         "old_qualified_anchor_bracket_missing_or_time_invalid")
        missing_right = old[:1]
        self.assertEqual(self._run(canonical, words, missing_right)["ledger"][1]["reason"],
                         "old_qualified_anchor_bracket_missing_or_time_invalid")
        first = self._run(canonical, words, old)
        second = self._run(deepcopy(canonical), deepcopy(words), deepcopy(old))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
