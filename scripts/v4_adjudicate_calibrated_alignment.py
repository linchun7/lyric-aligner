#!/usr/bin/env python3
"""Bind calibrated direct-final-mix alignment runs and adjudicate one plan.

The command is intentionally decision-only.  It verifies the complete calibration
suite, exact plan lineage, complete backend job coverage, and per-scope calibration
SHA before attaching authority.  It writes evidence + deterministic decisions into
one atomic output directory and never materializes subtitle timing itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.alignment.boundary_executor import BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION
from lyric_aligner.alignment.internal_executor import INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION
from lyric_aligner.evaluation.production_calibration import production_calibration_artifact_is_authoritative
from lyric_aligner.timeline.boundary_refinement import (
    BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION,
    adjudicate_boundary_refinement,
    bind_calibration_to_boundary_point,
    build_boundary_evidence_artifact,
)
from lyric_aligner.timeline.internal_segmentation import (
    INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION,
    adjudicate_internal_segmentation,
    bind_calibration_to_internal_point,
    build_internal_segmentation_evidence,
    build_internal_selective_consensus_evidence,
)
from lyric_aligner.evaluation.selective_consensus_calibration import (
    internal_selective_consensus_calibration_is_authoritative,
)
from scripts.v4_run_human_boundary_anchor_calibration_suite import (
    SUITE_SCHEMA_VERSION as ANCHOR_SUITE_SCHEMA_VERSION,
)
from scripts.v4_run_human_boundary_calibration_suite import (
    SUITE_SCHEMA_VERSION as FULL_SUITE_SCHEMA_VERSION,
)


BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION = (
    "human-boundary-anchor-calibration-suite-1.3-blind-scope-reuse-provenance"
)
SUPPORTED_SUITE_SCHEMA_MODES = {
    FULL_SUITE_SCHEMA_VERSION: {"calibration_only_no_subtitle_mutation"},
    ANCHOR_SUITE_SCHEMA_VERSION: {"lightweight_human_anchor_calibration_only_no_subtitle_mutation"},
    BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION: {
        "blind_scope_subset_calibration_only_no_subtitle_mutation"
    },
}
ADJUDICATION_BUNDLE_SCHEMA_VERSION = "calibrated-alignment-adjudication-bundle-1.0"


class CalibratedAlignmentAdjudicationError(ValueError):
    pass


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibratedAlignmentAdjudicationError(f"{label} is unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise CalibratedAlignmentAdjudicationError(f"{label} must be a JSON object")
    return payload


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"adjudication artifact already exists: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def verify_suite(
    suite_dir: Path,
    *,
    final_audio_sha256: str,
) -> tuple[
    dict[str, Any],
    dict[tuple[str, str], dict[str, Any]],
    dict[str, bool],
]:
    summary = load_json(suite_dir / "suite.summary.json", label="calibration suite summary")
    schema_version = str(summary.get("schema_version") or "")
    mode = str(summary.get("mode") or "")
    allowed_modes = SUPPORTED_SUITE_SCHEMA_MODES.get(schema_version)
    if allowed_modes is None:
        raise CalibratedAlignmentAdjudicationError("unsupported calibration suite schema")
    if mode not in allowed_modes:
        raise CalibratedAlignmentAdjudicationError("calibration suite mode is invalid")
    if bool(summary.get("subtitle_mutation_performed")):
        raise CalibratedAlignmentAdjudicationError("calibration suite unexpectedly claims subtitle mutation")
    if str(summary.get("final_audio_sha256") or "") != final_audio_sha256:
        raise CalibratedAlignmentAdjudicationError("calibration suite belongs to another final mix")
    claimed_suite_sha = str(summary.get("suite_sha256") or "")
    without_sha = dict(summary)
    without_sha.pop("suite_sha256", None)
    if len(claimed_suite_sha) != 64 or claimed_suite_sha != sha256_json(without_sha):
        raise CalibratedAlignmentAdjudicationError("calibration suite summary SHA is invalid")

    profile_rows = summary.get("backend_profiles")
    if not isinstance(profile_rows, list) or len(profile_rows) < 2:
        raise CalibratedAlignmentAdjudicationError("calibration suite does not contain two backend profiles")
    profiles: dict[str, Mapping[str, Any]] = {}
    for row in profile_rows:
        if not isinstance(row, Mapping):
            raise CalibratedAlignmentAdjudicationError("calibration suite backend profile row is invalid")
        profile_id = str(row.get("profile_id") or "").strip()
        if not profile_id or profile_id in profiles:
            raise CalibratedAlignmentAdjudicationError("calibration suite backend profile IDs are invalid/duplicated")
        profiles[profile_id] = row
    for identity_key in ("backend_id", "correlation_group", "family"):
        identities = [str(row.get(identity_key) or "").strip() for row in profiles.values()]
        if any(not value for value in identities) or len(set(identities)) != len(identities):
            raise CalibratedAlignmentAdjudicationError(
                f"calibration suite backend profiles must have distinct {identity_key} values"
            )

    scopes = summary.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        raise CalibratedAlignmentAdjudicationError("calibration suite has no scope summary")
    all_calibrations: dict[tuple[str, str], dict[str, Any]] = {}
    claimed_authority: dict[tuple[str, str], bool] = {}
    for row in scopes:
        if not isinstance(row, Mapping):
            raise CalibratedAlignmentAdjudicationError("calibration suite scope row must be an object")
        profile_id = str(row.get("profile_id") or "").strip()
        kind = str(row.get("boundary_kind") or "").strip()
        key = (profile_id, kind)
        if (
            profile_id not in profiles
            or kind not in {"start", "end", "internal"}
            or key in all_calibrations
        ):
            raise CalibratedAlignmentAdjudicationError("calibration suite scope identity is invalid/duplicated")
        calibration_path = suite_dir / profile_id / f"{kind}.calibration.json"
        calibration = load_json(calibration_path, label=f"{profile_id}/{kind} calibration")
        claimed = str(row.get("calibration_artifact_sha256") or "")
        if len(claimed) != 64 or str(calibration.get("artifact_sha256") or "") != claimed:
            raise CalibratedAlignmentAdjudicationError("calibration file SHA differs from suite summary")
        scope = calibration.get("scope")
        if not isinstance(scope, Mapping) or str(scope.get("backend_profile_id") or "") != profile_id:
            raise CalibratedAlignmentAdjudicationError("calibration file profile identity differs from suite summary")
        profile = profiles[profile_id]
        for identity_key in ("backend_id", "family", "correlation_group"):
            if str(calibration.get(identity_key) or "") != str(profile.get(identity_key) or ""):
                raise CalibratedAlignmentAdjudicationError(
                    f"calibration file {identity_key} differs from suite backend profile"
                )
        actual_authority = production_calibration_artifact_is_authoritative(
            calibration,
            backend_id=str(calibration.get("backend_id") or ""),
            family=str(calibration.get("family") or ""),
            correlation_group=str(calibration.get("correlation_group") or ""),
            expected_scope=dict(scope),
        )
        row_authority = bool(row.get("production_authoritative"))
        if row_authority != bool(actual_authority):
            raise CalibratedAlignmentAdjudicationError(
                "calibration suite scope authority claim differs from calibration artifact"
            )
        all_calibrations[key] = calibration
        claimed_authority[key] = row_authority

    expected_keys = {
        (profile_id, kind)
        for profile_id in profiles
        for kind in ("start", "end", "internal")
    }
    if set(all_calibrations) != expected_keys:
        raise CalibratedAlignmentAdjudicationError(
            "calibration suite must contain exactly one start/end/internal scope for every backend profile"
        )
    if int(summary.get("scope_count") or -1) != len(all_calibrations):
        raise CalibratedAlignmentAdjudicationError("calibration suite scope_count mismatch")

    kind_ready = {
        kind: all(claimed_authority[(profile_id, kind)] for profile_id in profiles)
        for kind in ("start", "end", "internal")
    }
    declared_kind_ready = summary.get("boundary_kind_authority_ready")
    if declared_kind_ready is not None:
        if not isinstance(declared_kind_ready, Mapping) or {
            kind: bool(declared_kind_ready.get(kind))
            for kind in ("start", "end", "internal")
        } != kind_ready:
            raise CalibratedAlignmentAdjudicationError(
                "calibration suite boundary-kind authority summary is inconsistent"
            )
    if bool(summary.get("production_authority_ready")) != all(kind_ready.values()):
        raise CalibratedAlignmentAdjudicationError(
            "calibration suite overall authority summary is inconsistent"
        )

    authoritative = {
        key: calibration
        for key, calibration in all_calibrations.items()
        if claimed_authority[key]
    }
    return summary, authoritative, kind_ready


def verify_run(
    run: Mapping[str, Any],
    *,
    mode: str,
    plan: Mapping[str, Any],
    expected_job_ids: set[str],
) -> str:
    expected_schema = BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION if mode == "outer" else INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION
    if run.get("schema_version") != expected_schema:
        raise CalibratedAlignmentAdjudicationError("backend run schema does not match adjudication mode")
    if str(run.get("final_audio_sha256") or "") != str(plan.get("final_audio_sha256") or ""):
        raise CalibratedAlignmentAdjudicationError("backend run belongs to another final mix")
    if str(run.get("plan_sha256") or "") != sha256_json(plan):
        raise CalibratedAlignmentAdjudicationError("backend run belongs to another plan")
    if str(run.get("timing_authority") or "") != "diagnostic_uncalibrated_direct_final_mix_evidence":
        raise CalibratedAlignmentAdjudicationError("backend run authority marker is invalid")
    profile_id = str(run.get("backend_profile_id") or "").strip()
    if not profile_id:
        raise CalibratedAlignmentAdjudicationError("backend run has no backend_profile_id")
    jobs = run.get("jobs")
    if not isinstance(jobs, list):
        raise CalibratedAlignmentAdjudicationError("backend run jobs must be a list")
    if int(run.get("job_count") or -1) != len(jobs):
        raise CalibratedAlignmentAdjudicationError("backend run job_count mismatch")
    actual_ids: set[str] = set()
    for row in jobs:
        if not isinstance(row, Mapping):
            raise CalibratedAlignmentAdjudicationError("backend run job must be an object")
        boundary_id = str(row.get("boundary_id") or "").strip()
        if not boundary_id or boundary_id in actual_ids:
            raise CalibratedAlignmentAdjudicationError("backend run boundary IDs must be unique/non-empty")
        actual_ids.add(boundary_id)
        point = row.get("point")
        status = str(row.get("status") or ("aligned" if isinstance(point, Mapping) else ""))
        if status == "aligned":
            if not isinstance(point, Mapping):
                raise CalibratedAlignmentAdjudicationError("aligned backend run job has no point")
            if bool(point.get("calibration_passed")) or str(point.get("calibration_sha256") or ""):
                raise CalibratedAlignmentAdjudicationError("raw backend run must not pre-claim calibration")
            if str(point.get("backend_profile_id") or "") != profile_id:
                raise CalibratedAlignmentAdjudicationError("backend run point/profile identity mismatch")
        elif status == "unaligned":
            if point is not None:
                raise CalibratedAlignmentAdjudicationError("unaligned backend run job must not carry a point")
            if not str(row.get("reason") or "").strip():
                raise CalibratedAlignmentAdjudicationError("unaligned backend run job has no reason")
        else:
            raise CalibratedAlignmentAdjudicationError("backend run job alignment status is invalid")
    if actual_ids != expected_job_ids:
        raise CalibratedAlignmentAdjudicationError("backend run does not cover the complete plan job set")
    return profile_id


def run_adjudication(
    *,
    mode: str,
    plan: Mapping[str, Any],
    backend_runs: list[Mapping[str, Any]],
    suite_dir: Path,
    structural_confirmations: list[str] | None = None,
    selective_consensus_calibration: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if mode not in {"outer", "internal"}:
        raise CalibratedAlignmentAdjudicationError("mode must be outer or internal")
    if mode == "outer" and selective_consensus_calibration is not None:
        raise CalibratedAlignmentAdjudicationError("selective consensus calibration is internal-only")
    expected_plan_schema = BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION if mode == "outer" else INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION
    if plan.get("schema_version") != expected_plan_schema:
        raise CalibratedAlignmentAdjudicationError("plan schema does not match adjudication mode")
    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise CalibratedAlignmentAdjudicationError("adjudication requires a non-empty complete plan")
    expected_job_ids = {str(row.get("boundary_id") or "") for row in jobs if isinstance(row, Mapping)}
    if "" in expected_job_ids or len(expected_job_ids) != len(jobs):
        raise CalibratedAlignmentAdjudicationError("plan boundary IDs are invalid/duplicated")
    normalized_structural_confirmations = sorted({str(value) for value in (structural_confirmations or ())})
    if mode == "internal" and normalized_structural_confirmations:
        raise CalibratedAlignmentAdjudicationError("internal adjudication does not accept structural confirmations")
    unknown_structural = sorted(set(normalized_structural_confirmations) - expected_job_ids)
    if unknown_structural:
        raise CalibratedAlignmentAdjudicationError("structural confirmation references unknown boundary_id")
    final_audio_sha = str(plan.get("final_audio_sha256") or "")
    if len(final_audio_sha) != 64:
        raise CalibratedAlignmentAdjudicationError("plan final-audio SHA is invalid")

    suite_summary, calibrations, kind_ready = verify_suite(
        suite_dir,
        final_audio_sha256=final_audio_sha,
    )
    profiles_in_suite = {str(row.get("profile_id") or "") for row in suite_summary.get("backend_profiles", []) if isinstance(row, Mapping)}
    if len(profiles_in_suite) < 2:
        raise CalibratedAlignmentAdjudicationError("calibration suite does not contain two backend profiles")
    if len(backend_runs) != len(profiles_in_suite):
        raise CalibratedAlignmentAdjudicationError("backend run count differs from calibrated profile count")

    runs_by_profile: dict[str, Mapping[str, Any]] = {}
    for run in backend_runs:
        profile_id = verify_run(run, mode=mode, plan=plan, expected_job_ids=expected_job_ids)
        if profile_id in runs_by_profile:
            raise CalibratedAlignmentAdjudicationError("duplicate backend run profile")
        runs_by_profile[profile_id] = run
    if set(runs_by_profile) != profiles_in_suite:
        raise CalibratedAlignmentAdjudicationError("backend run profile set differs from calibration suite")

    evidence_by_boundary_id: dict[str, list[dict[str, Any]]] = {boundary_id: [] for boundary_id in expected_job_ids}
    if mode == "outer":
        job_kind = {str(row["boundary_id"]): str(row["boundary_kind"]) for row in jobs}
        for profile_id, run in runs_by_profile.items():
            for row in run["jobs"]:
                boundary_id = str(row["boundary_id"])
                point = row.get("point")
                status = str(row.get("status") or ("aligned" if isinstance(point, Mapping) else ""))
                if status == "unaligned":
                    continue
                kind = job_kind[boundary_id]
                if not bool(kind_ready.get(kind)):
                    continue
                calibration = calibrations.get((profile_id, kind))
                if calibration is None:
                    raise CalibratedAlignmentAdjudicationError(
                        "authoritative outer calibration scope is missing"
                    )
                evidence_by_boundary_id[boundary_id].append(
                    bind_calibration_to_boundary_point(
                        point,
                        calibration,
                        boundary_kind=kind,
                    )
                )
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id=evidence_by_boundary_id,
        )
        decisions = adjudicate_boundary_refinement(
            plan=plan,
            evidence=evidence,
            structural_confirmations=normalized_structural_confirmations,
        )
    else:
        if bool(kind_ready.get("internal")):
            if selective_consensus_calibration is not None:
                raise CalibratedAlignmentAdjudicationError(
                    "selective consensus fallback must not replace an already-authoritative internal scope"
                )
            for profile_id, run in runs_by_profile.items():
                calibration = calibrations.get((profile_id, "internal"))
                if calibration is None:
                    raise CalibratedAlignmentAdjudicationError(
                        "authoritative internal calibration scope is missing"
                    )
                for row in run["jobs"]:
                    boundary_id = str(row["boundary_id"])
                    point = row.get("point")
                    status = str(row.get("status") or ("aligned" if isinstance(point, Mapping) else ""))
                    if status == "unaligned":
                        continue
                    evidence_by_boundary_id[boundary_id].append(
                        bind_calibration_to_internal_point(point, calibration)
                    )
            evidence = build_internal_segmentation_evidence(
                plan=plan,
                evidence_by_boundary_id=evidence_by_boundary_id,
            )
        elif selective_consensus_calibration is not None:
            source_calibrations = [
                load_json(
                    suite_dir / profile_id / "internal.calibration.json",
                    label=f"{profile_id}/internal source calibration",
                )
                for profile_id in sorted(profiles_in_suite)
            ]
            if not internal_selective_consensus_calibration_is_authoritative(
                selective_consensus_calibration,
                expected_final_audio_sha256=final_audio_sha,
                expected_source_suite_sha256=str(suite_summary["suite_sha256"]),
                source_calibrations=source_calibrations,
            ):
                raise CalibratedAlignmentAdjudicationError(
                    "selective consensus calibration is not authoritative for this suite/final mix"
                )
            for run in runs_by_profile.values():
                for row in run["jobs"]:
                    boundary_id = str(row["boundary_id"])
                    point = row.get("point")
                    status = str(row.get("status") or ("aligned" if isinstance(point, Mapping) else ""))
                    if status == "unaligned":
                        continue
                    evidence_by_boundary_id[boundary_id].append(dict(point))
            evidence = build_internal_selective_consensus_evidence(
                plan=plan,
                evidence_by_boundary_id=evidence_by_boundary_id,
                selective_consensus_calibration=selective_consensus_calibration,
                source_calibrations=source_calibrations,
            )
        else:
            evidence = build_internal_segmentation_evidence(
                plan=plan,
                evidence_by_boundary_id=evidence_by_boundary_id,
            )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)

    bundle = {
        "schema_version": ADJUDICATION_BUNDLE_SCHEMA_VERSION,
        "mode": mode,
        "plan_sha256": sha256_json(plan),
        "final_audio_sha256": final_audio_sha,
        "calibration_suite_sha256": suite_summary["suite_sha256"],
        "selective_consensus_calibration_sha256": (
            str(selective_consensus_calibration.get("artifact_sha256") or "")
            if selective_consensus_calibration is not None
            else ""
        ),
        "backend_profile_ids": sorted(runs_by_profile),
        "structural_confirmations": normalized_structural_confirmations,
        "evidence_sha256": sha256_json(evidence),
        "decisions_sha256": sha256_json(decisions),
        "subtitle_mutation_performed": False,
    }
    bundle["bundle_sha256"] = sha256_json(bundle)
    return evidence, decisions, bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("outer", "internal"), required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--backend-run", type=Path, action="append", dest="backend_runs", required=True)
    parser.add_argument("--calibration-suite-dir", type=Path, required=True)
    parser.add_argument("--selective-consensus-calibration", type=Path)
    parser.add_argument("--structural-confirmation", action="append", dest="structural_confirmations")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    destination = args.out_dir.resolve()
    staging = destination.with_name(destination.name + ".staging")
    if destination.exists() or staging.exists():
        raise FileExistsError("adjudication output/staging path must be new")
    plan = load_json(args.plan, label="adjudication plan")
    backend_runs = [load_json(path, label=f"backend run {path}") for path in args.backend_runs]
    selective_consensus_calibration = (
        load_json(args.selective_consensus_calibration, label="selective consensus calibration")
        if args.selective_consensus_calibration is not None
        else None
    )
    evidence, decisions, bundle = run_adjudication(
        mode=args.mode,
        plan=plan,
        backend_runs=backend_runs,
        suite_dir=args.calibration_suite_dir.resolve(),
        structural_confirmations=args.structural_confirmations,
        selective_consensus_calibration=selective_consensus_calibration,
    )

    staging.mkdir(parents=True, exist_ok=False)
    try:
        atomic_json(staging / "evidence.json", evidence)
        atomic_json(staging / "decisions.json", decisions)
        atomic_json(staging / "bundle.json", bundle)
        staging.replace(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    print(
        json.dumps(
            {
                "out_dir": str(destination),
                "mode": args.mode,
                "backend_profile_ids": bundle["backend_profile_ids"],
                "subtitle_mutation_performed": False,
                "bundle_sha256": bundle["bundle_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
