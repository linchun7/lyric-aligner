import unittest

from lyric_aligner.config import RenderConfig
from lyric_aligner.timeline.composer import TimelineComposeError, compose_canonical_timelines
from lyric_aligner.timeline.overlap import (
    ConfirmedOverlapRegion,
    OverlapRecompositionError,
    clip_projected_result_to_region,
    merge_primary_with_overlap_lines,
    region_from_issue,
)


def timeline(occurrence_id, track_id, ordinal, start, end, lines):
    return {
        "result": {
            "occurrence_id": occurrence_id,
            "track_id": track_id,
            "ordinal": ordinal,
            "canonical_selection_sha256": ("a" if ordinal == 1 else "b") * 64,
            "window": {"start_ms": start, "end_ms": end},
            "line_count": len(lines),
            "lines": lines,
        }
    }


def line(index, text, start, end):
    return {
        "canonical_line_index": index,
        "text": text,
        "timing_format": "line_lrc",
        "mix_start_ms": start,
        "mix_end_ms": end,
        "end_basis": "next_line_start",
        "tokens": [],
    }


class V4OverlapRecompositionTests(unittest.TestCase):
    def test_region_is_materialized_only_from_confirmed_overlap_issue(self):
        region = region_from_issue(
            {
                "issue_id": "issue-1",
                "candidate_id": "candidate-1",
                "left_occurrence_id": "left",
                "right_occurrence_id": "right",
                "interval_start": 9.0,
                "interval_end": 11.0,
                "confirmed_interval": [9.0, 11.0],
                "decision_action": "confirmed_overlap",
                "requires_recomposition": True,
            }
        )
        self.assertEqual(region.start_ms, 9000)
        self.assertEqual(region.end_ms, 11000)
        self.assertTrue(region.region_id)
        with self.assertRaises(OverlapRecompositionError):
            region_from_issue(
                {
                    "candidate_id": "candidate-1",
                    "left_occurrence_id": "left",
                    "right_occurrence_id": "right",
                    "interval_start": 9.0,
                    "interval_end": 11.0,
                    "decision_action": "resolved_clear",
                    "requires_recomposition": False,
                }
            )

    def test_confirmed_overlap_issue_requires_replayable_issue_id(self):
        with self.assertRaisesRegex(OverlapRecompositionError, "missing issue_id"):
            region_from_issue(
                {
                    "candidate_id": "candidate-1",
                    "left_occurrence_id": "left",
                    "right_occurrence_id": "right",
                    "interval_start": 9.0,
                    "interval_end": 11.0,
                    "confirmed_interval": [9.0, 11.0],
                    "decision_action": "confirmed_overlap",
                    "requires_recomposition": True,
                }
            )

    def test_projected_overlap_line_is_clipped_to_confirmed_region(self):
        region = ConfirmedOverlapRegion(
            candidate_id="candidate-1",
            left_occurrence_id="left",
            right_occurrence_id="right",
            start_ms=9000,
            end_ms=11000,
        )
        projected = {
            "lines": [
                line(2, "crossing line", 8500, 11500),
                line(3, "outside", 12000, 13000),
            ]
        }
        rows = clip_projected_result_to_region(projected, region)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mix_start_ms"], 9000)
        self.assertEqual(rows[0]["mix_end_ms"], 11000)
        self.assertEqual(rows[0]["overlap_candidate_id"], "candidate-1")

    def test_merge_extends_occurrence_window_without_inventing_gap_lines(self):
        region = ConfirmedOverlapRegion(
            candidate_id="candidate-1",
            left_occurrence_id="left",
            right_occurrence_id="right",
            start_ms=9000,
            end_ms=11000,
        )
        primary = timeline(
            "left",
            "track-left",
            1,
            0,
            10000,
            [line(0, "before", 7000, 9000), line(1, "cross", 9000, 10000)],
        )["result"]
        overlap = [line(1, "cross", 9000, 11000), line(2, "new", 10500, 11000)]
        merged = merge_primary_with_overlap_lines(primary, overlap, regions=[region])
        self.assertEqual(merged["window"], {"start_ms": 0, "end_ms": 11000})
        self.assertEqual([row["text"] for row in merged["lines"]], ["before", "cross", "new"])
        cross = merged["lines"][1]
        self.assertEqual(cross["mix_end_ms"], 11000)

    def test_composer_allows_only_overlap_fully_inside_confirmed_region(self):
        config = RenderConfig()
        left = timeline(
            "left",
            "track-left",
            1,
            0,
            11000,
            [line(0, "left vocal", 9000, 11000)],
        )
        right = timeline(
            "right",
            "track-right",
            2,
            9000,
            20000,
            [line(0, "right vocal", 9500, 10500)],
        )
        with self.assertRaisesRegex(TimelineComposeError, "outside confirmed-overlap"):
            compose_canonical_timelines([left, right], config=config)

        region = ConfirmedOverlapRegion(
            candidate_id="candidate-1",
            left_occurrence_id="left",
            right_occurrence_id="right",
            start_ms=9000,
            end_ms=11000,
        )
        cues = compose_canonical_timelines(
            [left, right],
            config=config,
            confirmed_overlap_regions=[region],
        )
        self.assertEqual([cue.text for cue in cues], ["left vocal", "right vocal"])

        too_narrow = ConfirmedOverlapRegion(
            candidate_id="candidate-1",
            left_occurrence_id="left",
            right_occurrence_id="right",
            start_ms=9700,
            end_ms=10300,
        )
        with self.assertRaisesRegex(TimelineComposeError, "outside confirmed-overlap"):
            compose_canonical_timelines(
                [left, right],
                config=config,
                confirmed_overlap_regions=[too_narrow],
            )

    def test_composer_checks_non_adjacent_cross_track_intersections(self):
        config = RenderConfig()
        left = timeline(
            "left",
            "track-left",
            1,
            0,
            15000,
            [line(0, "long left", 9000, 14000)],
        )
        right = timeline(
            "right",
            "track-right",
            2,
            9000,
            16000,
            [
                line(0, "right first", 9500, 10500),
                line(1, "right second", 12000, 13000),
            ],
        )
        first_only = ConfirmedOverlapRegion(
            candidate_id="candidate-first",
            left_occurrence_id="left",
            right_occurrence_id="right",
            start_ms=9000,
            end_ms=11000,
        )
        with self.assertRaisesRegex(TimelineComposeError, "outside confirmed-overlap"):
            compose_canonical_timelines(
                [left, right],
                config=config,
                confirmed_overlap_regions=[first_only],
            )

        second = ConfirmedOverlapRegion(
            candidate_id="candidate-second",
            left_occurrence_id="left",
            right_occurrence_id="right",
            start_ms=12000,
            end_ms=13000,
        )
        cues = compose_canonical_timelines(
            [left, right],
            config=config,
            confirmed_overlap_regions=[first_only, second],
        )
        self.assertEqual(
            [cue.text for cue in cues],
            ["long left", "right first", "right second"],
        )


if __name__ == "__main__":
    unittest.main()
