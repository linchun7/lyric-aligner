#!/usr/bin/env python3
"""Retarget one canonical occurrence from an independently rendered reference timeline."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    canonical_json_sha256,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.timeline.reference_retime import (
    ReferenceRetimeError,
    normalize_offset_segments,
    normalize_retained_segments,
    retime_reference_result,
    retime_reference_result_with_retained_segments,
)
from task_contract import load_task_manifest, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_artifact(
    artifact: dict,
    *,
    fingerprint: str,
    stage: str,
    role: str,
    output_path: Path,
) -> str:
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=output_path))
    if issues:
        raise ValueError(f"invalid {stage} artifact: " + "; ".join(issues))
    return str(artifact["artifact_id"])


def _audio_sha(task: dict) -> str:
    audio = task.get("inputs", {}).get("audio")
    if not isinstance(audio, dict) or not str(audio.get("sha256") or ""):
        raise ValueError("task manifest is missing audio sha256")
    return str(audio["sha256"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--reference-task-manifest", required=True, type=Path)
    parser.add_argument("--reference-timeline", required=True, type=Path)
    parser.add_argument("--reference-timeline-artifact", required=True, type=Path)
    parser.add_argument("--retime-spec", required=True, type=Path)
    parser.add_argument("--occurrence-id", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        target_task = load_task_manifest(args.task_manifest)
        target_issues = verify_manifest_inputs(args.task_manifest, target_task)
        if target_issues:
            raise ValueError("target task manifest validation failed: " + "; ".join(target_issues))
        reference_task = load_task_manifest(args.reference_task_manifest)
        reference_issues = verify_manifest_inputs(args.reference_task_manifest, reference_task)
        if reference_issues:
            raise ValueError("reference task manifest validation failed: " + "; ".join(reference_issues))
        target_fp = str(target_task["task_fingerprint_sha256"])
        reference_fp = str(reference_task["task_fingerprint_sha256"])

        protected_inputs = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=target_task,
            repository_root=REPOSITORY_ROOT,
        )
        protected_inputs.update(
            {
                "source_run": args.run,
                "source_run_artifact": args.run_artifact,
                "track_assets": args.track_assets,
                "asset_artifact": args.asset_artifact,
                "reference_task_manifest": args.reference_task_manifest,
                "reference_timeline": args.reference_timeline,
                "reference_timeline_artifact": args.reference_timeline_artifact,
                "retime_spec": args.retime_spec,
            }
        )
        validate_separate_artifact_paths(
            inputs=protected_inputs,
            outputs={"reference_retimed_run": args.out, "reference_retime_artifact": args.artifact_out},
        )

        source_run = _load(args.run)
        source_artifact = _load(args.run_artifact)
        source_run_id = _validate_artifact(
            source_artifact,
            fingerprint=target_fp,
            stage="overlap_recomposition",
            role="v4_recomposed_run",
            output_path=args.run,
        )
        source_config = source_artifact.get("normalized_config")
        if not isinstance(source_config, dict):
            raise ValueError("source overlap artifact has invalid normalized_config")
        source_review_artifact_id = str(source_config.get("source_review_artifact_id") or "")
        if not source_review_artifact_id:
            raise ValueError("source overlap artifact is missing source_review_artifact_id")
        if source_run.get("algorithm_version") != __version__:
            raise ValueError("source run algorithm version mismatch")
        if source_run.get("task_fingerprint_sha256") != target_fp:
            raise ValueError("source run belongs to another target task")
        if source_run.get("status") != "ready_for_render" or source_run.get("issues") not in ([], None):
            raise ValueError("reference retime requires a fully resolved overlap-recomposition run")
        if source_run.get("legacy_fallback_used") is not False:
            raise ValueError("reference retime refuses legacy fallback")

        track_assets = _load(args.track_assets)
        asset_artifact = _load(args.asset_artifact)
        asset_id = _validate_artifact(
            asset_artifact,
            fingerprint=target_fp,
            stage="asset_resolution",
            role="track_assets",
            output_path=args.track_assets,
        )
        context = build_pipeline_context(
            expected_task_fingerprint=target_fp,
            track_assets_payload=track_assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        occurrence_id = str(args.occurrence_id).strip()
        binding = context.binding_by_occurrence_id.get(occurrence_id)
        if binding is None:
            raise ValueError("reference-retime occurrence is not present in target TrackAssets")

        occurrence_rows = source_run.get("occurrences")
        if not isinstance(occurrence_rows, list):
            raise ValueError("source run occurrences must be a list")
        source_occurrence = next(
            (row for row in occurrence_rows if isinstance(row, dict) and row.get("occurrence_id") == occurrence_id),
            None,
        )
        if source_occurrence is None:
            raise ValueError("reference-retime occurrence is missing from source run")
        source_timeline_path = Path(str(source_occurrence.get("timeline_path") or ""))
        source_timeline_artifact_path = Path(str(source_occurrence.get("timeline_artifact_path") or ""))
        source_timeline = _load(source_timeline_path)
        source_timeline_artifact = _load(source_timeline_artifact_path)
        source_timeline_stage = str(source_occurrence.get("timeline_stage") or "")
        if source_timeline_stage not in {"canonical_timeline_projection", "overlap_timeline_recomposition"}:
            raise ValueError("reference retime only accepts primary/overlap canonical source timelines")
        source_timeline_id = _validate_artifact(
            source_timeline_artifact,
            fingerprint=target_fp,
            stage=source_timeline_stage,
            role="canonical_timeline",
            output_path=source_timeline_path,
        )
        source_result = source_timeline.get("result")
        if not isinstance(source_result, dict):
            raise ValueError("source target timeline has invalid result")
        window = source_result.get("window")
        if not isinstance(window, dict):
            raise ValueError("source target timeline has no authoritative window")
        window_start = int(window["start_ms"])
        window_end = int(window["end_ms"])

        reference_timeline = _load(args.reference_timeline)
        reference_artifact = _load(args.reference_timeline_artifact)
        reference_timeline_id = _validate_artifact(
            reference_artifact,
            fingerprint=reference_fp,
            stage="canonical_timeline_projection",
            role="canonical_timeline",
            output_path=args.reference_timeline,
        )
        reference_result = reference_timeline.get("result")
        if not isinstance(reference_result, dict):
            raise ValueError("reference timeline has invalid result")
        for key, expected in (
            ("occurrence_id", binding.occurrence_id),
            ("track_id", binding.track_id),
            ("canonical_selection_sha256", binding.canonical_selection_sha256),
        ):
            if str(reference_result.get(key) or "") != str(expected):
                raise ValueError(f"reference timeline {key} differs from target binding")
        if int(reference_result.get("ordinal", -1)) != binding.ordinal:
            raise ValueError("reference timeline ordinal differs from target binding")

        spec = _load(args.retime_spec)
        expected_spec = {
            "target_task_fingerprint_sha256": target_fp,
            "reference_task_fingerprint_sha256": reference_fp,
            "occurrence_id": occurrence_id,
            "canonical_selection_sha256": binding.canonical_selection_sha256,
            "target_audio_sha256": _audio_sha(target_task),
            "reference_audio_sha256": _audio_sha(reference_task),
        }
        for key, expected in expected_spec.items():
            if str(spec.get(key) or "") != str(expected):
                raise ValueError(f"reference retime spec {key} mismatch")
        has_offset_segments = "segments" in spec
        has_retained_segments = "retained_segments" in spec
        if has_offset_segments == has_retained_segments:
            raise ValueError(
                "reference retime spec must contain exactly one of segments or retained_segments"
            )
        if has_retained_segments:
            retime_mode = "retained_segments"
            retime_map = normalize_retained_segments(spec.get("retained_segments"))
            retime_config = {"retained_segments": list(retime_map)}
        else:
            retime_mode = "offset_segments"
            retime_map = normalize_offset_segments(spec.get("segments"))
            retime_config = {"segments": list(retime_map)}
        spec_sha = canonical_json_sha256(spec)

        if retime_mode == "retained_segments":
            retimed_result = retime_reference_result_with_retained_segments(
                reference_result,
                target_window_start_ms=window_start,
                target_window_end_ms=window_end,
                retained_segments=retime_map,
            )
        else:
            retimed_result = retime_reference_result(
                reference_result,
                target_window_start_ms=window_start,
                target_window_end_ms=window_end,
                segments=retime_map,
            )
        args.out_dir.mkdir(parents=True, exist_ok=True)
        timeline_path = args.out_dir / f"{occurrence_id}.reference-retimed.timeline.json"
        timeline_payload = {
            "schema_version": "1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": target_fp,
            "calibration_profile_version": context.calibration_profile_version,
            "calibration_profile_id": context.calibration_profile_id,
            "occurrence_id": occurrence_id,
            "track_id": binding.track_id,
            "mapping_source": "reference_timeline_retime",
            "source_target_timeline_artifact_id": source_timeline_id,
            "source_reference_timeline_artifact_id": reference_timeline_id,
            "reference_task_fingerprint_sha256": reference_fp,
            "retime_spec_sha256": spec_sha,
            "result": retimed_result,
        }
        atomic_write_json(timeline_path, timeline_payload)
        timeline_artifact_path = args.out_dir / f"{occurrence_id}.reference-retimed.timeline.artifact.json"
        timeline_artifact = build_artifact_manifest(
            task_fingerprint_sha256=target_fp,
            stage="reference_timeline_retime",
            algorithm_version=__version__,
            outputs=(("canonical_timeline", timeline_path),),
            normalized_config={
                **context.artifact_config(),
                "reference_task_fingerprint_sha256": reference_fp,
                "reference_timeline_artifact_id": reference_timeline_id,
                "source_target_timeline_artifact_id": source_timeline_id,
                "retime_spec_sha256": spec_sha,
                "retime_mode": retime_mode,
                **retime_config,
            },
            producer=({"git_commit": args.git_commit} if args.git_commit else {}),
            upstream_artifact_ids=(asset_id, source_run_id, source_timeline_id, reference_timeline_id),
            evidence={
                "occurrence_id": occurrence_id,
                "reference_retimed": True,
                "reference_retime_mode": retime_mode,
                "line_count": int(retimed_result["line_count"]),
                "target_window": retimed_result["window"],
            },
        )
        atomic_write_json(timeline_artifact_path, timeline_artifact)
        timeline_id = str(timeline_artifact["artifact_id"])

        retimed_run = deepcopy(source_run)
        for row in retimed_run["occurrences"]:
            if row.get("occurrence_id") == occurrence_id:
                row["timeline_path"] = str(timeline_path)
                row["timeline_artifact_path"] = str(timeline_artifact_path)
                row["timeline_stage"] = "reference_timeline_retime"
                row["mapping_source"] = "reference_timeline_retime"
                row["reference_retimed"] = True
                break
        retimed_run["reference_retime"] = {
            "source_run_artifact_id": source_run_id,
            "source_run_stage": "overlap_recomposition",
            "source_review_artifact_id": source_review_artifact_id,
            "reference_task_fingerprint_sha256": reference_fp,
            "reference_timeline_artifact_id": reference_timeline_id,
            "retime_spec_sha256": spec_sha,
            "retimed_occurrence_count": 1,
            "timeline_artifact_ids": [timeline_id],
        }
        atomic_write_json(args.out, retimed_run)

        upstreams = {str(value) for value in source_artifact.get("upstream_artifact_ids", [])}
        upstreams.update((source_run_id, asset_id, source_timeline_id, reference_timeline_id, timeline_id))
        run_artifact = build_artifact_manifest(
            task_fingerprint_sha256=target_fp,
            stage="reference_retime",
            algorithm_version=__version__,
            outputs=(("v4_reference_retimed_run", args.out),),
            normalized_config={
                **context.artifact_config(),
                "source_run_artifact_id": source_run_id,
                "source_run_stage": "overlap_recomposition",
                "source_review_artifact_id": source_review_artifact_id,
                "reference_task_fingerprint_sha256": reference_fp,
                "reference_timeline_artifact_id": reference_timeline_id,
                "retime_spec_sha256": spec_sha,
                "retimed_occurrence_count": 1,
            },
            producer=({"git_commit": args.git_commit} if args.git_commit else {}),
            upstream_artifact_ids=tuple(sorted(upstreams)),
            evidence={
                "reference_retimed": True,
                "occurrence_id": occurrence_id,
                "timeline_artifact_id": timeline_id,
                "reference_timeline_artifact_id": reference_timeline_id,
            },
        )
        atomic_write_json(args.artifact_out, run_artifact)
        print(json.dumps({"status": "ready_for_render", "occurrence_id": occurrence_id, "timeline_artifact_id": timeline_id, "artifact_id": run_artifact["artifact_id"]}, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError, ReferenceRetimeError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
