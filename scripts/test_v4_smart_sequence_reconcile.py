from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text_repair import MatchDecision, parse_srt_text
from lyric_aligner.timeline.anchor_repair import (
    build_anchor_timing_plan,
    parse_timed_canonical_files,
)
from lyric_aligner.timeline.sequence_reconcile import (
    reconcile_text_from_sequence_projection,
)
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
        for index, (text, start, duration) in enumerate(
            zip(texts, starts, durations), start=1
        )
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


def _decision(
    cue_ordinal: int,
    canonical_ordinal: int,
    source_text: str,
    canonical_text: str,
    *,
    score: float,
    action: str,
    reason: str,
) -> MatchDecision:
    return MatchDecision(
        cue_ordinal=cue_ordinal,
        canonical_ordinal=canonical_ordinal,
        score=score,
        action=action,
        reason=reason,
        cue_span=(cue_ordinal, cue_ordinal + 1),
        canonical_span=(canonical_ordinal, canonical_ordinal + 1),
        source_text=source_text,
        canonical_text=canonical_text,
        output_text=canonical_text if action != "review" else source_text,
        edit_operations=(),
    )


def _three_a_one_b_fixture(root: Path):
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
    path = _canonical(root, canonical_texts, canonical_starts)
    timed, _ = parse_timed_canonical_files([path])
    _, cues = parse_srt_text(_srt(srt_texts, srt_starts, durations))

    decisions = [
        _decision(0, 0, srt_texts[0], canonical_texts[0], score=1.0, action="unchanged", reason="baseline_exact"),
        _decision(1, 1, srt_texts[1], canonical_texts[1], score=1.0, action="unchanged", reason="baseline_exact"),
        _decision(2, 2, srt_texts[2], canonical_texts[2], score=1.0, action="unchanged", reason="baseline_exact"),
        _decision(3, 3, srt_texts[3], canonical_texts[3], score=0.2, action="review", reason="baseline_severe_asr"),
        _decision(4, 5, srt_texts[4], canonical_texts[5], score=0.2, action="review", reason="baseline_severe_asr"),
        _decision(5, 7, srt_texts[5], canonical_texts[7], score=0.2, action="review", reason="baseline_severe_asr"),
        _decision(6, 9, srt_texts[6], canonical_texts[9], score=0.2, action="review", reason="baseline_severe_asr"),
        _decision(7, 11, srt_texts[7], canonical_texts[11], score=0.94, action="replace", reason="baseline_strong_B"),
    ]
    return timed, cues, decisions


def _timing_payload(decisions: list[MatchDecision]) -> list[dict[str, object]]:
    return [
        {
            "cue_ordinal": item.cue_ordinal,
            "canonical_ordinal": item.canonical_ordinal,
            "cue_span": list(item.cue_span) if item.cue_span else None,
            "canonical_span": list(item.canonical_span) if item.canonical_span else None,
            "score": item.score,
            "action": item.action,
            "reason": item.reason,
        }
        for item in decisions
    ]


