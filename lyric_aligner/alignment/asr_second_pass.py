"""Execute selected P5 second-pass jobs and compose complete ASR evidence.

The second pass never widens the original P3 local windows. An empty P5
selection means execute zero jobs (and therefore load no model), never
"execute all". Unselected first-pass results are retained; selected jobs are
replaced by second-pass evidence.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from lyric_aligner.alignment.asr_executor import (
    AsrExecutionError,
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
)


ASR_COMPOSITE_SCHEMA_VERSION = "1.0"


class AsrSecondPassExecutionError(AsrExecutionError):
    """Raised when a second-pass plan cannot be executed/composed safely."""


def _job_index(rows: Any, *, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise AsrSecondPassExecutionError(f"{label} jobs must be a list")
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AsrSecondPassExecutionError(f"{label} job must be an object")
        job_id = str(row.get("job_id") or "").strip()
        if not job_id or job_id in output:
            raise AsrSecondPassExecutionError(
                f"{label} job IDs must be unique/non-empty"
            )
        output[job_id] = row
    return output


def _mix_job_index(plan: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if plan.get("mode") != "plan_only":
        raise AsrSecondPassExecutionError("alignment plan must be plan_only")
    if plan.get("backend_execution_performed") is not False:
        raise AsrSecondPassExecutionError("alignment plan already reports execution")
    rows = plan.get("jobs")
    if not isinstance(rows, list):
        raise AsrSecondPassExecutionError("alignment plan jobs must be a list")
    output: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise AsrSecondPassExecutionError("alignment plan job must be an object")
        if "mix_asr" not in (row.get("requested_capabilities") or []):
            continue
        job_id = str(row.get("job_id") or "").strip()
        if not job_id or job_id in output:
            raise AsrSecondPassExecutionError(
                "alignment plan mix-ASR job IDs must be unique/non-empty"
            )
        output[job_id] = row
        order.append(job_id)
    return output, order


def _same_field(
    original: dict[str, Any], selected: dict[str, Any], field: str, *, job_id: str
) -> None:
    if selected.get(field) != original.get(field):
        raise AsrSecondPassExecutionError(
            f"second-pass job {job_id} changed original {field}"
        )


def _validate_second_plan(
    *,
    alignment_plan: dict[str, Any],
    second_pass_plan: dict[str, Any],
    config: FasterWhisperExecutionConfig,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if second_pass_plan.get("mode") != "second_pass_plan_only":
        raise AsrSecondPassExecutionError(
            "second-pass plan must be second_pass_plan_only"
        )
    if second_pass_plan.get("policy_calibrated") is not False:
        raise AsrSecondPassExecutionError("second-pass policy must remain uncalibrated")
    if second_pass_plan.get("backend_execution_performed") is not False:
        raise AsrSecondPassExecutionError("second-pass plan already reports execution")
    if second_pass_plan.get("scope_policy") != "reuse_exact_first_pass_local_windows":
        raise AsrSecondPassExecutionError("second-pass scope policy is not exact-window reuse")

    expected_model = str(second_pass_plan.get("second_pass_model_id") or "").strip()
    if not expected_model:
        raise AsrSecondPassExecutionError("second-pass plan has no second_pass_model_id")
    if expected_model != str(config.model_id or "").strip():
        raise AsrSecondPassExecutionError(
            "executor model_id does not match second-pass plan model_id"
        )

    original, _ = _mix_job_index(alignment_plan)
    selected_rows = _job_index(second_pass_plan.get("jobs"), label="second-pass plan")
    declared = second_pass_plan.get("selected_job_ids")
    if declared is not None:
        if not isinstance(declared, list):
            raise AsrSecondPassExecutionError("selected_job_ids must be a list")
        declared_ids = [str(value or "").strip() for value in declared]
        if any(not value for value in declared_ids) or len(set(declared_ids)) != len(declared_ids):
            raise AsrSecondPassExecutionError(
                "selected_job_ids must be unique/non-empty"
            )
        if set(declared_ids) != set(selected_rows):
            raise AsrSecondPassExecutionError(
                "selected_job_ids do not match second-pass plan jobs"
            )

    for job_id, selected in selected_rows.items():
        original_row = original.get(job_id)
        if original_row is None:
            raise AsrSecondPassExecutionError(
                f"second-pass job {job_id} is not an original mix_asr job"
            )
        for field in (
            "occurrence_id",
            "track_id",
            "canonical_line_index",
            "language_profile",
            "mix_window_ms",
            "source_window_ms",
            "canonical_text_sha256",
        ):
            _same_field(original_row, selected, field, job_id=job_id)
        if "mix_asr" not in (selected.get("requested_capabilities") or []):
            raise AsrSecondPassExecutionError(
                f"second-pass job {job_id} does not request mix_asr"
            )

    # Preserve original planner order rather than P5 routing order in final evidence.
    selected_order = [job_id for job_id in original if job_id in selected_rows]
    return selected_rows, selected_order


def _validate_first_pass(
    *,
    alignment_plan: dict[str, Any],
    first_pass_evidence: dict[str, Any],
    second_model_id: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    if str(first_pass_evidence.get("backend") or "") != "faster_whisper":
        raise AsrSecondPassExecutionError(
            "first-pass evidence backend must be faster_whisper"
        )
    first_model = str(
        (first_pass_evidence.get("config") or {}).get("model_id") or ""
    ).strip()
    if not first_model:
        raise AsrSecondPassExecutionError("first-pass evidence has no config.model_id")
    if first_model == second_model_id:
        raise AsrSecondPassExecutionError(
            "second-pass model must differ from first-pass model"
        )

    original, _ = _mix_job_index(alignment_plan)
    first = _job_index(first_pass_evidence.get("jobs"), label="first-pass evidence")
    extras = sorted(set(first) - set(original))
    if extras:
        raise AsrSecondPassExecutionError(
            "first-pass evidence contains jobs outside original mix_asr plan"
        )
    for job_id, row in first.items():
        original_row = original[job_id]
        if row.get("occurrence_id") not in (None, original_row.get("occurrence_id")):
            raise AsrSecondPassExecutionError(
                f"first-pass job {job_id} occurrence identity mismatch"
            )
        if row.get("canonical_line_index") not in (
            None,
            original_row.get("canonical_line_index"),
        ):
            raise AsrSecondPassExecutionError(
                f"first-pass job {job_id} line identity mismatch"
            )
        if row.get("mix_window_ms") not in (None, original_row.get("mix_window_ms")):
            raise AsrSecondPassExecutionError(
                f"first-pass job {job_id} mix window mismatch"
            )
    return first, first_model


def execute_second_pass_and_compose(
    *,
    audio_path: Path,
    alignment_plan: dict[str, Any],
    second_pass_plan: dict[str, Any],
    first_pass_evidence: dict[str, Any],
    canonical_text_by_job_id: dict[str, str] | None,
    config: FasterWhisperExecutionConfig,
    model_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute exact selected P5 jobs and return one complete ASR evidence view."""

    config.validate()
    selected_rows, selected_order = _validate_second_plan(
        alignment_plan=alignment_plan,
        second_pass_plan=second_pass_plan,
        config=config,
    )
    first_rows, first_model = _validate_first_pass(
        alignment_plan=alignment_plan,
        first_pass_evidence=first_pass_evidence,
        second_model_id=config.model_id,
    )
    original, original_order = _mix_job_index(alignment_plan)

    # Critical safety rule: an empty second-pass selection is explicitly jobs=[],
    # never the P3 CLI's historical "no --job-id means all" behavior.
    execution_plan = deepcopy(alignment_plan)
    execution_plan["jobs"] = [deepcopy(original[job_id]) for job_id in selected_order]
    canonical_text_by_job_id = canonical_text_by_job_id or {}
    selected_canonical = {
        job_id: canonical_text_by_job_id[job_id]
        for job_id in selected_order
        if job_id in canonical_text_by_job_id
    }

    second = execute_faster_whisper_jobs(
        audio_path=audio_path,
        plan=execution_plan,
        canonical_text_by_job_id=selected_canonical,
        config=config,
        model_factory=model_factory,
    )
    second_rows = _job_index(second.get("jobs"), label="second-pass evidence")
    if set(second_rows) != set(selected_rows):
        raise AsrSecondPassExecutionError(
            "second-pass executor result IDs do not match selected second-pass jobs"
        )

    composite_rows: list[dict[str, Any]] = []
    retained = 0
    replaced = 0
    for job_id in original_order:
        if job_id in second_rows:
            row = deepcopy(second_rows[job_id])
            row["evidence_pass"] = "second"
            row["evidence_model_id"] = config.model_id
            composite_rows.append(row)
            replaced += 1
        elif job_id in first_rows:
            row = deepcopy(first_rows[job_id])
            row["evidence_pass"] = "first"
            row["evidence_model_id"] = first_model
            composite_rows.append(row)
            retained += 1

    return {
        "schema_version": ASR_COMPOSITE_SCHEMA_VERSION,
        "backend": "faster_whisper",
        "mode": "composite_second_pass_evidence",
        "policy_calibrated": False,
        "canonical_text_authority": "canonical_lyrics_only",
        "primary_timing_authority": "source_to_mix_only",
        "scope_policy": "reuse_exact_first_pass_local_windows",
        "config": {
            "first_pass_model_id": first_model,
            "second_pass_model_id": config.model_id,
            "second_pass_execution": config.to_dict(),
        },
        "model_loaded_second_pass": bool(second.get("model_loaded")),
        "first_pass_input_job_count": len(first_rows),
        "first_pass_retained_job_count": retained,
        "second_pass_selected_job_count": len(selected_rows),
        "second_pass_executed_job_count": replaced,
        "job_count": len(composite_rows),
        "jobs": composite_rows,
        "privacy": (
            "private ASR text may be included because include_private_text=true"
            if config.include_private_text
            else "raw ASR text omitted; hashes/confidence/timing/support only"
        ),
    }
