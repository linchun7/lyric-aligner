import unittest

from lyric_aligner.timeline.boundary_sequence_optimizer import (
    BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY,
    BoundaryCandidate,
    BoundarySequenceOptimizationError,
    node,
    optimize_boundary_sequence,
)


def candidate(candidate_id, ms, loss, *, editor=False, auto=False):
    return BoundaryCandidate(
        candidate_id=candidate_id,
        boundary_ms=ms,
        expected_loss_ms=loss,
        source="editor" if editor else "audio",
        authority_tier="editor_baseline" if editor else "calibrated_best_estimate",
        is_editor=editor,
        automatic_eligible=auto,
    )


class BoundarySequenceOptimizerTests(unittest.TestCase):
    def test_selects_minimum_expected_loss_path(self):
        nodes = [
            node(
                "cue1-start",
                "start",
                [candidate("s-editor", 1000, 300, editor=True), candidate("s-audio", 1100, 80, auto=True)],
            ),
            node(
                "cue1-end",
                "end",
                [candidate("e-editor", 2500, 250, editor=True), candidate("e-audio", 2400, 70, auto=True)],
                minimum_delta_from_previous_ms=250,
            ),
        ]
        result = optimize_boundary_sequence(nodes)
        self.assertTrue(result.feasible)
        self.assertEqual(result.selected_candidate_ids, ("s-audio", "e-audio"))
        self.assertEqual(result.selected_boundaries_ms, (1100, 2400))
        self.assertEqual(result.non_editor_selection_count, 2)
        self.assertEqual(result.automatic_eligible_selection_count, 2)
        self.assertFalse(result.automatic_mutation_allowed)
        self.assertEqual(result.authority, BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY)

    def test_global_minimum_duration_constraint_rejects_locally_best_candidate(self):
        nodes = [
            node(
                "start",
                "start",
                [candidate("start-audio", 1200, 40, auto=True), candidate("start-editor", 1000, 100, editor=True)],
            ),
            node(
                "end",
                "end",
                [candidate("end-too-early", 1350, 10, auto=True), candidate("end-safe", 1600, 60, auto=True)],
                minimum_delta_from_previous_ms=250,
            ),
        ]
        result = optimize_boundary_sequence(nodes)
        self.assertTrue(result.feasible)
        self.assertEqual(result.selected_candidate_ids, ("start-audio", "end-safe"))
        self.assertEqual(result.selected_boundaries_ms, (1200, 1600))

    def test_previous_node_choice_can_change_to_enable_better_global_path(self):
        nodes = [
            node(
                "first",
                "generic",
                [candidate("first-low-loss", 1500, 10, auto=True), candidate("first-earlier", 1000, 80, editor=True)],
            ),
            node(
                "second",
                "generic",
                [candidate("second-best", 1600, 10, auto=True), candidate("second-late", 2200, 200, editor=True)],
                minimum_delta_from_previous_ms=250,
            ),
        ]
        result = optimize_boundary_sequence(nodes)
        # Local greedy would choose first-low-loss then be forced to second-late (210 total).
        # Dynamic programming chooses first-earlier + second-best (90 total).
        self.assertEqual(result.selected_candidate_ids, ("first-earlier", "second-best"))
        self.assertEqual(result.total_expected_loss_ms, 90.0)

    def test_structural_lock_forces_editor_even_when_audio_loss_is_lower(self):
        nodes = [
            node(
                "ambiguous",
                "start",
                [candidate("editor", 1000, 500, editor=True), candidate("audio", 1400, 20, auto=True)],
                structural_lock_to_editor=True,
            )
        ]
        result = optimize_boundary_sequence(nodes)
        self.assertEqual(result.selected_candidate_ids, ("editor",))
        self.assertEqual(result.non_editor_selection_count, 0)

    def test_equal_loss_prefers_less_churn_editor_path(self):
        nodes = [
            node(
                "tie",
                "start",
                [candidate("audio", 1100, 100, auto=True), candidate("editor", 1000, 100, editor=True)],
            )
        ]
        result = optimize_boundary_sequence(nodes)
        self.assertEqual(result.selected_candidate_ids, ("editor",))

    def test_absolute_bounds_filter_candidates(self):
        nodes = [
            node(
                "bounded",
                "internal",
                [candidate("too-early", 900, 5, auto=True), candidate("inside", 1200, 50, auto=True)],
                lower_bound_ms=1000,
                upper_bound_ms=1500,
            )
        ]
        result = optimize_boundary_sequence(nodes)
        self.assertEqual(result.selected_candidate_ids, ("inside",))

    def test_maximum_delta_constraint_is_enforced(self):
        nodes = [
            node("a", "generic", [candidate("a", 1000, 10, editor=True)]),
            node(
                "b",
                "generic",
                [candidate("far", 3000, 5, auto=True), candidate("near", 1500, 20, editor=True)],
                minimum_delta_from_previous_ms=100,
                maximum_delta_from_previous_ms=1000,
            ),
        ]
        result = optimize_boundary_sequence(nodes)
        self.assertEqual(result.selected_candidate_ids, ("a", "near"))

    def test_no_feasible_path_returns_explicit_failure(self):
        nodes = [
            node("a", "generic", [candidate("a", 2000, 10, editor=True)]),
            node(
                "b",
                "generic",
                [candidate("b", 2100, 10, editor=True)],
                minimum_delta_from_previous_ms=500,
            ),
        ]
        result = optimize_boundary_sequence(nodes)
        self.assertFalse(result.feasible)
        self.assertEqual(result.selected_candidate_ids, ())
        self.assertIn("no_globally_feasible_path", result.reason)

    def test_structural_lock_without_editor_candidate_fails_closed(self):
        with self.assertRaises(BoundarySequenceOptimizationError):
            optimize_boundary_sequence(
                [
                    node(
                        "bad-lock",
                        "start",
                        [candidate("audio", 1000, 10, auto=True)],
                        structural_lock_to_editor=True,
                    )
                ]
            )


if __name__ == "__main__":
    unittest.main()
