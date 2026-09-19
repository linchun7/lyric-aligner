#!/usr/bin/env python3
"""Compose cut_rebuild and overlap_recomposition runs from the same review."""

from __future__ import annotations

import argparse
import json
import re
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
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.timeline.composition import (
    TimelineCompositionError,
    compose_cut_and_overlap_result,
    regions_for_occurrence,
)
from lyric_aligner.timeline.overlap import ConfirmedOverlapRegion
from task_contract import load_task_manifest, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "stage"


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


def _regions(rows: object) -> list[ConfirmedOverlapRegion]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("overlap run has no confirmed_overlap_regions")
    output: list[ConfirmedOverlapRegion] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("confirmed overlap region must be an object")
        try:
            output.append(
                ConfirmedOverlapRegion(
                    candidate_id=str(row["candidate_id"]),
                    issue_id=str(row.get("issue_id") or ""),
                    left_occurrence_id=str(row["left_occurrence_id"]),
                    right_occurrence_id=str(row["right_occurrence_id"]),
                    start_ms=int(row["start_ms"]),
                    end_ms=int(row["end_ms"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("confirmed overlap region is invalid") from exc
    if len({region.region_id for region in output}) != len(output):
        raise ValueError("confirmed overlap regions are duplicated")
    return output


def _issues_by_id(rows: object, *, label: str) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError(f"{label} issues must be a list")
    output: dict[str, dict] = {}
    for issue in rows:
        if not isinstance(issue, dict):
            raise ValueError(f"{label} issue must be an object")
        issue_id = str(issue.get("issue_id") or "").strip()
        if not issue_id:
            raise ValueError(f"{label} issue is missing issue_id")
        if issue_id in output:
            raise ValueError(f"{label} contains duplicate issue_id {issue_id}")
        output[issue_id] = deepcopy(issue)
    return output


def _processed_ids(metadata: dict, *, label: str) -> set[str]:
    rows = metadata.get("processed_issue_ids")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label} has no processed_issue_ids")
    values = {str(value).strip() for value in rows if str(value).strip()}
    if len(values) != len(rows):
        raise ValueError(f"{label} processed_issue_ids are missing/duplicated")
    return values


def _combined_remaining_issues(cut_run: dict, overlap_run: dict) -> list[dict]:
    cut_meta = cut_run["cut_rebuild"]
    overlap_meta = overlap_run["overlap_recomposition"]
    cut_processed = _processed_ids(cut_meta, label="cut_rebuild")
    overlap_processed = _processed_ids(overlap_meta, label="overlap_recomposition")
    if cut_processed & overlap_processed:
        raise ValueError("cut and overlap materializers processed the same issue_id")

    cut_active = _issues_by_id(cut_run.get("issues"), label="cut_rebuild")
    overlap_active = _issues_by_id(
        overlap_run.get("issues"), label="overlap_recomposition"
    )

    remaining: dict[str, dict] = {}
    for issue_id, issue in cut_active.items():
        if issue_id not in overlap_processed:
            remaining[issue_id] = issue
    for issue_id, issue in overlap_active.items():
        if issue_id in cut_processed:
            continue
        existing = remaining.get(issue_id)
        if existing is not None and existing != issue:
            raise ValueError("materializers disagree on unresolved issue snapshot")
        remaining[issue_id] = issue
    return [remaining[key] for key in sorted(remaining)]


def _validate_timeline(
    *,
    occurrence: dict,
    fingerprint: str,
    expected_run_upstreams: set[str],
    expected_binding,
) -> tuple[dict, dict, Path, Path]:
    payload_path = Path(str(occurrence.get("timeline_path") or ""))
    artifact_path = Path(str(occurrence.get("timeline_artifact_path") or ""))
    if not payload_path.is_file() or not artifact_path.is_file():
        raise ValueError("materialized occurrence is missing timeline files")
    stage = str(
        occurrence.get("timeline_stage") or "canonical_timeline_projection"
    )
    payload, artifact = _validate_stage(
        payload_path=payload_path,
        artifact_path=artifact_path,
        fingerprint=fingerprint,
        stage=stage,
        role="canonical_timeline",
    )
    artifact_id = str(artifact["artifact_id"])
    if artifact_id not in expected_run_upstreams:
        raise ValueError("timeline artifact is not upstream of its materialized run")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("timeline has no result")
    if str(result.get("occurrence_id") or "") != expected_binding.occurrence_id:
        raise ValueError("timeline occurrence identity mismatch")
    if str(result.get("track_id") or "") != expected_binding.track_id:
        raise ValueError("timeline track identity mismatch")
    if str(result.get("canonical_selection_sha256") or "") != (
        expected_binding.canonical_selection_sha256
    ):
        raise ValueError("timeline canonical selection identity mismatch")
    return payload, artifact, payload_path, artifact_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--cut-run", required=True, type=Path)
    parser.add_argument("--cut-artifact", required=True, type=Path)
    parser.add_argument("--overlap-run", required=True, type=Path)
    parser.add_argument("--overlap-artifact", required=True, type=Path)
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

        cut_run, cut_artifact = _validate_stage(
            payload_path=args.cut_run,
            artifact_path=args.cut_artifact,
            fingerprint=fingerprint,
            stage="cut_rebuild",
            role="v4_cut_rebuilt_run",
        )
        overlap_run, overlap_artifact = _validate_stage(
            payload_path=args.overlap_run,
            artifact_path=args.overlap_artifact,
            fingerprint=fingerprint,
            stage="overlap_recomposition",
            role="v4_recomposed_run",
        )
        if cut_run.get("legacy_fallback_used") is not False or overlap_run.get(
            "legacy_fallback_used"
        ) is not False:
            raise ValueError("combined composition refuses legacy fallback")

        cut_meta = cut_run.get("cut_rebuild")
        overlap_meta = overlap_run.get("overlap_recomposition")
        if not isinstance(cut_meta, dict) or not isinstance(overlap_meta, dict):
            raise ValueError("materialized runs are missing stage metadata")
        cut_review_id = str(cut_meta.get("source_review_artifact_id") or "")
        overlap_review_id = str(overlap_meta.get("source_review_artifact_id") or "")
        if not cut_review_id or cut_review_id != overlap_review_id:
            raise ValueError("cut and overlap runs do not share the same review artifact")

        cut_upstreams = {
            str(value) for value in cut_artifact.get("upstream_artifact_ids", [])
        }
        overlap_upstreams = {
            str(value) for value in overlap_artifact.get("upstream_artifact_ids", [])
        }
        if cut_review_id not in cut_upstreams or cut_review_id not in overlap_upstreams:
            raise ValueError("source review artifact is not upstream of both materializations")

        assets, asset_artifact = _validate_stage(
            payload_path=args.track_assets,
            artifact_path=args.asset_artifact,
            fingerprint=fingerprint,
            stage="asset_resolution",
            role="track_assets",
        )
        asset_artifact_id = str(asset_artifact["artifact_id"])
        if asset_artifact_id not in cut_upstreams or asset_artifact_id not in overlap_upstreams:
            raise ValueError("TrackAsset artifact is not upstream of both materializations")
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        for label, run in (("cut", cut_run), ("overlap", overlap_run)):
            if str(run.get("calibration_profile_id") or "") != context.calibration_profile_id:
                raise ValueError(f"{label} run calibration profile mismatch")
            if str(run.get("calibration_profile_version") or "") != (
                context.calibration_profile_version
            ):
                raise ValueError(f"{label} run calibration profile version mismatch")

        regions = _regions(overlap_run.get("confirmed_overlap_regions"))
        remaining_issues = _combined_remaining_issues(cut_run, overlap_run)

        cut_occurrences = cut_run.get("occurrences")
        overlap_occurrences = overlap_run.get("occurrences")
        if not isinstance(cut_occurrences, list) or not isinstance(overlap_occurrences, list):
            raise ValueError("materialized run occurrences must be lists")
        cut_by_id = {
            str(row.get("occurrence_id") or ""): row
            for row in cut_occurrences
            if isinstance(row, dict)
        }
        overlap_by_id = {
            str(row.get("occurrence_id") or ""): row
            for row in overlap_occurrences
            if isinstance(row, dict)
        }
        expected_ids = set(context.binding_by_occurrence_id)
        if set(cut_by_id) != expected_ids or set(overlap_by_id) != expected_ids:
            raise ValueError("materialized runs do not contain exactly all TrackOccurrences")

        out_dir = args.out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        combined_occurrences: list[dict] = []
        new_timeline_ids: list[str] = []
        all_upstreams = cut_upstreams | overlap_upstreams
        all_upstreams.update(
            {
                str(cut_artifact["artifact_id"]),
                str(overlap_artifact["artifact_id"]),
                cut_review_id,
                asset_artifact_id,
            }
        )
        combined_count = 0

        for binding in context.bindings:
            occurrence_id = binding.occurrence_id
            cut_occurrence = cut_by_id[occurrence_id]
            overlap_occurrence = overlap_by_id[occurrence_id]
            cut_rebuilt = bool(cut_occurrence.get("cut_rebuilt"))
            overlap_recomposed = bool(overlap_occurrence.get("overlap_recomposed"))
            relevant_regions = regions_for_occurrence(regions, occurrence_id)
            if bool(relevant_regions) != overlap_recomposed:
                raise ValueError("overlap occurrence flag differs from confirmed region coverage")

            if cut_rebuilt and overlap_recomposed:
                cut_timeline, cut_timeline_artifact, _, _ = _validate_timeline(
                    occurrence=cut_occurrence,
                    fingerprint=fingerprint,
                    expected_run_upstreams=cut_upstreams,
                    expected_binding=binding,
                )
                overlap_timeline, overlap_timeline_artifact, _, _ = _validate_timeline(
                    occurrence=overlap_occurrence,
                    fingerprint=fingerprint,
                    expected_run_upstreams=overlap_upstreams,
                    expected_binding=binding,
                )
                cut_result = cut_timeline.get("result")
                overlap_result = overlap_timeline.get("result")
                if not isinstance(cut_result, dict) or not isinstance(overlap_result, dict):
                    raise ValueError("materialized timeline result is invalid")
                merged_result = compose_cut_and_overlap_result(
                    cut_timeline_result=cut_result,
                    overlap_timeline_result=overlap_result,
                    occurrence_id=occurrence_id,
                    regions=relevant_regions,
                )
                safe_id = _safe(occurrence_id)
                combined_timeline_path = out_dir / f"{safe_id}.combined.timeline.json"
                combined_timeline = {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": context.calibration_profile_version,
                    "calibration_profile_id": context.calibration_profile_id,
                    "occurrence_id": occurrence_id,
                    "track_id": binding.track_id,
                    "mapping_source": "cut_overlap_combined_recomposition",
                    "source_cut_timeline_artifact_id": str(
                        cut_timeline_artifact["artifact_id"]
                    ),
                    "source_overlap_timeline_artifact_id": str(
                        overlap_timeline_artifact["artifact_id"]
                    ),
                    "confirmed_overlap_regions": [
                        region.to_dict() for region in relevant_regions
                    ],
                    "result": merged_result,
                }
                atomic_write_json(combined_timeline_path, combined_timeline)
                combined_timeline_artifact_path = (
                    out_dir / f"{safe_id}.combined.timeline.artifact.json"
                )
                timeline_upstreams = {
                    asset_artifact_id,
                    cut_review_id,
                    str(cut_artifact["artifact_id"]),
                    str(overlap_artifact["artifact_id"]),
                    str(cut_timeline_artifact["artifact_id"]),
                    str(overlap_timeline_artifact["artifact_id"]),
                }
                combined_timeline_artifact = build_artifact_manifest(
                    task_fingerprint_sha256=fingerprint,
                    stage="combined_timeline_recomposition",
                    algorithm_version=__version__,
                    outputs=(("canonical_timeline", combined_timeline_path),),
                    normalized_config={
                        **context.artifact_config(),
                        "source_review_artifact_id": cut_review_id,
                        "source_cut_timeline_artifact_id": str(
                            cut_timeline_artifact["artifact_id"]
                        ),
                        "source_overlap_timeline_artifact_id": str(
                            overlap_timeline_artifact["artifact_id"]
                        ),
                        "confirmed_region_ids": [
                            region.region_id for region in relevant_regions
                        ],
                    },
                    producer={"git_commit": args.git_commit} if args.git_commit else {},
                    upstream_artifact_ids=tuple(sorted(timeline_upstreams)),
                    evidence={
                        "occurrence_id": occurrence_id,
                        "track_id": binding.track_id,
                        "line_count": int(merged_result.get("line_count", 0)),
                        "cut_boundary_count": len(merged_result.get("cuts", [])),
                        "confirmed_overlap_region_count": len(relevant_regions),
                    },
                )
                atomic_write_json(
                    combined_timeline_artifact_path,
                    combined_timeline_artifact,
                )
                new_id = str(combined_timeline_artifact["artifact_id"])
                new_timeline_ids.append(new_id)
                all_upstreams.add(new_id)
                occurrence = deepcopy(cut_occurrence)
                occurrence.update(
                    {
                        "timeline_path": str(combined_timeline_path),
                        "timeline_artifact_path": str(combined_timeline_artifact_path),
                        "timeline_stage": "combined_timeline_recomposition",
                        "timeline_line_count": int(merged_result.get("line_count", 0)),
                        "cut_rebuilt": True,
                        "overlap_recomposed": True,
                        "combined_recomposed": True,
                    }
                )
                combined_count += 1
            elif cut_rebuilt:
                occurrence = deepcopy(cut_occurrence)
            elif overlap_recomposed:
                occurrence = deepcopy(overlap_occurrence)
            else:
                cut_artifact_path = str(cut_occurrence.get("timeline_artifact_path") or "")
                overlap_artifact_path = str(
                    overlap_occurrence.get("timeline_artifact_path") or ""
                )
                if cut_artifact_path != overlap_artifact_path:
                    raise ValueError(
                        "untouched occurrence timelines differ between materialized runs"
                    )
                occurrence = deepcopy(cut_occurrence)
            combined_occurrences.append(occurrence)

        combined_run = deepcopy(cut_run)
        combined_run["schema_version"] = "1.4"
        combined_run["occurrences"] = combined_occurrences
        combined_run["issues"] = remaining_issues
        combined_run["status"] = (
            "review_required" if remaining_issues else "ready_for_render"
        )
        combined_run["confirmed_overlap_regions"] = [
            region.to_dict() for region in regions
        ]
        combined_run["overlap_recomposition"] = deepcopy(overlap_meta)
        combined_run["combined_recomposition"] = {
            "source_review_artifact_id": cut_review_id,
            "source_cut_artifact_id": str(cut_artifact["artifact_id"]),
            "source_overlap_artifact_id": str(overlap_artifact["artifact_id"]),
            "new_timeline_artifact_ids": sorted(new_timeline_ids),
            "combined_occurrence_count": combined_count,
            "remaining_issue_count": len(remaining_issues),
        }
        atomic_write_json(args.out, combined_run)

        combined_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="combined_recomposition",
            algorithm_version=__version__,
            outputs=(("v4_combined_run", args.out),),
            normalized_config={
                **context.artifact_config(),
                "source_review_artifact_id": cut_review_id,
                "source_cut_artifact_id": str(cut_artifact["artifact_id"]),
                "source_overlap_artifact_id": str(overlap_artifact["artifact_id"]),
                "legacy_fallback": False,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(sorted(all_upstreams)),
            evidence={
                "status": combined_run["status"],
                "combined_occurrence_count": combined_count,
                "confirmed_overlap_region_count": len(regions),
                "rebuilt_cut_occurrence_count": int(
                    cut_meta.get("rebuilt_occurrence_count", 0)
                ),
                "remaining_issue_count": len(remaining_issues),
            },
        )
        atomic_write_json(args.artifact_out, combined_artifact)
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        TimelineCompositionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "status": combined_run["status"],
                "combined_occurrences": combined_count,
                "remaining_issues": len(remaining_issues),
                "artifact_id": combined_artifact["artifact_id"],
                "run": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
