"""Behavioral tests for conservative globally anchored edit correspondence."""

from __future__ import annotations

from itertools import product
import unittest

from lyric_aligner.alignment.anchored_edit import align_anchored_characters


def _enumerate_edit_paths(canonical: str, observed: str):
    """Small-string oracle which enumerates edit paths, without DP matrices."""

    paths = []

    def visit(canonical_index, observed_index, cost, operations):
        if canonical_index == len(canonical) and observed_index == len(observed):
            paths.append((cost, tuple(operations)))
            return
        if canonical_index < len(canonical) and observed_index < len(observed):
            if canonical[canonical_index] == observed[observed_index]:
                visit(
                    canonical_index + 1,
                    observed_index + 1,
                    cost,
                    operations + (("equal", canonical_index, observed_index),),
                )
            else:
                visit(
                    canonical_index + 1,
                    observed_index + 1,
                    cost + 1,
                    operations + (("substitute", canonical_index, observed_index),),
                )
        if canonical_index < len(canonical):
            visit(
                canonical_index + 1,
                observed_index,
                cost + 1,
                operations + (("delete", canonical_index, None),),
            )
        if observed_index < len(observed):
            visit(
                canonical_index,
                observed_index + 1,
                cost + 1,
                operations + (("insert", None, observed_index),),
            )

    visit(0, 0, 0, ())
    return paths


def _oracle_consensus(canonical: str, observed: str, slack: int):
    paths = _enumerate_edit_paths(canonical, observed)
    minimum_cost = min(cost for cost, _ in paths)
    admissible = [operations for cost, operations in paths if cost <= minimum_cost + slack]
    mapping = []
    states = []
    for canonical_index in range(len(canonical)):
        equal_indices = set()
        has_non_equal = False
        for operations in admissible:
            operation, _, observed_index = next(
                item for item in operations if item[1] == canonical_index
            )
            if operation == "equal":
                equal_indices.add(observed_index)
            else:
                has_non_equal = True
        equal_indices = sorted(equal_indices)
        mapping.append(equal_indices[0] if len(equal_indices) == 1 and not has_non_equal else None)
        states.append((equal_indices, has_non_equal))
    return minimum_cost, mapping, states


