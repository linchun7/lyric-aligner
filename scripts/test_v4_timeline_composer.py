import unittest

from lyric_aligner.config import RenderConfig
from lyric_aligner.timeline.composer import TimelineComposeError, compose_canonical_timelines


CONFIG = RenderConfig(
    minimum_cue_duration_ms=250,
    maximum_line_duration_ms=12000,
    open_line_duration_ms=5000,
    word_timing_tail_ms=120,
)


def timeline(occurrence_id, ordinal, start, end, lines):
    return {
        "result": {
            "occurrence_id": occurrence_id,
            "track_id": f"track-{occurrence_id}",
            "ordinal": ordinal,
            "window": {"start_ms": start, "end_ms": end},
            "lines": lines,
        }
    }


class V4TimelineComposerTests(unittest.TestCase):
    def test_open_last_line_uses_bounded_duration(self):
        payload = timeline(
            "occ-1",
            1,
            0,
            30000,
            [
                {
                    "canonical_line_index": 0,
                    "text": "last line",
                    "mix_start_ms": 10000,
                    "mix_end_ms": None,
                    "timing_format": "line_lrc",
                    "end_basis": "open_end",
                }
            ],
        )
        cues = compose_canonical_timelines([payload], config=CONFIG)
        self.assertEqual((cues[0].start_ms, cues[0].end_ms), (10000, 15000))

    def test_long_next_line_gap_does_not_hold_previous_lyric_indefinitely(self):
        payload = timeline(
            "occ-1",
            1,
            0,
            40000,
            [
                {
                    "canonical_line_index": 0,
                    "text": "first",
                    "mix_start_ms": 1000,
                    "mix_end_ms": 30000,
                    "timing_format": "line_lrc",
                    "end_basis": "next_line_start",
                },
                {
                    "canonical_line_index": 1,
                    "text": "second",
                    "mix_start_ms": 30000,
                    "mix_end_ms": None,
                    "timing_format": "line_lrc",
                    "end_basis": "open_end",
                },
            ],
        )
        cues = compose_canonical_timelines([payload], config=CONFIG)
        self.assertEqual(cues[0].end_ms, 13000)
        self.assertEqual(cues[1].start_ms, 30000)

    def test_word_timing_gets_small_profile_tail(self):
        payload = timeline(
            "occ-1",
            1,
            0,
            10000,
            [
                {
                    "canonical_line_index": 0,
                    "text": "word timed",
                    "mix_start_ms": 1000,
                    "mix_end_ms": 4200,
                    "timing_format": "enhanced_lrc",
                    "end_basis": "word_timing",
                }
            ],
        )
        cues = compose_canonical_timelines([payload], config=CONFIG)
        self.assertEqual(cues[0].end_ms, 4320)

    def test_primary_window_clips_line_without_inventing_text(self):
        payload = timeline(
            "occ-1",
            1,
            5000,
            10000,
            [
                {
                    "canonical_line_index": 0,
                    "text": "canonical",
                    "mix_start_ms": 4500,
                    "mix_end_ms": 6500,
                    "timing_format": "line_lrc",
                    "end_basis": "next_line_start",
                }
            ],
        )
        cues = compose_canonical_timelines([payload], config=CONFIG)
        self.assertEqual((cues[0].start_ms, cues[0].end_ms), (5000, 6500))
        self.assertEqual(cues[0].text, "canonical")

    def test_unconfirmed_cross_track_overlap_is_rejected(self):
        left = timeline(
            "left",
            1,
            0,
            10000,
            [
                {
                    "canonical_line_index": 0,
                    "text": "left",
                    "mix_start_ms": 8000,
                    "mix_end_ms": 10000,
                    "timing_format": "line_lrc",
                    "end_basis": "next_line_start",
                }
            ],
        )
        right = timeline(
            "right",
            2,
            9000,
            15000,
            [
                {
                    "canonical_line_index": 0,
                    "text": "right",
                    "mix_start_ms": 9000,
                    "mix_end_ms": 11000,
                    "timing_format": "line_lrc",
                    "end_basis": "next_line_start",
                }
            ],
        )
        with self.assertRaisesRegex(TimelineComposeError, "confirmed-overlap decision"):
            compose_canonical_timelines([left, right], config=CONFIG)

    def test_too_short_cue_blocks_instead_of_extending_through_next_line(self):
        payload = timeline(
            "occ-1",
            1,
            0,
            10000,
            [
                {
                    "canonical_line_index": 0,
                    "text": "too short",
                    "mix_start_ms": 1000,
                    "mix_end_ms": 1100,
                    "timing_format": "line_lrc",
                    "end_basis": "next_line_start",
                },
                {
                    "canonical_line_index": 1,
                    "text": "next",
                    "mix_start_ms": 1100,
                    "mix_end_ms": 3000,
                    "timing_format": "line_lrc",
                    "end_basis": "next_line_start",
                },
            ],
        )
        with self.assertRaisesRegex(TimelineComposeError, "too short"):
            compose_canonical_timelines([payload], config=CONFIG)


if __name__ == "__main__":
    unittest.main()
