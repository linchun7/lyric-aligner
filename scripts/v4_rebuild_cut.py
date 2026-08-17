#!/usr/bin/env python3
"""Rebuild confirmed middle cuts into explicit cut-aware TimeWarp/timelines."""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.audio.cuts import (
    CutRebuildError,
    build_cut_aware_timewarp,
    discontinuity_candidate_id,
    effective_path,
    locate_cut_boundary,
)
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    canonical_json_sha256,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.timeline.cuts import (
    CutTimelineProjectionError,
    project_binding_cut_timeline,
)
from task_contract import (
    load_task_manifest,
    resolve_manifest_record,
    verify_manifest_inputs,
)


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


def _effective_timewarp_payload(
    coarse: dict, fine: dict | None
) -> tuple[dict, str]:
    if fine is not None:
        fine_result = fine.get("result", {})
        if bool(fine_result.get("applied")) and isinstance(
            fine_result.get("timewarp"), dict
        ):
            return fine_result["timewarp"], "fine"
    timewarp = coarse.get("result", {}).get("timewarp")
    if not isinstance(timewarp, dict):
        raise ValueError("coarse alignment has no TimeWarp")
    return timewarp, "coarse"


def _find_discontinuity(
    *,
    occurrence_id: str,
    candidate_id: str,
    timewarp_payload: dict,
) -> dict:
    rows = timewarp_payload.get("discontinuities") or []
    if not isinstance(rows, list):
        raise ValueError("TimeWarp discontinuities must be a list")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and discontinuity_candidate_id(occurrence_id, row) == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError(
            "confirmed cut candidate_id does not map to exactly one TimeWarp discontinuity"
        )
    return matches[0]


def _fragment_issue_id(
    *, fingerprint: str, occurrence_id: str, issue: dict
) -> str:
    core = {
        "task_fingerprint_sha256": fingerprint,
        "kind": "canonical_fragment",
        "code": str(issue.get("code") or "canonical_fragment_unresolved"),
        "occurrence_id": occurrence_id,
        "canonical_line_index": int(issue.get("canonical_line_index", -1)),
        "token_index": int(issue.get("token_index", -1)),
    }
    return canonical_json_sha256(core)


def _validate_coarse_identity(
    coarse: dict,
    coarse_artifact: dict,
    *,
    binding,
    asset_artifact_id: str,
    review_upstreams: set[str],
) -> str:
    coarse_id = str(coarse_artifact["artifact_id"])
    if coarse_id not in review_upstreams:
        raise ValueError("primary coarse artifact is not upstream of reviewed run")
    if str(coarse.get("occurrence_id") or "") != binding.occurrence_id:
        raise ValueError("primary coarse occurrence identity mismatch")
    if str(coarse.get("track_id") or "") != binding.track_id:
        raise ValueError("primary coarse track identity mismatch")
    if (
        str(coarse.get("canonical_selection_sha256") or "")
        != binding.canonical_selection_sha256
    ):
        raise ValueError("primary coarse canonical selection mismatch")
    if str(coarse.get("upstream_asset_artifact_id") or "") != asset_artifact_id:
        raise ValueError("primary coarse TrackAsset identity mismatch")
    coarse_upstreams = {
        str(value) for value in coarse_artifact.get("upstream_artifact_ids", [])
    }
    if asset_artifact_id not in coarse_upstreams:
        raise ValueError("primary coarse artifact is not derived from TrackAssets")
    return coarse_id


