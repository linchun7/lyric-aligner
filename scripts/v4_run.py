#!/usr/bin/env python3
"""Run the production-first Lyric Aligner v4 evidence/timeline chain for one task.

This command does not silently fall back to v3.9. It resolves immutable assets,
builds one primary Source-to-Mix mapping per occurrence, selectively refines
uncertain mappings, probes every adjacent boundary with a shared transition
window, and projects canonical lyrics onto the v4 timeline. Uncertainty is
returned as review_required.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import librosa

from lyric_aligner import __version__
from lyric_aligner.audio.cuts import discontinuity_candidate_id
from lyric_aligner.audio.fine_alignment import should_run_fine_alignment
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
)
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.pipeline.production import build_production_plan, readiness_status
from lyric_aligner.timeline.projector import (
    ProjectionWindow,
    effective_timewarp,
    project_binding_timeline,
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


def _manifest_path(manifest_path: Path, manifest: dict, role: str) -> Path:
    record = manifest["inputs"].get(role)
    if record is None:
        raise ValueError(f"v4 production run requires task input {role}")
    return resolve_manifest_record(manifest_path, record)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "stage"


def _run(command: list[str]) -> str:
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
    return completed.stdout.strip()


def _append_optional(command: list[str], flag: str, value: Path | None) -> None:
    if value is not None:
        command.extend([flag, str(value)])


def _artifact_id(path: Path) -> str:
    return str(_load(path)["artifact_id"])


def _validate_asset_output(
    track_assets: Path, artifact_path: Path
) -> tuple[dict, dict]:
    assets = _load(track_assets)
    artifact = _load(artifact_path)
    issues = validate_artifact_output(
        artifact, role="track_assets", path=track_assets
    )
    if issues:
        raise ValueError(
            "asset artifact output mismatch: " + "; ".join(issues)
        )
    return assets, artifact


def _coarse_command(
    *,
    task_manifest: Path,
    mix_audio: Path,
    track_assets: Path,
    asset_artifact: Path,
    occurrence_id: str,
    out: Path,
    artifact_out: Path,
    git_commit: str,
    mix_start: float | None = None,
    mix_end: float | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "scripts" / "v4_coarse_align.py"),
        "--task-manifest",
        str(task_manifest),
        "--mix-audio",
        str(mix_audio),
        "--track-assets",
        str(track_assets),
        "--asset-artifact",
        str(asset_artifact),
        "--occurrence-id",
        occurrence_id,
        "--out",
        str(out),
        "--artifact-out",
        str(artifact_out),
    ]
    if mix_start is not None:
        command.extend(["--mix-start", f"{mix_start:.6f}"])
    if mix_end is not None:
        command.extend(["--mix-end", f"{mix_end:.6f}"])
    if git_commit:
        command.extend(["--git-commit", git_commit])
    return command


def _effective_timewarp_payload(
    coarse: dict, fine: dict | None
) -> tuple[dict, str]:
    if fine is not None:
        fine_result = fine.get("result", {})
        fine_timewarp = fine_result.get("timewarp")
        if bool(fine_result.get("applied")) and isinstance(
            fine_timewarp, dict
        ):
            return fine_timewarp, "fine"
    coarse_timewarp = coarse.get("result", {}).get("timewarp")
    if not isinstance(coarse_timewarp, dict):
        raise ValueError("coarse alignment has no TimeWarp")
    return coarse_timewarp, "coarse"


def _forward_discontinuity_issue(
    *, occurrence_id: str, selection: str, row: dict
) -> dict | None:
    try:
        mix_before = float(row["mix_before"])
        mix_after = float(row["mix_after"])
        source_before = float(row["source_before"])
        source_after = float(row["source_after"])
    except (KeyError, TypeError, ValueError):
        return None
    if mix_before < 0 or mix_after <= mix_before or source_after <= source_before:
        return None
    candidate_id = discontinuity_candidate_id(occurrence_id, row)
    issue = {
        "kind": "timewarp_discontinuity",
        "code": "source_position_discontinuity",
        "candidate_id": candidate_id,
        "occurrence_id": occurrence_id,
        "status": "review",
        "selection": selection,
        "mix_before": mix_before,
        "mix_after": mix_after,
        "source_before": source_before,
        "source_after": source_after,
        "reason": str(
            row.get("reason")
            or "forward source-position jump requires cut/remap review"
        ),
    }
    for key in (
        "type",
        "observed_rate",
        "excess_source_jump",
        "expected_continuous_advance",
        "allowed_continuous_advance",
        "observed_source_jump",
    ):
        if key in row:
            issue[key] = row[key]
    return issue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--language-map", type=Path)
    parser.add_argument("--middle-cut-map", type=Path)
    parser.add_argument("--lyric-role-map", type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        manifest = load_task_manifest(args.task_manifest)
        input_issues = verify_manifest_inputs(args.task_manifest, manifest)
        if input_issues:
            raise ValueError(
                "task manifest validation failed: " + "; ".join(input_issues)
            )
        fingerprint = str(manifest["task_fingerprint_sha256"])
        mix_audio = _manifest_path(args.task_manifest, manifest, "audio")
        song_list = _manifest_path(
            args.task_manifest, manifest, "song_list"
        )
        lyrics_dir = _manifest_path(
            args.task_manifest, manifest, "lyrics_dir"
        )
        source_dir = _manifest_path(
            args.task_manifest, manifest, "source_audio_dir"
        )

        out_dir = args.out_dir.resolve()
        asset_dir = out_dir / "assets"
        primary_dir = out_dir / "primary"
        transition_dir = out_dir / "transitions"
        timeline_dir = out_dir / "timelines"
        for directory in (
            asset_dir,
            primary_dir,
            transition_dir,
            timeline_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        track_assets = asset_dir / "track_assets.json"
        asset_artifact = asset_dir / "track_assets.artifact.json"
        resolve_command = [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "v4_resolve_assets.py"),
            "--task-manifest",
            str(args.task_manifest),
            "--song-list",
            str(song_list),
            "--lyrics-dir",
            str(lyrics_dir),
            "--source-dir",
            str(source_dir),
            "--out",
            str(track_assets),
            "--artifact-out",
            str(asset_artifact),
        ]
        _append_optional(resolve_command, "--profile", args.profile)
        _append_optional(
            resolve_command, "--language-map", args.language_map
        )
        _append_optional(
            resolve_command, "--middle-cut-map", args.middle_cut_map
        )
        _append_optional(
            resolve_command, "--lyric-role-map", args.lyric_role_map
        )
        if args.git_commit:
            resolve_command.extend(["--git-commit", args.git_commit])
        _run(resolve_command)

        assets_payload, asset_artifact_payload = _validate_asset_output(
            track_assets, asset_artifact
        )
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=assets_payload,
            asset_artifact=asset_artifact_payload,
            verify_asset_files=True,
        )
        mix_duration = float(librosa.get_duration(path=str(mix_audio)))
        plan = build_production_plan(
            context.bindings,
            mix_duration=mix_duration,
            transition_margin_seconds=(
                context.profile.transition.search_margin_seconds
            ),
        )

        issues: list[dict] = []
        upstream_artifact_ids: list[str] = [
            context.asset_artifact.artifact_id
        ]
        occurrence_summaries: list[dict] = []
        discontinuity_issue_count = 0

        for item in plan.occurrences:
            safe_id = _safe(item.occurrence_id)
            coarse_path = primary_dir / f"{safe_id}.coarse.json"
            coarse_artifact_path = (
                primary_dir / f"{safe_id}.coarse.artifact.json"
            )
            _run(
                _coarse_command(
                    task_manifest=args.task_manifest,
                    mix_audio=mix_audio,
                    track_assets=track_assets,
                    asset_artifact=asset_artifact,
                    occurrence_id=item.occurrence_id,
                    out=coarse_path,
                    artifact_out=coarse_artifact_path,
                    git_commit=args.git_commit,
                    mix_start=item.primary_start,
                    mix_end=item.primary_end,
                )
            )
            coarse = _load(coarse_path)
            upstream_artifact_ids.append(_artifact_id(coarse_artifact_path))

            fine: dict | None = None
            fine_path: Path | None = None
            fine_artifact_path: Path | None = None
            if should_run_fine_alignment(coarse):
                fine_path = primary_dir / f"{safe_id}.fine.json"
                fine_artifact_path = (
                    primary_dir / f"{safe_id}.fine.artifact.json"
                )
                fine_command = [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts" / "v4_fine_align.py"),
                    "--task-manifest",
                    str(args.task_manifest),
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
                if args.git_commit:
                    fine_command.extend(["--git-commit", args.git_commit])
                _run(fine_command)
                fine = _load(fine_path)
                upstream_artifact_ids.append(
                    _artifact_id(fine_artifact_path)
                )

            mapping, mapping_blocked, mapping_source = effective_timewarp(
                coarse, fine
            )
            timewarp_payload, timewarp_source = _effective_timewarp_payload(
                coarse, fine
            )
            if timewarp_source != mapping_source:
                raise ValueError(
                    "effective TimeWarp provenance disagrees with projected mapping"
                )
            coarse_selection = str(
                coarse.get("result", {})
                .get("timewarp", {})
                .get("selection", "")
            )
            timewarp_selection = str(
                timewarp_payload.get("selection") or coarse_selection
            )
            occurrence_discontinuities: list[dict] = []
            if mapping_blocked:
                rows = timewarp_payload.get("discontinuities") or []
                if not isinstance(rows, list):
                    raise ValueError("effective TimeWarp discontinuities must be a list")
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    issue = _forward_discontinuity_issue(
                        occurrence_id=item.occurrence_id,
                        selection=timewarp_selection,
                        row=row,
                    )
                    if issue is not None:
                        occurrence_discontinuities.append(issue)
                if occurrence_discontinuities:
                    issues.extend(occurrence_discontinuities)
                    discontinuity_issue_count += len(
                        occurrence_discontinuities
                    )
                else:
                    issues.append(
                        {
                            "kind": "timewarp",
                            "code": "effective_mapping_blocked",
                            "occurrence_id": item.occurrence_id,
                            "status": "review",
                            "selection": timewarp_selection,
                            "reason": (
                                "effective Source-to-Mix mapping is blocked; "
                                "no replayable forward cut candidate was materialized"
                            ),
                        }
                    )

            timeline_path: Path | None = None
            timeline_artifact_path: Path | None = None
            timeline_line_count = 0
            if not mapping_blocked:
                binding = context.binding_by_occurrence_id[
                    item.occurrence_id
                ]
                projected = project_binding_timeline(
                    binding,
                    mapping,
                    window=ProjectionWindow(
                        int(round(item.primary_start * 1000)),
                        int(round(item.primary_end * 1000)),
                    ),
                )
                timeline_line_count = int(projected["line_count"])
                timeline_path = timeline_dir / f"{safe_id}.timeline.json"
                timeline_payload = {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": (
                        context.calibration_profile_version
                    ),
                    "calibration_profile_id": context.calibration_profile_id,
                    "occurrence_id": item.occurrence_id,
                    "track_id": binding.track_id,
                    "mapping_source": mapping_source,
                    "result": projected,
                }
                atomic_write_json(timeline_path, timeline_payload)
                timeline_artifact_path = (
                    timeline_dir / f"{safe_id}.timeline.artifact.json"
                )
                timeline_upstream = [
                    context.asset_artifact.artifact_id,
                    _artifact_id(coarse_artifact_path),
                ]
                if fine_artifact_path is not None:
                    timeline_upstream.append(
                        _artifact_id(fine_artifact_path)
                    )
                timeline_artifact = build_artifact_manifest(
                    task_fingerprint_sha256=fingerprint,
                    stage="canonical_timeline_projection",
                    algorithm_version=__version__,
                    outputs=(("canonical_timeline", timeline_path),),
                    normalized_config={
                        **context.artifact_config(),
                        "mapping_source": mapping_source,
                        "primary_start": item.primary_start,
                        "primary_end": item.primary_end,
                    },
                    producer=(
                        {"git_commit": args.git_commit}
                        if args.git_commit
                        else {}
                    ),
                    upstream_artifact_ids=timeline_upstream,
                    evidence={
                        "occurrence_id": item.occurrence_id,
                        "track_id": binding.track_id,
                        "line_count": timeline_line_count,
                    },
                )
                atomic_write_json(
                    timeline_artifact_path, timeline_artifact
                )
                upstream_artifact_ids.append(
                    str(timeline_artifact["artifact_id"])
                )

            occurrence_summaries.append(
                {
                    "occurrence_id": item.occurrence_id,
                    "ordinal": item.ordinal,
                    "primary_interval": [
                        item.primary_start,
                        item.primary_end,
                    ],
                    "coarse_selection": coarse_selection,
                    "timewarp_selection": timewarp_selection,
                    "fine_applied": bool(
                        fine and fine.get("result", {}).get("applied")
                    ),
                    "mapping_source": mapping_source,
                    "mapping_blocked": mapping_blocked,
                    "discontinuity_candidate_count": len(
                        occurrence_discontinuities
                    ),
                    "coarse_path": str(coarse_path),
                    "coarse_artifact_path": str(coarse_artifact_path),
                    "fine_path": str(fine_path) if fine_path else None,
                    "fine_artifact_path": (
                        str(fine_artifact_path)
                        if fine_artifact_path
                        else None
                    ),
                    "timeline_line_count": timeline_line_count,
                    "timeline_path": (
                        str(timeline_path) if timeline_path else None
                    ),
                    "timeline_artifact_path": (
                        str(timeline_artifact_path)
                        if timeline_artifact_path
                        else None
                    ),
                    "timeline_stage": (
                        "canonical_timeline_projection"
                        if timeline_artifact_path
                        else None
                    ),
                }
            )

        transition_summaries: list[dict] = []
        for index, transition in enumerate(plan.transitions, start=1):
            stage_dir = transition_dir / (
                f"{index:02d}_{_safe(transition.left_occurrence_id)}__"
                f"{_safe(transition.right_occurrence_id)}"
            )
            stage_dir.mkdir(parents=True, exist_ok=True)
            boundary_coarse: dict[str, tuple[Path, Path]] = {}
            for side, occurrence_id in (
                ("left", transition.left_occurrence_id),
                ("right", transition.right_occurrence_id),
            ):
                coarse_path = stage_dir / f"{side}.coarse.json"
                coarse_artifact_path = (
                    stage_dir / f"{side}.coarse.artifact.json"
                )
                _run(
                    _coarse_command(
                        task_manifest=args.task_manifest,
                        mix_audio=mix_audio,
                        track_assets=track_assets,
                        asset_artifact=asset_artifact,
                        occurrence_id=occurrence_id,
                        out=coarse_path,
                        artifact_out=coarse_artifact_path,
                        git_commit=args.git_commit,
                        mix_start=transition.search_start,
                        mix_end=transition.search_end,
                    )
                )
                boundary_coarse[side] = (
                    coarse_path,
                    coarse_artifact_path,
                )
                upstream_artifact_ids.append(
                    _artifact_id(coarse_artifact_path)
                )

            transition_path = stage_dir / "transition.json"
            transition_artifact_path = (
                stage_dir / "transition.artifact.json"
            )
            probe_command = [
                sys.executable,
                str(REPOSITORY_ROOT / "scripts" / "v4_probe_transition.py"),
                "--task-manifest",
                str(args.task_manifest),
                "--track-assets",
                str(track_assets),
                "--asset-artifact",
                str(asset_artifact),
                "--left-coarse",
                str(boundary_coarse["left"][0]),
                "--left-artifact",
                str(boundary_coarse["left"][1]),
                "--right-coarse",
                str(boundary_coarse["right"][0]),
                "--right-artifact",
                str(boundary_coarse["right"][1]),
                "--out",
                str(transition_path),
                "--artifact-out",
                str(transition_artifact_path),
            ]
            if args.git_commit:
                probe_command.extend(["--git-commit", args.git_commit])
            _run(probe_command)
            transition_payload = _load(transition_path)
            transition_result = transition_payload.get("result", {})
            transition_blocked = bool(
                transition_result.get("blocked", False)
            )
            overlap_candidates = list(
                transition_result.get("overlap_candidates", [])
            )
            uncertain_intervals = list(
                transition_result.get("uncertain_intervals", [])
            )
            if transition_blocked and not (
                overlap_candidates or uncertain_intervals
            ):
                raise ValueError(
                    "blocked transition has no materialized review candidates"
                )

            for candidate in overlap_candidates:
                candidate_id = str(
                    candidate.get("candidate_id") or ""
                ).strip()
                if not candidate_id:
                    raise ValueError(
                        "transition overlap candidate is missing candidate_id"
                    )
                issues.append(
                    {
                        "kind": "transition_overlap",
                        "code": "cross_track_overlap_candidate",
                        "candidate_id": candidate_id,
                        "left_occurrence_id": (
                            transition.left_occurrence_id
                        ),
                        "right_occurrence_id": (
                            transition.right_occurrence_id
                        ),
                        "interval_start": float(candidate["start"]),
                        "interval_end": float(candidate["end"]),
                        "status": "review",
                        "reason": str(
                            candidate.get("reason")
                            or "overlap candidate"
                        ),
                        "left_score": float(
                            candidate.get("left_score", 0.0)
                        ),
                        "right_score": float(
                            candidate.get("right_score", 0.0)
                        ),
                    }
                )

            for candidate in uncertain_intervals:
                candidate_id = str(
                    candidate.get("candidate_id") or ""
                ).strip()
                if not candidate_id:
                    raise ValueError(
                        "transition ambiguity candidate is missing candidate_id"
                    )
                issues.append(
                    {
                        "kind": "transition_ambiguity",
                        "code": "ambiguous_source_occurrence",
                        "candidate_id": candidate_id,
                        "left_occurrence_id": (
                            transition.left_occurrence_id
                        ),
                        "right_occurrence_id": (
                            transition.right_occurrence_id
                        ),
                        "interval_start": float(candidate["start"]),
                        "interval_end": float(candidate["end"]),
                        "status": "review",
                        "reason": str(
                            candidate.get("reason")
                            or "transition ambiguity"
                        ),
                    }
                )

            upstream_artifact_ids.append(
                _artifact_id(transition_artifact_path)
            )
            transition_summaries.append(
                {
                    **transition.to_dict(),
                    "status": transition_result.get("status"),
                    "blocked": transition_blocked,
                    "overlap_candidate_count": len(overlap_candidates),
                    "uncertain_interval_count": len(uncertain_intervals),
                    "left_coarse_path": str(
                        boundary_coarse["left"][0]
                    ),
                    "left_coarse_artifact_path": str(
                        boundary_coarse["left"][1]
                    ),
                    "right_coarse_path": str(
                        boundary_coarse["right"][0]
                    ),
                    "right_coarse_artifact_path": str(
                        boundary_coarse["right"][1]
                    ),
                    "transition_path": str(transition_path),
                    "transition_artifact_path": str(
                        transition_artifact_path
                    ),
                }
            )

        status = readiness_status(issues=issues)
        run_path = out_dir / "v4_run.json"
        run_payload = {
            "schema_version": "1.2",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "calibration_profile_version": (
                context.calibration_profile_version
            ),
            "calibration_profile_id": context.calibration_profile_id,
            "status": status,
            "legacy_fallback_used": False,
            "plan": plan.to_dict(),
            "occurrences": occurrence_summaries,
            "transitions": transition_summaries,
            "issues": issues,
        }
        atomic_write_json(run_path, run_payload)
        run_artifact_path = out_dir / "v4_run.artifact.json"
        run_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="production_orchestration",
            algorithm_version=__version__,
            outputs=(("v4_production_run", run_path),),
            normalized_config={
                **context.artifact_config(),
                "transition_search_margin_seconds": (
                    context.profile.transition.search_margin_seconds
                ),
                "transition_issue_granularity": "candidate",
                "timewarp_issue_granularity": (
                    "candidate_when_forward_discontinuity_is_replayable"
                ),
                "primary_mapping_provenance_materialized": True,
                "legacy_fallback": False,
            },
            producer=(
                {"git_commit": args.git_commit} if args.git_commit else {}
            ),
            upstream_artifact_ids=tuple(
                sorted(set(upstream_artifact_ids))
            ),
            evidence={
                "status": status,
                "occurrence_count": len(occurrence_summaries),
                "transition_count": len(transition_summaries),
                "review_issue_count": len(issues),
                "timewarp_discontinuity_issue_count": (
                    discontinuity_issue_count
                ),
            },
        )
        atomic_write_json(run_artifact_path, run_artifact)
    except (
        OSError,
        KeyError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "status": status,
                "legacy_fallback_used": False,
                "occurrences": len(occurrence_summaries),
                "transitions": len(transition_summaries),
                "review_issues": len(issues),
                "timewarp_discontinuity_issues": (
                    discontinuity_issue_count
                ),
                "run": str(run_path),
                "artifact": str(run_artifact_path),
                "artifact_id": run_artifact["artifact_id"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
