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
from lyric_aligner.config import calibration_overrides
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.pipeline.context import build_pipeline_context
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--left-coarse", required=True, type=Path)
    parser.add_argument("--left-artifact", required=True, type=Path)
    parser.add_argument("--right-coarse", required=True, type=Path)
    parser.add_argument("--right-artifact", required=True, type=Path)
    parser.add_argument(
        "--min-score",
        type=float,
        help="Experimental override; release is blocked until moved into the asset profile.",
    )
    parser.add_argument(
        "--min-margin",
        type=float,
        help="Experimental override; release is blocked until moved into the asset profile.",
    )
    parser.add_argument(
        "--min-overlap-seconds",
        type=float,
        help="Experimental override; release is blocked until moved into the asset profile.",
    )
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
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=track_assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        defaults = context.profile.transition
        min_score = defaults.min_score if args.min_score is None else args.min_score
        min_margin = defaults.min_margin if args.min_margin is None else args.min_margin
        min_overlap_seconds = (
            defaults.min_overlap_seconds
            if args.min_overlap_seconds is None
            else args.min_overlap_seconds
        )
        minimum_feature_agreement = defaults.minimum_feature_agreement
        merge_gap_seconds = defaults.merge_gap_seconds
        overrides = calibration_overrides(
            defaults,
            {
                "min_score": min_score,
                "min_margin": min_margin,
                "min_overlap_seconds": min_overlap_seconds,
                "minimum_feature_agreement": minimum_feature_agreement,
                "merge_gap_seconds": merge_gap_seconds,
            },
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
        ordered_ids = [
            item.occurrence_id
            for item in sorted(context.bindings, key=lambda item: item.ordinal)
        ]
        try:
            left_pos = ordered_ids.index(left_id)
            right_pos = ordered_ids.index(right_id)
        except ValueError as exc:
            raise ValueError(
                "coarse alignment occurrence is missing from track_assets"
            ) from exc
        if right_pos != left_pos + 1:
            raise ValueError("transition probe only accepts adjacent TrackOccurrences")

        left_binding = context.binding_by_occurrence_id[left_id]
        right_binding = context.binding_by_occurrence_id[right_id]
        expected_asset_id = context.asset_artifact.artifact_id
        for label, coarse_payload, binding in (
            ("left", left, left_binding),
            ("right", right, right_binding),
        ):
            if str(coarse_payload.get("upstream_asset_artifact_id")) != expected_asset_id:
                raise ValueError(
                    f"{label} coarse alignment came from a different asset artifact"
                )
            if str(coarse_payload.get("calibration_profile_id")) != context.calibration_profile_id:
                raise ValueError(
                    f"{label} coarse alignment came from a different calibration profile"
                )
            if str(coarse_payload.get("track_id")) != binding.track_id:
                raise ValueError(f"{label} coarse track_id differs from TrackAsset")
            if str(coarse_payload.get("canonical_selection_sha256")) != binding.canonical_selection_sha256:
                raise ValueError(
                    f"{label} coarse canonical selection differs from TrackAsset"
                )

        result = probe_adjacent_transition(
            left,
            right,
            min_fused_score=min_score,
            min_margin=min_margin,
            minimum_feature_agreement=minimum_feature_agreement,
            minimum_overlap_seconds=min_overlap_seconds,
            merge_gap_seconds=merge_gap_seconds,
        )
        payload = {
            "schema_version": "1.1",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "calibration_profile_version": context.calibration_profile_version,
            "calibration_profile_id": context.calibration_profile_id,
            "calibration_overrides": overrides,
            "left_occurrence_id": left_id,
            "right_occurrence_id": right_id,
            "left_track_id": left_binding.track_id,
            "right_track_id": right_binding.track_id,
            "result": result,
        }
        atomic_write_json(args.out, payload)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="transition_probe",
            algorithm_version=__version__,
            outputs=(("transition_probe", args.out),),
            normalized_config={
                **context.artifact_config(),
                "calibration_overrides": overrides,
                "min_score": min_score,
                "min_margin": min_margin,
                "min_overlap_seconds": min_overlap_seconds,
                "minimum_feature_agreement": minimum_feature_agreement,
                "merge_gap_seconds": merge_gap_seconds,
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
                "left_track_id": left_binding.track_id,
                "right_track_id": right_binding.track_id,
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
                "calibration_profile_id": context.calibration_profile_id,
                "calibration_override": bool(overrides),
                "artifact_id": artifact["artifact_id"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
