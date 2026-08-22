import unittest

from lyric_aligner.config import RenderConfig
from lyric_aligner.timeline.composer import TimelineComposeError, compose_canonical_timelines


CONFIG = RenderConfig(
    minimum_cue_duration_ms=250,
    maximum_line_duration_ms=12000,
    open_line_duration_ms=5000,
    word_timing_tail_ms=120,
)


def timeline(*, omitted: int):
    return {
        "result": {
            "occurrence_id": "occ-1",
            "track_id": "track-1",
            "ordinal": 1,
            "window": {"start_ms": 0, "end_ms": 10000},
            "projection_coverage": {
                "status": "bounded_terminal_disconnect",
                "mix_end_ms": 6000,
                "requested_window_end_ms": 10000,
                "authority_omitted_line_count": omitted,
            },
            "lines": [
                {
                    "canonical_line_index": 0,
                    "text": "generic line",
                    "mix_start_ms": 1000,
                    "mix_end_ms": 3000,
                    "timing_format": "line_lrc",
                    "end_basis": "next_line_start",
                }
            ],
        }
    }


class V4ProjectionRenderGuardTests(unittest.TestCase):
    def test_incomplete_projection_cannot_be_rendered(self):
        with self.assertRaisesRegex(
            TimelineComposeError,
            "projection coverage is incomplete",
        ):
            compose_canonical_timelines([timeline(omitted=2)], config=CONFIG)

    def test_bounded_coverage_with_no_omitted_lines_remains_renderable(self):
        cues = compose_canonical_timelines([timeline(omitted=0)], config=CONFIG)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "generic line")

    def test_malformed_omitted_count_fails_closed(self):
        payload = timeline(omitted=0)
        payload["result"]["projection_coverage"]["authority_omitted_line_count"] = "bad"
        with self.assertRaisesRegex(
            TimelineComposeError,
            "invalid authority_omitted_line_count",
        ):
            compose_canonical_timelines([payload], config=CONFIG)


if __name__ == "__main__":
    unittest.main()
