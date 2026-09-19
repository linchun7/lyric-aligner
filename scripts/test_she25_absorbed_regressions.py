from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairConfig,
    _bounded_mix_window,
    build_selective_repair_plan,
)
from lyric_aligner.text.canonical_metadata import prepare_canonical_metadata
from lyric_aligner.text_repair import (
    CanonicalLine,
    MatchDecision,
    SubtitleCue,
    _normalize_for_match,
    _unique_exact_anchors,
    build_trusted_lexical_floor_report,
    parse_srt_text,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline import smart_current


def cues(*texts: str):
    return parse_srt_text(
        "\n\n".join(
            f"{index + 1}\n00:00:{2 * index:02d},000 --> 00:00:{2 * index + 1:02d},000\n{text}"
            for index, text in enumerate(texts)
        )
    )[1]


def canonical(*texts: str):
    return [
        CanonicalLine(index, "song.lrc", text, _normalize_for_match(text), 0)
        for index, text in enumerate(texts)
    ]


def cue(ordinal: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    def clock(ms: int) -> str:
        hour, rem = divmod(ms, 3_600_000)
        minute, rem = divmod(rem, 60_000)
        second, millis = divmod(rem, 1000)
        return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"

    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"{clock(start_ms)} --> {clock(end_ms)}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=ordinal * 2,
    )


class ContinuousIdentityRegressionTests(unittest.TestCase):
    def test_line_unique_phrase_hidden_in_other_line_break_is_not_anchor(self) -> None:
        source = cues("opening phrase", "we travel together", "closing phrase")
        lyrics = canonical(
            "opening phrase",
            "we travel",
            " together",
            "interlude",
            "we travel together",
            "closing phrase",
        )
        self.assertEqual(_unique_exact_anchors(source, lyrics), [(0, 0), (2, 5)])

    def test_true_unique_anchor_is_preserved(self) -> None:
        self.assertEqual(
            _unique_exact_anchors(cues("unique content"), canonical("unique content")),
            [(0, 0)],
        )


class ConnectedOwnershipFloorRegressionTests(unittest.TestCase):
    def test_review_multi_cue_envelope_quarantines_all_member_cues(self) -> None:
        output = cues("晴天", "暴雨")
        decisions = [
            MatchDecision(0, 0, 0.8, "review", "ownership_unknown", (0, 2), (0, 1)),
            MatchDecision(1, 0, 0.9, "replace", "recovery", (1, 2), (0, 1)),
        ]
        report = build_trusted_lexical_floor_report(output, decisions, canonical("晴天暴雨"))
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["trusted_region_count"], 0)
        self.assertEqual(report["partially_unresolved_region_count"], 1)
        self.assertEqual(report["trusted_region_lexical_error_count"], 0)

    def test_nonadjacent_duplicate_canonical_ownership_is_rejected(self) -> None:
        output = cues("甲甲", "乙乙", "丙丙", "甲甲", "乙乙")
        spans = [(0, 1), (0, 1), (1, 2), (0, 1), (0, 1)]
        decisions = [
            MatchDecision(index, span[0], 0.9, "replace", "recovery", (index, index + 1), span)
            for index, span in enumerate(spans)
        ]
        report = build_trusted_lexical_floor_report(
            output, decisions, canonical("甲甲乙乙", "丙丙")
        )
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any(row.get("ownership_error") for row in report["diagnostics"]))

    def test_legitimate_adjacent_split_is_audited_as_one_connected_region(self) -> None:
        output = cues("晴天暴雨", "都来这里")
        decisions = [
            MatchDecision(0, 0, 0.9, "replace", "recovery", (0, 1), (0, 1)),
            MatchDecision(1, 0, 0.9, "replace", "recovery", (1, 2), (0, 1)),
        ]
        report = build_trusted_lexical_floor_report(
            output, decisions, canonical("晴天暴雨都来这里")
        )
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["trusted_region_count"], 1)


