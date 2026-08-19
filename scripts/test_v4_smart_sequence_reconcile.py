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
    return "\n\n".join(
        f"{index}\n{_clock(start)} --> {_clock(start + duration)}\n{text}"
        for index, (text, start, duration) in enumerate(zip(texts, starts, durations), start=1)
    ) + "\n"


def _canonical(root: Path, texts: list[str], starts: list[int]) -> Path:
    path = root / "song.lrc"
    rows = []
    for text, start in zip(texts, starts):
        minute, rem = divmod(start, 60_000)
        second, millis = divmod(rem, 1000)
        rows.append(f"[{minute:02d}:{second:02d}.{millis:03d}]{text}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class SmartSequenceReconcileTests(unittest.TestCase):
    def test_three_a_plus_one_b_breaks_text_bootstrap_deadlock_without_timing_bootstrap(self) -> None:
        canonical_texts = [
            "唯一左锚点甲甲甲",
            "唯一左锚点乙乙乙",
            "唯一左锚点丙丙丙",
            "规范片段一前半",
            "规范片段一后半",
            "规范片段二前半",
            "规范片段二后半",
            "规范片段三前半",
            "规范片段三后半",
            "规范片段四前半",
            "规范片段四后半",
            "右侧非常可靠的结束锚点甲乙丙丁",
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
            64_000,
            70_000,
            74_000,
            80_000,
        ]
        srt_texts = [
            "唯一左锚点甲甲甲",
            "唯一左锚点乙乙乙",
            "唯一左锚点丙丙丙",
            "完全错误内容甲甲甲甲",
            "完全错误内容乙乙乙乙",
            "完全错误内容丙丙丙丙",
            "完全错误内容丁丁丁丁",
            "右侧非常可靠的结束锚点甲乙丙丙",
        ]
        srt_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000]
        durations = [1000, 1000, 1000, 9000, 9000, 9000, 9000, 1000]

        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(
                _srt(srt_texts, srt_starts, durations),
                timed,
                repair,
            )

        self.assertIn("规范片段一前半 规范片段一后半", rendered)
        self.assertIn("规范片段二前半 规范片段二后半", rendered)
        self.assertIn("规范片段三前半 规范片段三后半", rendered)
        self.assertIn("规范片段四前半 规范片段四后半", rendered)
        self.assertNotIn("完全错误内容甲甲甲甲", rendered)
        self.assertGreaterEqual(report["text_sequence_reconciled_cue_count"], 4)
        self.assertGreaterEqual(report["text_sequence_resolved_review_count"], 4)

        projection = report["text_sequence_projection_models"][0]
        self.assertEqual(projection["status"], "ready")
        self.assertEqual(projection["a_anchor_count"], 3)
        self.assertGreaterEqual(projection["strong_anchor_count"], 4)

        # Sequence-projected text is intentionally not promoted into timing
        # authority.  The timing model still has only the original three A
        # anchors and therefore remains below the four-A production gate.
        timing_model = report["models"][0]
        self.assertEqual(timing_model["anchor_count"], 3)
        self.assertEqual(timing_model["status"], "insufficient_anchors")
        sequence_rows = [
            row
            for row in report["text_decisions"]
            if row["reason"] == "sequence_projection_confirms_bounded_canonical"
        ]
        self.assertEqual(len(sequence_rows), 4)
        self.assertTrue(all(row["score"] <= 0.91 for row in sequence_rows))

    def test_bounded_sequence_keeps_editor_cue_boundaries_instead_of_lrc_line_count(self) -> None:
        canonical_texts = [
            "唯一锚点甲甲甲",
            "唯一锚点乙乙乙",
            "唯一锚点丙丙丙",
            "第一小句",
            "第二小句",
            "第三小句",
            "第四小句",
            "第五小句",
            "第六小句",
            "第七小句",
            "第八小句",
            "右侧非常可靠的结束锚点甲乙丙丁",
        ]
        canonical_starts = [10_000, 20_000, 30_000, 40_000, 44_000, 50_000, 54_000, 60_000, 64_000, 70_000, 74_000, 80_000]
        srt_texts = [
            "唯一锚点甲甲甲",
            "唯一锚点乙乙乙",
            "唯一锚点丙丙丙",
            "乱码甲甲甲",
            "乱码乙乙乙",
            "乱码丙丙丙",
            "乱码丁丁丁",
            "右侧非常可靠的结束锚点甲乙丙丙",
        ]
        starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000]
        durations = [1000, 1000, 1000, 9000, 9000, 9000, 9000, 1000]

        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, _ = smart_repair_srt_text_v11(
                _srt(srt_texts, starts, durations),
                timed,
                repair,
            )

        expected = [
            "第一小句 第二小句",
            "第三小句 第四小句",
            "第五小句 第六小句",
            "第七小句 第八小句",
        ]
        for start, text in zip(starts[3:7], expected):
            self.assertIn(f"{_clock(start)} -->", rendered)
            self.assertIn(f"\n{text}\n", rendered)
        self.assertEqual(rendered.count("\n\n"), 8)

    def test_projection_is_not_built_from_two_or_three_unconfirmed_anchors(self) -> None:
        canonical_texts = ["锚点甲甲甲", "锚点乙乙乙", "锚点丙丙丙", "规范目标歌词"]
        canonical_starts = [10_000, 20_000, 30_000, 40_000]
        srt_texts = ["锚点甲甲甲", "锚点乙乙乙", "锚点丙丙丙", "完全不相似的乱码"]
        starts = [10_000, 20_000, 30_000, 40_000]
        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(_srt(srt_texts, starts), timed, repair)

        self.assertIn("完全不相似的乱码", rendered)
        self.assertEqual(report["text_sequence_reconciled_cue_count"], 0)
        self.assertEqual(report["text_sequence_projection_models"][0]["status"], "insufficient_strong_anchors")

    def test_existing_safe_segmentation_is_not_rewritten_by_sequence_projection(self) -> None:
        canonical_texts = [
            "前半歌词后接短句",
            "继续播放最后几个字",
            "唯一锚点甲甲甲",
            "唯一锚点乙乙乙",
            "唯一锚点丙丙丙",
            "唯一锚点丁丁丁",
        ]
        canonical_starts = [1_000, 3_000, 10_000, 20_000, 30_000, 40_000]
        srt_texts = [
            "前半歌词",
            "后接短句继续播放",
            "最后几个字",
            "唯一锚点甲甲甲",
            "唯一锚点乙乙乙",
            "唯一锚点丙丙丙",
            "唯一锚点丁丁丁",
        ]
        starts = [1_000, 2_400, 4_200, 10_000, 20_000, 30_000, 40_000]
        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, repair = parse_timed_canonical_files([path])
            rendered, report = smart_repair_srt_text_v11(_srt(srt_texts, starts), timed, repair)

        self.assertIn("00:00:01,000 --> 00:00:02,000\n前半歌词", rendered)
        self.assertIn("00:00:02,400 --> 00:00:03,400\n后接短句继续播放", rendered)
        self.assertIn("00:00:04,200 --> 00:00:05,200\n最后几个字", rendered)
        self.assertEqual(report["text_sequence_reconciled_cue_count"], 0)


if __name__ == "__main__":
    unittest.main()
