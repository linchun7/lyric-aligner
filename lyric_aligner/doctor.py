"""Privacy-safe production readiness and resume diagnostics for v4.

The doctor is deliberately observational: it reads local artifacts, validates
basic stage contracts, inspects optional backend availability without loading
models, and recommends the next production action.  It never mutates task or
evidence artifacts and never treats backend discovery as an accuracy claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from lyric_aligner.alignment.backends import BackendCapability, inspect_backends
from lyric_aligner.evaluation.readiness import inspect_dataset_readiness


DOCTOR_SCHEMA_VERSION = "1.0"


class DoctorError(ValueError):
    """Raised when an explicitly supplied doctor input is malformed."""


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise DoctorError(f"{label} must be a JSON object")
    return payload


def _file_label(path: Path | None) -> str | None:
    return path.name if path is not None else None


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
            "file": _file_label(path),
            "exists": False,
            "valid": False,
            "detail": "file_not_found",
        }, None
    payload = _load_json(resolved, label=label)
    valid, detail = validator(payload)
    return {
        "provided": True,
        "file": _file_label(path),
        "exists": True,
        "valid": bool(valid),
        "detail": str(detail),
    }, payload


def _object(payload: dict[str, Any]) -> tuple[bool, str]:
    return True, "json_object"


def _task(payload: dict[str, Any]) -> tuple[bool, str]:
    tracks = payload.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return False, "task_manifest_missing_tracks"
    return True, "task_manifest_shape_ok"


def _run(payload: dict[str, Any]) -> tuple[bool, str]:
    stage = str(payload.get("stage") or "").strip()
    status = str(payload.get("status") or "").strip()
    if not stage:
        return False, "run_missing_stage"
    if not status:
        return False, "run_missing_status"
    return True, f"stage={stage};status={status}"


def _editor(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("mode") != "shadow_only":
        return False, "editor_mode_not_shadow_only"
    authority = payload.get("authority")
    if isinstance(authority, dict) and authority.get("automatic_timing_change_allowed") is True:
        return False, "editor_unexpected_timing_authority"
    return True, "editor_shadow_evidence"


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
    projected = sum(str(row.get("projection_status") or "") == "projected" for row in jobs if isinstance(row, dict))
    unprojectable = sum(str(row.get("projection_status") or "") == "unprojectable" for row in jobs if isinstance(row, dict))
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
    if str(payload.get("schema_version") or "") != "1.0":
        return False, "runtime_snapshot_schema_invalid"
    identity = str(payload.get("runtime_identity_sha256") or "")
    if len(identity) != 64:
        return False, "runtime_snapshot_identity_missing"
    return True, "runtime_snapshot_bound"


def _dataset_summary(path: Path | None, split: str | None) -> tuple[dict[str, Any], dict[str, bool]]:
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
            "file": _file_label(path),
            "valid": False,
            "detail": "file_not_found",
        }, readiness
    report = inspect_dataset_readiness(resolved, split=split)
    rows = report.get("splits") or {}
    selected = [rows[split]] if split is not None else list(rows.values())
    readiness["metadata"] = bool(report.get("metadata_ready"))
    readiness["references"] = bool(selected) and all(bool(row.get("reference_ready")) for row in selected)
    readiness["predictions"] = bool(selected) and all(bool(row.get("prediction_files_ready")) for row in selected)
    readiness["evaluation"] = bool(selected) and all(bool(row.get("evaluation_ready")) for row in selected)
    counts = {
        key: int(sum(int(row.get("case_count") or 0) for row in selected))
        if key == "case_count"
        else None
        for key in ("case_count",)
    }
    return {
        "provided": True,
        "file": _file_label(path),
        "valid": True,
        "detail": "dataset_readiness_inspected",
        "dataset": str(report.get("dataset") or ""),
        "dataset_revision": str(report.get("dataset_revision") or ""),
        "split": split or "all",
        "case_count": counts["case_count"],
        "readiness": readiness,
    }, readiness


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
                "missing_execution_requirements": list(status.missing_execution_requirements),
            }
        )
        if status.execution_ready:
            for capability in status.capabilities:
                capabilities[capability] = True
    return rows, capabilities


def _next_actions(stages: dict[str, dict[str, Any]], run_payload: dict[str, Any] | None) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if not stages["task"]["valid"]:
        actions.append({"action": "supply_task_manifest", "command": "python scripts/init_task.py ..."})
        return actions
    if not stages["run"]["valid"]:
        actions.append({"action": "reconstruct_source_to_mix", "command": "python scripts/v4_run.py ..."})
        return actions

    status = str((run_payload or {}).get("status") or "")
    if status == "review_required":
        actions.append({"action": "resolve_review", "command": "python scripts/v4_review.py template ..."})
        return actions

    if not stages["alignment_plan"]["valid"]:
        actions.append({"action": "plan_auxiliary_evidence", "command": "python scripts/v4_plan_alignment.py ..."})
    if stages["forced_source"]["valid"] and not stages["forced_mix"]["valid"]:
        actions.append({"action": "project_forced_evidence_to_mix", "command": "python scripts/v4_project_forced_alignment.py ..."})
    auxiliary_present = any(
        stages[name]["valid"] for name in ("editor", "asr", "forced_mix")
    )
    if auxiliary_present and not stages["fusion"]["valid"]:
        actions.append({"action": "build_shadow_fusion", "command": "python scripts/v4_fuse_evidence.py ..."})
    if status in {"ready_for_render", "resolved", "materialized"}:
        actions.append({"action": "render_authoritative_timeline", "command": "python scripts/v4_render.py ..."})
    if not actions:
        actions.append({"action": "inspect_current_artifacts", "command": "python scripts/v4_doctor.py ..."})
    return actions


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
    """Build a machine-readable readiness report without exposing local paths/text."""

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
    passed = all(requirement_results.values()) if requirement_results else True
    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "mode": "read_only_diagnostic",
        "requirements": {
            "passed": passed,
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
        "privacy": "no lyric text, local absolute paths, backend resolved paths, or full commands are emitted",
        "accuracy_boundary": "backend discovery/readiness is not a singing-accuracy claim",
    }
