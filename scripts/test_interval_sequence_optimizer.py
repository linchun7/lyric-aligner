import math
from itertools import product
import unittest

from lyric_aligner.timeline.boundary_sequence_optimizer import (
    INTERVAL_SEQUENCE_OPTIMIZER_AUTHORITY,
    BoundarySequenceOptimizationError,
    IntervalCandidate,
    interval_node,
    optimize_interval_sequence,
)


def candidate(
    candidate_id,
    start,
    end,
    cost,
    *,
    baseline=False,
    lane="main",
    occurrence="occ-main",
    mapping="map-main",
):
    return IntervalCandidate(
        candidate_id=candidate_id,
        start_ms=start,
        end_ms=end,
        lane_id=lane,
        occurrence_path_id=occurrence,
        map_path_id=mapping,
        is_baseline=baseline,
        selection_cost=cost,
    )


def brute_force_atomic_disjoint(nodes, atomic_proposals):
    """Independent small oracle for disjoint-window atomic proposal tests."""
    best = None
    candidate_lookup = {
        (node.node_id, item.candidate_id): item
        for node in nodes for item in node.candidates
    }
    for choices in product(*(node.candidates for node in nodes)):
        selected_ids = {item.candidate_id for item in choices}
        if any(
            any(candidate_lookup[member].candidate_id in selected_ids for member in members)
            and not all(candidate_lookup[member].candidate_id in selected_ids for member in members)
            for members in atomic_proposals.values()
        ):
            continue
        key = (
            sum(item.selection_cost for item in choices),
            sum(not item.is_baseline for item in choices),
            sum(abs(item.start_ms - next(candidate for candidate in node.candidates if candidate.is_baseline).start_ms)
                + abs(item.end_ms - next(candidate for candidate in node.candidates if candidate.is_baseline).end_ms)
                for node, item in zip(nodes, choices)),
            tuple(item.candidate_id for item in choices),
        )
        if best is None or key < best[0]:
            best = (key, choices)
    assert best is not None
    return best


