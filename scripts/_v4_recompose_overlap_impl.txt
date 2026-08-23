#!/usr/bin/env python3
"""Materialize confirmed cross-track overlap into dual canonical timelines."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.audio.fine_alignment import should_run_fine_alignment
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.timeline.overlap import (
    ConfirmedOverlapRegion,
    OverlapRecompositionError,
    clip_projected_result_to_region,
    merge_primary_with_overlap_lines,
    region_from_issue,
)
from lyric_aligner.timeline.projector import (
    ProjectionWindow,
    effective_timewarp,
    project_binding_timeline,
)
from task_contract import load_task_manifest, resolve_manifest_record, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "stage"


def _run(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"stage failed ({' '.join(command[:2])}): {detail}")
    if completed.stdout.strip():
        print(completed.stdout.strip(), file=sys.stderr)


def _validate_stage(
    *,
    payload_path: Path,
    artifact_path: Path,
    fingerprint: str,
    stage: str,
    role: str,
) -> tuple[dict, dict]:
    payload = _load(payload_path)
    artifact = _load(artifact_path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=payload_path))
    if issues:
        raise ValueError(f"invalid {stage} artifact: " + "; ".join(issues))
    if payload.get("task_fingerprint_sha256") != fingerprint:
        raise ValueError(f"{stage} payload belongs to another task")
    if payload.get("algorithm_version") != __version__:
        raise ValueError(f"{stage} payload algorithm version mismatch")
    return payload, artifact


def _transition_summary(run: dict, region: ConfirmedOverlapRegion) -> dict:
    transitions = run.get("transitions")
    if not isinstance(transitions, list):
        raise ValueError("reviewed run transitions must be a list")
    matches = [
        row
        for row in transitions
        if row.get("left_occurrence_id") == region.left_occurrence_id
        and row.get("right_occurrence_id") == region.right_occurrence_id
    ]
    if len(matches) != 1:
        raise ValueError("confirmed overlap does not map to exactly one transition summary")
    return matches[0]


def _validate_candidate(
    transition_payload: dict,
    region: ConfirmedOverlapRegion,
) -> None:
    result = transition_payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("transition payload has no result")
    candidates = list(result.get("overlap_candidates", [])) + list(
        result.get("uncertain_intervals", [])
    )
    matches = [
        item
        for item in candidates
        if str(item.get("candidate_id") or "") == region.candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("confirmed candidate_id does not map to exactly one transition candidate")
    candidate = matches[0]
    start_ms = int(round(float(candidate["start"]) * 1000.0))
    end_ms = int(round(float(candidate["end"]) * 1000.0))
    if (start_ms, end_ms) != (region.start_ms, region.end_ms):
        raise ValueError("confirmed overlap interval differs from transition evidence")
    result_pair = {
        str(result.get("left_occurrence_id") or ""),
        str(result.get("right_occurrence_id") or ""),
    }
    if result_pair != {region.left_occurrence_id, region.right_occurrence_id}:
        raise ValueError("confirmed overlap occurrence pair differs from transition evidence")


def _effective_boundary_mapping(
    *,
    task_manifest: Path,
    mix_audio: Path,
    track_assets: Path,
    asset_artifact: Path,
    coarse_path: Path,
    coarse_artifact_path: Path,
    out_dir: Path,
    side: str,
    git_commit: str,
    fingerprint: str,
    required_upstreams: set[str],
    expected_occurrence_id: str,
    expected_track_id: str,
    expected_canonical_selection_sha256: str,
    expected_asset_artifact_id: str,
) -> tuple[dict, list[str], str]:
    coarse, coarse_artifact = _validate_stage(
        payload_path=coarse_path,
        artifact_path=coarse_artifact_path,
        fingerprint=fingerprint,
        stage="coarse_audio_alignment",
        role="coarse_alignment",
    )
    coarse_artifact_id = str(coarse_artifact["artifact_id"])
    if coarse_artifact_id not in required_upstreams:
        raise ValueError("boundary coarse artifact is not upstream of reviewed run")
    if str(coarse.get("occurrence_id") or "") != expected_occurrence_id:
        raise ValueError(f"{side} boundary coarse occurrence identity mismatch")
    if str(coarse.get("track_id") or "") != expected_track_id:
        raise ValueError(f"{side} boundary coarse track identity mismatch")
    if (
        str(coarse.get("canonical_selection_sha256") or "")
        != expected_canonical_selection_sha256
    ):
        raise ValueError(f"{side} boundary coarse canonical selection mismatch")
    if str(coarse.get("upstream_asset_artifact_id") or "") != expected_asset_artifact_id:
        raise ValueError(f"{side} boundary coarse asset identity mismatch")
    coarse_upstreams = {
        str(value) for value in coarse_artifact.get("upstream_artifact_ids", [])
    }
    if expected_asset_artifact_id not in coarse_upstreams:
        raise ValueError(f"{side} boundary coarse artifact is not derived from TrackAssets")

    fine: dict | None = None
    fine_artifact_id = ""
    if should_run_fine_alignment(coarse):
        fine_path = out_dir / f"{side}.fine.json"
        fine_artifact_path = out_dir / f"{side}.fine.artifact.json"
        command = [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "v4_fine_align.py"),
            "--task-manifest",
            str(task_manifest),
            "--mix-audio",
            str(mix_audio),
            "--track-assets",
            str(track_assets),
            "--asset-artifact",
            str(asset_artifact),
            "--coarse",
            str(coarse_path),
            "--coarse-artifact",
            str(coarse_artifact_path),
            "--out",
            str(fine_path),
            "--artifact-out",
            str(fine_artifact_path),
        ]
        if git_commit:
            command.extend(["--git-commit", git_commit])
        _run(command)
        fine, fine_artifact = _validate_stage(
            payload_path=fine_path,
            artifact_path=fine_artifact_path,
            fingerprint=fingerprint,
            stage="fine_audio_alignment",
            role="fine_alignment",
        )
        fine_artifact_id = str(fine_artifact["artifact_id"])
        if str(fine.get("occurrence_id") or "") != expected_occurrence_id:
            raise ValueError(f"{side} boundary Fine occurrence identity mismatch")
        if str(fine.get("track_id") or "") != expected_track_id:
            raise ValueError(f"{side} boundary Fine track identity mismatch")
        if (
            str(fine.get("canonical_selection_sha256") or "")
            != expected_canonical_selection_sha256
        ):
            raise ValueError(f"{side} boundary Fine canonical selection mismatch")

    mapping, blocked, mapping_source = effective_timewarp(coarse, fine)
    if blocked:
        raise ValueError("confirmed overlap boundary mapping is still blocked after selective fine")
    upstream_ids = [coarse_artifact_id]
    if fine_artifact_id:
        upstream_ids.append(fine_artifact_id)
    return mapping, upstream_ids, mapping_source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
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
        mix_record = task["inputs"].get("audio")
        if mix_record is None:
            raise ValueError("task manifest has no mix audio")
        mix_audio = resolve_manifest_record(args.task_manifest, mix_record)

        reviewed_run, review_artifact = _validate_stage(
            payload_path=args.run,
            artifact_path=args.run_artifact,
            fingerprint=fingerprint,
            stage="review_resolution",
            role="v4_reviewed_run",
        )
        if reviewed_run.get("legacy_fallback_used") is not False:
            raise ValueError("overlap recomposition refuses legacy fallback")
        if reviewed_run.get("status") != "review_required":
            raise ValueError("overlap recomposition requires a review_required reviewed run")
        review_upstreams = {
            str(value) for value in review_artifact.get("upstream_artifact_ids", [])
        }

        track_assets, asset_artifact = _validate_stage(
            payload_path=args.track_assets,
            artifact_path=args.asset_artifact,
            fingerprint=fingerprint,
            stage="asset_resolution",
            role="track_assets",
        )
        asset_artifact_id = str(asset_artifact["artifact_id"])
        if asset_artifact_id not in review_upstreams:
            raise ValueError("TrackAsset artifact is not upstream of reviewed run")
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=track_assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        if reviewed_run.get("calibration_profile_id") != context.calibration_profile_id:
            raise ValueError("reviewed run calibration profile mismatch")

        confirmed_issues = [
            issue
            for issue in reviewed_run.get("issues", [])
            if isinstance(issue, dict)
            and issue.get("decision_action") == "confirmed_overlap"
            and bool(issue.get("requires_recomposition"))
        ]
        if not confirmed_issues:
            raise ValueError("reviewed run has no confirmed overlap requiring recomposition")
        regions = [region_from_issue(issue) for issue in confirmed_issues]
        if len({region.region_id for region in regions}) != len(regions):
            raise ValueError("reviewed run contains duplicate confirmed overlap regions")

        out_dir = args.out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        overlap_lines_by_occurrence: dict[str, list[dict]] = {}
        regions_by_occurrence: dict[str, list[ConfirmedOverlapRegion]] = {}
        provenance_by_occurrence: dict[str, set[str]] = {}
        processed_issue_ids: set[str] = set()
        all_new_upstreams: set[str] = set(review_upstreams)
        all_new_upstreams.add(str(review_artifact["artifact_id"]))

        for region in regions:
            transition = _transition_summary(reviewed_run, region)
            transition_path = Path(str(transition.get("transition_path") or ""))
            transition_artifact_path = Path(
                str(transition.get("transition_artifact_path") or "")
            )
            transition_payload, transition_artifact = _validate_stage(
                payload_path=transition_path,
                artifact_path=transition_artifact_path,
                fingerprint=fingerprint,
                stage="transition_probe",
                role="transition_probe",
            )
            transition_artifact_id = str(transition_artifact["artifact_id"])
            if transition_artifact_id not in review_upstreams:
                raise ValueError("transition artifact is not upstream of reviewed run")
            transition_upstreams = {
                str(value) for value in transition_artifact.get("upstream_artifact_ids", [])
            }
            if asset_artifact_id not in transition_upstreams:
                raise ValueError("transition artifact is not derived from supplied TrackAssets")
            _validate_candidate(transition_payload, region)
            all_new_upstreams.add(transition_artifact_id)

            region_dir = out_dir / _safe(region.candidate_id)
            region_dir.mkdir(parents=True, exist_ok=True)
            used_boundary_coarse_ids: set[str] = set()
            for side, occurrence_id in (
                ("left", region.left_occurrence_id),
                ("right", region.right_occurrence_id),
            ):
                coarse_path = Path(str(transition.get(f"{side}_coarse_path") or ""))
                coarse_artifact_path = Path(
                    str(transition.get(f"{side}_coarse_artifact_path") or "")
                )
                if not str(coarse_path) or not str(coarse_artifact_path):
                    raise ValueError("transition summary is missing boundary coarse provenance")
                binding = context.binding_by_occurrence_id.get(occurrence_id)
                if binding is None:
                    raise ValueError("confirmed overlap occurrence is missing from TrackAssets")
                mapping, mapping_upstreams, mapping_source = _effective_boundary_mapping(
                    task_manifest=args.task_manifest,
                    mix_audio=mix_audio,
                    track_assets=args.track_assets,
                    asset_artifact=args.asset_artifact,
                    coarse_path=coarse_path,
                    coarse_artifact_path=coarse_artifact_path,
                    out_dir=region_dir,
                    side=side,
                    git_commit=args.git_commit,
                    fingerprint=fingerprint,
                    required_upstreams=review_upstreams,
                    expected_occurrence_id=occurrence_id,
                    expected_track_id=binding.track_id,
                    expected_canonical_selection_sha256=(
                        binding.canonical_selection_sha256
                    ),
                    expected_asset_artifact_id=asset_artifact_id,
                )
                coarse_artifact_id = mapping_upstreams[0]
                if coarse_artifact_id not in transition_upstreams:
                    raise ValueError(
                        f"transition artifact is not upstream-bound to {side} boundary coarse"
                    )
                used_boundary_coarse_ids.add(coarse_artifact_id)
                projected = project_binding_timeline(
                    binding,
                    mapping,
                    window=ProjectionWindow(region.start_ms, region.end_ms),
                )
                clipped = clip_projected_result_to_region(projected, region)
                overlap_lines_by_occurrence.setdefault(occurrence_id, []).extend(clipped)
                regions_by_occurrence.setdefault(occurrence_id, []).append(region)
                provenance = provenance_by_occurrence.setdefault(occurrence_id, set())
                provenance.update(mapping_upstreams)
                provenance.add(transition_artifact_id)
                all_new_upstreams.update(mapping_upstreams)
                transition.setdefault("overlap_mapping_sources", {})[
                    region.candidate_id + ":" + side
                ] = mapping_source
            if len(used_boundary_coarse_ids) != 2:
                raise ValueError("confirmed overlap requires two distinct boundary coarse artifacts")
            processed_issue_ids.add(region.issue_id)

        recomposed_occurrences = deepcopy(reviewed_run.get("occurrences", []))
        if not isinstance(recomposed_occurrences, list):
            raise ValueError("reviewed run occurrences must be a list")
        new_timeline_artifact_ids: list[str] = []
        for occurrence in recomposed_occurrences:
            occurrence_id = str(occurrence.get("occurrence_id") or "")
            if occurrence_id not in overlap_lines_by_occurrence:
                continue
            timeline_path = Path(str(occurrence.get("timeline_path") or ""))
            timeline_artifact_path = Path(
                str(occurrence.get("timeline_artifact_path") or "")
            )
            timeline_stage = str(
                occurrence.get("timeline_stage") or "canonical_timeline_projection"
            )
            primary_timeline, primary_artifact = _validate_stage(
                payload_path=timeline_path,
                artifact_path=timeline_artifact_path,
                fingerprint=fingerprint,
                stage=timeline_stage,
                role="canonical_timeline",
            )
            primary_artifact_id = str(primary_artifact["artifact_id"])
            if primary_artifact_id not in review_upstreams:
                raise ValueError("primary timeline artifact is not upstream of reviewed run")
            binding = context.binding_by_occurrence_id.get(occurrence_id)
            if binding is None:
                raise ValueError("recomposed occurrence missing from TrackAssets")
            primary_result = primary_timeline.get("result")
            if not isinstance(primary_result, dict):
                raise ValueError("primary timeline has no result")
            if str(primary_result.get("occurrence_id") or "") != binding.occurrence_id:
                raise ValueError("primary timeline occurrence identity mismatch")
            if str(primary_result.get("track_id") or "") != binding.track_id:
                raise ValueError("primary timeline track identity mismatch")
            if (
                str(primary_result.get("canonical_selection_sha256") or "")
                != binding.canonical_selection_sha256
            ):
                raise ValueError("primary timeline canonical selection mismatch")
            merged_result = merge_primary_with_overlap_lines(
                primary_result,
                overlap_lines_by_occurrence[occurrence_id],
                regions=regions_by_occurrence[occurrence_id],
            )

            safe_id = _safe(occurrence_id)
            recomposed_timeline_path = out_dir / f"{safe_id}.overlap.timeline.json"
            recomposed_timeline = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": context.calibration_profile_version,
                "calibration_profile_id": context.calibration_profile_id,
                "occurrence_id": occurrence_id,
                "track_id": binding.track_id,
                "mapping_source": "confirmed_overlap_recomposition",
                "confirmed_overlap_regions": [
                    region.to_dict() for region in regions_by_occurrence[occurrence_id]
                ],
                "result": merged_result,
            }
            atomic_write_json(recomposed_timeline_path, recomposed_timeline)
            recomposed_timeline_artifact_path = (
                out_dir / f"{safe_id}.overlap.timeline.artifact.json"
            )
            timeline_upstreams = {
                asset_artifact_id,
                primary_artifact_id,
                str(review_artifact["artifact_id"]),
                *provenance_by_occurrence[occurrence_id],
            }
            recomposed_timeline_artifact = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="overlap_timeline_recomposition",
                algorithm_version=__version__,
                outputs=(("canonical_timeline", recomposed_timeline_path),),
                normalized_config={
                    **context.artifact_config(),
                    "source_timeline_artifact_id": primary_artifact_id,
                    "review_artifact_id": str(review_artifact["artifact_id"]),
                    "confirmed_region_ids": [
                        region.region_id for region in regions_by_occurrence[occurrence_id]
                    ],
                },
                producer={"git_commit": args.git_commit} if args.git_commit else {},
                upstream_artifact_ids=tuple(sorted(timeline_upstreams)),
                evidence={
                    "occurrence_id": occurrence_id,
                    "track_id": binding.track_id,
                    "line_count": int(merged_result.get("line_count", 0)),
                    "confirmed_overlap_region_count": len(
                        regions_by_occurrence[occurrence_id]
                    ),
                },
            )
            atomic_write_json(
                recomposed_timeline_artifact_path,
                recomposed_timeline_artifact,
            )
            new_id = str(recomposed_timeline_artifact["artifact_id"])
            new_timeline_artifact_ids.append(new_id)
            all_new_upstreams.add(new_id)
            occurrence["timeline_path"] = str(recomposed_timeline_path)
            occurrence["timeline_artifact_path"] = str(
                recomposed_timeline_artifact_path
            )
            occurrence["timeline_stage"] = "overlap_timeline_recomposition"
            occurrence["timeline_line_count"] = int(merged_result.get("line_count", 0))
            occurrence["overlap_recomposed"] = True

        remaining_issues = [
            deepcopy(issue)
            for issue in reviewed_run.get("issues", [])
            if str(issue.get("issue_id") or "") not in processed_issue_ids
        ]
        confirmed_regions = [region.to_dict() for region in regions]
        recomposed_run = deepcopy(reviewed_run)
        recomposed_run["schema_version"] = "1.2"
        recomposed_run["occurrences"] = recomposed_occurrences
        recomposed_run["issues"] = remaining_issues
        recomposed_run["confirmed_overlap_regions"] = confirmed_regions
        recomposed_run["status"] = (
            "review_required" if remaining_issues else "ready_for_render"
        )
        recomposed_run["overlap_recomposition"] = {
            "source_review_artifact_id": str(review_artifact["artifact_id"]),
            "processed_issue_ids": sorted(processed_issue_ids),
            "new_timeline_artifact_ids": sorted(new_timeline_artifact_ids),
            "region_count": len(confirmed_regions),
            "remaining_issue_count": len(remaining_issues),
        }
        atomic_write_json(args.out, recomposed_run)

        all_new_upstreams.add(asset_artifact_id)
        recomposed_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="overlap_recomposition",
            algorithm_version=__version__,
            outputs=(("v4_recomposed_run", args.out),),
            normalized_config={
                **context.artifact_config(),
                "source_review_artifact_id": str(review_artifact["artifact_id"]),
                "confirmed_region_ids": [region.region_id for region in regions],
                "legacy_fallback": False,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(sorted(all_new_upstreams)),
            evidence={
                "status": recomposed_run["status"],
                "confirmed_overlap_region_count": len(confirmed_regions),
                "recomposed_occurrence_count": len(overlap_lines_by_occurrence),
                "remaining_issue_count": len(remaining_issues),
            },
        )
        atomic_write_json(args.artifact_out, recomposed_artifact)
    except (
        OSError,
        KeyError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        OverlapRecompositionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "status": recomposed_run["status"],
                "confirmed_overlap_regions": len(confirmed_regions),
                "recomposed_occurrences": len(overlap_lines_by_occurrence),
                "remaining_issues": len(remaining_issues),
                "artifact_id": recomposed_artifact["artifact_id"],
                "run": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
