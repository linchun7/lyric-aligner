import unittest
from types import SimpleNamespace

from lyric_aligner.text.canonical_lyrics import CanonicalLine, CanonicalToken
from lyric_aligner.timeline.projector import project_canonical_lines
from lyric_aligner.timeline.source_clock import (
    SourceClockError,
    SourceClockTransform,
    source_clock_for_binding,
    source_clock_map_rows,
)


def identity_mapping():
    return {
        "intercept": 0.0,
        "base_slope": 1.0,
        "breakpoints": [],
        "slope_deltas": [],
    }


class SourceClockTests(unittest.TestCase):
    def test_default_projection_remains_in_canonical_clock(self):
        rows = project_canonical_lines(
            [CanonicalLine(0, 1000, "first"), CanonicalLine(1, 3000, "second")],
            identity_mapping(),
        )
        self.assertEqual([row["source_start_ms"] for row in rows], [1000, 3000])
        self.assertEqual([row["mix_start_ms"] for row in rows], [1000, 3000])

    def test_explicit_clock_transform_runs_before_source_to_mix_projection(self):
        clock = SourceClockTransform(
            rate=0.5,
            offset_ms=500.0,
            provenance_sha256="a" * 64,
        )
        rows = project_canonical_lines(
            [CanonicalLine(0, 1000, "first"), CanonicalLine(1, 3000, "second")],
            identity_mapping(),
            source_clock=clock,
        )
        self.assertEqual([row["source_start_ms"] for row in rows], [1000, 2000])
        self.assertEqual([row["mix_start_ms"] for row in rows], [1000, 2000])

    def test_source_clock_refuses_token_timed_lines_until_tokens_are_transformed(self):
        clock = SourceClockTransform(
            rate=0.5,
            offset_ms=500.0,
            provenance_sha256="a" * 64,
        )
        line = CanonicalLine(
            0,
            1000,
            "word",
            tokens=(CanonicalToken("word", 1000, 1500),),
            timing_format="enhanced_lrc",
        )
        with self.assertRaisesRegex(Exception, "token-timed"):
            project_canonical_lines([line], identity_mapping(), source_clock=clock)

    def test_map_is_bound_to_exact_occurrence_and_asset_identity(self):
        binding = SimpleNamespace(
            ordinal=5,
            occurrence_id="occ-5",
            track_id="track-5",
            source_audio_sha256="1" * 64,
            canonical_lyric_sha256="2" * 64,
            canonical_selection_sha256="3" * 64,
        )
        payload = {
            "schema_version": "source-clock-map-1.0",
            "policy_id": "bpm-fixed-source-clock-1.0",
            "automatic_timing_change_allowed": True,
            "tracks": [
                {
                    "ordinal": 5,
                    "occurrence_id": "occ-5",
                    "track_id": "track-5",
                    "source_audio_sha256": "1" * 64,
                    "canonical_lyric_sha256": "2" * 64,
                    "canonical_selection_sha256": "3" * 64,
                    "rate": 132 / 140,
                    "offset_ms": 1305.0,
                    "provenance_sha256": "4" * 64,
                }
            ],
        }
        rows = source_clock_map_rows(payload)
        clock = source_clock_for_binding(binding, rows)
        self.assertIsNotNone(clock)
        self.assertEqual(clock.map_ms(97_578), round(97_578 * 132 / 140 + 1305.0))
        payload["tracks"][0]["source_audio_sha256"] = "5" * 64
        with self.assertRaisesRegex(SourceClockError, "source_audio_sha256"):
            source_clock_for_binding(binding, source_clock_map_rows(payload))

    def test_map_requires_explicit_production_authority(self):
        payload = {
            "schema_version": "source-clock-map-1.0",
            "policy_id": "bpm-fixed-source-clock-1.0",
            "automatic_timing_change_allowed": False,
            "tracks": [],
        }
        with self.assertRaisesRegex(SourceClockError, "production timing authority"):
            source_clock_map_rows(payload)

    def test_map_rejects_unknown_clock_policy(self):
        with self.assertRaisesRegex(SourceClockError, "policy_id"):
            source_clock_map_rows(
                {
                    "schema_version": "source-clock-map-1.0",
                    "policy_id": "unknown-clock-policy",
                    "automatic_timing_change_allowed": True,
                    "tracks": [],
                }
            )

    def test_invalid_clock_fails_closed(self):
        with self.assertRaisesRegex(SourceClockError, "rate"):
            SourceClockTransform(rate=0.0, offset_ms=0.0, provenance_sha256="a" * 64)
        with self.assertRaisesRegex(SourceClockError, "provenance"):
            SourceClockTransform(rate=1.0, offset_ms=0.0, provenance_sha256="bad")


if __name__ == "__main__":
    unittest.main()