class Smart1211RegressionTests(unittest.TestCase):
    def test_failed_floor_is_recomputed_after_quarantine(self) -> None:
        text = (
            "1\n00:00:01,000 --> 00:00:02,000\n春天来了\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n春天来了\n"
        )
        base = {
            "timing_repair_count": 0,
            "text_replacement_count": 2,
            "text_decision_replacement_count": 2,
            "text_decisions": [
                {
                    "cue_ordinal": index,
                    "canonical_ordinal": 0,
                    "score": 0.9,
                    "action": "replace",
                    "reason": "recovery",
                    "cue_span": [index, index + 1],
                    "canonical_span": [0, 1],
                }
                for index in range(2)
            ],
            "timing_decisions": [],
            "timing_actionable_strong_model_count": 0,
            "timing_actionable_weak_or_unknown_model_count": 0,
            "timing_suspected_actionable_count": 0,
            "timing_high_value_pro_candidate_count": 0,
            "timing_unvalidated_count": 0,
        }
        with patch(
            "lyric_aligner.timeline.smart_policy_v1211.smart_repair_srt_text_v1210",
            return_value=(text, base),
        ):
            _, report = smart_current.smart_repair_srt_text(
                "", [], [CanonicalLine(0, "x.lrc", "春天来了", "春天来了")]
            )
        self.assertEqual(report["text_review_count"], 2)
        self.assertEqual(report["text_replacement_count"], 0)
        self.assertEqual(report["text_lexical_floor_prequarantine"]["status"], "failed")
        self.assertEqual(report["text_lexical_floor_status"], "review_required")
        self.assertEqual(report["text_lexical_floor"]["trusted_region_lexical_error_count"], 0)


class CanonicalMetadataRegressionTests(unittest.TestCase):
    def prepare(self, text: str, prefixes: list[str]):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.lrc"
            source.write_text(text, encoding="utf-8")
            raw = source.read_bytes()
            mapping = {
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "prefixes": prefixes,
                "evidence": "Explicit performer labels in the supplied lyric source.",
            }
            return prepare_canonical_metadata(source, mapping)

    def test_declared_cjk_kana_hangul_prefixes_are_supported_without_ascii_word_damage(self) -> None:
        prepared, report = self.prepare(
            "[00:01]Ella:一起唱\n[00:02]ユナ歌う\n[00:03]민지노래해\n[00:04]Hoh\n",
            ["Ella:", "ユナ", "민지", "H"],
        )
        self.assertIn("[00:01]一起唱", prepared)
        self.assertIn("[00:02]歌う", prepared)
        self.assertIn("[00:03]노래해", prepared)
        self.assertIn("[00:04]Hoh", prepared)
        self.assertEqual(report["removed_prefix_count"], 3)
        self.assertTrue(report["timestamps_immutable"])

    def test_stale_source_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.lrc"
            source.write_text("[00:01]Ella:一起唱\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source SHA mismatch"):
                prepare_canonical_metadata(
                    source,
                    {"source_sha256": "0" * 64, "prefixes": ["Ella:"], "evidence": "explicit"},
                )


class ProMediaIdentityRegressionTests(unittest.TestCase):
    def test_last_window_is_bounded_by_physical_media(self) -> None:
        self.assertEqual(
            _bounded_mix_window(cue(0, 9200, 9800, "tail"), context_ms=2500, minimum_ms=4500, duration_ms=10000),
            [5500, 10000],
        )

    def test_mix_audio_sha_participates_in_job_identity(self) -> None:
        smart = {
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "timing_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "review", "reason": "timing_review"}
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged", "reason": "ok"}
            ],
            "models": [],
        }
        source_cues = [cue(0, 1000, 2000, "line")]
        lyric = [
            TimedCanonicalOccurrence(
                ordinal=0,
                source="01.lrc",
                source_ordinal=0,
                time_ms=1000,
                text="line",
                normalized="line",
            )
        ]
        first = build_selective_repair_plan(
            smart_report=smart,
            cues=source_cues,
            canonical=lyric,
            config=SelectiveRepairConfig(mix_duration_ms=5000, mix_audio_sha256="a" * 64),
        )
        second = build_selective_repair_plan(
            smart_report=smart,
            cues=source_cues,
            canonical=lyric,
            config=SelectiveRepairConfig(mix_duration_ms=5000, mix_audio_sha256="b" * 64),
        )
        self.assertNotEqual(first["jobs"][0]["job_id"], second["jobs"][0]["job_id"])
        self.assertEqual(first["jobs"][0]["mix_audio_sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
