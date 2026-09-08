import unittest

from lyric_aligner.alignment.source_packets import (
    SOURCE_PACKET_AUTHORITY,
    build_source_packet_candidates,
    source_packet_cache_key,
)


class SourcePacketCandidatesTests(unittest.TestCase):
    def canonical(self, *texts):
        return [
            {"canonical_line_index": index, "text": text}
            for index, text in enumerate(texts)
        ]

    def target(self, line, start=0, end=None):
        text = self.lines[line]["text"]
        return {
            "cue_id": "cue-7",
            "canonical_character_ranges": [
                {
                    "canonical_line_index": line,
                    "start_char": start,
                    "end_char": len(text) if end is None else end,
                }
            ],
        }

    def build(self, *, lines, words, target, radius=2, **kwargs):
        self.lines = lines
        return build_source_packet_candidates(
            canonical_lines=lines,
            observed_words=words,
            target_cue=target,
            source_audio_sha256="source-sha-1",
            lyric_version="canonical-v7",
            source_search_domain={"start_ms": 0, "end_ms": 30_000, "domain_id": "full-source"},
            model_id="local-asr-model-v1",
            context_radius=radius,
            **kwargs,
        )

    def test_full_neighbourhood_selects_later_short_repeat_and_target_interval_not_packet_interval(self):
        lines = self.canonical("准备", "我爱你", "直到永远", "不回头", "再见")
        result = self.build(
            lines=lines,
            target={
                "cue_id": "cue-7",
                "canonical_character_ranges": [
                    {"canonical_line_index": 2, "start_char": 0, "end_char": 4}
                ],
            },
            words=[
                {"text": "直到永远", "start_ms": 100, "end_ms": 300, "window_id": "early-short-repeat"},
                {"text": "准备", "start_ms": 5_000, "end_ms": 5_100},
                {"text": "我爱你", "start_ms": 5_100, "end_ms": 5_200},
                {"text": "直到永远", "start_ms": 5_200, "end_ms": 5_400},
                {"text": "不回头", "start_ms": 5_400, "end_ms": 5_500},
                {"text": "再见", "start_ms": 5_500, "end_ms": 5_600},
            ],
            radius=2,
        )
        self.assertEqual(result["selection_reason"], "unique_complete_context_match")
        self.assertIsNotNone(result["selected_candidate_id"])
        candidate = result["candidates"][0]
        self.assertEqual(candidate["source_interval_ms"], [5_200, 5_400])
        self.assertEqual(candidate["packet_source_interval_ms"], [5_000, 5_600])
        self.assertTrue(all(row["match_status"] == "matched" for row in candidate["target_cue_character_attribution"]))
        self.assertEqual(result["authority"], SOURCE_PACKET_AUTHORITY)
        self.assertFalse(result["automatic_production_mutation_allowed"])
        self.assertFalse(result["forced_phoneme_alignment_performed"])

    def test_same_complete_packet_at_distinct_source_times_is_ambiguous_not_first_match(self):
        lines = self.canonical("左侧", "短句", "右侧")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 2}]},
            words=[
                {"text": "左侧", "start_ms": 1_000, "end_ms": 1_100},
                {"text": "短句", "start_ms": 1_100, "end_ms": 1_200},
                {"text": "右侧", "start_ms": 1_200, "end_ms": 1_300},
                {"text": "左侧", "start_ms": 10_000, "end_ms": 10_100},
                {"text": "短句", "start_ms": 10_100, "end_ms": 10_200},
                {"text": "右侧", "start_ms": 10_200, "end_ms": 10_300},
            ],
            radius=1,
        )
        self.assertEqual(result["selection_reason"], "ambiguous_context_match")
        self.assertIsNone(result["selected_candidate_id"])
        self.assertEqual(result["coverage"]["exact_full_context_match_count"], 2)
        self.assertEqual([item["source_interval_ms"] for item in result["candidates"]], [[1_100, 1_200], [10_100, 10_200]])

    def test_missing_neighbour_context_is_partial_not_a_timing_candidate(self):
        lines = self.canonical("before", "repeat me", "after")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 9}]},
            words=[{"text": "repeat me", "start_ms": 1_000, "end_ms": 1_600}],
            radius=1,
        )
        self.assertEqual(result["selection_reason"], "partial_context")
        # The exact target remains in the ledger with its real cue-owned
        # interval, but missing both independent neighbours cannot select it.
        self.assertEqual(len(result["candidates"]), 1)
        self.assertFalse(result["candidates"][0]["full_context_disambiguated"])
        self.assertEqual(result["candidates"][0]["source_interval_ms"], [1_000, 1_600])
        self.assertIsNone(result["selected_candidate_id"])

    def test_zero_duration_words_contribute_text_but_never_outer_boundary(self):
        lines = self.canonical("before", "target", "right")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[
                {"text": "before", "start_ms": 100, "end_ms": 200},
                {"text": "target", "start_ms": 200, "end_ms": 200},
                {"text": "right", "start_ms": 200, "end_ms": 300},
            ],
            radius=1,
        )
        candidate = result["candidates"][0]
        self.assertEqual(result["selection_reason"], "unique_complete_context_match")
        self.assertEqual(candidate["source_interval_ms"], [None, None])
        target_line = candidate["lines"][1]
        self.assertEqual((target_line["start_ms"], target_line["end_ms"]), (None, None))
        self.assertEqual(target_line["start_reason"], "unknown_zero_duration_word")
        self.assertTrue(candidate["target_cue_character_attribution"][0]["observed_matches"])

    def test_missing_start_or_end_stays_unknown_independently(self):
        lines = self.canonical("left", "target", "right")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[
                {"text": "left", "start_ms": 100, "end_ms": 200},
                {"text": "target", "start_ms": None, "end_ms": 250},
                {"text": "right", "start_ms": 250, "end_ms": 300},
            ],
            radius=1,
        )
        candidate = result["candidates"][0]
        self.assertEqual(candidate["source_interval_ms"], [None, None])
        self.assertEqual(candidate["source_interval_start_reason"], "unknown_missing_word_endpoint")
        self.assertEqual(candidate["source_interval_end_reason"], "unknown_missing_word_endpoint")

    def test_bijective_han_and_korean_japanese_latin_all_use_same_context_logic(self):
        cases = [
            (self.canonical("前", "愛與裝飾", "尾"), ["前", "爱与装饰", "尾"], 1),
            (self.canonical("앞", "이제", "뒤"), ["앞", "이제", "뒤"], 1),
            (self.canonical("前", "さよなら", "後"), ["前", "さよなら", "後"], 1),
            (self.canonical("before", "Hello World", "after"), ["before", "hello world", "after"], 1),
        ]
        for lines, texts, target_line in cases:
            with self.subTest(texts=texts):
                result = self.build(
                    lines=lines,
                    target={"canonical_character_ranges": [{"canonical_line_index": target_line, "start_char": 0, "end_char": len(lines[target_line]["text"])}]},
                    words=[
                        {"text": text, "start_ms": 100 + index * 100, "end_ms": 180 + index * 100}
                        for index, text in enumerate(texts)
                    ],
                    radius=1,
                )
                self.assertEqual(result["selection_reason"], "unique_complete_context_match")

    def test_overlapping_window_duplicates_merge_but_different_source_occurrences_do_not(self):
        lines = self.canonical("left", "target", "right")
        common = [
            {"text": "left", "start_ms": 1_000, "end_ms": 1_100, "window_id": "a"},
            {"text": "target", "start_ms": 1_100, "end_ms": 1_200, "window_id": "a"},
            {"text": "right", "start_ms": 1_200, "end_ms": 1_300, "window_id": "a"},
            {"text": "left", "start_ms": 1_000, "end_ms": 1_100, "window_id": "b"},
            {"text": "target", "start_ms": 1_100, "end_ms": 1_200, "window_id": "b"},
            {"text": "right", "start_ms": 1_200, "end_ms": 1_300, "window_id": "b"},
        ]
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=common,
            radius=1,
        )
        self.assertEqual(result["coverage"]["observed_word_count_after_domain_filter"], 3)
        self.assertEqual(result["selection_reason"], "unique_complete_context_match")
        match = result["candidates"][0]["target_cue_character_attribution"][0]["observed_matches"][0]
        self.assertEqual(match["observed_window_ids"], ["a", "b"])

        second = [
            {**word, "start_ms": word["start_ms"] + 10_000, "end_ms": word["end_ms"] + 10_000, "window_id": "c"}
            for word in common[:3]
        ]
        ambiguous = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[*common, *second],
            radius=1,
        )
        self.assertEqual(ambiguous["selection_reason"], "ambiguous_context_match")

    def test_same_window_and_untimed_or_zero_duration_repeats_preserve_text_order(self):
        cases = [
            (
                self.canonical("left", "nana", "right"),
                [
                    {"text": "left", "start_ms": 100, "end_ms": 200, "window_id": "whole"},
                    {"text": "na", "start_ms": 200, "end_ms": 200, "window_id": "whole"},
                    {"text": "na", "start_ms": 200, "end_ms": 200, "window_id": "whole"},
                    {"text": "right", "start_ms": 200, "end_ms": 300, "window_id": "whole"},
                ],
                "na",
            ),
            (
                self.canonical("left", "lala", "right"),
                [
                    {"text": "left", "start_ms": 100, "end_ms": 200, "window_id": "whole"},
                    {"text": "la", "start_ms": None, "end_ms": None, "window_id": "whole"},
                    {"text": "la", "start_ms": None, "end_ms": None, "window_id": "whole"},
                    {"text": "right", "start_ms": 200, "end_ms": 300, "window_id": "whole"},
                ],
                "la",
            ),
        ]
        for lines, words, target_text in cases:
            with self.subTest(target_text=target_text):
                result = self.build(
                    lines=lines,
                    target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 4}]},
                    words=words,
                    radius=1,
                )
                self.assertEqual(result["selection_reason"], "unique_complete_context_match")
                self.assertEqual(result["coverage"]["observed_word_count_after_domain_filter"], 4)
                self.assertEqual(result["candidates"][0]["source_interval_ms"], [None, None])

    def test_cache_key_binds_packet_character_domain_domain_model_and_lyric_version(self):
        lines = self.canonical("a", "target", "b", "c")
        target = {"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]}
        kwargs = dict(
            canonical_lines=lines,
            target_cue=target,
            source_audio_sha256="audio-sha",
            lyric_version="v1",
            source_search_domain={"start_ms": 0, "end_ms": 1_000},
            model_id="model-a",
            context_radius=1,
        )
        key = source_packet_cache_key(**kwargs)
        self.assertNotEqual(key, source_packet_cache_key(**{**kwargs, "lyric_version": "v2"}))
        self.assertNotEqual(key, source_packet_cache_key(**{**kwargs, "model_id": "model-b"}))
        self.assertNotEqual(key, source_packet_cache_key(**{**kwargs, "source_search_domain": {"start_ms": 1, "end_ms": 1_000}}))
        self.assertNotEqual(
            key,
            source_packet_cache_key(
                **{
                    **kwargs,
                    "target_cue": {"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 1, "end_char": 6}]},
                }
            ),
        )

    def test_one_neighbour_substitution_keeps_exact_target_with_auditable_monotonic_trace(self):
        lines = self.canonical("leftalpha", "target", "rightomega")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[
                {"text": "leftalpza", "start_ms": 100, "end_ms": 200},
                {"text": "target", "start_ms": 200, "end_ms": 300},
                {"text": "rightomega", "start_ms": 300, "end_ms": 400},
            ],
            radius=1,
        )
        self.assertEqual(result["selection_reason"], "unique_monotonic_context_support")
        candidate = result["candidates"][0]
        self.assertTrue(candidate["full_context_disambiguated"])
        self.assertEqual(candidate["match_kind"], "exact_target_tolerant_context")
        self.assertTrue(any(
            item["operation"] == "replace"
            for item in candidate["context_alignment"]["trace"]
        ))
        self.assertTrue(candidate["canonical_to_observed_characters"])

    def test_one_matching_character_in_an_otherwise_wrong_long_neighbour_is_not_context_support(self):
        lines = self.canonical("abcdefghij", "target", "klmnopqrst")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[
                # One lexical equality plus one continuous edit run must not
                # turn an unrelated long neighbour into context proof.
                {"text": "azzzzzzzzz", "start_ms": 100, "end_ms": 200},
                {"text": "target", "start_ms": 200, "end_ms": 300},
                {"text": "klmnopqrst", "start_ms": 300, "end_ms": 400},
            ],
            radius=1,
        )
        candidate = result["candidates"][0]
        left = candidate["context_alignment"]["left_context"]
        self.assertEqual(left["matched_characters"], 1)
        self.assertEqual(left["edit_run_count"], 1)
        self.assertLess(left["matched_character_ratio"], 0.8)
        self.assertEqual(result["selection_reason"], "partial_context")
        self.assertFalse(candidate["full_context_disambiguated"])

    def test_one_edit_margin_between_repeated_target_anchors_stays_ambiguous(self):
        lines = self.canonical("left", "target", "right")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[
                {"text": "left", "start_ms": 100, "end_ms": 200},
                {"text": "target", "start_ms": 200, "end_ms": 300},
                {"text": "right", "start_ms": 300, "end_ms": 400},
                {"text": "lezt", "start_ms": 500, "end_ms": 600},
                {"text": "target", "start_ms": 600, "end_ms": 700},
                {"text": "right", "start_ms": 700, "end_ms": 800},
            ],
            radius=1,
        )
        self.assertEqual(result["selection_reason"], "ambiguous_context_match")
        self.assertIsNone(result["selected_candidate_id"])
        self.assertTrue(all(not item["full_context_disambiguated"] for item in result["candidates"]))

    def test_duplicate_canonical_packet_cannot_be_selected_from_one_source_occurrence(self):
        lines = self.canonical("left", "target", "right", "bridge", "left", "target", "right")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[
                {"text": "left", "start_ms": 100, "end_ms": 200},
                {"text": "target", "start_ms": 200, "end_ms": 300},
                {"text": "right", "start_ms": 300, "end_ms": 400},
            ],
            radius=1,
        )
        self.assertEqual(result["coverage"]["canonical_packet_duplicate_count"], 2)
        self.assertEqual(result["selection_reason"], "ambiguous_canonical_packet_identity")
        self.assertIsNone(result["selected_candidate_id"])

    def test_partial_target_missing_head_or_tail_never_infers_the_missing_edge(self):
        lines = self.canonical("before", "target", "right")
        result = self.build(
            lines=lines,
            target={"canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 6}]},
            words=[
                {"text": "before", "start_ms": 100, "end_ms": 200},
                {"text": "arget", "start_ms": 200, "end_ms": 300},
                {"text": "right", "start_ms": 300, "end_ms": 400},
            ],
            radius=1,
        )
        candidate = result["candidates"][0]
        self.assertEqual(result["selection_reason"], "partial_context")
        self.assertEqual(candidate["match_kind"], "partial_target_observed_match")
        self.assertEqual(candidate["source_interval_ms"], [None, 300])
        self.assertEqual(candidate["source_interval_start_reason"], "unmatched_observed_transcript")
        self.assertFalse(candidate["full_context_disambiguated"])

    def test_target_edge_inside_a_multicharacter_observed_word_is_unknown(self):
        cases = [
            (self.canonical("left", "foo", "bar"), 1, [100, None]),
            (self.canonical("foo", "bar", "right"), 1, [None, 200]),
        ]
        for lines, target_line, expected in cases:
            with self.subTest(target=lines[target_line]["text"]):
                result = self.build(
                    lines=lines,
                    target={"canonical_character_ranges": [{
                        "canonical_line_index": target_line,
                        "start_char": 0,
                        "end_char": len(lines[target_line]["text"]),
                    }]},
                    words=[
                        {"text": "left" if target_line == 1 and lines[0]["text"] == "left" else "foobar", "start_ms": 0, "end_ms": 100},
                        {"text": "foobar", "start_ms": 100, "end_ms": 200},
                        {"text": "right", "start_ms": 200, "end_ms": 300},
                    ] if lines[0]["text"] == "left" else [
                        {"text": "foobar", "start_ms": 100, "end_ms": 200},
                        {"text": "right", "start_ms": 200, "end_ms": 300},
                    ],
                    radius=1,
                )
                candidate = result["candidates"][0]
                self.assertEqual(candidate["source_interval_ms"], expected)
                self.assertIn(
                    "unknown_cue_edge_inside_observed_word",
                    (candidate["source_interval_start_reason"], candidate["source_interval_end_reason"]),
                )


if __name__ == "__main__":
    unittest.main()