class IntervalSequenceOptimizerTests(unittest.TestCase):
    def test_adjacent_conflict_uses_global_feasible_pair(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "first",
                    [
                        candidate("first-keep", 100, 200, 8, baseline=True),
                        candidate("first-preferred", 100, 260, 0),
                    ],
                    continuity_group_id="phrase",
                ),
                interval_node(
                    "second",
                    [
                        candidate("second-keep", 240, 340, 8, baseline=True),
                        candidate("second-conflict", 230, 340, 0),
                        candidate("second-safe", 260, 340, 2),
                    ],
                    continuity_group_id="phrase",
                ),
            ]
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.selected_candidate_ids, ("first-preferred", "second-safe"))
        self.assertEqual(result.total_selection_cost, 2.0)
        self.assertEqual(result.baseline_selection_count, 0)

    def test_existing_cross_lane_overlap_is_not_serialized(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "lead",
                    [
                        candidate("lead-keep", 100, 300, 7, baseline=True, lane="lead"),
                        candidate("lead-audio", 100, 290, 0, lane="lead"),
                    ],
                ),
                interval_node(
                    "backing",
                    [
                        candidate("backing-keep", 250, 350, 7, baseline=True, lane="backing"),
                        candidate("backing-audio", 240, 350, 0, lane="backing"),
                    ],
                ),
            ]
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.selected_candidate_ids, ("lead-audio", "backing-audio"))
        self.assertEqual(result.selected_intervals_ms, ((100, 290), (240, 350)))

    def test_cross_lane_new_overlap_is_rejected(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "lane-a",
                    [
                        candidate("a-keep", 100, 200, 5, baseline=True, lane="a"),
                        candidate("a-overlap", 100, 260, 0, lane="a"),
                    ],
                ),
                interval_node(
                    "lane-b",
                    [
                        candidate("b-keep", 240, 340, 5, baseline=True, lane="b"),
                        candidate("b-overlap", 230, 340, 0, lane="b"),
                    ],
                ),
            ]
        )
        self.assertNotEqual(result.selected_candidate_ids, ("a-overlap", "b-overlap"))
        self.assertEqual(result.total_selection_cost, 5.0)

    def test_one_group_cannot_mix_occurrence_or_mapping_paths(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "left",
                    [
                        candidate("left-keep", 100, 180, 50, baseline=True, occurrence="old", mapping="old-map"),
                        candidate("left-new", 100, 180, 0, occurrence="new", mapping="new-map"),
                    ],
                    continuity_group_id="phrase",
                ),
                interval_node(
                    "right",
                    [
                        candidate("right-keep", 220, 300, 50, baseline=True, occurrence="old", mapping="old-map"),
                        candidate("right-new", 220, 300, 60, occurrence="new", mapping="new-map"),
                    ],
                    continuity_group_id="phrase",
                ),
            ]
        )
        # The invalid local mix would cost 50.  The selector must either keep
        # the old path (100) or change both atomic intervals to the new path
        # (60); it chooses the latter.
        self.assertEqual(result.selected_candidate_ids, ("left-new", "right-new"))
        self.assertEqual(result.total_selection_cost, 60.0)

    def test_existing_nested_geometry_is_feasible_but_cannot_worsen(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "outer",
                    [
                        candidate("outer-keep", 100, 500, 5, baseline=True, lane="lead"),
                        candidate("outer-shift", 90, 500, 0, lane="lead"),
                    ],
                ),
                interval_node(
                    "inner",
                    [
                        candidate("inner-keep", 200, 300, 4, baseline=True, lane="backing"),
                        candidate("inner-wider", 180, 320, 0, lane="backing"),
                    ],
                ),
            ]
        )
        self.assertEqual(result.selected_candidate_ids, ("outer-shift", "inner-keep"))
        self.assertEqual(result.selected_intervals_ms, ((90, 500), (200, 300)))

    def test_same_lane_overlapping_baselines_cannot_reverse_canonical_start_order(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "first",
                    [
                        candidate("first-keep", 100, 300, 5, baseline=True, lane="lead"),
                        candidate("first-swapped", 300, 400, 0, lane="lead"),
                    ],
                ),
                interval_node(
                    "second",
                    [
                        candidate("second-keep", 200, 400, 5, baseline=True, lane="lead"),
                        candidate("second-swapped", 100, 200, 0, lane="lead"),
                    ],
                ),
            ]
        )
        self.assertNotEqual(
            result.selected_candidate_ids,
            ("first-swapped", "second-swapped"),
        )
        first_start, second_start = (interval[0] for interval in result.selected_intervals_ms)
        self.assertLessEqual(first_start, second_start)

    def test_same_group_overlapping_baselines_cannot_reverse_start_order_across_lanes(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "first",
                    [
                        candidate("first-keep", 100, 300, 5, baseline=True, lane="lead"),
                        candidate("first-swapped", 300, 400, 0, lane="lead"),
                    ],
                    continuity_group_id="one-phrase",
                ),
                interval_node(
                    "second",
                    [
                        candidate("second-keep", 200, 400, 5, baseline=True, lane="backing"),
                        candidate("second-swapped", 100, 200, 0, lane="backing"),
                    ],
                    continuity_group_id="one-phrase",
                ),
            ]
        )
        self.assertNotEqual(
            result.selected_candidate_ids,
            ("first-swapped", "second-swapped"),
        )

    def test_discarded_disjoint_frontier_cannot_hide_a_future_start_order_reversal(self):
        # The first interval leaves the active frontier immediately because
        # every later candidate starts after it ends.  That proof also means a
        # later candidate cannot move before its start, so no start-order check
        # has been lost when the frontier entry is discarded.
        result = optimize_interval_sequence(
            [
                interval_node(
                    "first",
                    [
                        candidate("first-keep", 100, 150, 1, baseline=True, lane="lead"),
                        candidate("first-alt", 105, 160, 0, lane="lead"),
                    ],
                ),
                interval_node(
                    "second",
                    [
                        candidate("second-keep", 200, 250, 1, baseline=True, lane="lead"),
                        candidate("second-alt", 200, 260, 0, lane="lead"),
                    ],
                ),
                interval_node(
                    "third",
                    [
                        candidate("third-keep", 300, 350, 1, baseline=True, lane="lead"),
                        candidate("third-alt", 300, 360, 0, lane="lead"),
                    ],
                ),
            ],
            max_active_states=1,
        )
        self.assertTrue(result.globally_optimal)
        self.assertEqual(
            result.selected_candidate_ids,
            ("first-alt", "second-alt", "third-alt"),
        )
        starts = [interval[0] for interval in result.selected_intervals_ms]
        self.assertEqual(starts, sorted(starts))

    def test_unordered_input_returns_selected_values_in_input_order(self):
        result = optimize_interval_sequence(
            [
                interval_node("late", [candidate("late-keep", 300, 400, 0, baseline=True)]),
                interval_node("early", [candidate("early-keep", 100, 200, 0, baseline=True)]),
            ]
        )
        self.assertEqual(result.selected_node_ids, ("late", "early"))
        self.assertEqual(result.selected_candidate_ids, ("late-keep", "early-keep"))
        self.assertEqual(result.selection_order_node_ids, ("early", "late"))
        self.assertEqual(result.input_order_by_selection_order, (1, 0))
        self.assertEqual(
            result.to_dict()["selected_intervals_ms"],
            [[300, 400], [100, 200]],
        )

    def test_single_baseline_candidate_is_a_keep_path(self):
        result = optimize_interval_sequence(
            [interval_node("only", [candidate("keep", 100, 200, 3, baseline=True)])]
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.selected_candidate_ids, ("keep",))
        self.assertEqual(result.baseline_selection_count, 1)
        self.assertFalse(result.automatic_mutation_allowed)
        self.assertEqual(result.authority, INTERVAL_SEQUENCE_OPTIMIZER_AUTHORITY)

    def test_active_state_limit_returns_complete_keep_without_claiming_optimum(self):
        result = optimize_interval_sequence(
            [
                interval_node(
                    "first",
                    [
                        candidate("first-keep", 100, 400, 3, baseline=True),
                        candidate("first-alt", 100, 500, 0),
                    ],
                ),
                interval_node("second", [candidate("second-keep", 450, 550, 3, baseline=True)]),
            ],
            max_active_states=1,
        )
        self.assertTrue(result.feasible)
        self.assertFalse(result.globally_optimal)
        self.assertEqual(result.selected_candidate_ids, ("first-keep", "second-keep"))
        self.assertIn("keep_baseline", result.reason)

    def test_long_disjoint_sequence_keeps_one_active_frontier_state(self):
        nodes = [
            interval_node(
                f"node-{index}",
                [
                    candidate(f"keep-{index}", index * 20, index * 20 + 10, 1, baseline=True),
                    candidate(f"alt-{index}", index * 20, index * 20 + 10, 0),
                ],
            )
            for index in range(1000)
        ]
        result = optimize_interval_sequence(nodes, max_active_states=1)
        self.assertTrue(result.globally_optimal)
        self.assertEqual(result.node_count, 1000)
        self.assertEqual(result.selected_candidate_ids[0], "alt-0")
        self.assertEqual(result.selected_candidate_ids[-1], "alt-999")

    def test_missing_candidates_or_keep_baseline_fail_closed(self):
        with self.assertRaises(BoundarySequenceOptimizationError):
            optimize_interval_sequence([interval_node("empty", [])])
        with self.assertRaises(BoundarySequenceOptimizationError):
            optimize_interval_sequence(
                [interval_node("no-keep", [candidate("audio", 100, 200, 0)])]
            )

    def test_invalid_or_nonfinite_interval_input_fails_closed(self):
        malformed = [
            candidate("nan", 100, 200, math.nan, baseline=True),
            candidate("infinite", 100, 200, math.inf, baseline=True),
            candidate("negative-cost", 100, 200, -1, baseline=True),
            candidate("bad-end", 200, 200, 1, baseline=True),
            candidate("float-start", 100.0, 200, 1, baseline=True),
            candidate("empty-lane", 100, 200, 1, baseline=True, lane=""),
            candidate("text-cost", 100, 200, "invalid", baseline=True),
        ]
        for index, malformed_candidate in enumerate(malformed):
            with self.subTest(index=index):
                with self.assertRaises(BoundarySequenceOptimizationError):
                    optimize_interval_sequence([interval_node("bad", [malformed_candidate])])

    def test_path_mismatch_in_keep_baselines_fails_before_selection(self):
        with self.assertRaises(BoundarySequenceOptimizationError):
            optimize_interval_sequence(
                [
                    interval_node(
                        "one",
                        [candidate("one-keep", 100, 200, 0, baseline=True, occurrence="a")],
                        continuity_group_id="shared",
                    ),
                    interval_node(
                        "two",
                        [candidate("two-keep", 300, 400, 0, baseline=True, occurrence="b")],
                        continuity_group_id="shared",
                    ),
                ]
            )

    def test_atomic_proposals_match_small_disjoint_exhaustive_oracle(self):
        nodes = [
            interval_node("one", [candidate("one-keep", 100, 150, 9, baseline=True),
                                  candidate("one-a", 100, 150, 1), candidate("one-free", 100, 150, 3)]),
            interval_node("two", [candidate("two-keep", 200, 250, 9, baseline=True),
                                  candidate("two-a", 200, 250, 1), candidate("two-free", 200, 250, 0)]),
            interval_node("three", [candidate("three-keep", 300, 350, 9, baseline=True),
                                    candidate("three-a", 300, 350, 1), candidate("three-free", 300, 350, 2)]),
        ]
        proposals = {
            "outer-pair": (("one", "one-a"), ("three", "three-a")),
            "middle-pair": (("two", "two-a"), ("three", "three-free")),
        }
        expected, _choices = brute_force_atomic_disjoint(nodes, proposals)
        result = optimize_interval_sequence(nodes, atomic_proposals=proposals)
        self.assertTrue(result.feasible)
        self.assertEqual(
            (result.total_selection_cost, result.baseline_selection_count,
             tuple(result.selected_candidate_ids)),
            (float(expected[0]), 3 - expected[1], expected[3]),
        )

    def test_overlapping_atomic_proposals_cannot_be_mixed(self):
        nodes = [
            interval_node("one", [candidate("one-keep", 100, 150, 10, baseline=True),
                                  candidate("one-a", 100, 150, 0), candidate("one-b", 100, 150, 0)]),
            interval_node("two", [candidate("two-keep", 200, 250, 10, baseline=True),
                                  candidate("two-a", 200, 250, 0)]),
            interval_node("three", [candidate("three-keep", 300, 350, 10, baseline=True),
                                    candidate("three-b", 300, 350, 0)]),
        ]
        result = optimize_interval_sequence(nodes, atomic_proposals={
            "a": (("one", "one-a"), ("two", "two-a")),
            "b": (("one", "one-b"), ("three", "three-b")),
        })
        self.assertEqual(result.selected_candidate_ids, ("one-a", "two-a", "three-keep"))
        self.assertNotIn("three-b", result.selected_candidate_ids)

    def test_atomic_pair_with_internal_geometry_conflict_is_wholly_rejected(self):
        nodes = [
            interval_node("one", [candidate("one-keep", 100, 200, 10, baseline=True),
                                  candidate("one-a", 100, 300, 0)]),
            interval_node("two", [candidate("two-keep", 250, 350, 10, baseline=True),
                                  candidate("two-a", 240, 350, 0)]),
        ]
        result = optimize_interval_sequence(nodes, atomic_proposals={
            "pair": (("one", "one-a"), ("two", "two-a")),
        })
        self.assertEqual(result.selected_candidate_ids, ("one-keep", "two-keep"))

    def test_atomic_pair_with_first_and_last_external_conflicts_is_wholly_rejected(self):
        nodes = [
            interval_node("left", [candidate("left-keep", 100, 200, 0, baseline=True)]),
            interval_node("first", [candidate("first-keep", 220, 300, 10, baseline=True),
                                    candidate("first-a", 190, 300, 0)]),
            interval_node("last", [candidate("last-keep", 320, 400, 10, baseline=True),
                                   candidate("last-a", 320, 430, 0)]),
            interval_node("right", [candidate("right-keep", 420, 500, 0, baseline=True)]),
        ]
        result = optimize_interval_sequence(nodes, atomic_proposals={
            "pair": (("first", "first-a"), ("last", "last-a")),
        })
        self.assertEqual(result.selected_candidate_ids,
                         ("left-keep", "first-keep", "last-keep", "right-keep"))

    def test_atomic_proposal_validation_is_fail_closed(self):
        nodes = [
            interval_node("one", [candidate("one-keep", 100, 150, 1, baseline=True), candidate("one-a", 100, 150, 0)]),
            interval_node("two", [candidate("two-keep", 200, 250, 1, baseline=True), candidate("two-a", 200, 250, 0)]),
        ]
        invalid = [
            {"short": (("one", "one-a"),)},
            {"duplicate-node": (("one", "one-a"), ("one", "one-keep"))},
            {"missing-node": (("missing", "one-a"), ("two", "two-a"))},
            {"missing-candidate": (("one", "missing"), ("two", "two-a"))},
            {"baseline": (("one", "one-keep"), ("two", "two-a"))},
            {"first": (("one", "one-a"), ("two", "two-a")),
             "second": (("one", "one-a"), ("two", "two-keep"))},
        ]
        for proposal in invalid:
            with self.subTest(proposal=proposal):
                with self.assertRaises(BoundarySequenceOptimizationError):
                    optimize_interval_sequence(nodes, atomic_proposals=proposal)

    def test_empty_atomic_proposals_preserves_default_result_exactly(self):
        nodes = [
            interval_node("one", [candidate("one-keep", 100, 200, 5, baseline=True), candidate("one-a", 100, 200, 0)]),
            interval_node("two", [candidate("two-keep", 250, 350, 5, baseline=True), candidate("two-a", 250, 350, 0)]),
        ]
        self.assertEqual(
            optimize_interval_sequence(nodes).to_dict(),
            optimize_interval_sequence(nodes, atomic_proposals={}).to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
