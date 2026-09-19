import unittest

from lyric_aligner.audio.cuts import (
    CutRebuildError,
    LocalizedCutBoundary,
    build_cut_aware_timewarp,
)
from lyric_aligner.config import DEFAULT_V4_PROFILE
from lyric_aligner.timeline.projector import source_time_at_mix


def point(mix, source):
    return {
        "mix_center": mix,
        "source_center": source,
        "estimated_slope": 1.0,
        "fused_score": 0.96,
        "feature_scores": {"chroma": 0.96, "mfcc": 0.94},
    }


def path():
    return [
        point(0.5, 0.5),
        point(1.5, 1.5),
        point(2.5, 2.5),
        point(3.5, 3.5),
        point(4.5, 4.5),
        point(5.5, 8.5),
        point(6.5, 9.5),
        point(7.5, 10.5),
        point(8.5, 11.5),
    ]


def boundary():
    return LocalizedCutBoundary(
        candidate_id="candidate-1",
        issue_id="issue-1",
        cut_mix_time=5.0,
        localized_source_gap_start=5.0,
        localized_source_gap_end=8.0,
        left_score=0.95,
        right_score=0.94,
        left_margin=0.12,
        right_margin=0.11,
        boundary_margin=0.08,
        left_feature_agreement=2,
        right_feature_agreement=2,
        search_start=4.0,
        search_end=6.0,
    )


class V4CutMappingTests(unittest.TestCase):
    def test_cut_aware_mapping_preserves_explicit_forward_source_gap(self):
        rebuilt = build_cut_aware_timewarp(
            alignment_path=path(),
            localized_boundaries=[boundary()],
            mix_start=0.0,
            mix_end=9.0,
            timewarp_config=DEFAULT_V4_PROFILE.timewarp,
        )
        self.assertEqual(rebuilt["kind"], "CUT_AWARE")
        self.assertEqual(len(rebuilt["segments"]), 2)
        self.assertEqual(len(rebuilt["cuts"]), 1)
        left, right = rebuilt["segments"]
        self.assertAlmostEqual(left["mix_end"], 5.0, places=6)
        self.assertAlmostEqual(right["mix_start"], 5.0, places=6)
        self.assertLess(left["source_end"], right["source_start"])
        cut = rebuilt["cuts"][0]
        self.assertGreater(cut["mapped_source_gap_seconds"], 2.5)
        self.assertAlmostEqual(
            source_time_at_mix(left["mapping"], 4.0), 4.0, delta=0.2
        )
        self.assertAlmostEqual(
            source_time_at_mix(right["mapping"], 6.0), 9.0, delta=0.2
        )

    def test_multiple_cuts_require_strict_mix_order(self):
        second = LocalizedCutBoundary(
            candidate_id="candidate-2",
            issue_id="issue-2",
            cut_mix_time=5.0,
            localized_source_gap_start=8.0,
            localized_source_gap_end=9.0,
            left_score=0.95,
            right_score=0.95,
            left_margin=0.10,
            right_margin=0.10,
            boundary_margin=0.05,
            left_feature_agreement=2,
            right_feature_agreement=2,
            search_start=4.5,
            search_end=5.5,
        )
        with self.assertRaisesRegex(CutRebuildError, "strictly increasing"):
            build_cut_aware_timewarp(
                alignment_path=path(),
                localized_boundaries=[boundary(), second],
                mix_start=0.0,
                mix_end=9.0,
                timewarp_config=DEFAULT_V4_PROFILE.timewarp,
            )

    def test_cut_boundary_must_be_inside_occurrence(self):
        outside = LocalizedCutBoundary(
            candidate_id="candidate-outside",
            issue_id="issue-outside",
            cut_mix_time=9.0,
            localized_source_gap_start=12.0,
            localized_source_gap_end=14.0,
            left_score=0.95,
            right_score=0.95,
            left_margin=0.10,
            right_margin=0.10,
            boundary_margin=0.05,
            left_feature_agreement=2,
            right_feature_agreement=2,
            search_start=8.0,
            search_end=9.0,
        )
        with self.assertRaisesRegex(CutRebuildError, "inside the occurrence"):
            build_cut_aware_timewarp(
                alignment_path=path(),
                localized_boundaries=[outside],
                mix_start=0.0,
                mix_end=9.0,
                timewarp_config=DEFAULT_V4_PROFILE.timewarp,
            )


if __name__ == "__main__":
    unittest.main()
