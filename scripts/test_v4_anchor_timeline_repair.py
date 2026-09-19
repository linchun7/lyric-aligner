from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.timeline.anchor_repair import (
    build_anchor_timing_plan,
    parse_timed_canonical_files,
    smart_repair_srt_text,
)
from lyric_aligner.text.canonical_lyrics import CanonicalToken
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

    def _split_line_timing_fixture(
        self,
        root: Path,
        *,
        word_timed: bool = False,
    ):
        canonical_texts = ["锚点一", "锚点二", "合并歌词", "锚点四", "锚点五", "锚点六"]
        canonical_starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000]
        cue_texts = ["锚点一", "锚点二", "合并", "歌词", "锚点四", "锚点五", "锚点六"]
        cue_starts = [10_000, 20_000, 31_500, 32_500, 40_000, 50_000, 60_000]
        canonical_path = self._canonical(root, canonical_texts, canonical_starts)
        timed, _ = parse_timed_canonical_files([canonical_path])
        if word_timed:
            occurrence = timed[2]
            timed[2] = type(occurrence)(
                ordinal=occurrence.ordinal,
                source=occurrence.source,
                source_ordinal=occurrence.source_ordinal,
                time_ms=occurrence.time_ms,
                text=occurrence.text,
                normalized=occurrence.normalized,
                tokens=(
                    CanonicalToken("合并", 30_000, 31_000),
                    CanonicalToken("歌词", 31_000, 32_000),
                ),
                timing_format="qrc_word_timing",
            )
        _, cues = parse_srt_text(_srt(cue_texts, cue_starts, duration=800))
        payload = []
        cue_to_canonical = [0, 1, 2, 2, 3, 4, 5]
        for cue_ordinal, canonical_ordinal in enumerate(cue_to_canonical):
            split = cue_ordinal in {2, 3}
            payload.append(
                {
                    "cue_ordinal": cue_ordinal,
                    "canonical_span": [canonical_ordinal, canonical_ordinal + 1],
                    "cue_span": [2, 4] if split else [cue_ordinal, cue_ordinal + 1],
                    "score": 1.0,
                    "action": "unchanged",
                    "reason": "canonical_content_matches_source_segmentation",
                }
            )
        return cues, timed, payload

    def test_split_line_only_first_cue_gets_line_onset_without_word_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cues, timed, payload = self._split_line_timing_fixture(Path(tmp))
            decisions, _ = build_anchor_timing_plan(
                cues,
                timed,
                payload,
                rate_prior_by_source={0: 1.0},
                segmentation_internal_boundary_guard=True,
            )

        first, internal = decisions[2], decisions[3]
        self.assertEqual(first.proposed_start_ms, 30_000)
        self.assertEqual(first.reason, "non_A_identity_not_auto_repairable")
        self.assertEqual(internal.action, "review")
        self.assertEqual(
            internal.reason,
            "segmentation_internal_boundary_unvalidated",
        )
        self.assertIsNone(internal.proposed_start_ms)
        self.assertIsNone(internal.proposed_end_ms)
        self.assertIsNone(internal.residual_ms)

    def test_split_line_with_reliable_word_timing_keeps_existing_proposal_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cues, timed, payload = self._split_line_timing_fixture(
                Path(tmp),
                word_timed=True,
            )
            decisions, _ = build_anchor_timing_plan(
                cues,
                timed,
                payload,
                rate_prior_by_source={0: 1.0},
                segmentation_internal_boundary_guard=True,
            )

        internal = decisions[3]
        self.assertEqual(internal.reason, "non_A_identity_not_auto_repairable")
        self.assertEqual(internal.proposed_start_ms, 31_000)
        self.assertIn("word_timing", internal.evidence)
        self.assertIn("word_token_boundary", internal.evidence)

    def test_split_line_word_timing_without_exact_token_boundary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cues, timed, payload = self._split_line_timing_fixture(
                Path(tmp),
                word_timed=True,
            )
            occurrence = timed[2]
            timed[2] = type(occurrence)(
                ordinal=occurrence.ordinal,
                source=occurrence.source,
                source_ordinal=occurrence.source_ordinal,
                time_ms=occurrence.time_ms,
                text=occurrence.text,
                normalized=occurrence.normalized,
                tokens=(CanonicalToken("合并歌词", 30_000, 32_000),),
                timing_format="qrc_word_timing",
            )
            decisions, _ = build_anchor_timing_plan(
                cues,
                timed,
                payload,
                rate_prior_by_source={0: 1.0},
                segmentation_internal_boundary_guard=True,
            )

        internal = decisions[3]
        self.assertEqual(
            internal.reason,
            "segmentation_internal_boundary_unvalidated",
        )
        self.assertIsNone(internal.proposed_start_ms)

    def test_split_line_guard_is_version_scoped_and_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cues, timed, payload = self._split_line_timing_fixture(Path(tmp))
            decisions, _ = build_anchor_timing_plan(
                cues,
                timed,
                payload,
                rate_prior_by_source={0: 1.0},
            )

        internal = decisions[3]
        self.assertEqual(internal.reason, "non_A_identity_not_auto_repairable")
        self.assertEqual(internal.proposed_start_ms, 30_000)

    def test_split_line_equal_or_out_of_line_token_onset_fails_closed(self) -> None:
        for starts in ((30_000, 30_000), (30_000, 41_000)):
            with self.subTest(starts=starts), tempfile.TemporaryDirectory() as tmp:
                cues, timed, payload = self._split_line_timing_fixture(
                    Path(tmp),
                    word_timed=True,
                )
                occurrence = timed[2]
                timed[2] = type(occurrence)(
                    ordinal=occurrence.ordinal,
                    source=occurrence.source,
                    source_ordinal=occurrence.source_ordinal,
                    time_ms=occurrence.time_ms,
                    text=occurrence.text,
                    normalized=occurrence.normalized,
                    tokens=(
                        CanonicalToken("合并", starts[0], starts[1]),
                        CanonicalToken("歌词", starts[1], starts[1] + 1_000),
                    ),
                    timing_format="qrc_word_timing",
                )
                decisions, _ = build_anchor_timing_plan(
                    cues,
                    timed,
                    payload,
                    rate_prior_by_source={0: 1.0},
                    segmentation_internal_boundary_guard=True,
                )

            internal = decisions[3]
            self.assertEqual(
                internal.reason,
                "segmentation_internal_boundary_unvalidated",
            )
            self.assertIsNone(internal.proposed_start_ms)

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
