from __future__ import annotations

import unittest

from lyric_aligner.partial_timeline_repair import (
    PartialTimelineRepairError,
    build_partial_timeline_preview,
    extract_source_to_mix_mapping,
    parse_srt_timing,
)
from lyric_aligner.text.canonical_lyrics import CanonicalLine
from lyric_aligner.text_repair import parse_srt_text


def canonical(*rows: tuple[int, str]) -> list[CanonicalLine]:
    return [
        CanonicalLine(index=index, time_ms=time_ms, text=text)
        for index, (time_ms, text) in enumerate(rows)
    ]


def source_srt(
    *,
    second_timing: str = "00:00:02,200 --> 00:00:03,200",
    third_timing: str = "00:00:03,100 --> 00:00:03,900",
) -> str:
    return (
        "1\n00:00:01,000 --> 00:00:01,900\n这是第一句\n\n"
        f"2\n{second_timing}\n这是第二句\n\n"
        f"3\n{third_timing}\n这是第三句\n"
    )


class PartialTimelineRepairTests(unittest.TestCase):
    def affine(self):
        return {
            "mode": "AFFINE",
            "intercept": 0.0,
            "base_slope": 2.0,
            "breakpoints": [],
            "slope_deltas": [],
        }

    def piecewise(self):
        return {
            "mode": "PIECEWISE_RATE",
            "intercept": 0.0,
            "base_slope": 2.0,
            "breakpoints": [1.0],
            "slope_deltas": [1.0],
        }

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
                    "mapping": self.affine(),
                },
                {
                    "index": 1,
                    "mix_start": 1.0,
                    "mix_end": 2.0,
                    "source_start": 4.0,
                    "source_end": 6.0,
                    "mapping": {
                        "mode": "AFFINE",
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

    def standard_canonical(self):
        return canonical(
            (2000, "这是第一句"),
            (4000, "这是第二句"),
            (6000, "这是第三句"),
            (8000, "这是第四句"),
        )

    def test_affine_rate_change_only_updates_selected_preview_cue(self):
        source = source_srt(third_timing="00:00:03,100 --> 00:00:03,900")
        preview, report = build_partial_timeline_preview(
            source,
            self.standard_canonical(),
            self.affine(),
            repair_cue_numbers=[2],
        )
        self.assertEqual(report["status"], "preview_ready")
        self.assertFalse(report["releaseable"])
        self.assertFalse(report["automatic_timing_change_allowed"])
        self.assertTrue(report["subtitle_text_unchanged"])
        self.assertEqual(report["proposed_change_count"], 1)
        self.assertEqual(report["review_count"], 0)
        self.assertIn("2\n00:00:02,000 --> 00:00:03,000\n这是第二句", preview)

        before = parse_srt_text(source)[1]
        after = parse_srt_text(preview)[1]
        self.assertEqual(before[0].timing, after[0].timing)
        self.assertEqual(before[0].text, after[0].text)
        self.assertEqual(before[1].text, after[1].text)
        self.assertEqual(before[2].timing, after[2].timing)
        self.assertEqual([cue.number for cue in before], [cue.number for cue in after])

    def test_piecewise_rate_projection_is_used_for_selected_cue(self):
        lines = canonical(
            (2000, "这是第一句"),
            (5000, "这是第二句"),
            (8000, "这是第三句"),
            (11000, "这是第四句"),
        )
        preview, report = build_partial_timeline_preview(
            source_srt(third_timing="00:00:03,100 --> 00:00:03,900"),
            lines,
            self.piecewise(),
            repair_cue_numbers=[2],
        )
        self.assertEqual(report["status"], "preview_ready")
        self.assertIn("2\n00:00:02,000 --> 00:00:03,000\n这是第二句", preview)
        self.assertEqual(report["decisions"][0]["canonical_source_start_ms"], 5000)
        self.assertEqual(report["decisions"][0]["canonical_source_end_ms"], 8000)

    def test_cut_aware_interval_inside_one_retained_segment_projects(self):
        source = (
            "1\n00:00:00,100 --> 00:00:00,700\n这是第一句\n\n"
            "2\n00:00:01,000 --> 00:00:01,500\n这是第二句\n\n"
            "3\n00:00:01,600 --> 00:00:01,900\n这是第三句\n"
        )
        lines = canonical(
            (500, "这是第一句"),
            (1500, "这是第二句"),
            (4500, "这是第三句"),
            (5500, "这是第四句"),
        )
        preview, report = build_partial_timeline_preview(
            source,
            lines,
            self.cut_mapping(),
            repair_cue_numbers=[1],
        )
        self.assertEqual(report["status"], "preview_ready")
        self.assertIn("00:00:00,250 --> 00:00:00,750", preview)
        self.assertEqual(report["decisions"][0]["cut_aware_segment_index"], 0)

    def test_cut_aware_interval_crossing_confirmed_cut_requires_review(self):
        source = (
            "1\n00:00:00,100 --> 00:00:00,700\n这是第一句\n\n"
            "2\n00:00:00,800 --> 00:00:01,400\n这是第二句\n\n"
            "3\n00:00:01,500 --> 00:00:01,900\n这是第三句\n"
        )
        lines = canonical(
            (500, "这是第一句"),
            (1500, "这是第二句"),
            (4500, "这是第三句"),
            (5500, "这是第四句"),
        )
        preview, report = build_partial_timeline_preview(
            source,
            lines,
            self.cut_mapping(),
            repair_cue_numbers=[2],
        )
        self.assertEqual(preview, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["review_count"], 1)
        decision = report["decisions"][0]
        self.assertEqual(decision["reason"], "source_interval_is_unprojectable")
        self.assertEqual(
            decision["projection_reason"], "source_interval_crosses_confirmed_cut"
        )

    def test_one_editor_cue_to_two_canonical_lines_is_not_timing_repaired(self):
        source = (
            "1\n00:00:01,000 --> 00:00:03,000\n这是第一句这是第二句\n\n"
            "2\n00:00:03,100 --> 00:00:04,000\n这是第三句\n"
        )
        lines = canonical(
            (2000, "这是第一句"),
            (4000, "这是第二句"),
            (6000, "这是第三句"),
            (8000, "这是第四句"),
        )
        preview, report = build_partial_timeline_preview(
            source,
            lines,
            self.affine(),
            repair_cue_numbers=[1],
        )
        self.assertEqual(preview, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(
            report["decisions"][0]["reason"],
            "timing_repair_requires_one_cue_one_canonical_line",
        )

    def test_repeated_subtitle_or_canonical_text_fails_closed(self):
        source = (
            "1\n00:00:01,000 --> 00:00:01,900\n开头唯一一句\n\n"
            "2\n00:00:02,000 --> 00:00:02,900\n我们一起走吧\n\n"
            "3\n00:00:03,000 --> 00:00:03,900\n我们一起走吧\n\n"
            "4\n00:00:04,000 --> 00:00:04,900\n结尾唯一一句\n"
        )
        lines = canonical(
            (2000, "开头唯一一句"),
            (4000, "我们一起走吧"),
            (6000, "我们一起走吧"),
            (8000, "结尾唯一一句"),
            (10000, "尾声唯一一句"),
        )
        preview, report = build_partial_timeline_preview(
            source,
            lines,
            self.affine(),
            repair_cue_numbers=[2],
        )
        self.assertEqual(preview, source)
        self.assertEqual(report["status"], "review_required")
        self.assertIn(
            report["decisions"][0]["reason"],
            {
                "subtitle_text_occurrence_is_not_unique",
                "canonical_text_occurrence_is_not_unique",
            },
        )

    def test_projected_interval_may_not_overlap_locked_neighbor(self):
        source = source_srt(
            second_timing="00:00:02,200 --> 00:00:02,700",
            third_timing="00:00:02,800 --> 00:00:03,900",
        )
        preview, report = build_partial_timeline_preview(
            source,
            self.standard_canonical(),
            self.affine(),
            repair_cue_numbers=[2],
        )
        self.assertEqual(preview, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(
            report["decisions"][0]["reason"],
            "proposed_timing_overlaps_locked_or_selected_neighbor",
        )

    def test_last_line_without_word_end_requires_review(self):
        source = (
            "1\n00:00:01,000 --> 00:00:01,900\n这是第一句\n\n"
            "2\n00:00:02,000 --> 00:00:02,900\n这是第二句\n"
        )
        lines = canonical((2000, "这是第一句"), (4000, "这是第二句"))
        preview, report = build_partial_timeline_preview(
            source,
            lines,
            self.affine(),
            repair_cue_numbers=[2],
        )
        self.assertEqual(preview, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(
            report["decisions"][0]["reason"], "canonical_line_has_open_end"
        )

    def test_selected_cue_must_exist_and_be_unique(self):
        with self.assertRaisesRegex(PartialTimelineRepairError, "does not exist"):
            build_partial_timeline_preview(
                source_srt(),
                self.standard_canonical(),
                self.affine(),
                repair_cue_numbers=[99],
            )
        with self.assertRaisesRegex(PartialTimelineRepairError, "must be unique"):
            build_partial_timeline_preview(
                source_srt(),
                self.standard_canonical(),
                self.affine(),
                repair_cue_numbers=[2, 2],
            )

    def test_timing_parser_supports_comma_and_dot(self):
        self.assertEqual(
            parse_srt_timing("00:00:01,250 --> 00:00:02.500 position:50%"),
            (1250, 2500),
        )

    def test_mapping_payload_requires_matching_occurrence_and_unblocked_mapping(self):
        payload = {
            "occurrence_id": "occ-1",
            "track_id": "track-1",
            "canonical_selection_sha256": "a" * 64,
            "result": {
                "timewarp": {
                    "mapping": self.affine(),
                    "blocked": False,
                }
            },
        }
        mapping, identity = extract_source_to_mix_mapping(
            payload,
            expected_occurrence_id="occ-1",
        )
        self.assertEqual(mapping, self.affine())
        self.assertEqual(identity["occurrence_id"], "occ-1")
        self.assertEqual(identity["mapping_kind"], "AFFINE")

        with self.assertRaisesRegex(PartialTimelineRepairError, "occurrence mismatch"):
            extract_source_to_mix_mapping(
                payload,
                expected_occurrence_id="occ-2",
            )

        blocked = {
            **payload,
            "result": {
                "timewarp": {
                    "mapping": self.affine(),
                    "blocked": True,
                }
            },
        }
        with self.assertRaisesRegex(PartialTimelineRepairError, "mapping is blocked"):
            extract_source_to_mix_mapping(
                blocked,
                expected_occurrence_id="occ-1",
            )

    def test_mapping_payload_requires_explicit_supported_mapping_kind(self):
        payload = {
            "occurrence_id": "occ-1",
            "result": {
                "timewarp": {
                    "mapping": {
                        "intercept": 0.0,
                        "base_slope": 2.0,
                        "breakpoints": [],
                        "slope_deltas": [],
                    },
                    "blocked": False,
                }
            },
        }
        with self.assertRaisesRegex(PartialTimelineRepairError, "kind is missing"):
            extract_source_to_mix_mapping(
                payload,
                expected_occurrence_id="occ-1",
            )

    def test_cut_mapping_payload_is_accepted_without_continuous_fallback(self):
        payload = {
            "occurrence_id": "occ-cut",
            "track_id": "track-cut",
            "canonical_selection_sha256": "b" * 64,
            "result": self.cut_mapping(),
        }
        mapping, identity = extract_source_to_mix_mapping(
            payload,
            expected_occurrence_id="occ-cut",
        )
        self.assertEqual(mapping["kind"], "CUT_AWARE")
        self.assertEqual(identity["mapping_source"], "cut_aware_rebuild")

    def test_unapplied_fine_mapping_is_rejected(self):
        payload = {
            "occurrence_id": "occ-1",
            "result": {
                "applied": False,
                "timewarp": {
                    "mapping": self.affine(),
                    "blocked": False,
                },
            },
        }
        with self.assertRaisesRegex(PartialTimelineRepairError, "not applied"):
            extract_source_to_mix_mapping(
                payload,
                expected_occurrence_id="occ-1",
            )


if __name__ == "__main__":
    unittest.main()
