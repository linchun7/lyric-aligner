#!/usr/bin/env python3
"""Run one versioned direct-final-mix backend against outer or internal boundary jobs.

This command emits raw, explicitly uncalibrated evidence only.  It never mutates
subtitle timing and never fabricates calibration claims.  Production authority is
attached later from an exact human-gold calibration scope.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.alignment.batch_executor import (
    BatchAlignmentExecutionError,
    ExternalBatchAlignmentConfig,
    execute_external_alignment_batch,
)
from lyric_aligner.alignment.boundary_executor import (
    ExternalBoundaryAlignmentConfig,
    execute_external_boundary_alignment_jobs,
)
from lyric_aligner.alignment.internal_executor import (
    ExternalInternalAlignmentConfig,
    execute_external_internal_alignment_jobs,
)
from lyric_aligner.alignment.production_backend_profiles import (
    PRODUCTION_BOUNDARY_BACKEND_PROFILES,
    get_production_boundary_backend_profile,
)


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError("alignment backend evidence output must be a new path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


_HUBERTFA_ITEM_LOCAL_BATCH_FAILURE = "No duplicate groups"
_HUBERTFA_ITEM_LOCAL_UNALIGNED_REASON = "backend_inference_unavailable:no_duplicate_groups"


def execute_batch_with_known_item_isolation(
    *,
    plan: Mapping[str, Any],
    mode: str,
    final_audio_path: Path,
    final_audio_sha256: str,
    config: ExternalBatchAlignmentConfig,
    selected_boundary_ids: list[str] | None,
    execute_batch=execute_external_alignment_batch,
) -> dict[str, Any]:
    """Preserve the calibrated batch contract while isolating one known HuBERTFA item miss.

    The HuBERTFA batch sidecar can abort a whole process when one sample's padded
    passes have no duplicate phoneme group.  This orchestration layer is outside
    the calibrated observer contract.  It retries the unchanged batch adapter on
    successively smaller subsets and converts only a singleton that reproduces the
    exact known failure into an unaligned/no-point record.  Unknown failures remain
    fatal and all successful predictions still originate from the calibrated batch
    adapter/model identity.
    """

    jobs = plan.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("alignment plan jobs must be a list")
    plan_order: list[str] = []
    job_by_id: dict[str, Mapping[str, Any]] = {}
    for job in jobs:
        if not isinstance(job, Mapping):
            raise ValueError("alignment plan job must be an object")
        boundary_id = str(job.get("boundary_id") or "").strip()
        if not boundary_id or boundary_id in job_by_id:
            raise ValueError("alignment plan boundary IDs must be unique/non-empty")
        plan_order.append(boundary_id)
        job_by_id[boundary_id] = job

    if selected_boundary_ids is None:
        requested_ids = list(plan_order)
    else:
        requested_set = {str(value or "").strip() for value in selected_boundary_ids}
        if "" in requested_set or len(requested_set) != len(selected_boundary_ids):
            raise ValueError("selected boundary IDs must be unique/non-empty")
        unknown = sorted(requested_set - set(job_by_id))
        if unknown:
            raise ValueError("selected boundary is not in plan")
        requested_ids = [boundary_id for boundary_id in plan_order if boundary_id in requested_set]

    try:
        return execute_batch(
            plan=plan,
            mode=mode,
            final_audio_path=final_audio_path,
            final_audio_sha256=final_audio_sha256,
            config=config,
            selected_boundary_ids=requested_ids,
        )
    except BatchAlignmentExecutionError as exc:
        if _HUBERTFA_ITEM_LOCAL_BATCH_FAILURE not in str(exc):
            raise

    base = execute_batch(
        plan=plan,
        mode=mode,
        final_audio_path=final_audio_path,
        final_audio_sha256=final_audio_sha256,
        config=config,
        selected_boundary_ids=[],
    )
    merged_by_id: dict[str, dict[str, Any]] = {}
    isolated_failures: list[str] = []
    if len(requested_ids) == 1:
        boundary_id = requested_ids[0]
        job = job_by_id[boundary_id]
        merged_by_id[boundary_id] = {
            "boundary_id": boundary_id,
            "cue_number": int(job["cue_number"]),
            "boundary_kind": str(job["boundary_kind"]),
            "status": "unaligned",
            "reason": _HUBERTFA_ITEM_LOCAL_UNALIGNED_REASON,
            "point": None,
        }
        isolated_failures.append(boundary_id)
        pending: list[list[str]] = []
    elif requested_ids:
        midpoint = len(requested_ids) // 2
        pending = [requested_ids[:midpoint], requested_ids[midpoint:]]
    else:
        pending = []
    while pending:
        subset = pending.pop(0)
        try:
            partial = execute_batch(
                plan=plan,
                mode=mode,
                final_audio_path=final_audio_path,
                final_audio_sha256=final_audio_sha256,
                config=config,
                selected_boundary_ids=subset,
            )
        except BatchAlignmentExecutionError as exc:
            if _HUBERTFA_ITEM_LOCAL_BATCH_FAILURE not in str(exc):
                raise
            if len(subset) == 1:
                boundary_id = subset[0]
                job = job_by_id[boundary_id]
                merged_by_id[boundary_id] = {
                    "boundary_id": boundary_id,
                    "cue_number": int(job["cue_number"]),
                    "boundary_kind": str(job["boundary_kind"]),
                    "status": "unaligned",
                    "reason": _HUBERTFA_ITEM_LOCAL_UNALIGNED_REASON,
                    "point": None,
                }
                isolated_failures.append(boundary_id)
                continue
            midpoint = len(subset) // 2
            pending.insert(0, subset[midpoint:])
            pending.insert(0, subset[:midpoint])
            continue

        partial_jobs = partial.get("jobs")
        if not isinstance(partial_jobs, list):
            raise ValueError("batch isolation partial run has no jobs list")
        actual_ids = {str(row.get("boundary_id") or "") for row in partial_jobs if isinstance(row, Mapping)}
        if actual_ids != set(subset) or len(partial_jobs) != len(subset):
            raise ValueError("batch isolation partial run does not exactly cover its requested subset")
        for row in partial_jobs:
            boundary_id = str(row["boundary_id"])
            if boundary_id in merged_by_id:
                raise ValueError("batch isolation produced a duplicate boundary result")
            merged_by_id[boundary_id] = dict(row)

    if set(merged_by_id) != set(requested_ids):
        raise ValueError("batch isolation did not account for every requested boundary")
    ordered_jobs = [merged_by_id[boundary_id] for boundary_id in requested_ids]
    aligned_count = sum(str(row.get("status") or "") == "aligned" for row in ordered_jobs)
    unaligned_count = len(ordered_jobs) - aligned_count
    return {
        **dict(base),
        "command_invoked": bool(requested_ids),
        "job_count": len(ordered_jobs),
        "aligned_job_count": aligned_count,
        "unaligned_job_count": unaligned_count,
        "jobs": ordered_jobs,
        "batch_item_isolation_applied": bool(isolated_failures),
        "isolated_unaligned_boundary_ids": isolated_failures,
        "privacy": "canonical text exists only in ephemeral local batch requests; persisted run stores lexical identity, timing, backend provenance, and per-job alignment status",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("outer", "internal"), required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument(
        "--backend-profile",
        choices=tuple(sorted(PRODUCTION_BOUNDARY_BACKEND_PROFILES)),
        required=True,
    )
    parser.add_argument("--boundary-id", action="append", dest="boundary_ids")
    parser.add_argument("--execution-strategy", choices=("batch", "single"), default="batch")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    plan = load_json(args.plan, label="alignment plan")
    plan_audio_sha = str(plan.get("final_audio_sha256") or "").strip()
    if len(plan_audio_sha) != 64:
        raise ValueError("alignment plan does not bind a final audio SHA-256")
    resolved = get_production_boundary_backend_profile(args.backend_profile).resolve(
        REPOSITORY_ROOT,
        language=str(args.language).strip().lower(),
    )

    if args.execution_strategy == "batch":
        config = ExternalBatchAlignmentConfig(
            command=resolved["batch_command"],
            family=resolved["family"],
            correlation_group=resolved["correlation_group"],
            backend_id=resolved["backend_id"],
            backend_version=resolved["backend_version"],
            model_id=resolved["model_id"],
            model_revision=resolved["model_revision"],
            language=resolved["language"],
            backend_profile_id=resolved["profile_id"],
            implementation_revision=resolved["implementation_revision"],
            adapter_contract_revision=resolved["adapter_contract_revision"],
            window_policy_id=resolved["window_policy_id"],
            timeout_seconds=args.timeout_seconds,
        )
        if resolved["profile_id"] == "hubertfa_mandarin_v1":
            artifact = execute_batch_with_known_item_isolation(
                plan=plan,
                mode=args.mode,
                final_audio_path=args.audio,
                final_audio_sha256=plan_audio_sha,
                config=config,
                selected_boundary_ids=args.boundary_ids,
            )
        else:
            artifact = execute_external_alignment_batch(
                plan=plan,
                mode=args.mode,
                final_audio_path=args.audio,
                final_audio_sha256=plan_audio_sha,
                config=config,
                selected_boundary_ids=args.boundary_ids,
            )
    elif args.mode == "outer":
        config = ExternalBoundaryAlignmentConfig(
            command=resolved["boundary_command"],
            family=resolved["family"],
            correlation_group=resolved["correlation_group"],
            backend_id=resolved["backend_id"],
            backend_version=resolved["backend_version"],
            model_id=resolved["model_id"],
            model_revision=resolved["model_revision"],
            language=resolved["language"],
            backend_profile_id=resolved["profile_id"],
            implementation_revision=resolved["implementation_revision"],
            adapter_contract_revision=resolved["adapter_contract_revision"],
            window_policy_id=resolved["window_policy_id"],
            timeout_seconds=args.timeout_seconds,
        )
        artifact = execute_external_boundary_alignment_jobs(
            plan=plan,
            final_audio_path=args.audio,
            final_audio_sha256=plan_audio_sha,
            config=config,
            selected_boundary_ids=args.boundary_ids,
        )
    else:
        config = ExternalInternalAlignmentConfig(
            command=resolved["internal_command"],
            family=resolved["family"],
            correlation_group=resolved["correlation_group"],
            backend_id=resolved["backend_id"],
            backend_version=resolved["backend_version"],
            model_id=resolved["model_id"],
            model_revision=resolved["model_revision"],
            language=resolved["language"],
            backend_profile_id=resolved["profile_id"],
            implementation_revision=resolved["implementation_revision"],
            adapter_contract_revision=resolved["adapter_contract_revision"],
            window_policy_id=resolved["window_policy_id"],
            timeout_seconds=args.timeout_seconds,
        )
        artifact = execute_external_internal_alignment_jobs(
            plan=plan,
            final_audio_path=args.audio,
            final_audio_sha256=plan_audio_sha,
            config=config,
            selected_boundary_ids=args.boundary_ids,
        )

    artifact = dict(artifact)
    artifact["backend_profile_id"] = resolved["profile_id"]
    artifact["profile_model_revision"] = resolved["model_revision"]
    artifact["automatic_mutation_allowed"] = False
    atomic_json(args.out, artifact)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "mode": args.mode,
                "backend_profile_id": resolved["profile_id"],
                "execution_strategy": args.execution_strategy,
                "job_count": artifact["job_count"],
                "aligned_job_count": artifact.get("aligned_job_count", artifact["job_count"]),
                "unaligned_job_count": artifact.get("unaligned_job_count", 0),
                "automatic_mutation_allowed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
