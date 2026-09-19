#!/usr/bin/env python3
"""Merge exact preselected ASR shards into one standard asr_evidence_local artifact.

This is an execution-layer transaction only. It does not select jobs, change ASR
settings, read human gold, or grant timing authority. Every selected job from the
frozen lock must appear exactly once across the supplied standard ASR shards.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    sha256_file,
    validate_artifact_output,
    validate_upstream_artifact,
)

LOCK_SCHEMA = "semantic-asr-selection-lock-1.0"
MERGE_POLICY_ID = "exact-frozen-asr-shard-union-1.0"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _validate_shard_artifact(path: Path, payload_path: Path, *, fingerprint: str) -> dict[str, Any]:
    artifact = _load(path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage="asr_evidence_local",
    )
    issues.extend(validate_artifact_output(artifact, role="asr_evidence", path=payload_path))
    if issues:
        raise ValueError("invalid ASR shard artifact: " + "; ".join(issues))
    return artifact


def merge_asr_shards(
    *,
    selection_lock: Path,
    shard_paths: list[Path],
    shard_artifact_paths: list[Path],
    out: Path,
    artifact_out: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(shard_paths) != len(shard_artifact_paths) or not shard_paths:
        raise ValueError("shard payload/artifact lists must be nonempty and paired")
    lock = _load(selection_lock)
    if lock.get("schema_version") != LOCK_SCHEMA:
        raise ValueError("unsupported ASR selection lock schema")
    if lock.get("asr_result_read") is not False:
        raise ValueError("ASR selection lock must be frozen before ASR results are read")
    selected_ids = lock.get("selected_job_ids")
    if not isinstance(selected_ids, list) or not selected_ids or any(not isinstance(value, str) or not value for value in selected_ids):
        raise ValueError("selection lock has invalid selected_job_ids")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("selection lock contains duplicate job ids")
    if int(lock.get("selected_job_count", -1)) != len(selected_ids):
        raise ValueError("selection lock selected_job_count mismatch")

    payloads = [_load(path) for path in shard_paths]
    fingerprints = {str(payload.get("task_fingerprint_sha256") or "") for payload in payloads}
    if len(fingerprints) != 1 or not next(iter(fingerprints)):
        raise ValueError("ASR shards do not share one task fingerprint")
    fingerprint = next(iter(fingerprints))
    artifacts = [
        _validate_shard_artifact(artifact_path, payload_path, fingerprint=fingerprint)
        for payload_path, artifact_path in zip(shard_paths, shard_artifact_paths)
    ]

    invariant_fields = (
        "schema_version",
        "backend",
        "algorithm_version",
        "task_fingerprint_sha256",
        "source_plan_artifact_id",
        "source_run_artifact_id",
        "mix_audio_sha256",
        "word_match_policy_id",
        "language_hint_policy_id",
        "composition_policy_id",
        "routing_policy_id",
        "pass_policy_ids",
    )
    first = payloads[0]
    for index, payload in enumerate(payloads, start=1):
        for field in invariant_fields:
            if payload.get(field) != first.get(field):
                raise ValueError(f"ASR shard {index} differs in invariant field {field}")
        if payload.get("config") != first.get("config"):
            raise ValueError(f"ASR shard {index} config differs")
        if payload.get("model_loaded") is not True:
            raise ValueError(f"ASR shard {index} did not load its model")
        jobs = payload.get("jobs")
        ids = payload.get("selected_job_ids")
        if not isinstance(jobs, list) or not isinstance(ids, list):
            raise ValueError(f"ASR shard {index} has invalid jobs/selected_job_ids")
        if int(payload.get("job_count", -1)) != len(jobs) or len(ids) != len(jobs):
            raise ValueError(f"ASR shard {index} job counts do not reconcile")
        row_ids = [str(row.get("job_id") or "") for row in jobs if isinstance(row, dict)]
        if row_ids != [str(value) for value in ids] or any(not value for value in row_ids):
            raise ValueError(f"ASR shard {index} job ordering/identity mismatch")

    job_by_id: dict[str, dict[str, Any]] = {}
    shard_for_job: dict[str, int] = {}
    for shard_index, payload in enumerate(payloads, start=1):
        for row in payload["jobs"]:
            job_id = str(row["job_id"])
            if job_id in job_by_id:
                raise ValueError(f"ASR job appears in more than one shard: {job_id}")
            job_by_id[job_id] = dict(row)
            shard_for_job[job_id] = shard_index
    selected_set = set(selected_ids)
    if set(job_by_id) != selected_set:
        missing = sorted(selected_set - set(job_by_id))
        extra = sorted(set(job_by_id) - selected_set)
        raise ValueError(f"ASR shard union differs from frozen lock: missing={len(missing)} extra={len(extra)}")

    merged_jobs = [job_by_id[job_id] for job_id in selected_ids]
    merged: dict[str, Any] = {
        key: first.get(key)
        for key in invariant_fields
        if first.get(key) is not None
    }
    merged.update(
        {
            "backend": first.get("backend"),
            "execution_strategy": "frozen_selection_shard_union_v1",
            "config": dict(first.get("config") or {}),
            "model_loaded": True,
            "job_count": len(merged_jobs),
            "jobs": merged_jobs,
            "selected_job_ids": list(selected_ids),
            "privacy": first.get("privacy"),
            "shard_merge": {
                "policy_id": MERGE_POLICY_ID,
                "selection_lock_sha256": sha256_file(selection_lock),
                "selection_plan_sha256": str(lock.get("plan_sha256") or ""),
                "shard_count": len(payloads),
                "shard_artifact_ids": [str(artifact["artifact_id"]) for artifact in artifacts],
                "job_shard_index": {job_id: shard_for_job[job_id] for job_id in selected_ids},
                "gold_read": False,
            },
        }
    )
    atomic_write_json(out, merged)

    source_run_artifact_id = str(first.get("source_run_artifact_id") or "")
    source_plan_artifact_id = str(first.get("source_plan_artifact_id") or "")
    if not source_run_artifact_id or not source_plan_artifact_id:
        raise ValueError("ASR shards are missing source run/plan artifact identity")
    upstream_ids = {
        source_run_artifact_id,
        source_plan_artifact_id,
        *(str(artifact["artifact_id"]) for artifact in artifacts),
    }
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=fingerprint,
        stage="asr_evidence_local",
        algorithm_version=__version__,
        outputs=(("asr_evidence", out),),
        normalized_config={
            "backend": first.get("backend"),
            **dict(first.get("config") or {}),
            "composition_policy_id": first.get("composition_policy_id"),
            "routing_policy_id": first.get("routing_policy_id"),
            "word_match_policy_id": first.get("word_match_policy_id"),
            "language_hint_policy_id": first.get("language_hint_policy_id"),
            "pass_policy_ids": first.get("pass_policy_ids"),
            "execution_strategy": "frozen_selection_shard_union_v1",
            "source_plan_artifact_id": source_plan_artifact_id,
            "source_run_artifact_id": source_run_artifact_id,
            "mix_audio_sha256": first.get("mix_audio_sha256"),
            "merge_policy_id": MERGE_POLICY_ID,
            "selection_lock_sha256": sha256_file(selection_lock),
            "shard_artifact_ids": [str(artifact["artifact_id"]) for artifact in artifacts],
        },
        upstream_artifact_ids=tuple(sorted(upstream_ids)),
        evidence={
            "backend": first.get("backend"),
            "job_count": len(merged_jobs),
            "raw_private_text_included": bool((first.get("config") or {}).get("include_private_text")),
            "canonical_text_authority_unchanged": True,
            "primary_timing_authority_unchanged": True,
            "frozen_selection_exact_union": True,
            "shard_count": len(payloads),
        },
    )
    atomic_write_json(artifact_out, artifact)
    return merged, artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-lock", required=True, type=Path)
    parser.add_argument("--shard", action="append", required=True, type=Path)
    parser.add_argument("--shard-artifact", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    args = parser.parse_args()
    try:
        merged, artifact = merge_asr_shards(
            selection_lock=args.selection_lock,
            shard_paths=list(args.shard),
            shard_artifact_paths=list(args.shard_artifact),
            out=args.out,
            artifact_out=args.artifact_out,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": "merged",
        "jobs": merged["job_count"],
        "shards": merged["shard_merge"]["shard_count"],
        "artifact_id": artifact["artifact_id"],
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
