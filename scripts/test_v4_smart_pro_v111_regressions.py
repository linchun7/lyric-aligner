from __future__ import annotations

import unittest
from pathlib import Path

from lyric_aligner.alignment.selective_policy import build_selective_repair_plan_v11
from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairConfig,
    SelectiveRepairPlanningError,
)
from lyric_aligner.io.path_safety import PathCollisionError, validate_separate_artifact_paths
from lyric_aligner.text.canonical_lyrics import CanonicalToken
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence, TimingDecision
from lyric_aligner.timeline.smart_policy import (
    SMART_POLICY_ID,
    SMART_SCHEMA_VERSION,
    _hard_rate_priors,
    _harden_combined_timeline,
)


def _clock(ms: int) -> str:
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, millis = divmod(rem, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _cue(ordinal: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"{_clock(start_ms)} --> {_clock(end_ms)}",
        text=text,
        normalized=text,
        raw_block_index=ordinal * 2,
    )


def _canonical(
    ordinal: int,
    source_ordinal: int,
    time_ms: int,
    text: str,
    *,
    tokens: tuple[CanonicalToken, ...] = (),
) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=ordinal,
        source=f"{source_ordinal + 1:02d}.lrc",
        source_ordinal=source_ordinal,
        time_ms=time_ms,
        text=text,
        normalized=text,
        tokens=tokens,
        timing_format="enhanced_lrc" if tokens else "line_lrc",
    )


def _smart_report(
    *,
    timing_decisions: list[dict],
    text_decisions: list[dict],
    models: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": SMART_SCHEMA_VERSION,
        "policy_id": SMART_POLICY_ID,
        "mode": "smart_anchor_timeline_repair_no_audio",
        "audio_read": False,
        "models": models or [],
        "timing_decisions": timing_decisions,
        "text_decisions": text_decisions,
    }


