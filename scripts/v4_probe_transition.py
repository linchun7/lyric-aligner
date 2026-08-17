#!/usr/bin/env python3
"""Probe one adjacent TrackOccurrence boundary for overlap evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.audio.transition import probe_adjacent_transition
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from task_contract import load_task_manifest, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_stage(
    *,
    payload_path: Path,
    artifact_path: Path,
    fingerprint: str,
    role: str,
    stage: str,
) -> tuple[dict, dict]:
    payload = _load(payload_path)
    if payload.get("task_fingerprint_sha256") != fingerprint:
        raise ValueError(f"{role} task fingerprint mismatch")
    if payload.get("algorithm_version") != __version__:
        raise ValueError(f"{role} algorithm version mismatch")
    artifact = _load(artifact_path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=payload_path))
    if issues:
        raise ValueError(f"invalid {role} artifact: " + "; ".join(issues))
    return payload, artifact


def _ensure_adjacent(track_assets: dict, left_id: str, right_id: str) -> None:
    ordered = sorted(track_assets.get("occurrences", []), key=lambda item: int(item["ordinal"]))
    ids = [str(item["occurrence_id"]) for item in ordered]
    try:
        left_pos = ids.index(left_id)
        right_pos = ids.index(right_id)
    except ValueError as exc:
        raise ValueError("coarse alignment occurrence is missing from track_assets") from exc
    if right_pos != left_pos + 1:
        raise ValueError("transition probe only accepts adjacent TrackOccurrences")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--left-coarse", required=True, type=Path)
    parser.add_argument("--left-artifact", required=True, type=Path)
    parser.add_argument("--right-coarse", required=True, type=Path)
    parser.add_argument("--right-artifact", required=True, type=Path)
    parser.add_argument("--min-score", type=float, default=0.72)
    parser.add_argument("--min-margin", type=float, default=0.02)
    parser.add_argument("--min-overlap-seconds", type=float, default=0.75)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        task_issues = verify_manifest_inputs(args.task_manifest, task)
        if task_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(task_issues))
        fingerprint = str(task["task_fingerprint_sha256"])

        track_assets, asset_artifact = _validate_stage(
            payload_path=args.track_assets,
            artifact_path=args.asset_artifact,
            fingerprint=fingerprint,
            role="track_assets",
            stage="asset_resolution",
        )
        left, left_artifact = _validate_stage(
            payload_path=args.left_coarse,
            artifact_path=args.left_artifact,
            fingerprint=fingerprint,
            role="coarse_alignment",
            stage="coarse_audio_alignment",
        )
        right, right_artifact = _validate_stage(
            payload_path=args.right_coarse,
            artifact_path=args.right_artifact,
            fingerprint=fingerprint,
            role="coarse_alignment",
            stage="coarse_audio_alignment",
        )
        left_id = str(left["occurrence_id"])
        right_id = str(right["occurrence_id"])
        _ensure_adjacent(track_assets, left_id, right_id)
        expected_asset_id = str(asset_artifact["artifact_id"])
        for label, payload in (("left", left), ("right", right)):
            if str(payload.get("upstream_asset_artifact_id")) != expected_asset_id:
                raise ValueError(f"{label} coarse alignment came from a different asset artifact")

        result = probe_adjacent_transition(
            left,
            right,
            min_fused_score=args.min_score,
            min_margin=args.min_margin,
            minimum_overlap_seconds=args.min_overlap_seconds,
        )
        payload = {
            "schema_version": "1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "left_occurrence_id": left_id,
            "right_occurrence_id": right_id,
            "result": result,
        }
        atomic_write_json(args.out, payload)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="transition_probe",
            algorithm_version=__version__,
            outputs=(("transition_probe", args.out),),
            normalized_config={
                "min_score": args.min_score,
                "min_margin": args.min_margin,
                "min_overlap_seconds": args.min_overlap_seconds,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(
                expected_asset_id,
                str(left_artifact["artifact_id"]),
                str(right_artifact["artifact_id"]),
            ),
            evidence={
                "left_occurrence_id": left_id,
                "right_occurrence_id": right_id,
                "status": result["status"],
                "blocked": result["blocked"],
                "overlap_candidate_count": len(result["overlap_candidates"]),
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": result["status"],
                "blocked": result["blocked"],
                "overlap_candidate_count": len(result["overlap_candidates"]),
                "artifact_id": artifact["artifact_id"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
