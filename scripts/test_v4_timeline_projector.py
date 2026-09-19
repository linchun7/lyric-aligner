import tempfile
import unittest
from pathlib import Path

from lyric_aligner.assets.bindings import CanonicalOriginal, ResolvedAssetBinding
from lyric_aligner.timeline.projector import (
    ProjectionWindow,
    effective_timewarp,
    mix_time_for_source,
    project_binding_timeline,
    source_time_at_mix,
)


class V4TimelineProjectorTests(unittest.TestCase):
    def affine(self):
        return {
            "mode": "AFFINE",
            "intercept": 2.0,
            "base_slope": 1.25,
            "breakpoints": [],
            "slope_deltas": [],
        }

    def test_affine_inverse_round_trip(self):
        mapping = self.affine()
        for mix in (0.0, 3.2, 10.5, 27.0):
            source = source_time_at_mix(mapping, mix)
            self.assertAlmostEqual(mix_time_for_source(mapping, source), mix, places=7)

    def test_piecewise_inverse_round_trip(self):
        mapping = {
            "mode": "PIECEWISE_RATE",
            "intercept": 1.0,
            "base_slope": 1.0,
            "breakpoints": [10.0, 20.0],
            "slope_deltas": [0.2, -0.1],
        }
        for mix in (2.0, 10.0, 14.0, 20.0, 29.0):
            source = source_time_at_mix(mapping, mix)
            self.assertAlmostEqual(mix_time_for_source(mapping, source), mix, places=7)

    def test_binding_projection_uses_canonical_selection_and_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lyric = root / "song.lrc"
            lyric.write_text(
                "[00:02.00]translation\n"
                "[00:02.00]ORIGINAL\n"
                "[00:05.00]<00:05.00>he<00:05.50>llo\n"
                "[00:09.00]tail\n",
                encoding="utf-8",
            )
            binding = ResolvedAssetBinding(
                ordinal=1,
                occurrence_id="occ-1",
                track_id="track-1",
                artist="Artist",
                title="Song",
                version_id="version-1",
                nominal_start_ms=0,
                middle_cut="false",
                language_profile="en",
                source_audio_path=str(root / "source.wav"),
                source_audio_sha256="a" * 64,
                canonical_lyric_path=str(lyric),
                canonical_lyric_sha256="b" * 64,
                canonical_selection_sha256="c" * 64,
                canonical_originals=(
                    CanonicalOriginal(2000, 1, "ORIGINAL"),
                    CanonicalOriginal(5000, 0, "hello"),
                    CanonicalOriginal(9000, 0, "tail"),
                ),
            )
            payload = project_binding_timeline(
                binding,
                {
                    "mode": "AFFINE",
                    "intercept": 0.0,
                    "base_slope": 1.0,
                    "breakpoints": [],
                    "slope_deltas": [],
                },
                window=ProjectionWindow(1000, 8000),
            )
            self.assertEqual([row["text"] for row in payload["lines"]], ["ORIGINAL", "hello"])
            self.assertEqual(payload["lines"][0]["mix_start_ms"], 2000)
            self.assertEqual(payload["lines"][1]["tokens"][0]["mix_start_ms"], 5000)
            self.assertEqual(payload["lines"][1]["end_basis"], "word_timing")

    def test_effective_timewarp_prefers_applied_fine(self):
        coarse = {
            "result": {
                "timewarp": {
                    "mapping": self.affine(),
                    "blocked": False,
                }
            }
        }
        fine_mapping = {
            "mode": "AFFINE",
            "intercept": 3.0,
            "base_slope": 1.1,
            "breakpoints": [],
            "slope_deltas": [],
        }
        fine = {
            "result": {
                "applied": True,
                "timewarp": {"mapping": fine_mapping, "blocked": False},
            }
        }
        mapping, blocked, source = effective_timewarp(coarse, fine)
        self.assertEqual(mapping, fine_mapping)
        self.assertFalse(blocked)
        self.assertEqual(source, "fine")


if __name__ == "__main__":
    unittest.main()
