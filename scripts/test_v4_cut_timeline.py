import unittest

from lyric_aligner.text.canonical_lyrics import CanonicalLine, CanonicalToken
from lyric_aligner.timeline.cuts import project_cut_aware_lines


def mapping():
    return {
        "kind": "CUT_AWARE",
        "mix_start": 0.0,
        "mix_end": 9.0,
        "segments": [
            {
                "index": 0,
                "mix_start": 0.0,
                "mix_end": 5.0,
                "source_start": 0.0,
                "source_end": 5.0,
                "mapping": {
                    "intercept": 0.0,
                    "base_slope": 1.0,
                    "breakpoints": [],
                    "slope_deltas": [],
                },
            },
            {
                "index": 1,
                "mix_start": 5.0,
                "mix_end": 9.0,
                "source_start": 8.0,
                "source_end": 12.0,
                "mapping": {
                    "intercept": 3.0,
                    "base_slope": 1.0,
                    "breakpoints": [],
                    "slope_deltas": [],
                },
            },
        ],
        "cuts": [
            {
                "candidate_id": "cut-1",
                "issue_id": "issue-1",
                "cut_mix_time": 5.0,
                "source_gap_start": 5.0,
                "source_gap_end": 8.0,
            }
        ],
    }


def line(index, time_ms, text, tokens=()):
    return CanonicalLine(
        index=index,
        time_ms=time_ms,
        text=text,
        timing_format="enhanced_lrc" if tokens else "line_lrc",
        tokens=tuple(tokens),
    )


def token(text, start_ms, end_ms):
    return CanonicalToken(text=text, start_ms=start_ms, end_ms=end_ms)


class V4CutTimelineTests(unittest.TestCase):
    def test_line_lrc_omits_only_when_entire_inferred_interval_is_inside_gap(self):
        rows = [
            line(0, 1000, "before"),
            line(1, 4000, "crosses cut"),
            line(2, 5500, "fully removed"),
            line(3, 6500, "starts in gap but survives"),
            line(4, 8500, "after"),
        ]
        projected, issues, omitted = project_cut_aware_lines(rows, mapping())
        self.assertIn("before", [row["text"] for row in projected])
        self.assertIn("after", [row["text"] for row in projected])
        self.assertNotIn("fully removed", [row["text"] for row in projected])
        self.assertIn("fully removed", [row["text"] for row in omitted])
        self.assertTrue(
            any(
                issue["code"] == "line_lrc_intersects_confirmed_cut"
                and issue["canonical_line_index"] == 1
                for issue in issues
            )
        )
        self.assertTrue(
            any(
                issue["code"] == "line_lrc_starts_in_confirmed_cut"
                and issue["canonical_line_index"] == 3
                for issue in issues
            )
        )

    def test_last_line_starting_inside_gap_is_review_not_silently_omitted(self):
        rows = [line(0, 6000, "open partial")]
        projected, issues, omitted = project_cut_aware_lines(rows, mapping())
        self.assertEqual(projected, [])
        self.assertEqual(omitted, [])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], "line_lrc_starts_in_confirmed_cut")

    def test_word_timing_keeps_only_complete_surviving_canonical_tokens(self):
        rows = [
            line(
                0,
                4000,
                "ABC",
                tokens=(
                    token("A", 4000, 4500),
                    token("B", 5500, 6000),
                    token("C", 8200, 8500),
                ),
            )
        ]
        projected, issues, omitted = project_cut_aware_lines(rows, mapping())
        self.assertEqual(issues, [])
        self.assertEqual(omitted, [])
        self.assertEqual([row["text"] for row in projected], ["A", "C"])
        self.assertTrue(all(row["canonical_fragment"] for row in projected))
        self.assertEqual(projected[0]["mix_start_ms"], 4000)
        self.assertEqual(projected[1]["mix_start_ms"], 5200)

    def test_token_intersected_by_cut_stays_review_required(self):
        rows = [
            line(
                0,
                4700,
                "AB",
                tokens=(
                    token("A", 4700, 5200),
                    token("B", 8200, 8500),
                ),
            )
        ]
        projected, issues, _ = project_cut_aware_lines(rows, mapping())
        self.assertEqual([row["text"] for row in projected], ["B"])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], "token_intersects_confirmed_cut")
        self.assertEqual(issues[0]["token_text"], "A")

    def test_full_word_timed_line_outside_gap_preserves_canonical_full_text(self):
        rows = [
            line(
                0,
                1000,
                "hello world",
                tokens=(
                    token("hello ", 1000, 1500),
                    token("world", 1500, 2000),
                ),
            )
        ]
        projected, issues, omitted = project_cut_aware_lines(rows, mapping())
        self.assertEqual(issues, [])
        self.assertEqual(omitted, [])
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["text"], "hello world")
        self.assertFalse(projected[0]["canonical_fragment"])


if __name__ == "__main__":
    unittest.main()
