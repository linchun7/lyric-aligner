import unittest

from lyric_aligner.alignment.forced_projection import (
    ForcedMixProjectionError,
    project_forced_alignment_to_mix,
)


class V4ForcedMixProjectionTests(unittest.TestCase):
    def forced(self, *, start=1000, end=2000, spans=None):
        return {
            "backend": "external_forced_aligner",
            "jobs": [
                {
                    "job_id": "job-1",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "canonical_line_index": 0,
                    "canonical_text_sha256": "a" * 64,
                    "source_audio_sha256": "b" * 64,
                    "source_window_ms": [0, 7000],
                    "line_source_start_ms": start,
                    "line_source_end_ms": end,
                    "line_confidence": 0.9,
                    "backend_id": "fake",
                    "backend_version": "1",
                    "model_id": "model",
                    "model_revision": "rev",
                    "spans": spans or [],
                }
            ],
        }

    def test_affine_projection_uses_existing_timewarp_inverse(self):
        result = project_forced_alignment_to_mix(
            forced_evidence=self.forced(start=1000, end=2000),
            mappings_by_occurrence={
                "occ-1": {
                    "intercept": 0.0,
                    "base_slope": 2.0,
                    "breakpoints": [],
                    "slope_deltas": [],
                }
            },
        )
        row = result["jobs"][0]
        self.assertEqual(row["projection_status"], "projected")
        self.assertEqual(row["mix_start_ms"], 500)
        self.assertEqual(row["mix_end_ms"], 1000)
        self.assertEqual(result["projected_line_count"], 1)
        self.assertEqual(result["unprojectable_line_count"], 0)

    def test_piecewise_rate_projection_respects_hinge_slope(self):
        result = project_forced_alignment_to_mix(
            forced_evidence=self.forced(start=2000, end=5000),
            mappings_by_occurrence={
                "occ-1": {
                    "intercept": 0.0,
                    "base_slope": 2.0,
                    "breakpoints": [1.0],
                    "slope_deltas": [1.0],
                }
            },
        )
        row = result["jobs"][0]
        self.assertEqual(row["mix_start_ms"], 1000)
        self.assertEqual(row["mix_end_ms"], 2000)

    def cut_mapping(self):
        return {
            "kind": "CUT_AWARE",
            "segments": [
                {
                    "index": 0,
                    "mix_start": 0.0,
                    "mix_end": 1.0,
                    "source_start": 0.0,
                    "source_end": 2.0,
                    "mapping": {
                        "intercept": 0.0,
                        "base_slope": 2.0,
                        "breakpoints": [],
                        "slope_deltas": [],
                    },
                },
                {
                    "index": 1,
                    "mix_start": 1.0,
                    "mix_end": 2.0,
                    "source_start": 4.0,
                    "source_end": 6.0,
                    "mapping": {
                        "intercept": 2.0,
                        "base_slope": 2.0,
                        "breakpoints": [],
                        "slope_deltas": [],
                    },
                },
            ],
            "cuts": [
                {
                    "candidate_id": "cut-1",
                    "source_gap_start": 2.0,
                    "source_gap_end": 4.0,
                }
            ],
        }

    def test_cut_aware_same_retained_segment_projects(self):
        result = project_forced_alignment_to_mix(
            forced_evidence=self.forced(start=500, end=1500),
            mappings_by_occurrence={"occ-1": self.cut_mapping()},
        )
        row = result["jobs"][0]
        self.assertEqual(row["projection_status"], "projected")
        self.assertEqual(row["cut_aware_segment_index"], 0)
        self.assertEqual(row["mix_start_ms"], 250)
        self.assertEqual(row["mix_end_ms"], 750)

    def test_cut_aware_cross_gap_line_is_not_bridged(self):
        result = project_forced_alignment_to_mix(
            forced_evidence=self.forced(start=1500, end=4500),
            mappings_by_occurrence={"occ-1": self.cut_mapping()},
        )
        row = result["jobs"][0]
        self.assertEqual(row["projection_status"], "unprojectable")
        self.assertEqual(row["projection_reason"], "source_interval_crosses_confirmed_cut")
        self.assertIsNone(row["mix_start_ms"])
        self.assertIsNone(row["mix_end_ms"])
        self.assertEqual(result["unprojectable_line_count"], 1)

    def test_cut_aware_boundary_inside_gap_is_not_projected(self):
        result = project_forced_alignment_to_mix(
            forced_evidence=self.forced(start=2500, end=3500),
            mappings_by_occurrence={"occ-1": self.cut_mapping()},
        )
        row = result["jobs"][0]
        self.assertEqual(row["projection_status"], "unprojectable")
        self.assertIn("confirmed_gap", row["projection_reason"])

    def test_spans_are_projected_independently_across_cut_segments(self):
        spans = [
            {
                "span_index": 0,
                "char_start": 0,
                "char_end": 5,
                "canonical_fragment_sha256": "c" * 64,
                "source_start_ms": 500,
                "source_end_ms": 1500,
                "confidence": 0.9,
            },
            {
                "span_index": 1,
                "char_start": 6,
                "char_end": 11,
                "canonical_fragment_sha256": "d" * 64,
                "source_start_ms": 4200,
                "source_end_ms": 5200,
                "confidence": 0.8,
            },
        ]
        result = project_forced_alignment_to_mix(
            forced_evidence=self.forced(start=1500, end=4500, spans=spans),
            mappings_by_occurrence={"occ-1": self.cut_mapping()},
        )
        row = result["jobs"][0]
        self.assertEqual(row["projection_status"], "unprojectable")
        self.assertEqual(row["spans"][0]["mix_start_ms"], 250)
        self.assertEqual(row["spans"][0]["mix_end_ms"], 750)
        self.assertEqual(row["spans"][1]["mix_start_ms"], 1100)
        self.assertEqual(row["spans"][1]["mix_end_ms"], 1600)
        self.assertEqual(result["projected_span_count"], 2)

    def test_missing_occurrence_mapping_fails_closed(self):
        with self.assertRaisesRegex(ForcedMixProjectionError, "no Source-to-Mix mapping"):
            project_forced_alignment_to_mix(
                forced_evidence=self.forced(),
                mappings_by_occurrence={},
            )


if __name__ == "__main__":
    unittest.main()