class AnchoredEditTests(unittest.TestCase):
    def test_repeated_short_token_has_no_forced_correspondence(self):
        result = align_anchored_characters("a", "aa", ambiguity_slack=0)
        self.assertEqual(result["minimum_edit_distance"], 1)
        self.assertEqual(result["consensus_observed_indices"], [None])
        self.assertEqual(result["character_states"][0]["equal_observed_indices"], [0, 1])
        self.assertFalse(result["character_states"][0]["can_be_non_equal"])

    def test_deleted_character_is_not_mapped_while_surrounding_characters_are(self):
        result = align_anchored_characters("abc", "ac", ambiguity_slack=0)
        self.assertEqual(result["minimum_edit_distance"], 1)
        self.assertEqual(result["consensus_observed_indices"], [0, None, 1])
        self.assertTrue(result["character_states"][1]["can_be_non_equal"])

    def test_internal_substitution_keeps_unaffected_ends_as_consensus(self):
        result = align_anchored_characters("abcd", "axcd", ambiguity_slack=0)
        self.assertEqual(result["consensus_observed_indices"], [0, None, 2, 3])
        self.assertTrue(result["character_states"][1]["can_be_non_equal"])

    def test_extra_repeated_token_does_not_force_its_correspondence(self):
        result = align_anchored_characters("ab", "aab", ambiguity_slack=0)
        self.assertEqual(result["consensus_observed_indices"], [None, 2])
        self.assertEqual(result["character_states"][0]["equal_observed_indices"], [0, 1])

    def test_empty_inputs_and_empty_sides(self):
        self.assertEqual(align_anchored_characters("", "")["consensus_observed_indices"], [])
        inserted = align_anchored_characters("", "雨")
        self.assertEqual(inserted["minimum_edit_distance"], 1)
        self.assertEqual(inserted["diagnostic_trace"][0]["operation"], "insert")
        deleted = align_anchored_characters("歌", "")
        self.assertEqual(deleted["minimum_edit_distance"], 1)
        self.assertEqual(deleted["consensus_observed_indices"], [None])
        self.assertEqual(deleted["diagnostic_trace"][0]["operation"], "delete")

    def test_unicode_is_characterwise_and_never_normalized(self):
        result = align_anchored_characters("東京Ａ", "東京A", ambiguity_slack=0)
        self.assertEqual(result["minimum_edit_distance"], 1)
        self.assertEqual(result["consensus_observed_indices"], [0, 1, None])
        self.assertEqual(result["diagnostic_trace"][-1]["operation"], "substitute")

    def test_non_none_consensus_indices_are_strictly_monotonic(self):
        result = align_anchored_characters("我爱你啊", "我真爱你啊", ambiguity_slack=0)
        indexes = [index for index in result["consensus_observed_indices"] if index is not None]
        self.assertEqual(indexes, sorted(indexes))
        self.assertEqual(len(indexes), len(set(indexes)))

    def test_slack_exposes_near_optimal_non_equal_paths(self):
        exact = align_anchored_characters("a", "a", ambiguity_slack=0)
        # Replacing an equal edge by delete+insert costs two.  A slack of one
        # must not invent that non-standard one-cost "substitution".
        near = align_anchored_characters("a", "a", ambiguity_slack=2)
        self.assertEqual(exact["consensus_observed_indices"], [0])
        self.assertEqual(near["consensus_observed_indices"], [None])
        self.assertTrue(near["character_states"][0]["can_be_non_equal"])

    def test_max_cells_boundary_and_too_large_is_before_dp(self):
        allowed = align_anchored_characters("ab", "cd", max_cells=9)
        self.assertEqual(allowed["status"], "ok")
        self.assertEqual(allowed["matrix_cells"], 9)
        rejected = align_anchored_characters("x" * 20_000, "y", max_cells=3)
        self.assertEqual(rejected["status"], "too_large")
        self.assertEqual(rejected["minimum_edit_distance"], None)
        self.assertIsNone(rejected["consensus_observed_indices"])
        self.assertIsNone(rejected["diagnostic_trace"])
        self.assertEqual(rejected["matrix_cells"], 40_002)

    def test_oracle_matches_all_small_binary_strings_at_exact_and_slack_one(self):
        strings = ["".join(characters) for length in range(4) for characters in product("ab", repeat=length)]
        for canonical in strings:
            for observed in strings:
                for slack in (0, 1):
                    with self.subTest(canonical=canonical, observed=observed, slack=slack):
                        expected_distance, expected_mapping, expected_states = _oracle_consensus(
                            canonical, observed, slack
                        )
                        actual = align_anchored_characters(canonical, observed, ambiguity_slack=slack)
                        self.assertEqual(actual["minimum_edit_distance"], expected_distance)
                        self.assertEqual(actual["consensus_observed_indices"], expected_mapping)
                        self.assertEqual(
                            [
                                (state["equal_observed_indices"], state["can_be_non_equal"])
                                for state in actual["character_states"]
                            ],
                            expected_states,
                        )

    def test_diagnostic_trace_is_deterministic_and_replays_to_minimum_distance(self):
        first = align_anchored_characters("ab", "ba")
        second = align_anchored_characters("ab", "ba")
        self.assertEqual(first["diagnostic_trace"], second["diagnostic_trace"])
        trace = first["diagnostic_trace"]
        self.assertEqual(
            sum(item["operation"] != "equal" for item in trace),
            first["minimum_edit_distance"],
        )
        self.assertEqual("".join(item["canonical_character"] or "" for item in trace), "ab")
        self.assertEqual("".join(item["observed_character"] or "" for item in trace), "ba")


if __name__ == "__main__":
    unittest.main()