def _validate_fine_identity(
    fine: dict,
    fine_artifact: dict,
    *,
    binding,
    coarse_artifact_id: str,
    asset_artifact_id: str,
    review_upstreams: set[str],
) -> str:
    fine_id = str(fine_artifact["artifact_id"])
    if fine_id not in review_upstreams:
        raise ValueError("primary Fine artifact is not upstream of reviewed run")
    if str(fine.get("occurrence_id") or "") != binding.occurrence_id:
        raise ValueError("primary Fine occurrence identity mismatch")
    if str(fine.get("track_id") or "") != binding.track_id:
        raise ValueError("primary Fine track identity mismatch")
    if (
        str(fine.get("canonical_selection_sha256") or "")
        != binding.canonical_selection_sha256
    ):
        raise ValueError("primary Fine canonical selection mismatch")
    fine_upstreams = {
        str(value) for value in fine_artifact.get("upstream_artifact_ids", [])
    }
    if asset_artifact_id not in fine_upstreams:
        raise ValueError("primary Fine artifact is not derived from TrackAssets")
    if coarse_artifact_id not in fine_upstreams:
        raise ValueError("primary Fine artifact is not derived from primary Coarse")
    return fine_id


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
            raise ValueError(
                "task manifest validation failed: " + "; ".join(task_issues)
            )
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
            raise ValueError("cut rebuild refuses legacy fallback")
        if reviewed_run.get("status") != "review_required":
            raise ValueError("cut rebuild requires a review_required reviewed run")
        review_upstreams = {
            str(value) for value in review_artifact.get("upstream_artifact_ids", [])
        }

        assets, asset_artifact = _validate_stage(
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
            track_assets_payload=assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        if (
            reviewed_run.get("calibration_profile_id")
            != context.calibration_profile_id
        ):
            raise ValueError("reviewed run calibration profile mismatch")
        if (
            reviewed_run.get("calibration_profile_version")
            != context.calibration_profile_version
        ):
            raise ValueError("reviewed run calibration profile version mismatch")

        issues_raw = reviewed_run.get("issues")
        if not isinstance(issues_raw, list):
            raise ValueError("reviewed run issues must be a list")
        confirmed = [
            issue
            for issue in issues_raw
            if isinstance(issue, dict)
            and issue.get("kind") == "timewarp_discontinuity"
            and issue.get("decision_action") == "confirmed_cut"
            and bool(issue.get("requires_timeline_rebuild"))
        ]
        if not confirmed:
            raise ValueError("reviewed run has no confirmed cut requiring rebuild")

        occurrence_rows = reviewed_run.get("occurrences")
        if not isinstance(occurrence_rows, list):
            raise ValueError("reviewed run occurrences must be a list")
        occurrence_by_id = {
            str(row.get("occurrence_id") or ""): row
            for row in occurrence_rows
            if isinstance(row, dict)
        }
        if "" in occurrence_by_id or len(occurrence_by_id) != len(occurrence_rows):
            raise ValueError("reviewed run occurrence identity is missing/duplicated")

        issues_by_occurrence: dict[str, list[dict]] = {}
        for issue in confirmed:
            occurrence_id = str(issue.get("occurrence_id") or "")
            if occurrence_id not in occurrence_by_id:
                raise ValueError(
                    "confirmed cut occurrence is missing from reviewed run"
                )
            issues_by_occurrence.setdefault(occurrence_id, []).append(issue)

        out_dir = args.out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        rebuilt_occurrences = deepcopy(occurrence_rows)
        rebuilt_by_id = {
            str(row.get("occurrence_id") or ""): row
            for row in rebuilt_occurrences
        }
        processed_issue_ids: set[str] = set()
        fragment_issues: list[dict] = []
        new_mapping_artifact_ids: list[str] = []
        new_timeline_artifact_ids: list[str] = []
        all_upstreams = set(review_upstreams)
        all_upstreams.add(str(review_artifact["artifact_id"]))
        all_upstreams.add(asset_artifact_id)

        for occurrence_id, occurrence_issues in issues_by_occurrence.items():
            summary = occurrence_by_id[occurrence_id]
            binding = context.binding_by_occurrence_id.get(occurrence_id)
            if binding is None:
                raise ValueError(
                    "confirmed cut occurrence is missing from TrackAssets"
                )

            coarse_path = Path(str(summary.get("coarse_path") or ""))
            coarse_artifact_path = Path(
                str(summary.get("coarse_artifact_path") or "")
            )
            if not coarse_path.is_file() or not coarse_artifact_path.is_file():
                raise ValueError(
                    "reviewed run is missing primary coarse provenance for cut rebuild"
                )
            coarse, coarse_artifact = _validate_stage(
                payload_path=coarse_path,
                artifact_path=coarse_artifact_path,
                fingerprint=fingerprint,
                stage="coarse_audio_alignment",
                role="coarse_alignment",
            )
            coarse_id = _validate_coarse_identity(
                coarse,
                coarse_artifact,
                binding=binding,
                asset_artifact_id=asset_artifact_id,
                review_upstreams=review_upstreams,
            )

            fine: dict | None = None
            fine_id = ""
            fine_path_raw = summary.get("fine_path")
            fine_artifact_raw = summary.get("fine_artifact_path")
            if fine_path_raw or fine_artifact_raw:
                if not fine_path_raw or not fine_artifact_raw:
                    raise ValueError("primary Fine provenance is incomplete")
                fine_path = Path(str(fine_path_raw))
                fine_artifact_path = Path(str(fine_artifact_raw))
                fine, fine_artifact = _validate_stage(
                    payload_path=fine_path,
                    artifact_path=fine_artifact_path,
                    fingerprint=fingerprint,
                    stage="fine_audio_alignment",
                    role="fine_alignment",
                )
                fine_id = _validate_fine_identity(
                    fine,
                    fine_artifact,
                    binding=binding,
                    coarse_artifact_id=coarse_id,
                    asset_artifact_id=asset_artifact_id,
                    review_upstreams=review_upstreams,
                )

            timewarp_payload, path_source = _effective_timewarp_payload(coarse, fine)
            path = effective_path(coarse, fine)
            localized = []
            for issue in occurrence_issues:
                issue_id = str(issue.get("issue_id") or "").strip()
                candidate_id = str(issue.get("candidate_id") or "").strip()
                if not issue_id or not candidate_id:
                    raise ValueError(
                        "confirmed cut issue is missing replayable identity"
                    )
                discontinuity = _find_discontinuity(
                    occurrence_id=occurrence_id,
                    candidate_id=candidate_id,
                    timewarp_payload=timewarp_payload,
                )
                snapshot = issue.get("confirmed_discontinuity")
                if not isinstance(snapshot, dict):
                    raise ValueError(
                        "confirmed cut issue is missing discontinuity snapshot"
                    )
                for key in (
                    "mix_before",
                    "mix_after",
                    "source_before",
                    "source_after",
                ):
                    try:
                        expected = float(snapshot[key])
                        current = float(discontinuity[key])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            "confirmed cut snapshot is malformed"
                        ) from exc
                    if abs(expected - current) > 1e-6:
                        raise ValueError(
                            "confirmed cut snapshot differs from effective TimeWarp evidence"
                        )
                localized.append(
                    locate_cut_boundary(
                        mix_audio=mix_audio,
                        source_audio=Path(binding.source_audio_path),
                        candidate_id=candidate_id,
                        issue_id=issue_id,
                        discontinuity=discontinuity,
                        effective_alignment_path=path,
                        config=context.profile.cut_boundary,
                    )
                )
                processed_issue_ids.add(issue_id)

            try:
                primary_start, primary_end = summary["primary_interval"]
                primary_start = float(primary_start)
                primary_end = float(primary_end)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("occurrence primary interval is invalid") from exc

            cut_mapping = build_cut_aware_timewarp(
                alignment_path=path,
                localized_boundaries=localized,
                mix_start=primary_start,
                mix_end=primary_end,
                timewarp_config=context.profile.timewarp,
            )
            safe_id = _safe(occurrence_id)
            mapping_path = out_dir / f"{safe_id}.cut-timewarp.json"
            mapping_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": context.calibration_profile_version,
                "calibration_profile_id": context.calibration_profile_id,
                "occurrence_id": occurrence_id,
                "track_id": binding.track_id,
                "canonical_selection_sha256": binding.canonical_selection_sha256,
                "source_alignment_path": path_source,
                "result": cut_mapping,
            }
            atomic_write_json(mapping_path, mapping_payload)

            mapping_artifact_path = (
                out_dir / f"{safe_id}.cut-timewarp.artifact.json"
            )
            mapping_upstreams = {
                asset_artifact_id,
                str(review_artifact["artifact_id"]),
                coarse_id,
            }
            if fine_id:
                mapping_upstreams.add(fine_id)
            mapping_artifact = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="cut_timewarp_rebuild",
                algorithm_version=__version__,
                outputs=(("cut_aware_timewarp", mapping_path),),
                normalized_config={
                    **context.artifact_config(),
                    "source_review_artifact_id": str(
                        review_artifact["artifact_id"]
                    ),
                    "cut_boundary": asdict(context.profile.cut_boundary),
                    "confirmed_candidate_ids": [
                        row.candidate_id for row in localized
                    ],
                },
                producer=(
                    {"git_commit": args.git_commit} if args.git_commit else {}
                ),
                upstream_artifact_ids=tuple(sorted(mapping_upstreams)),
                evidence={
                    "occurrence_id": occurrence_id,
                    "cut_count": len(localized),
                    "localized_boundaries": [
                        row.to_dict() for row in localized
                    ],
                },
            )
            atomic_write_json(mapping_artifact_path, mapping_artifact)
            mapping_artifact_id = str(mapping_artifact["artifact_id"])
            new_mapping_artifact_ids.append(mapping_artifact_id)
            all_upstreams.add(mapping_artifact_id)

            timeline_result = project_binding_cut_timeline(binding, cut_mapping)
            for projection_issue in timeline_result.get("projection_issues", []):
                fragment_issue = {
                    **projection_issue,
                    "occurrence_id": occurrence_id,
                }
                fragment_issue["issue_id"] = _fragment_issue_id(
                    fingerprint=fingerprint,
                    occurrence_id=occurrence_id,
                    issue=fragment_issue,
                )
                fragment_issues.append(fragment_issue)

            timeline_path = out_dir / f"{safe_id}.cut.timeline.json"
            timeline_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": context.calibration_profile_version,
                "calibration_profile_id": context.calibration_profile_id,
                "occurrence_id": occurrence_id,
                "track_id": binding.track_id,
                "mapping_source": "cut_aware_rebuild",
                "cut_mapping_artifact_id": mapping_artifact_id,
                "result": timeline_result,
            }
            atomic_write_json(timeline_path, timeline_payload)

            timeline_artifact_path = (
                out_dir / f"{safe_id}.cut.timeline.artifact.json"
            )
            timeline_artifact = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="cut_timeline_rebuild",
                algorithm_version=__version__,
                outputs=(("canonical_timeline", timeline_path),),
                normalized_config={
                    **context.artifact_config(),
                    "cut_mapping_artifact_id": mapping_artifact_id,
                    "source_review_artifact_id": str(
                        review_artifact["artifact_id"]
                    ),
                },
                producer=(
                    {"git_commit": args.git_commit} if args.git_commit else {}
                ),
                upstream_artifact_ids=(
                    asset_artifact_id,
                    str(review_artifact["artifact_id"]),
                    mapping_artifact_id,
                ),
                evidence={
                    "occurrence_id": occurrence_id,
                    "line_count": int(timeline_result["line_count"]),
                    "omitted_line_count": len(
                        timeline_result.get("omitted_lines", [])
                    ),
                    "projection_issue_count": len(
                        timeline_result.get("projection_issues", [])
                    ),
                },
            )
            atomic_write_json(timeline_artifact_path, timeline_artifact)
            timeline_artifact_id = str(timeline_artifact["artifact_id"])
            new_timeline_artifact_ids.append(timeline_artifact_id)
            all_upstreams.add(timeline_artifact_id)

            rebuilt_summary = rebuilt_by_id[occurrence_id]
            rebuilt_summary.update(
                {
                    "mapping_blocked": False,
                    "mapping_source": "cut_aware_rebuild",
                    "cut_rebuilt": True,
                    "cut_mapping_path": str(mapping_path),
                    "cut_mapping_artifact_path": str(mapping_artifact_path),
                    "timeline_path": str(timeline_path),
                    "timeline_artifact_path": str(timeline_artifact_path),
                    "timeline_stage": "cut_timeline_rebuild",
                    "timeline_line_count": int(timeline_result["line_count"]),
                    "cut_count": len(localized),
                }
            )

        remaining_issues = [
            deepcopy(issue)
            for issue in issues_raw
            if str(issue.get("issue_id") or "") not in processed_issue_ids
        ]
        remaining_issues.extend(fragment_issues)

        rebuilt_run = deepcopy(reviewed_run)
        rebuilt_run["schema_version"] = "1.3"
        rebuilt_run["occurrences"] = rebuilt_occurrences
        rebuilt_run["issues"] = remaining_issues
        rebuilt_run["status"] = (
            "review_required" if remaining_issues else "ready_for_render"
        )
        rebuilt_run["cut_rebuild"] = {
            "source_review_artifact_id": str(review_artifact["artifact_id"]),
            "processed_issue_ids": sorted(processed_issue_ids),
            "new_mapping_artifact_ids": sorted(new_mapping_artifact_ids),
            "new_timeline_artifact_ids": sorted(new_timeline_artifact_ids),
            "rebuilt_occurrence_count": len(issues_by_occurrence),
            "canonical_fragment_issue_count": len(fragment_issues),
            "remaining_issue_count": len(remaining_issues),
        }
        atomic_write_json(args.out, rebuilt_run)

        rebuild_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="cut_rebuild",
            algorithm_version=__version__,
            outputs=(("v4_cut_rebuilt_run", args.out),),
            normalized_config={
                **context.artifact_config(),
                "source_review_artifact_id": str(review_artifact["artifact_id"]),
                "cut_boundary": asdict(context.profile.cut_boundary),
                "legacy_fallback": False,
            },
            producer=(
                {"git_commit": args.git_commit} if args.git_commit else {}
            ),
            upstream_artifact_ids=tuple(sorted(all_upstreams)),
            evidence={
                "status": rebuilt_run["status"],
                "rebuilt_occurrence_count": len(issues_by_occurrence),
                "confirmed_cut_count": len(processed_issue_ids),
                "canonical_fragment_issue_count": len(fragment_issues),
                "remaining_issue_count": len(remaining_issues),
            },
        )
        atomic_write_json(args.artifact_out, rebuild_artifact)
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        CutRebuildError,
        CutTimelineProjectionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "status": rebuilt_run["status"],
                "rebuilt_occurrences": len(issues_by_occurrence),
                "confirmed_cuts": len(processed_issue_ids),
                "canonical_fragment_issues": len(fragment_issues),
                "remaining_issues": len(remaining_issues),
                "artifact_id": rebuild_artifact["artifact_id"],
                "run": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
