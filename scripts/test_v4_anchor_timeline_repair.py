from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.timeline.anchor_repair import (
    build_anchor_timing_plan,
    parse_timed_canonical_files,
    smart_repair_srt_text,
)
from lyric_aligner.text_repair import build_repair_plan_v2, parse_srt_text


def _clock(ms: int) -> str:
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, millis = divmod(rem, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _srt(texts: list[str], starts: list[int], duration: int = 1000) -> str:
    blocks = []
    for index, (text, start) in enumerate(zip(texts, starts), start=1):
        blocks.append(
            f"{index}\n{_clock(start)} --> {_clock(start + duration)}\n{text}"
        )
    return "\n\n".join(blocks) + "\n"


class AnchorTimelineRepairTests(unittest.TestCase):
    def _canonical(self, root: Path, texts: list[str], starts: list[int]) -> Path:
        path = root / "song.lrc"
        rows = []
        for text, start in zip(texts, starts):
            minute, rem = divmod(start, 60_000)
            second, millis = divmod(rem, 1000)
            rows.append(f"[{minute:02d}:{second:02d}.{millis:03d}]{text}")
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return path

    def test_smart_repairs_isolated_exact_timing_outlier_without_audio(self) -> None:
        texts = ["第一句", "第二句", "第三句", "第四句", "第五句", "第六句"]
        source_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000]
        mix_starts = [10_000, 20_000, 30_000, 44_000, 50_000, 60_000]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical_path = self._canonical(root, texts, source_starts)
            timed, repair = parse_timed_canonical_files([canonical_path])
            rendered, report = smart_repair_srt_text(
                _srt(texts, mix_starts),
                timed,
                repair,
                rate_prior_by_source={0: 1.0},
            )

        self.assertFalse(report["audio_read"])
        self.assertEqual(report["timing_repair_count"], 1)
        self.assertIn("00:00:40,000 --> 00:00:41,000\n第四句", rendered)
        repaired = [
            item
            for item in report["timing_decisions"]
            if item["action"] == "repair"
        ]
        self.assertEqual(repaired[0]["reason"], "multi_evidence_timing_outlier")
        self.assertIn("bilateral_anchor_support", repaired[0]["evidence"])

    def test_word_timing_is_preserved_and_used_as_anchor_onset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "word.lrc"
            path.write_text(
                "[00:10.00]<00:10.20>你<00:10.55>好\n"
                "[00:20.00]<00:20.10>世<00:20.50>界\n",
                encoding="utf-8",
            )
            timed, _ = parse_timed_canonical_files([path])

        self.assertTrue(timed[0].has_word_timing)
        self.assertEqual(timed[0].anchor_time_ms, 10_200)
        self.assertEqual(timed[0].tokens[1].start_ms, 10_550)
        self.assertEqual(timed[0].timing_format, "enhanced_lrc")

    def test_repeated_lyric_occurrence_is_not_A_anchor_or_auto_repaired(self) -> None:
        texts = ["甲一", "甲二", "重复句", "甲四", "甲五", "重复句", "甲七"]
        source_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000]
        mix_starts = [10_000, 20_000, 34_000, 40_000, 50_000, 60_000, 70_000]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical_path = self._canonical(root, texts, source_starts)
            timed, repair = parse_timed_canonical_files([canonical_path])
            _, cues = parse_srt_text(_srt(texts, mix_starts))
            _, text_decisions, _ = build_repair_plan_v2(cues, repair)
            payload = [
                {
                    "cue_ordinal": item.cue_ordinal,
                    "canonical_span": list(item.canonical_span) if item.canonical_span else None,
                    "cue_span": list(item.cue_span) if item.cue_span else None,
                    "score": item.score,
                    "action": item.action,
                    "reason": item.reason,
                }
                for item in text_decisions
            ]
            decisions, _ = build_anchor_timing_plan(
                cues,
                timed,
                payload,
                rate_prior_by_source={0: 1.0},
            )

        target = decisions[2]
        self.assertEqual(target.anchor_grade, "C")
        self.assertNotEqual(target.action, "repair")

    def test_song_start_repair_requires_rate_prior_for_one_sided_support(self) -> None:
        texts = ["开头", "二", "三", "四", "五", "六"]
        source_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000]
        mix_starts = [14_000, 20_000, 30_000, 40_000, 50_000, 60_000]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical_path = self._canonical(root, texts, source_starts)
            timed, repair = parse_timed_canonical_files([canonical_path])
            _, cues = parse_srt_text(_srt(texts, mix_starts))
            _, text_decisions, _ = build_repair_plan_v2(cues, repair)
            payload = [
                {
                    "cue_ordinal": item.cue_ordinal,
                    "canonical_span": list(item.canonical_span) if item.canonical_span else None,
                    "cue_span": list(item.cue_span) if item.cue_span else None,
                    "score": item.score,
                    "action": item.action,
                    "reason": item.reason,
                }
                for item in text_decisions
            ]
            without_prior, _ = build_anchor_timing_plan(cues, timed, payload)
            with_prior, _ = build_anchor_timing_plan(
                cues,
                timed,
                payload,
                rate_prior_by_source={0: 1.0},
            )

        self.assertNotEqual(without_prior[0].action, "repair")
        self.assertEqual(with_prior[0].action, "repair")
        self.assertIn("one_sided_anchor_support", with_prior[0].evidence)

    def test_japanese_exact_text_can_use_smart_without_forcing_max(self) -> None:
        texts = ["春の風", "青い空", "君の声", "夜の街", "星の光", "また会おう"]
        starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000]
        mixed = [10_000, 20_000, 30_000, 44_000, 50_000, 60_000]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical_path = self._canonical(root, texts, starts)
            timed, repair = parse_timed_canonical_files([canonical_path])
            _, report = smart_repair_srt_text(
                _srt(texts, mixed),
                timed,
                repair,
                rate_prior_by_source={0: 1.0},
            )

        self.assertFalse(report["audio_read"])
        self.assertEqual(report["timing_repair_count"], 1)


if __name__ == "__main__":
    unittest.main()
