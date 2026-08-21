from __future__ import annotations

import unittest

from lyric_aligner.alignment.selective_policy import build_selective_repair_plan_v11
from lyric_aligner.alignment.selective_repair import SelectiveRepairConfig
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_policy import SMART_POLICY_ID, SMART_SCHEMA_VERSION


def _cue(ordinal: int, second: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"00:00:{second:02d},000 --> 00:00:{second:02d},900",
        text=text,
        normalized=text,
        raw_block_index=ordinal * 2,
    )


def _canonical(ordinal: int, second: int, text: str) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=ordinal,
        source="song.lrc",
        source_ordinal=0,
        time_ms=second * 1000,
        text=text,
        normalized=text,
    )


class ProConfigSemanticsTests(unittest.TestCase):
    def test_public_max_jobs_reports_requested_primary_budget(self) -> None:
        cues = [_cue(0, 1, "甲句"), _cue(1, 2, "乙句")]
        canonical = [_canonical(0, 1, "甲句"), _canonical(1, 2, "乙句")]
        report = {
            "schema_version": SMART_SCHEMA_VERSION,
            "policy_id": SMART_POLICY_ID,
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "cue_count": 2,
            "canonical_line_count": 2,
            "models": [],
            "timing_decisions": [
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 0,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                },
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 1,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                },
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"},
                {"cue_ordinal": 1, "canonical_ordinal": 1, "action": "unchanged"},
            ],
        }

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            config=SelectiveRepairConfig(max_jobs=1),
        )

        self.assertEqual(plan["config"]["max_jobs"], 1)
        self.assertEqual(plan["summary"]["primary_candidate_job_count"], 2)
        self.assertEqual(plan["summary"]["primary_job_count"], 1)
        self.assertEqual(
            plan["summary"]["max_jobs_applies_to"],
            "primary_jobs_only_shadow_competitors_additive",
        )


if __name__ == "__main__":
    unittest.main()
