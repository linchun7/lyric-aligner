from __future__ import annotations

import json
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from lyric_aligner.assets.bindings import CanonicalOriginal, ResolvedAssetBinding
from lyric_aligner.audio.content_extent import AudioContentExtent, apply_content_end_override, detect_audio_content_extent
from lyric_aligner.pipeline.production import ProductionPlanError, build_production_plan


def write_pcm16(path: Path, *, sr: int, active_seconds: float, trailing_zero_seconds: float) -> None:
    active_frames = round(active_seconds * sr)
    zero_frames = round(trailing_zero_seconds * sr)
    samples = array("h", [1200] * active_frames + [0] * zero_frames)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


def binding(ordinal: int, start_ms: int) -> ResolvedAssetBinding:
    return ResolvedAssetBinding(
        ordinal=ordinal,
        occurrence_id=f"occ-{ordinal}",
        track_id=f"track-{ordinal}",
        artist="Artist",
        title=f"Song {ordinal}",
        version_id=f"version-{ordinal}",
        nominal_start_ms=start_ms,
        middle_cut="false",
        language_profile="auto",
        source_audio_path=f"/tmp/source-{ordinal}.wav",
        source_audio_sha256="a" * 64,
        canonical_lyric_path=f"/tmp/song-{ordinal}.lrc",
        canonical_lyric_sha256="b" * 64,
        canonical_selection_sha256="c" * 64,
        canonical_originals=(CanonicalOriginal(1000, 0, "line"),),
    )


class AudioContentExtentTests(unittest.TestCase):
    def test_long_digital_zero_tail_is_trimmed_but_full_duration_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mix.wav"
            write_pcm16(path, sr=1000, active_seconds=4.25, trailing_zero_seconds=35.0)
            extent = detect_audio_content_extent(path, min_trailing_digital_silence_seconds=30.0)
            self.assertTrue(extent.trimmed)
            self.assertAlmostEqual(extent.full_duration, 39.25, places=6)
            self.assertAlmostEqual(extent.content_end, 4.25, places=6)
            self.assertAlmostEqual(extent.trailing_digital_silence, 35.0, places=6)

    def test_short_digital_zero_tail_is_not_trimmed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mix.wav"
            write_pcm16(path, sr=1000, active_seconds=4.25, trailing_zero_seconds=5.0)
            extent = detect_audio_content_extent(path, min_trailing_digital_silence_seconds=30.0)
            self.assertFalse(extent.trimmed)
            self.assertAlmostEqual(extent.content_end, extent.full_duration, places=6)

    def test_explicit_content_end_override_may_only_shorten_and_binds_audio_sha(self):
        with tempfile.TemporaryDirectory() as td:
            override = Path(td) / "extent.json"
            override.write_text(
                json.dumps(
                    {
                        "schema_version": "mix-content-extent-1.0",
                        "audio_sha256": "a" * 64,
                        "content_end_seconds": 72.5,
                        "reason": "detached tail after long digital silence",
                    }
                ),
                encoding="utf-8",
            )
            extent = apply_content_end_override(
                AudioContentExtent(100.0, 100.0, 0.0, False),
                override,
                expected_audio_sha256="a" * 64,
            )
            self.assertAlmostEqual(extent.full_duration, 100.0)
            self.assertAlmostEqual(extent.content_end, 72.5)
            self.assertTrue(extent.trimmed)

    def test_explicit_content_end_override_rejects_extension_and_audio_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            override = Path(td) / "extent.json"
            override.write_text(
                json.dumps(
                    {
                        "schema_version": "mix-content-extent-1.0",
                        "audio_sha256": "b" * 64,
                        "content_end_seconds": 101.0,
                        "reason": "bad override",
                    }
                ),
                encoding="utf-8",
            )
            extent = AudioContentExtent(100.0, 100.0, 0.0, False)
            with self.assertRaisesRegex(ValueError, "audio_sha256"):
                apply_content_end_override(extent, override, expected_audio_sha256="a" * 64)
            payload = json.loads(override.read_text(encoding="utf-8"))
            payload["audio_sha256"] = "a" * 64
            override.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "may only shorten"):
                apply_content_end_override(extent, override, expected_audio_sha256="a" * 64)

    def test_effective_content_end_only_limits_last_occurrence_and_end_clamp(self):
        plan = build_production_plan(
            [binding(1, 0), binding(2, 30000), binding(3, 60000)],
            mix_duration=120.0,
            content_end=75.0,
            transition_margin_seconds=20.0,
        )
        self.assertEqual(plan.mix_duration, 120.0)
        self.assertEqual(plan.content_end, 75.0)
        self.assertEqual(
            [(row.primary_start, row.primary_end) for row in plan.occurrences],
            [(0.0, 30.0), (30.0, 60.0), (60.0, 75.0)],
        )
        self.assertEqual(plan.transitions[-1].search_end, 75.0)

    def test_content_end_cannot_precede_a_nominal_start(self):
        with self.assertRaises(ProductionPlanError):
            build_production_plan(
                [binding(1, 0), binding(2, 30000)],
                mix_duration=120.0,
                content_end=25.0,
                transition_margin_seconds=10.0,
            )

    def test_content_end_cannot_exceed_physical_duration(self):
        with self.assertRaises(ProductionPlanError):
            build_production_plan(
                [binding(1, 0)],
                mix_duration=10.0,
                content_end=10.1,
                transition_margin_seconds=2.0,
            )


if __name__ == "__main__":
    unittest.main()
