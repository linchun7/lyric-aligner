"""Fail-closed Smart/Pro decision fusion for bounded selective evidence.

This layer converts independent Smart hypotheses and Pro evidence into product
states and a ranked manual queue.  It never mutates subtitle timing.  Local
acoustic retrieval is treated as an unadjudicated observation; ASR supports
text identity but does not become canonical text authority.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from lyric_aligner.alignment.selective_policy import PRO_POLICY_ID
from lyric_aligner.timeline.smart_current import (
    SMART_POLICY_ID,
    SMART_SCHEMA_VERSION,
    SMART_TIMING_ACTIONABLE_SHIFT_MS,
)

PRO_DECISION_SCHEMA_VERSION = "1.0"
PRO_DECISION_POLICY_ID = "pro-selective-decision-fusion-2026-08-22-v1.1"


class ProDecisionFusionError(ValueError):
    """Raised when Smart/Pro evidence cannot be safely bound."""


@dataclass(frozen=True)
class ProDecisionConfig:
    editor_support_tolerance_ms: int = 150
    smart_actionable_shift_ms: int = SMART_TIMING_ACTIONABLE_SHIFT_MS
    agreement_tolerance_ms: int = 750
    pro_only_anomaly_ms: int = 1000
    asr_text_support_threshold: float = 0.72

    def validate(self) -> None:
        values = (
            self.editor_support_tolerance_ms,
            self.smart_actionable_shift_ms,
            self.agreement_tolerance_ms,
            self.pro_only_anomaly_ms,
        )
        if any(int(value) < 0 for value in values):
            raise ProDecisionFusionError("Pro decision timing thresholds must be >= 0")
        if not 0.0 <= float(self.asr_text_support_threshold) <= 1.0:
            raise ProDecisionFusionError("ASR support threshold must be within [0,1]")


def _index(payload: Mapping[str, Any] | None, key: str) -> dict[str, Mapping[str, Any]]:
    if payload is None:
        return {}
    rows = payload.get("jobs")
    if not isinstance(rows, list):
        raise ProDecisionFusionError(f"{key} evidence jobs must be a list")
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        job_id = str(row.get("job_id") or "").strip()
        if not job_id:
            raise ProDecisionFusionError(f"{key} evidence job is missing job_id")
        if job_id in output:
            raise ProDecisionFusionError(f"duplicate {key} evidence job_id")
        output[job_id] = row
    return output


def _cue_index(payload: Mapping[str, Any], key: str) -> dict[int, Mapping[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise ProDecisionFusionError(f"Smart {key} must be a list")
    output: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("cue_ordinal") is None:
            continue
        output[int(row["cue_ordinal"])] = row
    return output


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _smart_shift(row: Mapping[str, Any] | None) -> int | None:
    if row is None or row.get("action") != "review":
        return None
    proposed = _number(row.get("proposed_start_ms"))
    old = _number(row.get("old_start_ms"))
    if proposed is None or old is None:
        return None
    return int(round(proposed - old))


def _acoustic_shift(row: Mapping[str, Any] | None) -> int | None:
    if row is None:
        return None
    explicit = _number(row.get("acoustic_shift_ms"))
    if explicit is not None:
        return int(round(explicit))
    # Legacy artifacts stored editor - prediction rather than the actionable
    # prediction - editor shift.
    residual = _number(row.get("editor_start_residual_ms"))
    return None if residual is None else -int(round(residual))


def _local_gate(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    if "local_match_gate_passed" in row:
        return bool(row.get("local_match_gate_passed"))
    return bool(row.get("reliable_local_match", False))


def _timing_state(
    smart_row: Mapping[str, Any] | None,
    acoustic_row: Mapping[str, Any] | None,
    config: ProDecisionConfig,
) -> tuple[str, int | None, int | None]:
    if smart_row is None or smart_row.get("action") != "review":
        return "not_requested", None, _acoustic_shift(acoustic_row)
    smart_shift = _smart_shift(smart_row)
    acoustic_shift = _acoustic_shift(acoustic_row)
    gate = _local_gate(acoustic_row)
    if smart_shift is None:
        if gate and acoustic_shift is not None:
            if abs(acoustic_shift) >= config.pro_only_anomaly_ms:
                return "pro_detected_anomaly", None, acoustic_shift
            if abs(acoustic_shift) <= config.editor_support_tolerance_ms:
                return "editor_supported_by_acoustic", None, acoustic_shift
            return "unvalidated_acoustic_observation", None, acoustic_shift
        return "unvalidated_no_actionable_evidence", None, acoustic_shift

    if not gate or acoustic_shift is None:
        return "smart_candidate_unverified", smart_shift, acoustic_shift
    if abs(smart_shift) < config.smart_actionable_shift_ms:
        if abs(acoustic_shift) >= config.pro_only_anomaly_ms:
            return "smart_pro_conflict", smart_shift, acoustic_shift
        return "editor_within_display_tolerance", smart_shift, acoustic_shift
    same_direction = (smart_shift > 0) == (acoustic_shift > 0)
    if same_direction and abs(smart_shift - acoustic_shift) <= config.agreement_tolerance_ms:
        return "smart_candidate_supported", smart_shift, acoustic_shift
    # When Pro lands inside the same product display tolerance used to decide
    # whether a Smart shift is actionable, it supports keeping the editor cue
    # strongly enough to rebut a materially different Smart hypothesis.  The
    # narrower editor threshold remains useful when Smart has no candidate.
    if abs(acoustic_shift) < config.smart_actionable_shift_ms:
        return "smart_candidate_rebutted", smart_shift, acoustic_shift
    return "smart_pro_conflict", smart_shift, acoustic_shift


def _text_state(
    smart_row: Mapping[str, Any] | None,
    asr_row: Mapping[str, Any] | None,
    acoustic_row: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    timing_state: str,
    config: ProDecisionConfig,
) -> tuple[str, float | None]:
    if (
        smart_row is not None
        and smart_row.get("reason")
        == "preceding_canonical_anchor_confirms_cross_script_vocalization"
    ):
        return "cross_script_vocalization_resolved_by_smart", None
    if smart_row is None or smart_row.get("action") != "review":
        return "resolved_by_smart", None
    score = _number(asr_row.get("canonical_text_support_score")) if asr_row else None
    if score is not None and score >= config.asr_text_support_threshold:
        return "canonical_text_supported", score
    mapped = smart_row.get("canonical_ordinal")
    if mapped is None:
        span = smart_row.get("canonical_span")
        if isinstance(span, list) and len(span) == 2 and int(span[1]) - int(span[0]) == 1:
            mapped = span[0]
    planned = job.get("canonical_line_index")
    same_occurrence = (
        mapped is not None
        and planned is not None
        and int(mapped) == int(planned)
    )
    if (
        same_occurrence
        and _local_gate(acoustic_row)
        and timing_state == "smart_candidate_supported"
    ):
        return "canonical_occurrence_supported_by_acoustic", score
    if score is not None:
        return "text_review_asr_insufficient", score
    return "text_review_unvalidated", None


def _priority(timing_state: str, text_state: str) -> tuple[int, str]:
    # Local music retrieval and Smart both consume the canonical/LRC timeline;
    # agreement between them is correlated evidence, not an independent vocal
    # onset measurement.  Promote it to the smallest high-value queue only when
    # it also resolves a previously uncertain one-to-one text occurrence.
    if (
        timing_state == "smart_candidate_supported"
        and text_state in {
            "canonical_occurrence_supported_by_acoustic",
            "cross_script_vocalization_resolved_by_smart",
        }
    ):
        return 3, "high"
    if timing_state in {
        "pro_detected_anomaly",
        "smart_candidate_supported",
        "smart_pro_conflict",
    }:
        return 2, "medium"
    if text_state in {
        "canonical_text_supported",
        "canonical_occurrence_supported_by_acoustic",
    }:
        return 2, "medium"
    if timing_state == "smart_candidate_rebutted":
        return 1, "diagnostic"
    return 0, "none"


def build_pro_decisions(
    *,
    smart_report: Mapping[str, Any],
    plan: Mapping[str, Any],
    acoustic_evidence: Mapping[str, Any] | None = None,
    asr_evidence: Mapping[str, Any] | None = None,
    forced_evidence: Mapping[str, Any] | None = None,
    config: ProDecisionConfig | None = None,
) -> dict[str, Any]:
    config = config or ProDecisionConfig()
    config.validate()
    if smart_report.get("schema_version") != SMART_SCHEMA_VERSION:
        raise ProDecisionFusionError("Pro decisions require current Smart schema")
    if smart_report.get("policy_id") != SMART_POLICY_ID:
        raise ProDecisionFusionError("Pro decisions require current Smart policy")
    if plan.get("schema_version") != "1.1" or plan.get("policy_id") != PRO_POLICY_ID:
        raise ProDecisionFusionError("Pro decisions require current selective plan")

    timing = _cue_index(smart_report, "timing_decisions")
    text = _cue_index(smart_report, "text_decisions")
    acoustic = _index(acoustic_evidence, "acoustic")
    asr = _index(asr_evidence, "ASR")
    forced = _index(forced_evidence, "forced")
    rows = plan.get("jobs")
    if not isinstance(rows, list):
        raise ProDecisionFusionError("Pro plan jobs must be a list")

    decisions: list[dict[str, Any]] = []
    timing_states: Counter[str] = Counter()
    text_states: Counter[str] = Counter()
    for job in rows:
        if not isinstance(job, Mapping) or bool(job.get("shadow_evidence_only", False)):
            continue
        job_id = str(job.get("job_id") or "").strip()
        cue = int(job["cue_ordinal"])
        timing_state, smart_shift, acoustic_shift = _timing_state(
            timing.get(cue), acoustic.get(job_id), config
        )
        text_state, asr_score = _text_state(
            text.get(cue),
            asr.get(job_id),
            acoustic.get(job_id),
            job,
            timing_state,
            config,
        )
        requested = {str(value) for value in job.get("requested_capabilities") or []}
        missing: list[str] = []
        if "source_local_acoustic_match" in requested and job_id not in acoustic:
            missing.append("source_local_acoustic_match")
        if "mix_asr" in requested and job_id not in asr:
            missing.append("mix_asr")
        if "source_forced_alignment" in requested and job_id not in forced:
            missing.append("source_forced_alignment")
        priority_rank, priority = _priority(timing_state, text_state)
        timing_states[timing_state] += 1
        text_states[text_state] += 1
        decisions.append(
            {
                "job_id": job_id,
                "cue_ordinal": cue,
                "editor_cue_start_ms": job.get("editor_cue_start_ms"),
                "editor_cue_end_ms": job.get("editor_cue_end_ms"),
                "source_ordinal": job.get("source_ordinal"),
                "canonical_line_index": job.get("canonical_line_index"),
                "canonical_text_sha256": job.get("canonical_text_sha256"),
                "timing_state": timing_state,
                "text_state": text_state,
                "smart_shift_ms": smart_shift,
                "acoustic_shift_ms": acoustic_shift,
                "asr_canonical_support_score": asr_score,
                "local_match_gate_passed": _local_gate(acoustic.get(job_id)),
                "timing_evidence_semantics": (
                    "correlated_canonical_timeline_observation_not_vocal_onset"
                    if _local_gate(acoustic.get(job_id))
                    else "no_local_acoustic_gate_support"
                ),
                "independent_vocal_onset_evidence_used": False,
                "missing_evidence": missing,
                "partial_evidence": bool(missing),
                "manual_review_priority_rank": priority_rank,
                "manual_review_priority": priority,
                "automatic_timing_change_allowed": False,
                "automatic_text_change_allowed": False,
                "timing_mutation_performed": False,
            }
        )

    decisions.sort(
        key=lambda row: (
            -int(row["manual_review_priority_rank"]),
            int(row["cue_ordinal"]),
        )
    )
    high_priority_positions = [
        {
            "cue_ordinal": row["cue_ordinal"],
            "editor_cue_start_ms": row["editor_cue_start_ms"],
            "timing_state": row["timing_state"],
        }
        for row in decisions
        if row["manual_review_priority"] == "high"
    ]
    return {
        "schema_version": PRO_DECISION_SCHEMA_VERSION,
        "policy_id": PRO_DECISION_POLICY_ID,
        "product_mode": "Pro",
        "authority": "decision_support_only",
        "automatic_timing_change_allowed": False,
        "automatic_text_change_allowed": False,
        "timing_mutation_performed": False,
        "config": asdict(config),
        "summary": {
            "decision_count": len(decisions),
            "timing_state_counts": dict(sorted(timing_states.items())),
            "text_state_counts": dict(sorted(text_states.items())),
            "high_priority_manual_review_count": sum(
                row["manual_review_priority"] == "high" for row in decisions
            ),
            "high_priority_manual_review_positions": high_priority_positions,
            "partial_evidence_count": sum(row["partial_evidence"] for row in decisions),
        },
        "decisions": decisions,
    }


__all__ = (
    "PRO_DECISION_POLICY_ID",
    "PRO_DECISION_SCHEMA_VERSION",
    "ProDecisionConfig",
    "ProDecisionFusionError",
    "build_pro_decisions",
)
