import unittest

from lyric_aligner.timeline.composition import (
    TimelineCompositionError,
    compose_cut_and_overlap_result,
    overlap_delta_lines,
    validate_cut_overlap_disjoint,
)
from lyric_aligner.timeline.overlap import ConfirmedOverlapRegion


def region(start_ms=9000, end_ms=11000):
    return ConfirmedOverlapRegion(
        candidate_id="overlap-1",
        issue_id="issue-overlap-1",
        left_occurrence_id="occ-a",
        right_occurrence_id="occ-b",
        start_ms=start_ms,
        end_ms=end_ms,
    )


def cut_result():
    return {
        "occurrence_id": "occ-a",
        "track_id": "track-a",
        "canonical_selection_sha256": "a" * 64,
        "ordinal": 0,
        "window": {"start_ms": 0, "end_ms": 12000},
        "line_count": 2,
        "cut_aware": True,
        "cuts": [
            {
                "candidate_id": "cut-1",
                "cut_mix_time": 5.0,
                "source_gap_start": 5.0,
                "source_gap_end": 8.0,
            }
        ],
        "lines": [
            {
                "canonical_line_index": 0,
                "text": "before",
                "mix_start_ms": 1000,
                "mix_end_ms": 3000,
            },
            {
                "canonical_line_index": 2,
                "text": "after",
                "mix_start_ms": 6000,
                "mix_end_ms": 8000,
            },
        ],
        "projection_issues": [],
    }


def overlap_result():
    return {
        "occurrence_id": "occ-a",
        "track_id": "track-a",
        "canonical_selection_sha256": "a" * 64,
        "ordinal": 0,
        "window": {"start_ms": 0, "end_ms": 11000},
        "line_count": 3,
        "lines": [
            {
                "canonical_line_index": 0,
                "text": "before",
                "mix_start_ms": 1000,
                "mix_end_ms": 3000,
            },
            {
                "canonical_line_index": 2,
                "text": "after",
                "mix_start_ms": 6000,
                "mix_end_ms": 8000,
            },
            {
                "canonical_line_index": 3,
                "text": "transition tail",
                "mix_start_ms": 9500,
                "mix_end_ms": 10500,
                "overlap_region_id": region().region_id,
                "overlap_candidate_id": "overlap-1",
                "overlap_clip": True,
            },
        ],
        "overlap_recomposition": {
            "region_ids": [region().region_id],
            "candidate_ids": ["overlap-1"],
        },
    }


class V4CutOverlapCompositionTests(unittest.TestCase):
    def test_disjoint_overlap_can_merge_onto_cut_aware_base(self):
        merged = compose_cut_and_overlap_result(
            cut_timeline_result=cut_result(),
            overlap_timeline_result=overlap_result(),
            occurrence_id="occ-a",
            regions=[region()],
        )
        self.assertTrue(merged["cut_aware"])
        self.assertEqual(len(merged["cuts"]), 1)
        self.assertEqual(
            [line["text"] for line in merged["lines"]],
            ["before", "after", "transition tail"],
        )
        self.assertEqual(
            merged["combined_recomposition"]["overlap_delta_line_count"],
            1,
        )

    def test_overlap_crossing_cut_boundary_is_fail_closed(self):
        crossing = region(start_ms=4500, end_ms=5500)
        with self.assertRaisesRegex(
            TimelineCompositionError,
            "intersects a localized cut boundary",
        ):
            validate_cut_overlap_disjoint(
                cut_timeline_result=cut_result(),
                occurrence_id="occ-a",
                regions=[crossing],
            )

    def test_only_overlap_materialized_lines_are_extracted_as_delta(self):
        rows = overlap_delta_lines(overlap_result())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "transition tail")

    def test_canonical_selection_mismatch_blocks_composition(self):
        overlap = overlap_result()
        overlap["canonical_selection_sha256"] = "b" * 64
        with self.assertRaisesRegex(
            TimelineCompositionError,
            "canonical selection identity mismatch",
        ):
            compose_cut_and_overlap_result(
                cut_timeline_result=cut_result(),
                overlap_timeline_result=overlap,
                occurrence_id="occ-a",
                regions=[region()],
            )


if __name__ == "__main__":
    unittest.main()