class SmartProV111RegressionTests(unittest.TestCase):
    def test_enhanced_lrc_open_final_token_can_plan_pro(self) -> None:
        cues = [_cue(0, 10_000, 11_000, "hello world")]
        canonical = [
            _canonical(
                0,
                0,
                1_000,
                "hello world",
                tokens=(
                    CanonicalToken("hello", 1_000, 1_500),
                    CanonicalToken("world", 1_500, None),
                ),
            )
        ]
        report = _smart_report(
            timing_decisions=[
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 0,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                }
            ],
            text_decisions=[
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"}
            ],
        )

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
        )

        self.assertEqual(plan["summary"]["primary_job_count"], 1)
        job = next(row for row in plan["jobs"] if not row.get("shadow_evidence_only"))
        self.assertIn("source_local_acoustic_match", job["requested_capabilities"])
        self.assertNotIn("source_forced_alignment", job["requested_capabilities"])

    def test_adaptive_source_window_is_long_enough_for_no_prior_search(self) -> None:
        cues = [_cue(0, 10_000, 11_000, "line")]
        canonical = [_canonical(0, 0, 1_000, "line")]
        report = _smart_report(
            timing_decisions=[
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 0,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                }
            ],
            text_decisions=[
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"}
            ],
        )

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
        )
        job = next(row for row in plan["jobs"] if not row.get("shadow_evidence_only"))
        mix_duration = job["mix_window_ms"][1] - job["mix_window_ms"][0]
        source_duration = job["source_window_ms"][1] - job["source_window_ms"][0]
        self.assertGreaterEqual(source_duration, int(mix_duration * 1.35) + 750)

    def test_pro_rejects_stale_smart_policy(self) -> None:
        cues = [_cue(0, 10_000, 11_000, "line")]
        canonical = [_canonical(0, 0, 1_000, "line")]
        stale = _smart_report(
            timing_decisions=[],
            text_decisions=[],
        )
        stale["policy_id"] = "smart-validation-policy-2026-08-19-v1"

        with self.assertRaises(SelectiveRepairPlanningError):
            build_selective_repair_plan_v11(
                smart_report=stale,
                cues=cues,
                canonical=canonical,
            )

    def test_asr_only_job_does_not_widen_acoustic_region(self) -> None:
        cues = [
            _cue(0, 10_000, 11_000, "timing"),
            _cue(1, 11_400, 12_400, "text"),
        ]
        canonical = [
            _canonical(0, 0, 1_000, "timing"),
            _canonical(1, 0, 2_500, "text"),
        ]
        report = _smart_report(
            timing_decisions=[
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 0,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                },
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 1,
                    "action": "preserve",
                    "reason": "timing_matches_anchor_model",
                },
            ],
            text_decisions=[
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"},
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 1,
                    "action": "review",
                    "reason": "ambiguous_nearby_match",
                },
            ],
        )

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
        )
        primary = [row for row in plan["jobs"] if not row.get("shadow_evidence_only")]
        acoustic = next(
            row for row in primary
            if "source_local_acoustic_match" in row["requested_capabilities"]
        )
        asr_only = next(
            row for row in primary
            if "source_local_acoustic_match" not in row["requested_capabilities"]
        )
        self.assertEqual(acoustic["region_mix_window_ms"], acoustic["mix_window_ms"])
        self.assertEqual(asr_only["region_mix_window_ms"], asr_only["mix_window_ms"])

    def test_max_jobs_caps_primary_while_shadow_competitors_are_additive(self) -> None:
        cues = [
            _cue(0, 10_000, 11_000, "尾句"),
            _cue(1, 20_000, 21_000, "第二条"),
        ]
        canonical = [
            _canonical(0, 0, 1_000, "前句"),
            _canonical(1, 0, 5_000, "尾句"),
            _canonical(2, 1, 1_000, "第二条"),
            _canonical(3, 1, 5_000, "后句"),
        ]
        report = _smart_report(
            timing_decisions=[
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 1,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                },
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 2,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                },
            ],
            text_decisions=[
                {"cue_ordinal": 0, "canonical_ordinal": 1, "action": "unchanged"},
                {"cue_ordinal": 1, "canonical_ordinal": 2, "action": "unchanged"},
            ],
        )

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            config=SelectiveRepairConfig(max_jobs=2),
        )

        primary = [row for row in plan["jobs"] if not row.get("shadow_evidence_only")]
        shadow = [row for row in plan["jobs"] if row.get("shadow_evidence_only")]
        primary_ids = {row["job_id"] for row in primary}
        self.assertEqual(len(primary), 2)
        self.assertGreaterEqual(len(shadow), 1)
        self.assertEqual(plan["summary"]["job_count"], len(plan["jobs"]))
        self.assertEqual(plan["summary"]["boundary_competitor_omitted_due_to_max_jobs"], 0)
        self.assertEqual(
            plan["summary"]["max_jobs_applies_to"],
            "primary_jobs_only_shadow_competitors_additive",
        )
        self.assertTrue(
            all(row["boundary_competitor_for_job_id"] in primary_ids for row in shadow)
        )

    def test_combined_repairs_may_not_create_overlap(self) -> None:
        cues = [
            _cue(0, 1_000, 2_000, "a"),
            _cue(1, 3_000, 4_000, "b"),
        ]
        decisions = [
            TimingDecision(
                0, 0, 0, "A", "repair", "test", 1_000, 2_000,
                1_500, 2_500, -500.0, "ready", ()
            ),
            TimingDecision(
                1, 0, 1, "A", "repair", "test", 3_000, 4_000,
                2_400, 3_400, 600.0, "ready", ()
            ),
        ]

        hardened = _harden_combined_timeline(cues, decisions)
        self.assertEqual([item.action for item in hardened], ["review", "review"])
        self.assertTrue(
            all(item.reason == "proposed_shift_increases_overlap" for item in hardened)
        )

    def test_existing_overlap_may_not_be_worsened(self) -> None:
        cues = [
            _cue(0, 1_000, 2_500, "a"),
            _cue(1, 2_400, 3_400, "b"),
        ]
        decisions = [
            TimingDecision(
                0, 0, 0, "A", "repair", "test", 1_000, 2_500,
                1_000, 3_000, -500.0, "ready", ()
            ),
            TimingDecision(
                1, 0, 1, "A", "preserve", "test", 2_400, 3_400,
                None, None, 0.0, "ready", ()
            ),
        ]

        hardened = _harden_combined_timeline(cues, decisions)
        self.assertEqual(hardened[0].action, "review")
        self.assertEqual(hardened[0].reason, "proposed_shift_increases_overlap")

    def test_bpm_derived_rate_is_not_a_hard_prior(self) -> None:
        hard = _hard_rate_priors(
            {0: 1.10, 1: 1.20},
            {
                0: {"value": 1.10, "provenance": "bpm_derived"},
                1: {"value": 1.20, "provenance": "exact_daw"},
            },
        )
        self.assertEqual(hard, {1: 1.20})

    def test_artifact_paths_may_not_overwrite_inputs_or_each_other(self) -> None:
        source = Path("/tmp/source.srt")
        with self.assertRaises(PathCollisionError):
            validate_separate_artifact_paths(
                inputs={"source": source},
                outputs={"report": source},
            )
        with self.assertRaises(PathCollisionError):
            validate_separate_artifact_paths(
                inputs={"source": source},
                outputs={
                    "output": Path("/tmp/out.json"),
                    "report": Path("/tmp/out.json"),
                },
            )


if __name__ == "__main__":
    unittest.main()
