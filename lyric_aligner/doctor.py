"""Privacy-safe production readiness and resume diagnostics for v4.

The doctor is observational: it reads supplied artifacts, performs lightweight
contract checks, optionally inspects backend execution readiness, and recommends
what to do next. It never mutates artifacts and never treats backend discovery
as an accuracy claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from lyric_aligner.alignment.backends import BackendCapability, inspect_backends
from lyric_aligner.evaluation.readiness import inspect_dataset_readiness
from lyric_aligner.runtime_snapshot import validate_runtime_snapshot


DOCTOR_SCHEMA_VERSION = "1.0"
_TASK_SCHEMA_VERSION = "2.0"
_TASK_REQUIRED_INPUTS = ("source_srt", "audio", "song_list", "lyrics_dir")


class DoctorError(ValueError):
    """Raised when an explicitly supplied doctor input is malformed."""


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise DoctorError(f"{label} must be a JSON object")
    return payload


def _is_sha256(value: str) -> bool:
    value = str(value).lower()
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _stage(
    path: Path | None,
    *,
    label: str,
    validator: Callable[[dict[str, Any]], tuple[bool, str]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if path is None:
        return {
            "provided": False,
            "file": None,
            "exists": False,
            "valid": False,
            "detail": "not_provided",
        }, None
    resolved = path.resolve()
    if not resolved.is_file():
        return {
            "provided": True,
            "file": path.name,
            "exists": False,
            "valid": False,
            "detail": "file_not_found",
        }, None
    payload = _load_json(resolved, label=label)
    valid, detail = validator(payload)
    return {
        "provided": True,
        "file": path.name,
        "exists": True,
        "valid": bool(valid),
        "detail": str(detail),
    }, payload


def _task(payload: dict[str, Any]) -> tuple[bool, str]:
    """Check the real schema-2.0 task-manifest shape used by init_task.py."""

    if payload.get("schema_version") != _TASK_SCHEMA_VERSION:
        return False, "task_manifest_schema_invalid"
    project = payload.get("project")
    if not isinstance(project, str) or not project.strip():
        return False, "task_manifest_project_invalid"
    if not _is_sha256(str(payload.get("task_fingerprint_sha256") or "")):
        return False, "task_manifest_fingerprint_invalid"
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return False, "task_manifest_inputs_invalid"
    for role in _TASK_REQUIRED_INPUTS:
        if not isinstance(inputs.get(role), dict):
            return False, f"task_manifest_missing_required_input:{role}"
    return True, "task_manifest_shape_ok"


def _run(payload: dict[str, Any]) -> tuple[bool, str]:
    """Validate an effective run; payload `stage` belongs to its artifact, not run."""

    status = str(payload.get("status") or "").strip()
    algorithm_version = str(payload.get("algorithm_version") or "").strip()
    fingerprint = str(payload.get("task_fingerprint_sha256") or "")
    if not status:
        return False, "run_missing_status"
    if not algorithm_version:
        return False, "run_missing_algorithm_version"
    if not _is_sha256(fingerprint):
        return False, "run_missing_task_fingerprint"
    return True, f"status={status};algorithm={algorithm_version}"


def _editor(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("mode") != "shadow_only":
        return False, "editor_mode_not_shadow_only"
    authority = payload.get("authority")
    if isinstance(authority, dict) and authority.get("automatic_timing_change_allowed") is True:
        return False, "editor_unexpected_timing_authority"
    occurrences = payload.get("occurrences")
    if not isinstance(occurrences, list):
        return False, "editor_occurrences_not_list"
    return True, f"editor_occurrences={len(occurrences)}"


def _plan(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("mode") != "plan_only":
        return False, "alignment_plan_mode_not_plan_only"
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return False, "alignment_plan_jobs_not_list"
    return True, f"alignment_jobs={len(jobs)}"


def _asr(payload: dict[str, Any]) -> tuple[bool, str]:
    if str(payload.get("backend") or "") != "faster_whisper":
        return False, "unsupported_asr_backend"
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return False, "asr_jobs_not_list"
    return True, f"asr_jobs={len(jobs)}"


def _forced_source(payload: dict[str, Any]) -> tuple[bool, str]:
    if str(payload.get("backend") or "") != "external_forced_aligner":
        return False, "unsupported_forced_backend"
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return False, "forced_jobs_not_list"
    return True, f"forced_source_jobs={len(jobs)}"


def _forced_mix(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("mode") != "forced_alignment_mix_projection":
        return False, "forced_mix_mode_invalid"
    if payload.get("primary_timing_authority") != "source_to_mix_only":
        return False, "forced_mix_primary_authority_invalid"
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return False, "forced_mix_jobs_not_list"
    projected = sum(
        isinstance(row, dict) and row.get("projection_status") == "projected"
        for row in jobs
    )
    unprojectable = sum(
        isinstance(row, dict) and row.get("projection_status") == "unprojectable"
        for row in jobs
    )
    return True, f"projected={projected};unprojectable={unprojectable}"


def _fusion(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("mode") != "shadow_only":
        return False, "fusion_mode_not_shadow_only"
    if payload.get("policy_calibrated") is not False:
        return False, "fusion_policy_must_remain_uncalibrated"
    if payload.get("release_gate_eligible") is not False:
        return False, "fusion_must_not_be_release_gate_eligible"
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return False, "fusion_lines_not_list"
    conflicts = sum(
        isinstance(row, dict) and row.get("shadow_level") == "CONFLICT"
        for row in lines
    )
    return True, f"fusion_lines={len(lines)};conflicts={conflicts}"


def _runtime_snapshot(payload: dict[str, Any]) -> tuple[bool, str]:
    try:
        identity = validate_runtime_snapshot(payload)
    except ValueError:
        return False, "runtime_snapshot_identity_or_content_invalid"
    return True, f"runtime_snapshot_bound={identity[:12]}"


def _dataset_summary(
    path: Path | None,
    split: str | None,
) -> tuple[dict[str, Any], dict[str, bool]]:
    readiness = {
        "metadata": False,
        "references": False,
        "predictions": False,
        "evaluation": False,
    }
    if path is None:
        return {
            "provided": False,
            "file": None,
            "valid": False,
            "detail": "not_provided",
        }, readiness
    resolved = path.resolve()
    if not resolved.is_file():
        return {
            "provided": True,
            "file": path.name,
            "valid": False,
            "detail": "file_not_found",
        }, readiness
    report = inspect_dataset_readiness(resolved, split=split)
    split_rows = report.get("splits") or {}
    selected = [split_rows[split]] if split is not None else list(split_rows.values())
    readiness["metadata"] = bool(report.get("metadata_ready"))
    readiness["references"] = bool(selected) and all(
        bool(row.get("reference_ready")) for row in selected
    )
    readiness["predictions"] = bool(selected) and all(
        bool(row.get("prediction_files_ready")) for row in selected
    )
    readiness["evaluation"] = bool(selected) and all(
        bool(row.get("evaluation_ready")) for row in selected
    )
    return {
        "provided": True,
        "file": path.name,
        "valid": True,
        "detail": "dataset_readiness_inspected",
        "dataset": str(report.get("dataset") or ""),
        "dataset_revision": str(report.get("dataset_revision") or ""),
        "split": split or "all",
        "case_count": sum(int(row.get("case_count") or 0) for row in selected),
        "readiness": readiness,
    }, readiness


def _safe_missing_requirement(value: str) -> str:
    """Redact executable paths from backend readiness diagnostics."""

    text = str(value)
    prefix = "command_not_found:"
    if not text.startswith(prefix):
        return text
    executable = text[len(prefix) :].replace("\\", "/")
    return prefix + (executable.rsplit("/", 1)[-1] or "<redacted>")


def _backend_report(
    *,
    inspect: bool,
    faster_whisper_model_id: str | None,
    whisperx_model_id: str | None,
    whisperx_align_model_id: str | None,
    external_forced_aligner_command: str | None,
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    capabilities = {capability.value: False for capability in BackendCapability}
    if not inspect:
        return [], capabilities
    statuses = inspect_backends(
        faster_whisper_model_id=faster_whisper_model_id,
        whisperx_model_id=whisperx_model_id,
        whisperx_align_model_id=whisperx_align_model_id,
        external_forced_aligner_command=external_forced_aligner_command,
    )
    rows: list[dict[str, Any]] = []
    for status in statuses:
        rows.append(
            {
                "backend_id": status.backend_id,
                "available": bool(status.available),
                "execution_ready": bool(status.execution_ready),
                "capabilities": list(status.capabilities),
                "missing_execution_requirements": [
                    _safe_missing_requirement(value)
                    for value in status.missing_execution_requirements
                ],
            }
        )
        if status.execution_ready:
            for capability in status.capabilities:
                capabilities[capability] = True
    return rows, capabilities


def _next_actions(
    stages: dict[str, dict[str, Any]],
    run_payload: dict[str, Any] | None,
) -> list[dict[str, str]]:
    if not stages["task"]["valid"]:
        return [
            {
                "action": "supply_task_manifest",
                "command": "python scripts/init_task.py ...",
            }
        ]
    if not stages["run"]["valid"]:
        return [
            {
                "action": "reconstruct_source_to_mix",
                "command": "python scripts/v4_run.py ...",
            }
        ]

    status = str((run_payload or {}).get("status") or "")
    if status == "review_required":
        return [
            {
                "action": "resolve_review",
                "command": "python scripts/v4_review.py template ...",
            }
        ]

    actions: list[dict[str, str]] = []
    if not stages["alignment_plan"]["valid"]:
        actions.append(
            {
                "action": "plan_auxiliary_evidence",
                "command": "python scripts/v4_plan_alignment.py ...",
            }
        )
    if stages["forced_source"]["valid"] and not stages["forced_mix"]["valid"]:
        actions.append(
            {
                "action": "project_forced_evidence_to_mix",
                "command": "python scripts/v4_project_forced_alignment.py ...",
            }
        )
    if any(stages[name]["valid"] for name in ("editor", "asr", "forced_mix")) and not stages["fusion"]["valid"]:
        actions.append(
            {
                "action": "build_shadow_fusion",
                "command": "python scripts/v4_fuse_evidence.py ...",
            }
        )
    if status in {"ready_for_render", "resolved", "materialized"}:
        actions.append(
            {
                "action": "render_authoritative_timeline",
                "command": "python scripts/v4_render.py ...",
            }
        )
    return actions or [
        {
            "action": "inspect_current_artifacts",
            "command": "python scripts/v4_doctor.py ...",
        }
    ]


def build_doctor_report(
    *,
    task_manifest: Path | None = None,
    dataset: Path | None = None,
    dataset_split: str | None = None,
    run: Path | None = None,
    editor_evidence: Path | None = None,
    alignment_plan: Path | None = None,
    asr_evidence: Path | None = None,
    forced_evidence: Path | None = None,
    forced_mix_evidence: Path | None = None,
    fusion: Path | None = None,
    runtime_snapshot: Path | None = None,
    inspect_backend_status: bool = True,
    faster_whisper_model_id: str | None = None,
    whisperx_model_id: str | None = None,
    whisperx_align_model_id: str | None = None,
    external_forced_aligner_command: str | None = None,
    requirements: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a machine-readable readiness report without exposing paths/text."""

    stages: dict[str, dict[str, Any]] = {}
    payloads: dict[str, dict[str, Any] | None] = {}
    specs = (
        ("task", task_manifest, "task manifest", _task),
        ("run", run, "v4 run", _run),
        ("editor", editor_evidence, "editor evidence", _editor),
        ("alignment_plan", alignment_plan, "alignment plan", _plan),
        ("asr", asr_evidence, "ASR evidence", _asr),
        ("forced_source", forced_evidence, "source forced evidence", _forced_source),
        ("forced_mix", forced_mix_evidence, "forced mix evidence", _forced_mix),
        ("fusion", fusion, "fusion evidence", _fusion),
        ("runtime_snapshot", runtime_snapshot, "runtime snapshot", _runtime_snapshot),
    )
    for name, path, label, validator in specs:
        stages[name], payloads[name] = _stage(path, label=label, validator=validator)

    dataset_report, dataset_ready = _dataset_summary(dataset, dataset_split)
    backends, capabilities = _backend_report(
        inspect=inspect_backend_status,
        faster_whisper_model_id=faster_whisper_model_id,
        whisperx_model_id=whisperx_model_id,
        whisperx_align_model_id=whisperx_align_model_id,
        external_forced_aligner_command=external_forced_aligner_command,
    )

    requirement_results: dict[str, bool] = {}
    for requirement in requirements:
        value = str(requirement)
        if value in stages:
            passed = bool(stages[value]["valid"])
        elif value.startswith("dataset:"):
            key = value.split(":", 1)[1]
            if key not in dataset_ready:
                raise DoctorError(f"unknown dataset requirement {value}")
            passed = dataset_ready[key]
        elif value.startswith("backend:"):
            key = value.split(":", 1)[1]
            if key not in capabilities:
                raise DoctorError(f"unknown backend requirement {value}")
            passed = capabilities[key]
        else:
            raise DoctorError(f"unknown doctor requirement {value}")
        requirement_results[value] = bool(passed)

    next_actions = _next_actions(stages, payloads["run"])
    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "mode": "read_only_diagnostic",
        "requirements": {
            "passed": all(requirement_results.values()) if requirement_results else True,
            "results": requirement_results,
        },
        "stages": stages,
        "dataset": dataset_report,
        "backends": backends,
        "backend_execution_ready_capabilities": capabilities,
        "recommended_next_action": next_actions[0],
        "next_actions": next_actions,
        "authority": {
            "canonical_text": "canonical_lyrics_only",
            "primary_timing": "source_to_mix_only",
            "auxiliary_evidence": "diagnostic_shadow_only_until_calibrated",
        },
        "privacy": "no raw lyric text, local absolute paths, backend resolved paths, or full commands are emitted",
        "accuracy_boundary": "backend discovery/readiness is not a singing-accuracy claim",
    }
