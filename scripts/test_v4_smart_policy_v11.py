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


if __name__ == "__main__":
    unittest.main()
