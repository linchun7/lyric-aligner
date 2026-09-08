import unittest
from unittest.mock import patch

from lyric_aligner.alignment import anchored_edit
from lyric_aligner.alignment.source_packets import (
    BOUNDED_TARGET_POLICY, EXACT_TARGET_POLICY, SOURCE_PACKET_POLICY_ID,
    build_source_packet_candidates, source_packet_cache_key,
)


class SourcePacketRescueTests(unittest.TestCase):
    def build(self, target="hold me through the rain", observed="hold me through a rain",
              *, policy=BOUNDED_TARGET_POLICY, words=None, lines=None, n_best=3):
        texts = lines or ["fading city", target, "morning finds us"]
        self.kwargs = dict(
            canonical_lines=[{"canonical_line_index": i, "text": text} for i, text in enumerate(texts)],
            target_cue={"cue_id": "middle", "canonical_character_ranges": [
                {"canonical_line_index": 1, "start_char": 0, "end_char": len(target)}]},
            source_audio_sha256="recording", lyric_version="canonical-v1", model_id="observed-v1",
            source_search_domain={"start_ms": 0, "end_ms": 30000, "domain_id": "whole"},
            context_radius=1, target_alignment_policy=policy,
        )
        if words is None:
            words = self.words((texts[0] + " " + observed + " " + texts[2]).split())
        return build_source_packet_candidates(**self.kwargs, observed_words=words, n_best=n_best)

    @staticmethod
    def words(texts, origin=100):
        return [{"text": text, "start_ms": origin+i*200, "end_ms": origin+i*200+180,
                 "window_id": "whole"} for i, text in enumerate(texts)]

    def test_internal_word_error_recovers_both_true_outer_edges_and_keeps_wrong_characters_unmatched(self):
        old = self.build(policy=EXACT_TARGET_POLICY)
        self.assertIsNone(old["selected_candidate_id"])
        new = self.build()
        self.assertEqual(new["selection_reason"], "unique_outer_anchored_bounded_target_edit")
        c = new["candidates"][0]
        self.assertEqual(c["source_interval_ms"], [500, 1480])
        self.assertTrue(c["full_context_disambiguated"])
        alignment = c["context_alignment"]["target_alignment"]
        self.assertTrue(alignment["outer_edges_consensus"])
        self.assertEqual(alignment["edit_run_count"], 1)
        self.assertTrue(any(r["match_status"] == "unmatched_observed_transcript"
                            for r in c["target_cue_character_attribution"]))
        self.assertFalse(new["forced_phoneme_alignment_performed"])
        self.assertFalse(new["automatic_production_mutation_allowed"])

    def test_legacy_mode_identity_and_results_stay_compatible(self):
        old = self.build(policy=EXACT_TARGET_POLICY)
        old_key = source_packet_cache_key(**self.kwargs)
        self.assertEqual(old["policy_id"], SOURCE_PACKET_POLICY_ID)
        self.assertEqual(old["schema_version"], "source-packet-candidates-1.1")
        new = self.build()
        self.assertNotEqual(old_key, source_packet_cache_key(**self.kwargs))
        self.assertEqual(new["cache_key_sha256"], source_packet_cache_key(**self.kwargs))
        self.assertEqual(new["schema_version"], "source-packet-candidates-1.2")
        self.assertEqual(
            new["cache_identity"]["target_alignment_resource_limits"],
            {"max_alignment_dp_cells": 250_000, "max_total_dp_cells": 2_000_000,
             "max_search_spans": 512, "max_partial_matcher_cells": 250_000},
        )

    def test_head_tail_errors_missing_words_and_low_coverage_do_not_get_edges(self):
        for observed in ("xold me through a rain", "hold me through a raxx",
                         "me through a rain", "hold me through a", "cold xx through xx rain"):
            with self.subTest(observed=observed):
                self.assertIsNone(self.build(observed=observed)["selected_candidate_id"])

    def test_two_separate_internal_edits_are_not_rescued(self):
        r = self.build(target="alpha bravo charlie delta echo", observed="alpha brxvo charlie delxa echo")
        self.assertIsNone(r["selected_candidate_id"])

    def test_zero_duration_outer_word_does_not_become_a_boundary(self):
        for index in (2, 6):
            words = self.words("fading city hold me through a rain morning finds us".split())
            words[index]["end_ms"] = words[index]["start_ms"]
            self.assertIsNone(self.build(words=words)["selected_candidate_id"])

    def test_word_internal_endpoints_do_not_borrow_full_word_time(self):
        for replacement in ("xhold", "rainx"):
            texts = "fading city hold me through a rain morning finds us".split()
            texts[2 if replacement == "xhold" else 6] = replacement
            self.assertIsNone(self.build(words=self.words(texts))["selected_candidate_id"])

    def test_multiple_eligible_observed_spans_remain_ambiguous_before_nbest_truncation(self):
        texts = "fading city hold me through a rain morning finds us".split()
        words = self.words(texts) + self.words(texts, origin=5000)
        r = self.build(words=words, n_best=1)
        self.assertEqual(r["selection_reason"], "ambiguous_outer_anchored_target_edit")
        self.assertIsNone(r["selected_candidate_id"])
        self.assertEqual(r["coverage"]["outer_anchored_target_candidate_count"], 2)
        self.assertTrue(r["candidates_truncated"])

    def test_duplicate_canonical_packet_is_not_resolved_by_one_observed_copy(self):
        target = "hold me through the rain"
        r = self.build(lines=["fading city", target, "morning finds us", "bridge",
                              "fading city", target, "morning finds us"])
        self.assertEqual(r["selection_reason"], "ambiguous_canonical_packet_identity")
        self.assertIsNone(r["selected_candidate_id"])

    def test_context_edit_run_requirement_is_not_relaxed(self):
        texts = "fading city hold me through a rain morxing finds ux".split()
        self.assertIsNone(self.build(words=self.words(texts))["selected_candidate_id"])

    def test_repeated_edge_character_with_multiple_edit_paths_is_unknown(self):
        r = self.build(target="aabsolutelywonderful", observed="absolutelywonderful")
        self.assertIsNone(r["selected_candidate_id"])

    def test_unicode_edit_retains_raw_canonical_attribution(self):
        lines = ["夜空に星が光る", "いつまでも君を待つ", "明日もここにいる"]
        words = self.words([lines[0], *"いつまでも君は待つ", lines[2]])
        r = self.build(target=lines[1], lines=lines, words=words)
        self.assertIsNotNone(r["selected_candidate_id"])
        c = r["candidates"][0]
        self.assertEqual("".join(x["canonical_character"] for x in c["target_cue_character_attribution"]), lines[1])
        self.assertEqual(c["source_interval_ms"], [300, 2080])

    def test_single_line_still_requires_context(self):
        r = build_source_packet_candidates(
            canonical_lines=[{"canonical_line_index": 0, "text": "hold me through the rain"}],
            observed_words=self.words("hold me through a rain".split()),
            target_cue={"canonical_character_ranges": [{"canonical_line_index": 0, "start_char": 0, "end_char": 24}]},
            source_audio_sha256="recording", lyric_version="v1", model_id="m1",
            source_search_domain={"start_ms": 0, "end_ms": 30000}, target_alignment_policy=BOUNDED_TARGET_POLICY)
        self.assertIsNone(r["selected_candidate_id"])

    def test_unknown_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "target_alignment_policy"):
            self.build(policy="invented")

    def test_orphan_exact_target_does_not_block_context_supported_rescue(self):
        words = self.words(["charlie"], origin=0) + self.words(["alpha", "charxie", "delta"], origin=1000)
        r = self.build(target="charlie", lines=["alpha", "charlie", "delta"], words=words)
        self.assertEqual(r["selection_reason"], "unique_outer_anchored_bounded_target_edit")
        self.assertEqual(r["candidates"][0]["source_interval_ms"], [1200, 1380])

    def test_bounded_qualified_exact_precedes_lower_ranked_orphan_at_nbest_one(self):
        # The first exact occurrence has two left-context edits and must not
        # hide the later occurrence, whose one edit per side is admissible.
        words = self.words(["abxdy", "target", "vwxyz", "abcdf", "target", "vwxya"])
        r = self.build(
            target="target",
            lines=["abcde", "target", "vwxyz"],
            words=words,
            n_best=1,
        )
        self.assertEqual(r["selection_reason"], "unique_monotonic_context_support")
        self.assertIsNotNone(r["selected_candidate_id"])
        self.assertEqual(r["candidates"][0]["source_interval_ms"], [900, 1080])
        self.assertEqual(r["candidates"][0]["candidate_id"], r["selected_candidate_id"])

    def test_resource_exhaustion_discards_earlier_candidate_instead_of_selecting_before_unseen_rival(self):
        texts = "fading city hold me through a rain morning finds us".split()
        words = self.words(texts) + self.words(texts, origin=5000)
        with patch("lyric_aligner.alignment.source_packets.SOURCE_PACKET_MAX_SEARCH_SPANS", 1, create=True):
            r = self.build(words=words)
        self.assertEqual(r["selection_reason"], "source_packet_resource_limit")
        self.assertIsNone(r["selected_candidate_id"])
        self.assertFalse(r["candidates"])
        self.assertEqual(r["coverage"]["bounded_target_search"]["resource_limit"], "max_search_spans")

    def test_repeated_observed_characters_hit_span_limit_and_fail_closed(self):
        target = "aaaaaaaa" + "b" + "aaaaaaaa"
        words = self.words(["a"] * 80)
        with patch("lyric_aligner.alignment.source_packets.SOURCE_PACKET_MAX_SEARCH_SPANS", 3):
            r = self.build(target=target, lines=["a", target, "a"], words=words)
        self.assertEqual(r["selection_reason"], "source_packet_resource_limit")
        self.assertFalse(r["candidates"])
        search = r["coverage"]["bounded_target_search"]
        self.assertEqual(search["resource_limit"], "max_search_spans")
        self.assertEqual(search["search_spans_examined"], 3)

    def test_total_dp_budget_covers_context_checks_and_fails_closed(self):
        with patch("lyric_aligner.alignment.source_packets.SOURCE_PACKET_MAX_TOTAL_DP_CELLS", 1):
            r = self.build()
        self.assertEqual(r["selection_reason"], "source_packet_resource_limit")
        self.assertFalse(r["candidates"])
        search = r["coverage"]["bounded_target_search"]
        self.assertEqual(search["resource_limit"], "max_total_dp_cells")
        self.assertEqual(search["dp_cells_used"], 0)

    def test_adversarial_repetition_has_bounded_lattice_calls_and_skips_partial_matcher(self):
        target = "a" * 8 + "b" + "a" * 8
        with patch(
            "lyric_aligner.alignment.anchored_edit.align_anchored_characters",
            wraps=anchored_edit.align_anchored_characters,
        ) as lattice:
            r = self.build(target=target, lines=["a", target, "a"], words=self.words(["a"] * 320))
        self.assertEqual(r["selection_reason"], "source_packet_resource_limit")
        self.assertFalse(r["candidates"])
        search = r["coverage"]["bounded_target_search"]
        self.assertEqual(search["resource_limit"], "max_search_spans")
        self.assertEqual(search["target_dp_calls"], lattice.call_count)
        self.assertLessEqual(lattice.call_count, search["max_search_spans"])
        self.assertLess(lattice.call_count, 2_718)
        self.assertTrue(search["partial_target_matching_skipped_due_to_resource_limit"])

    def test_bounded_partial_matcher_preflight_rejects_quadratic_repetition(self):
        size = 500
        target = "a" * size + "b"
        r = build_source_packet_candidates(
            canonical_lines=[{"canonical_line_index": 0, "text": target}],
            observed_words=[{"text": "a" * size, "start_ms": 100, "end_ms": 200, "window_id": "whole"}],
            target_cue={"cue_id": "only", "canonical_character_ranges": [
                {"canonical_line_index": 0, "start_char": 0, "end_char": len(target)}]},
            source_audio_sha256="recording", lyric_version="v1", model_id="observed-v1",
            source_search_domain={"start_ms": 0, "end_ms": 30_000, "domain_id": "whole"},
            context_radius=0, target_alignment_policy=BOUNDED_TARGET_POLICY,
        )
        self.assertEqual(r["selection_reason"], "source_packet_resource_limit")
        self.assertFalse(r["candidates"])
        search = r["coverage"]["bounded_target_search"]
        self.assertEqual(search["resource_limit"], "max_partial_matcher_cells")
        self.assertEqual(search["partial_matcher_calls"], 0)
        self.assertTrue(search["partial_target_matching_skipped_due_to_resource_limit"])

    def test_affordable_bounded_partial_matcher_retains_diagnostic_coverage(self):
        target = "aaaaab"
        r = build_source_packet_candidates(
            canonical_lines=[{"canonical_line_index": 0, "text": target}],
            observed_words=[{"text": "aaaaa", "start_ms": 100, "end_ms": 200, "window_id": "whole"}],
            target_cue={"cue_id": "only", "canonical_character_ranges": [
                {"canonical_line_index": 0, "start_char": 0, "end_char": len(target)}]},
            source_audio_sha256="recording", lyric_version="v1", model_id="observed-v1",
            source_search_domain={"start_ms": 0, "end_ms": 30_000, "domain_id": "whole"},
            context_radius=0, target_alignment_policy=BOUNDED_TARGET_POLICY,
        )
        self.assertEqual(r["selection_reason"], "partial_context")
        self.assertEqual(r["candidates"][0]["match_kind"], "partial_target_observed_match")
        search = r["coverage"]["bounded_target_search"]
        self.assertEqual(search["partial_matcher_calls"], 1)
        self.assertFalse(search["partial_target_matching_skipped_due_to_resource_limit"])


if __name__ == "__main__":
    unittest.main()
