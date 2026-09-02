import unittest
from pathlib import Path

from lyric_aligner.io.materializer_path_safety import declared_input_paths
from lyric_aligner.qa.final_candidate_audit import (
    FinalCandidateAuditError,
    audit_final_candidate,
)
from lyric_aligner.srt import Cue


def row(occurrence_id: str, ordinal: int, line_index: int, *, end_basis="next_line_start"):
    return {
        "occurrence_id": occurrence_id,
        "ordinal": str(ordinal),
        "canonical_line_index": str(line_index),
        "end_basis": end_basis,
    }


class V4FinalCandidateAuditTests(unittest.TestCase):
    def test_clean_candidate_passes_and_reports_duration_distribution(self):
        result = audit_final_candidate(
            [
                Cue(1, 100, 900, "alpha"),
                Cue(2, 1000, 1800, "beta"),
            ],
            [row("occ-a", 1, 0), row("occ-b", 2, 0)],
            occurrence_windows={"occ-a": (0, 1000), "occ-b": (1000, 2000)},
            content_end_ms=2000,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["cue_count"], 2)
        self.assertEqual(result["duration"]["median_ms"], 800.0)
        self.assertEqual(result["last_end_minus_content_end_ms"], -200)

    def test_occurrence_window_violation_fails(self):
        result = audit_final_candidate(
            [Cue(1, 900, 1200, "outside")],
            [row("occ-a", 1, 0)],
            occurrence_windows={"occ-a": (0, 1000)},
            content_end_ms=2000,
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "occurrence_window_violation",
            {item["kind"] for item in result["errors"]},
        )

    def test_unconfirmed_cross_occurrence_overlap_fails(self):
        result = audit_final_candidate(
            [
                Cue(1, 800, 1200, "left"),
                Cue(2, 1000, 1500, "right"),
            ],
            [row("occ-a", 1, 0), row("occ-b", 2, 0)],
            occurrence_windows={"occ-a": (0, 1300), "occ-b": (900, 2000)},
            content_end_ms=2000,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["unconfirmed_overlap_cue_intersections"]), 1)

    def test_nonadjacent_overlap_is_not_missed(self):
        result = audit_final_candidate(
            [
                Cue(1, 0, 3000, "long-left"),
                Cue(2, 500, 900, "middle"),
                Cue(3, 1200, 1600, "later"),
            ],
            [
                row("occ-a", 1, 0),
                row("occ-b", 2, 0),
                row("occ-c", 3, 0),
            ],
            occurrence_windows={
                "occ-a": (0, 3000),
                "occ-b": (400, 1000),
                "occ-c": (1100, 1700),
            },
            confirmed_overlap_regions=[
                {
                    "left_occurrence_id": "occ-a",
                    "right_occurrence_id": "occ-b",
                    "start_ms": 500,
                    "end_ms": 900,
                }
            ],
            content_end_ms=3000,
        )
        self.assertFalse(result["passed"])
        unconfirmed = result["unconfirmed_overlap_cue_intersections"]
        self.assertTrue(
            any(
                item["left_position"] == 1 and item["right_position"] == 3
                for item in unconfirmed
            )
        )

    def test_confirmed_overlap_region_allows_exact_cross_occurrence_intersection(self):
        result = audit_final_candidate(
            [
                Cue(1, 800, 1200, "left"),
                Cue(2, 1000, 1500, "right"),
            ],
            [row("occ-a", 1, 0), row("occ-b", 2, 0)],
            occurrence_windows={"occ-a": (0, 1300), "occ-b": (900, 2000)},
            confirmed_overlap_regions=[
                {
                    "left_occurrence_id": "occ-a",
                    "right_occurrence_id": "occ-b",
                    "start_ms": 950,
                    "end_ms": 1250,
                }
            ],
            content_end_ms=2000,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["confirmed_overlap_cue_intersections"]), 1)
        self.assertEqual(result["unconfirmed_overlap_cue_intersections"], [])

    def test_same_occurrence_overlap_remains_invalid(self):
        result = audit_final_candidate(
            [
                Cue(1, 100, 800, "left"),
                Cue(2, 700, 1200, "right"),
            ],
            [row("occ-a", 1, 0), row("occ-a", 1, 1)],
            occurrence_windows={"occ-a": (0, 1500)},
            confirmed_overlap_regions=[
                {
                    "left_occurrence_id": "occ-a",
                    "right_occurrence_id": "occ-b",
                    "start_ms": 0,
                    "end_ms": 1500,
                }
            ],
            content_end_ms=1500,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["unconfirmed_overlap_cue_intersections"][0]["reason"],
            "same_occurrence_overlap",
        )

    def test_long_holds_are_diagnostic_only(self):
        result = audit_final_candidate(
            [Cue(1, 100, 9100, "long")],
            [row("occ-a", 1, 0)],
            occurrence_windows={"occ-a": (0, 10000)},
            content_end_ms=10000,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["duration"]["over_long_hold_count"], 1)
        self.assertEqual(result["duration"]["at_or_over_extreme_hold_count"], 1)
        self.assertEqual({item["kind"] for item in result["warnings"]}, {
            "long_display_holds",
            "extreme_display_holds",
        })

    def test_nonmonotonic_final_file_order_fails(self):
        result = audit_final_candidate(
            [
                Cue(1, 1000, 1400, "later"),
                Cue(2, 100, 500, "earlier"),
            ],
            [row("occ-b", 2, 0), row("occ-a", 1, 0)],
            occurrence_windows={"occ-a": (0, 600), "occ-b": (900, 1500)},
            content_end_ms=1500,
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "nonmonotonic_final_file_order",
            {item["kind"] for item in result["errors"]},
        )

    def test_count_mismatch_fails_closed(self):
        with self.assertRaisesRegex(FinalCandidateAuditError, "count mismatch"):
            audit_final_candidate(
                [Cue(1, 0, 500, "one")],
                [],
                occurrence_windows={"occ-a": (0, 1000)},
            )

    def test_run_declared_timeline_path_is_collectable_for_output_protection(self):
        paths = declared_input_paths(
            {
                "run": {
                    "occurrences": [
                        {
                            "occurrence_id": "occ-a",
                            "timeline_path": "output/task/timelines/occ-a.timeline.json",
                        }
                    ]
                }
            }
        )
        self.assertIn("run.occurrences[0].timeline_path", paths)
        self.assertEqual(
            paths["run.occurrences[0].timeline_path"],
            Path("output/task/timelines/occ-a.timeline.json"),
        )


if __name__ == "__main__":
    unittest.main()
