import unittest

from lyric_aligner.timeline.reference_retime import (
    ReferenceRetimeError,
    map_reference_time_ms,
    normalize_offset_segments,
    normalize_retained_segments,
    retime_reference_result,
    retime_reference_result_with_retained_segments,
)


class ReferenceTimelineRetimeTests(unittest.TestCase):
    def test_step_mapping_and_crossing_cue_preserve_inserted_time(self):
        segments = normalize_offset_segments(
            [
                {"reference_start_ms": 0, "offset_ms": -20},
                {"reference_start_ms": 9900, "offset_ms": 533},
            ]
        )
        self.assertEqual(map_reference_time_ms(3983, segments), 3963)
        self.assertEqual(map_reference_time_ms(8217, segments), 8197)
        self.assertEqual(map_reference_time_ms(12952, segments), 13485)

        reference = {
            "occurrence_id": "occ_test",
            "ordinal": 1,
            "track_id": "track_test",
            "canonical_selection_sha256": "selection",
            "window": {"start_ms": 0, "end_ms": 20000},
            "line_count": 3,
            "lines": [
                {
                    "canonical_line_index": 0,
                    "text": "first",
                    "mix_start_ms": 3983,
                    "mix_end_ms": 8217,
                },
                {
                    "canonical_line_index": 1,
                    "text": "crosses inserted region",
                    "mix_start_ms": 8217,
                    "mix_end_ms": 12952,
                },
                {
                    "canonical_line_index": 2,
                    "text": "after step",
                    "mix_start_ms": 12952,
                    "mix_end_ms": 17291,
                },
            ],
        }
        result = retime_reference_result(
            reference,
            target_window_start_ms=0,
            target_window_end_ms=20000,
            segments=segments,
        )
        self.assertTrue(result["reference_retimed"])
        self.assertEqual(result["projection_issues"], [])
        self.assertEqual(
            [(row["mix_start_ms"], row["mix_end_ms"]) for row in result["lines"]],
            [(3963, 8197), (8197, 13485), (13485, 17824)],
        )
        # The cue spanning the edit naturally absorbs the inserted interval.
        self.assertEqual(result["lines"][1]["mix_end_ms"] - result["lines"][1]["mix_start_ms"], 5288)

    def test_segments_fail_closed_on_non_monotone_offsets(self):
        with self.assertRaises(ReferenceRetimeError):
            normalize_offset_segments(
                [
                    {"reference_start_ms": 0, "offset_ms": 500},
                    {"reference_start_ms": 1000, "offset_ms": 100},
                ]
            )

    def test_retime_clips_to_target_window(self):
        segments = normalize_offset_segments(
            [{"reference_start_ms": 0, "offset_ms": 100}]
        )
        reference = {
            "lines": [
                {"canonical_line_index": 0, "mix_start_ms": 0, "mix_end_ms": 1000},
                {"canonical_line_index": 1, "mix_start_ms": 1000, "mix_end_ms": 2000},
            ]
        }
        result = retime_reference_result(
            reference,
            target_window_start_ms=500,
            target_window_end_ms=1500,
            segments=segments,
        )
        self.assertEqual(
            [(row["mix_start_ms"], row["mix_end_ms"]) for row in result["lines"]],
            [(500, 1100), (1100, 1500)],
        )

    def test_open_end_is_preserved_when_line_remains_in_window(self):
        segments = normalize_offset_segments(
            [{"reference_start_ms": 0, "offset_ms": 100}]
        )
        reference = {
            "lines": [
                {
                    "canonical_line_index": 0,
                    "mix_start_ms": 1000,
                    "mix_end_ms": None,
                    "end_basis": "open_end",
                    "tokens": [],
                }
            ]
        }
        result = retime_reference_result(
            reference,
            target_window_start_ms=0,
            target_window_end_ms=3000,
            segments=segments,
        )
        self.assertEqual(result["lines"][0]["mix_start_ms"], 1100)
        self.assertIsNone(result["lines"][0]["mix_end_ms"])
        self.assertEqual(result["lines"][0]["end_basis"], "open_end")

    def test_token_timed_reference_fails_closed(self):
        segments = normalize_offset_segments(
            [{"reference_start_ms": 0, "offset_ms": 0}]
        )
        reference = {
            "lines": [
                {
                    "canonical_line_index": 0,
                    "mix_start_ms": 1000,
                    "mix_end_ms": 2000,
                    "tokens": [{"text": "x", "mix_start_ms": 1000, "mix_end_ms": 1200}],
                }
            ]
        }
        with self.assertRaises(ReferenceRetimeError):
            retime_reference_result(
                reference,
                target_window_start_ms=0,
                target_window_end_ms=3000,
                segments=segments,
            )

    def test_retained_segments_drop_removed_line_and_clip_boundary_lines(self):
        retained = normalize_retained_segments(
            [
                {"reference_start_ms": 0, "reference_end_ms": 12000, "target_start_ms": -2000},
                {"reference_start_ms": 18000, "reference_end_ms": None, "target_start_ms": 10000},
            ]
        )
        reference = {
            "lines": [
                {"canonical_line_index": 0, "text": "before", "mix_start_ms": 4000, "mix_end_ms": 8000},
                {"canonical_line_index": 1, "text": "clips at cut", "mix_start_ms": 8000, "mix_end_ms": 13000},
                {"canonical_line_index": 2, "text": "removed", "mix_start_ms": 13000, "mix_end_ms": 17000},
                {"canonical_line_index": 3, "text": "clips at resume", "mix_start_ms": 17000, "mix_end_ms": 19000},
                {"canonical_line_index": 4, "text": "after", "mix_start_ms": 19000, "mix_end_ms": 22000},
            ]
        }
        result = retime_reference_result_with_retained_segments(
            reference,
            target_window_start_ms=0,
            target_window_end_ms=20000,
            retained_segments=retained,
        )
        self.assertEqual(result["reference_retime_mode"], "retained_segments")
        self.assertEqual([row["canonical_line_index"] for row in result["lines"]], [0, 1, 3, 4])
        self.assertEqual(
            [(row["mix_start_ms"], row["mix_end_ms"]) for row in result["lines"]],
            [(2000, 6000), (6000, 10000), (10000, 11000), (11000, 14000)],
        )
        self.assertTrue(result["lines"][1]["reference_splice_clipped"])
        self.assertTrue(result["lines"][2]["reference_splice_clipped"])

    def test_retained_segments_fail_closed_when_one_line_survives_both_sides(self):
        retained = normalize_retained_segments(
            [
                {"reference_start_ms": 0, "reference_end_ms": 10000, "target_start_ms": 0},
                {"reference_start_ms": 12000, "reference_end_ms": None, "target_start_ms": 10000},
            ]
        )
        reference = {
            "lines": [
                {"canonical_line_index": 0, "mix_start_ms": 9000, "mix_end_ms": 13000},
            ]
        }
        with self.assertRaises(ReferenceRetimeError):
            retime_reference_result_with_retained_segments(
                reference,
                target_window_start_ms=0,
                target_window_end_ms=20000,
                retained_segments=retained,
            )

    def test_retained_segments_require_monotone_non_overlapping_target(self):
        with self.assertRaises(ReferenceRetimeError):
            normalize_retained_segments(
                [
                    {"reference_start_ms": 0, "reference_end_ms": 10000, "target_start_ms": 0},
                    {"reference_start_ms": 12000, "reference_end_ms": None, "target_start_ms": 9000},
                ]
            )


if __name__ == "__main__":
    unittest.main()
