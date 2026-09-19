import unittest

from lyric_aligner.text.canonical_lyrics import CanonicalLine
from lyric_aligner.timeline.projector import (
    ProjectionWindow,
    TimelineProjectionError,
    effective_timewarp,
    project_canonical_lines,
)


def identity_mapping():
    return {
        "intercept": 0.0,
        "base_slope": 1.0,
        "breakpoints": [],
        "slope_deltas": [],
    }


def bounded_coarse():
    windows = [
        {"mix_start": 0.0, "mix_end": 6.0},
        {"mix_start": 3.0, "mix_end": 9.0},
        {"mix_start": 6.0, "mix_end": 12.0},
        {"mix_start": 9.0, "mix_end": 15.0},
        {"mix_start": 12.0, "mix_end": 18.0},
    ]
    return {
        "result": {
            "path_coverage": {
                "status": "bounded_terminal_disconnect",
                "retrieved_window_count": 5,
                "selected_window_count": 3,
                "excluded_trailing_window_count": 2,
                "maximum_excluded_trailing_windows": 2,
                "excluded_mix_centers": [12.0, 15.0],
            },
            "windows": windows,
            "path": [{}, {}, {}],
            "timewarp": {"mapping": identity_mapping(), "blocked": False},
        }
    }


def canonical_lines():
    return [
        CanonicalLine(0, 1000, "safe-a"),
        CanonicalLine(1, 7000, "safe-b"),
        CanonicalLine(2, 11000, "crosses-cap"),
        CanonicalLine(3, 13000, "suffix"),
    ]


class V4ProjectionAuthorityTests(unittest.TestCase):
    def test_bounded_terminal_disconnect_caps_normal_projection(self):
        mapping, blocked, source = effective_timewarp(bounded_coarse())

        self.assertFalse(blocked)
        self.assertEqual(source, "coarse")
        self.assertEqual(
            mapping["projection_authority"],
            {
                "status": "bounded_terminal_disconnect",
                "mix_end_ms": 12000,
                "selected_window_count": 3,
                "excluded_trailing_window_count": 2,
            },
        )
        projected = project_canonical_lines(
            canonical_lines(),
            mapping,
            window=ProjectionWindow(0, 18000),
        )
        self.assertEqual([row["text"] for row in projected], ["safe-a", "safe-b"])
        self.assertTrue(all(row["mix_end_ms"] <= 12000 for row in projected))

    def test_complete_coarse_path_preserves_existing_projection_behavior(self):
        coarse = {
            "result": {
                "path_coverage": {
                    "status": "complete",
                    "retrieved_window_count": 4,
                    "selected_window_count": 4,
                    "excluded_trailing_window_count": 0,
                },
                "windows": [
                    {"mix_start": 0.0, "mix_end": 6.0},
                    {"mix_start": 3.0, "mix_end": 9.0},
                    {"mix_start": 6.0, "mix_end": 12.0},
                    {"mix_start": 9.0, "mix_end": 15.0},
                ],
                "path": [{}, {}, {}, {}],
                "timewarp": {"mapping": identity_mapping(), "blocked": False},
            }
        }
        mapping, blocked, source = effective_timewarp(coarse)

        self.assertFalse(blocked)
        self.assertEqual(source, "coarse")
        self.assertNotIn("projection_authority", mapping)
        projected = project_canonical_lines(
            canonical_lines(),
            mapping,
            window=ProjectionWindow(0, 18000),
        )
        self.assertEqual(
            [row["text"] for row in projected],
            ["safe-a", "safe-b", "crosses-cap", "suffix"],
        )

    def test_bounded_authority_survives_fine_mapping_selection(self):
        fine = {
            "result": {
                "applied": True,
                "timewarp": {"mapping": identity_mapping(), "blocked": False},
            }
        }
        mapping, blocked, source = effective_timewarp(bounded_coarse(), fine)

        self.assertFalse(blocked)
        self.assertEqual(source, "fine")
        self.assertEqual(mapping["projection_authority"]["mix_end_ms"], 12000)

    def test_malformed_bounded_coverage_fails_closed(self):
        coarse = bounded_coarse()
        coarse["result"]["path_coverage"]["selected_window_count"] = 4

        with self.assertRaisesRegex(
            TimelineProjectionError,
            "path_coverage does not match serialized evidence",
        ):
            effective_timewarp(coarse)


if __name__ == "__main__":
    unittest.main()
