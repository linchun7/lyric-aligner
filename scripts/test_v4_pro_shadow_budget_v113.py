from __future__ import annotations

import unittest

from lyric_aligner.alignment.selective_policy import (
    PRO_V11_POLICY_ID,
    build_selective_repair_plan_v11,
)
from lyric_aligner.alignment.selective_repair import SelectiveRepairConfig
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_policy import SMART_POLICY_ID, SMART_SCHEMA_VERSION


def _cue(ordinal: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
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
        normalized=text,
        raw_block_index=ordinal * 2,
    )


def _canonical(ordinal: int, source_ordinal: int, time_ms: int, text: str):
    return TimedCanonicalOccurrence(
        ordinal=ordinal,
        source=f"{source_ordinal + 1:02d}.lrc",
        source_ordinal=source_ordinal,
        time_ms=time_ms,
        text=text,
        normalized=text,
    )


class ProShadowBudgetV113Tests(unittest.TestCase):
    def test_shadow_competitor_is_additive_to_primary_budget(self) -> None:
        cues = [_cue(0, 10_000, 11_000, "末句")]
        canonical = [
            _canonical(0, 0, 1_000, "甲"),
            _canonical(1, 0, 5_000, "乙"),
            _canonical(2, 0, 9_000, "末句"),
            _canonical(3, 1, 1_000, "下一首首句"),
            _canonical(4, 1, 5_000, "下一首次句"),
        ]
        report = {
            "schema_version": SMART_SCHEMA_VERSION,
            "policy_id": SMART_POLICY_ID,
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "models": [
                {"source_ordinal": 0, "source": "01.lrc", "rate": 1.0, "status": "ready"},
                {"source_ordinal": 1, "source": "02.lrc", "rate": 1.0, "status": "ready"},
            ],
            "timing_decisions": [
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 2,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                    "proposed_start_ms": None,
                    "proposed_end_ms": None,
                }
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 2, "action": "unchanged"}
            ],
        }

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            config=SelectiveRepairConfig(max_jobs=1),
        )

        primary = [job for job in plan["jobs"] if not job.get("shadow_evidence_only")]
        shadow = [job for job in plan["jobs"] if job.get("shadow_evidence_only")]
        self.assertEqual(PRO_V11_POLICY_ID, "smart-to-pro-reason-aware-2026-08-21-v1.1.3")
        self.assertEqual(len(primary), 1)
        self.assertEqual(len(shadow), 1)
        self.assertEqual(plan["summary"]["job_count"], 2)
        self.assertEqual(plan["summary"]["primary_job_count"], 1)
        self.assertEqual(plan["summary"]["boundary_competitor_job_count"], 1)
        self.assertEqual(plan["summary"]["boundary_competitor_omitted_due_to_max_jobs"], 0)
        self.assertEqual(
            plan["summary"]["max_jobs_applies_to"],
            "primary_jobs_only_shadow_competitors_additive",
        )
        self.assertEqual(shadow[0]["boundary_competitor_for_job_id"], primary[0]["job_id"])


if __name__ == "__main__":
    unittest.main()