class SmartSequenceReconcileTests(unittest.TestCase):
    def test_three_a_plus_one_b_breaks_text_bootstrap_deadlock_without_timing_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            timed, cues, decisions = _three_a_one_b_fixture(Path(tmp))
            replacements, reconciled, summary, projections = (
                reconcile_text_from_sequence_projection(cues, timed, decisions)
            )

        self.assertEqual(replacements[3], "规范片段一前半 规范片段一后半")
        self.assertEqual(replacements[4], "规范片段二前半 规范片段二后半")
        self.assertEqual(replacements[5], "规范片段三前半 规范片段三后半")
        self.assertEqual(replacements[6], "规范片段四前半 规范片段四后半")
        self.assertEqual(summary.reconciled_cue_count, 4)
        self.assertEqual(summary.resolved_review_cue_count, 4)
        self.assertEqual(projections[0].status, "ready")
        self.assertEqual(projections[0].a_anchor_count, 3)
        self.assertEqual(projections[0].strong_anchor_count, 4)

        # Sequence-projected text is final-text evidence only. Even after the
        # recovery, the primary Smart timing engine still sees exactly the
        # original three A anchors; the B and all projected rows remain non-A.
        _, models = build_anchor_timing_plan(cues, timed, _timing_payload(reconciled))
        self.assertEqual(models[0].anchor_count, 3)
        self.assertEqual(models[0].status, "insufficient_anchors")
        sequence_rows = [
            row
            for row in reconciled
            if row.reason == "sequence_projection_confirms_bounded_canonical"
        ]
        self.assertEqual(len(sequence_rows), 4)
        self.assertTrue(all(row.score <= 0.91 for row in sequence_rows))

    def test_bounded_sequence_keeps_editor_cue_count_while_consuming_more_lrc_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            timed, cues, decisions = _three_a_one_b_fixture(Path(tmp))
            replacements, _, summary, _ = reconcile_text_from_sequence_projection(
                cues, timed, decisions
            )

        self.assertEqual(len(replacements), 4)
        self.assertEqual(
            [replacements[index] for index in range(3, 7)],
            [
                "规范片段一前半 规范片段一后半",
                "规范片段二前半 规范片段二后半",
                "规范片段三前半 规范片段三后半",
                "规范片段四前半 规范片段四后半",
            ],
        )
        self.assertEqual(summary.reconciled_region_count, 1)
        self.assertEqual([_clock(_cue_start(cues[index])) for index in range(3, 7)], ["00:00:40,000", "00:00:50,000", "00:01:00,000", "00:01:10,000"])

    def test_projection_is_not_built_from_only_three_exact_anchors(self) -> None:
        canonical_texts = ["锚点甲甲甲", "锚点乙乙乙", "锚点丙丙丙", "规范目标歌词"]
        canonical_starts = [10_000, 20_000, 30_000, 40_000]
        srt_texts = ["锚点甲甲甲", "锚点乙乙乙", "锚点丙丙丙", "完全不相似的乱码"]
        starts = [10_000, 20_000, 30_000, 40_000]
        with tempfile.TemporaryDirectory() as tmp:
            path = _canonical(Path(tmp), canonical_texts, canonical_starts)
            timed, _ = parse_timed_canonical_files([path])
            _, cues = parse_srt_text(_srt(srt_texts, starts))
            decisions = [
                _decision(0, 0, srt_texts[0], canonical_texts[0], score=1.0, action="unchanged", reason="baseline_exact"),
                _decision(1, 1, srt_texts[1], canonical_texts[1], score=1.0, action="unchanged", reason="baseline_exact"),
                _decision(2, 2, srt_texts[2], canonical_texts[2], score=1.0, action="unchanged", reason="baseline_exact"),
                _decision(3, 3, srt_texts[3], canonical_texts[3], score=0.1, action="review", reason="baseline_severe_asr"),
            ]
            replacements, _, summary, models = reconcile_text_from_sequence_projection(
                cues, timed, decisions
            )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.reconciled_cue_count, 0)
        self.assertEqual(models[0].status, "insufficient_strong_anchors")

    def test_stronger_ready_model_recovery_is_immutable_to_sequence_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            timed, cues, decisions = _three_a_one_b_fixture(Path(tmp))
            protected = decisions[3]
            decisions[3] = MatchDecision(
                cue_ordinal=protected.cue_ordinal,
                canonical_ordinal=3,
                score=0.2,
                action="replace",
                reason="timing_model_confirms_canonical_sequence",
                cue_span=(3, 4),
                canonical_span=(3, 5),
                source_text=protected.source_text,
                canonical_text="规范片段一前半 规范片段一后半",
                output_text="规范片段一前半 规范片段一后半",
                edit_operations=(),
            )
            replacements, reconciled, summary, _ = reconcile_text_from_sequence_projection(
                cues, timed, decisions
            )

        self.assertNotIn(3, replacements)
        self.assertEqual(reconciled[3].reason, "timing_model_confirms_canonical_sequence")
        # The lower-authority layer leaves the whole bounded region untouched
        # instead of repartitioning around a stronger recovered decision.
        self.assertEqual(summary.reconciled_region_count, 0)

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
            rendered, report = smart_repair_srt_text_v11(
                _srt(srt_texts, starts), timed, repair
            )

        self.assertIn("00:00:01,000 --> 00:00:02,000\n前半歌词", rendered)
        self.assertIn("00:00:02,400 --> 00:00:03,400\n后接短句继续播放", rendered)
        self.assertIn("00:00:04,200 --> 00:00:05,200\n最后几个字", rendered)
        self.assertEqual(report["text_sequence_reconciled_cue_count"], 0)


def _cue_start(cue) -> int:
    timing = cue.timing.split(" --> ", 1)[0]
    hours, minutes, rest = timing.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(millis)
    )


if __name__ == "__main__":
    unittest.main()
