"""Route weak local first-pass ASR jobs to a bounded second accuracy pass.

Routing policy is deliberately uncalibrated. It decides only which already-
planned local jobs deserve more evidence; it never decides canonical text,
final timing, or release eligibility.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


ASR_SECOND_PASS_SCHEMA_VERSION = "1.0"
ASR_SECOND_PASS_POLICY_ID = "asr-second-pass-bootstrap-2026-08-18-v1"


class AsrRoutingError(ValueError):
    """Raised when first-pass ASR evidence cannot be routed deterministically."""


@dataclass(frozen=True)
class AsrSecondPassRoutingConfig:
    min_canonical_text_support: float = 0.65
    min_avg_logprob: float = -0.75
    max_no_speech_prob: float = 0.60
    min_language_probability: float = 0.65
    reroute_missing_segments: bool = True
    reroute_missing_line_support: bool = True
    max_jobs: int = 100

    def validate(self) -> None:
        for label, value in (
            ("min_canonical_text_support", self.min_canonical_text_support),
            ("max_no_speech_prob", self.max_no_speech_prob),
            ("min_language_probability", self.min_language_probability),
        ):
            if not math.isfinite(float(value)):
                raise AsrRoutingError(f"{label} must be finite")
            if not 0.0 <= float(value) <= 1.0:
                raise AsrRoutingError(f"{label} must be within [0,1]")
        if not math.isfinite(float(self.min_avg_logprob)):
            raise AsrRoutingError("min_avg_logprob must be finite")
        if self.max_jobs < 1:
            raise AsrRoutingError("max_jobs must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_float(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AsrRoutingError(f"{label} is invalid") from exc
    if not math.isfinite(number):
        raise AsrRoutingError(f"{label} must be finite")
    return number


def _aggregate_segment_quality(job: dict[str, Any]) -> dict[str, Any]:
    segments = job.get("segments")
    if not isinstance(segments, list):
        raise AsrRoutingError("ASR job segments must be a list")
    rows = [segment for segment in segments if isinstance(segment, dict)]
    if not rows:
        return {
            "segment_count": 0,
            "avg_logprob": None,
            "max_no_speech_prob": None,
            "segment_quality_complete": False,
        }

    logprobs: list[float] = []
    no_speech: list[float] = []
    quality_complete = True
    for segment in rows:
        if segment.get("avg_logprob") is None:
            quality_complete = False
        else:
            logprobs.append(
                _finite_float(segment.get("avg_logprob"), label="segment avg_logprob")
            )
        if segment.get("no_speech_prob") is None:
            quality_complete = False
        else:
            probability = _finite_float(
                segment.get("no_speech_prob"), label="segment no_speech_prob"
            )
            if not 0.0 <= probability <= 1.0:
                raise AsrRoutingError("segment no_speech_prob must be within [0,1]")
            no_speech.append(probability)

    return {
        "segment_count": len(rows),
        "avg_logprob": sum(logprobs) / len(logprobs) if logprobs else None,
        "max_no_speech_prob": max(no_speech) if no_speech else None,
        "segment_quality_complete": quality_complete,
    }


def _first_pass_index(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if str(evidence.get("backend") or "") != "faster_whisper":
        raise AsrRoutingError("first-pass evidence backend must be faster_whisper")
    jobs = evidence.get("jobs")
    if not isinstance(jobs, list):
        raise AsrRoutingError("first-pass ASR jobs must be a list")
    output: dict[str, dict[str, Any]] = {}
    for job in jobs:
        if not isinstance(job, dict):
            raise AsrRoutingError("first-pass ASR job must be an object")
        job_id = str(job.get("job_id") or "").strip()
        if not job_id or job_id in output:
            raise AsrRoutingError("first-pass ASR job IDs must be unique/non-empty")
        output[job_id] = job
    return output


def _route_reasons(
    evidence_job: dict[str, Any] | None,
    *,
    config: AsrSecondPassRoutingConfig,
) -> tuple[list[str], dict[str, Any]]:
    if evidence_job is None:
        return ["missing_first_pass_evidence"], {
            "canonical_text_support_score": None,
            "language_probability": None,
            "segment_count": 0,
            "avg_logprob": None,
            "max_no_speech_prob": None,
            "segment_quality_complete": False,
        }

    quality = _aggregate_segment_quality(evidence_job)
    reasons: list[str] = []

    support_raw = evidence_job.get("canonical_text_support_score")
    support = None
    if support_raw is not None:
        support = _finite_float(support_raw, label="canonical text support")
        if not 0.0 <= support <= 1.0:
            raise AsrRoutingError("canonical text support must be within [0,1]")
        if support < config.min_canonical_text_support:
            reasons.append("low_canonical_text_support")
    elif config.reroute_missing_line_support:
        reasons.append("missing_canonical_text_support")

    language_raw = evidence_job.get("language_probability")
    language_probability = None
    if language_raw is not None:
        language_probability = _finite_float(language_raw, label="language probability")
        if not 0.0 <= language_probability <= 1.0:
            raise AsrRoutingError("language probability must be within [0,1]")
        if language_probability < config.min_language_probability:
            reasons.append("low_language_probability")

    if quality["segment_count"] == 0:
        if config.reroute_missing_segments:
            reasons.append("missing_segments")
    else:
        if not quality["segment_quality_complete"]:
            reasons.append("missing_segment_quality")
        if (
            quality["avg_logprob"] is not None
            and quality["avg_logprob"] < config.min_avg_logprob
        ):
            reasons.append("low_avg_logprob")
        if (
            quality["max_no_speech_prob"] is not None
            and quality["max_no_speech_prob"] > config.max_no_speech_prob
        ):
            reasons.append("high_no_speech_probability")

    snapshot = {
        "canonical_text_support_score": support,
        "language_probability": language_probability,
        **quality,
    }
    return sorted(set(reasons)), snapshot


def _priority(value: Any) -> str:
    priority = str(value or "low").strip().lower()
    if priority not in {"high", "medium", "low"}:
        raise AsrRoutingError(f"invalid first-pass planner priority {priority!r}")
    return priority


def _severity_rank(reasons: list[str]) -> int:
    """Return an ordinal severity class; lower values are routed first.

    This is deterministic routing policy, not calibrated confidence.
    """

    reason_set = set(reasons)
    if "missing_first_pass_evidence" in reason_set:
        return 0
    if "missing_segments" in reason_set or "missing_segment_quality" in reason_set:
        return 1
    if "low_canonical_text_support" in reason_set:
        return 2
    return 3


def build_second_pass_plan(
    *,
    alignment_plan: dict[str, Any],
    first_pass_evidence: dict[str, Any],
    config: AsrSecondPassRoutingConfig | None = None,
) -> dict[str, Any]:
    config = config or AsrSecondPassRoutingConfig()
    config.validate()
    if alignment_plan.get("mode") != "plan_only":
        raise AsrRoutingError("alignment plan must be plan_only")
    if alignment_plan.get("backend_execution_performed") is not False:
        raise AsrRoutingError("alignment plan already reports execution")
    plan_jobs = alignment_plan.get("jobs")
    if not isinstance(plan_jobs, list):
        raise AsrRoutingError("alignment plan jobs must be a list")
    evidence_index = _first_pass_index(first_pass_evidence)

    selected: list[dict[str, Any]] = []
    first_pass_mix_job_ids: set[str] = set()
    for plan_job in plan_jobs:
        if not isinstance(plan_job, dict):
            raise AsrRoutingError("alignment plan job must be an object")
        capabilities = plan_job.get("requested_capabilities") or []
        if "mix_asr" not in capabilities:
            continue
        job_id = str(plan_job.get("job_id") or "").strip()
        if not job_id or job_id in first_pass_mix_job_ids:
            raise AsrRoutingError("alignment plan mix-ASR job IDs must be unique/non-empty")
        first_pass_mix_job_ids.add(job_id)
        reasons, snapshot = _route_reasons(evidence_index.get(job_id), config=config)
        if not reasons:
            continue

        priority = _priority(plan_job.get("priority"))
        selected.append(
            {
                "job_id": job_id,
                "occurrence_id": str(plan_job.get("occurrence_id") or ""),
                "track_id": str(plan_job.get("track_id") or ""),
                "ordinal": int(plan_job.get("ordinal", -1)),
                "canonical_line_index": plan_job.get("canonical_line_index"),
                "language_profile": str(plan_job.get("language_profile") or "auto"),
                # Scope must remain identical to the first-pass planner job.
                "mix_window_ms": plan_job.get("mix_window_ms"),
                "source_window_ms": plan_job.get("source_window_ms"),
                "canonical_text_sha256": plan_job.get("canonical_text_sha256"),
                "first_pass_priority": priority,
                "first_pass_reasons": list(plan_job.get("reasons") or []),
                "second_pass_reasons": reasons,
                "second_pass_severity_rank": _severity_rank(reasons),
                "first_pass_quality": snapshot,
                "requested_capabilities": ["mix_asr", "word_timestamps"],
                "execution_state": "second_pass_planned_not_executed",
            }
        )

    extra_evidence = sorted(set(evidence_index) - first_pass_mix_job_ids)
    if extra_evidence:
        raise AsrRoutingError(
            "first-pass ASR evidence contains jobs not present as mix_asr jobs in alignment plan"
        )

    priority_order = {"high": 0, "medium": 1, "low": 2}
    selected.sort(
        key=lambda row: (
            priority_order[row["first_pass_priority"]],
            row["second_pass_severity_rank"],
            -len(row["second_pass_reasons"]),
            row["ordinal"],
            int(row["canonical_line_index"])
            if row["canonical_line_index"] is not None
            else -1,
            row["mix_window_ms"] or [0, 0],
            row["occurrence_id"],
            row["job_id"],
        )
    )
    total_selected_before_truncation = len(selected)
    truncated = total_selected_before_truncation > config.max_jobs
    selected = selected[: config.max_jobs]

    reason_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    for row in selected:
        priority_counts[row["first_pass_priority"]] = (
            priority_counts.get(row["first_pass_priority"], 0) + 1
        )
        for reason in row["second_pass_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return {
        "schema_version": ASR_SECOND_PASS_SCHEMA_VERSION,
        "policy_id": ASR_SECOND_PASS_POLICY_ID,
        "policy_calibrated": False,
        "mode": "second_pass_plan_only",
        "backend_execution_performed": False,
        "canonical_text_authority": "canonical_lyrics_only",
        "primary_timing_authority": "source_to_mix_only",
        "scope_policy": "reuse_exact_first_pass_local_windows",
        "config": config.to_dict(),
        "summary": {
            "first_pass_mix_asr_job_count": len(first_pass_mix_job_ids),
            "eligible_second_pass_job_count_before_truncation": total_selected_before_truncation,
            "second_pass_job_count": len(selected),
            "second_pass_plan_truncated": truncated,
            "reason_counts": dict(sorted(reason_counts.items())),
            "priority_counts": dict(sorted(priority_counts.items())),
        },
        "jobs": selected,
    }
