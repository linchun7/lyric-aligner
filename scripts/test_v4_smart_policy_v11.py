from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.timeline.anchor_repair import parse_timed_canonical_files
from lyric_aligner.timeline.smart_policy import smart_repair_srt_text_v11


def _clock(ms: int) -> str:
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, millis = divmod(rem, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _srt(texts: list[str], starts: list[int], durations: list[int] | None = None) -> str:
    durations = durations or [1000] * len(texts)
    blocks = []
    for index, (text, start, duration) in enumerate(zip(texts, starts, durations), start=1):
        blocks.append(
            f"{index}\n{_clock(start)} --> {_clock(start + duration)}\n{text}"
        )
    return "\n\n".join(blocks) + "\n"


def _canonical(root: Path, texts: list[str], starts: list[int]) -> Path:
    path = root / "song.lrc"
    rows = []
    for text, start in zip(texts, starts):
        minute, rem = divmod(start, 60_000)
        second, millis = divmod(rem, 1000)
        rows.append(f"[{minute:02d}:{second:02d}.{millis:03d}]{text}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class SmartPolicyV11Tests(unittest.TestCase):
    def test_insufficient_anchor_validation_escalates_to_pro(self) -> None:
        texts = ["第一句", "第二句", "第三句"]
        starts = [10_000, 20_000, 30_000]
        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), texts, starts)
            timed, repair = parse_timed_canonical_files([path])
            _, report = smart_repair_srt_text_v11(
                _srt(texts, starts),
                timed,
                repair,
            )

        self.assertEqual(report["status"], "review_required")
        self.assertTrue(report["pro_escalation_required"])
        self.assertEqual(report["timing_review_count"], 3)
        self.assertTrue(
            all(
                row["reason"] == "unresolved_timing_model_not_ready"
                for row in report["timing_decisions"]
            )
        )

    def test_auto_shift_may_not_create_new_overlap(self) -> None:
        texts = ["一", "二", "三", "四", "五", "六"]
        source_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000]
        mix_starts = [10_000, 20_000, 30_000, 44_000, 50_000, 60_000]
        durations = [1000, 1000, 10_500, 1000, 1000, 1000]
        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), texts, source_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(
                _srt(texts, mix_starts, durations),
                timed,
                repair,
                rate_prior_by_source={0: 1.0},
                rate_prior_metadata_by_source={
                    0: {"value": 1.0, "provenance": "exact_daw"}
                },
            )

        target = report["timing_decisions"][3]
        self.assertEqual(target["action"], "review")
        self.assertEqual(target["reason"], "proposed_shift_creates_new_overlap")
        self.assertIn("00:00:44,000 --> 00:00:45,000\n四", rendered)
        self.assertEqual(report["models"][0]["rate_provenance"], "exact_daw")

    def test_ready_timing_model_recovers_low_similarity_text_block(self) -> None:
        canonical_texts = [
            "左侧锚点甲",
            "左侧锚点乙",
            "左侧锚点丙",
            "规范歌词甲",
            "规范歌词乙",
            "规范歌词丙",
            "规范歌词丁",
            "右侧锚点甲",
            "右侧锚点乙",
            "右侧锚点丙",
            "右侧锚点丁",
        ]
        canonical_starts = [
            10_000,
            20_000,
            30_000,
            40_000,
            44_000,
            50_000,
            54_000,
            60_000,
            70_000,
            80_000,
            90_000,
        ]
        srt_texts = [
            "左侧锚点甲",
            "左侧锚点乙",
            "左侧锚点丙",
            "完全错误毫不相似一",
            "完全错误毫不相似二",
            "右侧锚点甲",
            "右侧锚点乙",
            "右侧锚点丙",
            "右侧锚点丁",
        ]
        srt_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000, 90_000]
        durations = [1000, 1000, 1000, 9000, 9000, 1000, 1000, 1000, 1000]

        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(
                _srt(srt_texts, srt_starts, durations),
                timed,
                repair,
            )

        self.assertIn("规范歌词甲 规范歌词乙", rendered)
        self.assertIn("规范歌词丙 规范歌词丁", rendered)
        self.assertNotIn("完全错误毫不相似一", rendered)
        self.assertNotIn("完全错误毫不相似二", rendered)
        self.assertEqual(report["text_timing_recovery_count"], 2)
        self.assertEqual(report["text_timing_recovery_block_count"], 1)
        self.assertGreaterEqual(report["text_review_count_before_timing_recovery"], 2)
        self.assertEqual(report["text_review_count"], 0)
        recovered = [
            row
            for row in report["text_decisions"]
            if row["reason"] == "timing_model_confirms_canonical_sequence"
        ]
        self.assertEqual(len(recovered), 2)
        # Text can be resolved while timing identity remains conservative for a
        # multi-line cue; Smart must not turn this recovery into a timing anchor.
        self.assertGreaterEqual(report["timing_review_count"], 2)

    def test_text_timing_recovery_requires_ready_model(self) -> None:
        canonical_texts = [
            "左侧锚点甲",
            "规范歌词甲",
            "规范歌词乙",
            "右侧锚点甲",
            "右侧锚点乙",
        ]
        canonical_starts = [10_000, 20_000, 24_000, 30_000, 40_000]
        srt_texts = [
            "左侧锚点甲",
            "完全错误毫不相似",
            "右侧锚点甲",
            "右侧锚点乙",
        ]
        srt_starts = [10_000, 20_000, 30_000, 40_000]

        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(
                _srt(srt_texts, srt_starts),
                timed,
                repair,
            )

        self.assertIn("完全错误毫不相似", rendered)
        self.assertEqual(report["text_timing_recovery_count"], 0)
        self.assertGreater(report["text_review_count"], 0)

    def test_text_timing_recovery_rejects_misaligned_review_block(self) -> None:
        canonical_texts = [
            "左侧锚点甲",
            "左侧锚点乙",
            "左侧锚点丙",
            "规范歌词甲",
            "规范歌词乙",
            "右侧锚点甲",
            "右侧锚点乙",
            "右侧锚点丙",
            "右侧锚点丁",
        ]
        canonical_starts = [10_000, 20_000, 30_000, 40_000, 44_000, 50_000, 60_000, 70_000, 80_000]
        srt_texts = [
            "左侧锚点甲",
            "左侧锚点乙",
            "左侧锚点丙",
            "完全错误毫不相似",
            "右侧锚点甲",
            "右侧锚点乙",
            "右侧锚点丙",
            "右侧锚点丁",
        ]
        # The bad cue is three seconds away from the canonical onset. The
        # surrounding anchors still fit a ready model, so this specifically
        # verifies the recovery-start tolerance rather than model readiness.
        srt_starts = [10_000, 20_000, 30_000, 43_000, 50_000, 60_000, 70_000, 80_000]

        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(
                _srt(srt_texts, srt_starts),
                timed,
                repair,
            )

        self.assertIn("完全错误毫不相似", rendered)
        self.assertEqual(report["text_timing_recovery_count"], 0)

    def test_text_timing_recovery_does_not_skip_weak_adjacent_cue(self) -> None:
        canonical_texts = [
            "左侧锚点甲",
            "左侧锚点乙",
            "左侧锚点丙",
            "规范歌词甲",
            "规范歌词乙",
            "边界歌词甲",
            "右侧锚点甲",
            "右侧锚点乙",
            "右侧锚点丙",
            "右侧锚点丁",
        ]
        canonical_starts = [10_000, 20_000, 30_000, 40_000, 44_000, 50_000, 60_000, 70_000, 80_000, 90_000]
        srt_texts = [
            "左侧锚点甲",
            "左侧锚点乙",
            "左侧锚点丙",
            "完全错误毫不相似",
            # This is a safe lexical repair but deliberately below the strong
            # recovery-anchor threshold; recovery must not jump over it and
            # borrow the farther 60s anchor.
            "边界歌词乙",
            "右侧锚点甲",
            "右侧锚点乙",
            "右侧锚点丙",
            "右侧锚点丁",
        ]
        srt_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000, 90_000]

        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(
                _srt(srt_texts, srt_starts),
                timed,
                repair,
            )

        self.assertIn("完全错误毫不相似", rendered)
        self.assertIn("边界歌词甲", rendered)
        self.assertEqual(report["text_timing_recovery_count"], 0)


if __name__ == "__main__":
    unittest.main()
