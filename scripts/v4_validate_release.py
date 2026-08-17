#!/usr/bin/env python3
"""Fail-closed v4 release guard for final SRT/audit/QA artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json, validate_upstream_artifact
from lyric_aligner.qa.final_integrity import FinalIntegrityError, build_release_artifact_manifest
from task_contract import load_task_manifest, verify_manifest_inputs


def _load_upstream_artifacts(paths: list[Path], *, fingerprint: str) -> tuple[tuple[str, ...], dict]:
    ids: list[str] = []
    profile_ids: set[str] = set()
    profile_versions: set[str] = set()
    algorithm_versions: set[str] = set()
    stages: list[str] = []
    unprofiled_overrides: list[dict] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        issues = validate_upstream_artifact(payload, expected_task_fingerprint=fingerprint)
        if issues:
            raise ValueError(f"invalid upstream artifact {path}: " + "; ".join(issues))
        ids.append(str(payload["artifact_id"]))
        stage = str(payload.get("stage", ""))
        stages.append(stage)
        version = str(payload.get("algorithm_version") or "").strip()
        if version:
            algorithm_versions.add(version)
        config = payload.get("normalized_config", {})
        profile_id = str(config.get("calibration_profile_id") or "").strip()
        profile_version = str(config.get("calibration_profile_version") or "").strip()
        if profile_id:
            profile_ids.add(profile_id)
        if profile_version:
            profile_versions.add(profile_version)
        overrides = config.get("calibration_overrides") or {}
        if overrides:
            unprofiled_overrides.append({"stage": stage, "overrides": overrides})
    if len(algorithm_versions) > 1:
        raise ValueError("release upstream artifacts use different v4 algorithm_version values")
    if len(profile_ids) > 1:
        raise ValueError("release upstream artifacts use different calibration_profile_id values")
    if len(profile_versions) > 1:
        raise ValueError("release upstream artifacts use different calibration_profile_version values")
    if unprofiled_overrides:
        raise ValueError(
            "release blocked: calibration CLI overrides are not a named profile; "
            "move them into a complete v4 profile and rerun all stages"
        )
    return tuple(ids), {
        "v4_upstream_algorithm_version": next(iter(algorithm_versions), ""),
        "calibration_profile_id": next(iter(profile_ids), ""),
        "calibration_profile_version": next(iter(profile_versions), ""),
        "upstream_stages": stages,
        "calibration_overrides": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
    parser.add_argument("--algorithm-version", required=True)
    parser.add_argument(
        "--upstream-artifact",
        type=Path,
        action="append",
        default=[],
        help="Repeat for asset/coarse/fine/transition artifacts to bind release lineage.",
    )
    parser.add_argument("--git-commit", default="")
    parser.add_argument("--out-manifest", required=True, type=Path)
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        issues = verify_manifest_inputs(args.task_manifest, task)
        if issues:
            raise ValueError("task manifest validation failed: " + "; ".join(issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        upstream_ids, upstream_metadata = _load_upstream_artifacts(
            args.upstream_artifact, fingerprint=fingerprint
        )
        manifest = build_release_artifact_manifest(
            final_srt=args.final_srt,
            audit_csv=args.report,
            qa_json=args.qa_json,
            task_fingerprint_sha256=fingerprint,
            algorithm_version=args.algorithm_version,
            git_commit=args.git_commit,
            normalized_config=upstream_metadata,
            upstream_artifact_ids=upstream_ids,
        )
        atomic_write_json(args.out_manifest, manifest)
    except (OSError, KeyError, ValueError, json.JSONDecodeError, FinalIntegrityError) as exc:
        parser.error(str(exc))

    print(json.dumps({
        "artifact_id": manifest["artifact_id"],
        "release_status": "ready",
        "upstream_artifact_count": len(upstream_ids),
        "v4_upstream_algorithm_version": upstream_metadata["v4_upstream_algorithm_version"],
        "calibration_profile_id": upstream_metadata["calibration_profile_id"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
