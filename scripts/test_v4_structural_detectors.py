from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from lyric_aligner.evaluation.structural_detectors import (
    StructuralDetectorPolicy,
    _exact_activity_runs,
    detect_detached_tail_events,
    detect_editor_reorder_events,
    detect_structural_events,
)


def _clock(ms: int) -> str:
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _write_srt(path: Path, rows: list[tuple[int, int]]) -> None:
    blocks = []
    for index, (start_ms, end_ms) in enumerate(rows, start=1):
        blocks.append(
            f"{index}\n{_clock(start_ms)} --> {_clock(end_ms)}\ncue {index}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def _write_audio(path: Path, values: np.ndarray, sample_rate: int = 1000) -> None:
    sf.write(path, values.astype(np.float32), sample_rate, subtype="FLOAT")


class StructuralDetectorTests(unittest.TestCase):
    def test_editor_reorder_detects_material_backward_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.srt"
            _write_srt(
                path,
                [
                    (0, 800),
                    (10_000, 10_800),
                    (3_000, 3_800),
                    (4_000, 5_000),
                    (12_000, 12_800),
                ],
            )
            self.assertEqual(
                detect_editor_reorder_events(path, mapped_cue_positions=set(range(5))),
                [{"kind": "reorder", "start_ms": 3000.0, "end_ms": 5000.0}],
            )

    def test_editor_reorder_ignores_small_file_order_jitter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.srt"
            _write_srt(path, [(0, 500), (1000, 1500), (800, 1300), (1600, 2100)])
            self.assertEqual(
                detect_editor_reorder_events(path, mapped_cue_positions=set(range(4))),
                [],
            )

    def test_editor_reorder_can_emit_two_disjoint_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.srt"
            _write_srt(
                path,
                [
                    (0, 400),
                    (5000, 5400),
                    (1000, 1400),
                    (6000, 6400),
                    (7000, 7400),
                    (6200, 6600),
                    (8000, 8400),
                ],
            )
            self.assertEqual(
                detect_editor_reorder_events(path, mapped_cue_positions=set(range(7))),
                [
                    {"kind": "reorder", "start_ms": 1000.0, "end_ms": 1400.0},
                    {"kind": "reorder", "start_ms": 6200.0, "end_ms": 6600.0},
                ],
            )

    def test_editor_reorder_ignores_unmapped_prefix_inversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.srt"
            _write_srt(
                path,
                [
                    (54_000, 55_000),
                    (56_000, 57_000),
                    (58_000, 59_000),
                    (60_000, 61_000),
                    (1_500, 2_000),
                    (2_500, 3_000),
                    (10_000, 11_000),
                    (12_000, 13_000),
                ],
            )
            self.assertEqual(
                detect_editor_reorder_events(path, mapped_cue_positions={6, 7}),
                [],
            )

    def test_editor_reorder_keeps_unmapped_tail_inside_detected_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.srt"
            _write_srt(
                path,
                [
                    (0, 500),
                    (40_000, 40_500),
                    (20_000, 20_500),
                    (21_000, 21_500),
                    (22_000, 22_500),
                ],
            )
            self.assertEqual(
                detect_editor_reorder_events(path, mapped_cue_positions={0, 1, 2}),
                [{"kind": "reorder", "start_ms": 20_000.0, "end_ms": 22_500.0}],
            )

    def test_editor_reorder_requires_mapped_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.srt"
            _write_srt(path, [(0, 500), (1000, 1500)])
            with self.assertRaises(ValueError):
                detect_editor_reorder_events(path, mapped_cue_positions=None)

    def test_detached_tail_detects_island_after_long_exact_zero_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.wav"
            samples = np.zeros(10_000, dtype=np.float32)
            samples[:5000] = 0.25
            samples[8000:9000] = 0.4
            _write_audio(path, samples)
            policy = StructuralDetectorPolicy(
                detached_tail_scan_seconds=20.0,
                detached_tail_min_zero_gap_seconds=2.0,
                detached_tail_min_active_seconds=0.25,
                detached_tail_max_island_seconds=3.0,
                detached_tail_min_gap_start_fraction=0.4,
                audio_block_seconds=1.0,
            )
            self.assertEqual(
                detect_detached_tail_events(path, policy=policy),
                [{"kind": "detached_tail", "start_ms": 8000.0, "end_ms": 9000.0}],
            )

    def test_detached_tail_merges_tiny_zero_inside_active_island(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.wav"
            samples = np.zeros(10_000, dtype=np.float32)
            samples[:5000] = 0.25
            samples[8000:9000] = 0.4
            samples[8400:8401] = 0.0
            _write_audio(path, samples)
            policy = StructuralDetectorPolicy(
                detached_tail_scan_seconds=20.0,
                detached_tail_min_zero_gap_seconds=2.0,
                detached_tail_min_active_seconds=0.25,
                detached_tail_max_island_seconds=3.0,
                detached_tail_min_gap_start_fraction=0.4,
                audio_block_seconds=1.0,
            )
            self.assertEqual(
                detect_detached_tail_events(path, policy=policy),
                [{"kind": "detached_tail", "start_ms": 8000.0, "end_ms": 9000.0}],
            )

    def test_trailing_silence_without_reactivation_is_not_detached_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.wav"
            samples = np.zeros(10_000, dtype=np.float32)
            samples[:6500] = 0.25
            _write_audio(path, samples)
            policy = StructuralDetectorPolicy(
                detached_tail_scan_seconds=20.0,
                detached_tail_min_zero_gap_seconds=2.0,
                detached_tail_min_gap_start_fraction=0.4,
                audio_block_seconds=1.0,
            )
            self.assertEqual(detect_detached_tail_events(path, policy=policy), [])

    def test_continuous_audio_is_not_detached_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.wav"
            samples = np.full(10_000, 0.25, dtype=np.float32)
            _write_audio(path, samples)
            policy = StructuralDetectorPolicy(
                detached_tail_scan_seconds=20.0,
                detached_tail_min_zero_gap_seconds=2.0,
                detached_tail_min_gap_start_fraction=0.4,
                audio_block_seconds=1.0,
            )
            self.assertEqual(detect_detached_tail_events(path, policy=policy), [])

    def test_long_program_after_gap_is_not_short_detached_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.wav"
            samples = np.zeros(20_000, dtype=np.float32)
            samples[:8000] = 0.25
            samples[11_000:19_000] = 0.4
            _write_audio(path, samples)
            policy = StructuralDetectorPolicy(
                detached_tail_scan_seconds=30.0,
                detached_tail_min_zero_gap_seconds=2.0,
                detached_tail_min_active_seconds=0.25,
                detached_tail_max_island_seconds=3.0,
                detached_tail_min_gap_start_fraction=0.3,
                audio_block_seconds=1.0,
            )
            self.assertEqual(detect_detached_tail_events(path, policy=policy), [])

    def test_combined_detector_sorts_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            wav = root / "input.wav"
            _write_srt(srt, [(0, 500), (5000, 5500), (1000, 1500), (6000, 6500)])
            samples = np.zeros(10_000, dtype=np.float32)
            samples[:5000] = 0.25
            samples[8000:9000] = 0.4
            _write_audio(wav, samples)
            policy = StructuralDetectorPolicy(
                detached_tail_scan_seconds=20.0,
                detached_tail_min_zero_gap_seconds=2.0,
                detached_tail_min_active_seconds=0.25,
                detached_tail_max_island_seconds=3.0,
                detached_tail_min_gap_start_fraction=0.4,
                audio_block_seconds=1.0,
            )
            self.assertEqual(
                detect_structural_events(
                    editor_srt=srt,
                    mapped_cue_positions=set(range(4)),
                    audio_path=wav,
                    policy=policy,
                ),
                [
                    {"kind": "reorder", "start_ms": 1000.0, "end_ms": 1500.0},
                    {"kind": "detached_tail", "start_ms": 8000.0, "end_ms": 9000.0},
                ],
            )

    def test_tail_scan_accepts_decoder_short_read_at_eof(self) -> None:
        class FakeSoundFile:
            samplerate = 10

            def __init__(self) -> None:
                self.position = 0
                self.data = np.array(
                    [[0.2], [0.2], [0.2], [0.2], [0.0], [0.0], [0.0], [0.3], [0.3]],
                    dtype=np.float32,
                )

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def __len__(self) -> int:
                return 10  # advertised metadata is one frame longer than decodable data

            def seek(self, frame: int) -> int:
                self.position = min(int(frame), len(self.data))
                return self.position

            def read(self, count: int, *, dtype: str, always_2d: bool):
                end = min(len(self.data), self.position + int(count))
                block = self.data[self.position:end]
                self.position = end
                return block

        fake = FakeSoundFile()
        with patch(
            "lyric_aligner.evaluation.structural_detectors.sf.SoundFile",
            return_value=fake,
        ):
            runs, sample_rate, frame_count = _exact_activity_runs(
                "ignored.mp3", scan_seconds=20.0, block_seconds=10.0
            )
        self.assertEqual(sample_rate, 10)
        self.assertEqual(frame_count, 9)
        self.assertEqual(runs[-1][2], 9)

    def test_policy_validation_is_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            StructuralDetectorPolicy(detached_tail_min_zero_gap_seconds=0.0).validate()
        with self.assertRaises(ValueError):
            StructuralDetectorPolicy(detached_tail_min_gap_start_fraction=1.0).validate()
        with self.assertRaises(ValueError):
            StructuralDetectorPolicy(
                detached_tail_min_active_seconds=2.0,
                detached_tail_max_island_seconds=1.0,
            ).validate()


if __name__ == "__main__":
    unittest.main()
