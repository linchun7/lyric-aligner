#!/usr/bin/env python3
"""Run production calibration on the 24-clip / 36-point human-anchor authority pack.

This is the lightweight production-authority path. The original 60-clip / 90-point
benchmark remains available for deeper diagnostics, but is no longer a prerequisite
for granting production authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.alignment.production_backend_profiles import (
    PRODUCTION_BOUNDARY_BACKEND_PROFILES,
    ProductionBoundaryBackendProfile,
    get_production_boundary_backend_profile,
)
from lyric_aligner.evaluation.human_gold_prediction import (
    ExternalHumanGoldPredictionConfig,
    execute_human_gold_prediction_scope,
)
from lyric_aligner.evaluation.production_calibration import (
    production_calibration_artifact_is_authoritative,
)
from scripts.v4_build_production_boundary_calibration import (
    build_production_calibration_from_prediction_artifact,
)
from scripts.v4_ingest_human_boundary_anchor import build_human_boundary_anchor_artifact
from scripts.v4_build_human_boundary_anchor_pack import sha256_json


SUITE_SCHEMA_VERSION = "human-boundary-anchor-calibration-suite-1.0"
DEFAULT_PROFILE_IDS = ("sofa_mandarin_v1", "hubertfa_mandarin_v1")
BOUNDARY_KINDS = ("start", "end", "internal")


class HumanBoundaryAnchorCalibrationSuiteError(ValueError):
    pass


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"suite artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_lock(lock_path: Path) -> dict[str, Any]:
    payload = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise HumanBoundaryAnchorCalibrationSuiteError("anchor selection lock must be an object")
    return payload


def _summary_row(*, profile_id: str, prediction: Mapping[str, Any], calibration: Mapping[str, Any]) -> dict[str, Any]:
    scope = calibration["scope"]
    holdout = calibration["summary"]["holdout"]
    all_summary = calibration["summary"]["all"]
    authoritative = production_calibration_artifact_is_authoritative(
        calibration,
        backend_id=str(calibration["backend_id"]),
        family=str(calibration["family"]),
        correlation_group=str(calibration["correlation_group"]),
        expected_scope=dict(scope),
    )
    return {
        "profile_id": profile_id,
        "backend_id": calibration["backend_id"],
        "family": calibration["family"],
        "correlation_group": calibration["correlation_group"],
        "boundary_kind": scope["boundary_kind"],
        "passed": bool(calibration["passed"]),
        "production_authoritative": bool(authoritative),
        "prediction_count": int(all_summary["prediction_count"]),
        "prediction_coverage": float(all_summary["prediction_coverage"]),
        "holdout_prediction_coverage": float(holdout["prediction_coverage"]),
        "holdout_median_effective_error_frames": holdout["median_effective_error_frames"],
        "holdout_p90_effective_error_frames": holdout["p90_effective_error_frames"],
        "holdout_max_effective_error_frames": holdout["max_effective_error_frames"],
        "holdout_catastrophic_fraction": holdout["catastrophic_fraction"],
        "prediction_artifact_sha256": prediction["artifact_sha256"],
        "calibration_artifact_sha256": calibration["artifact_sha256"],
    }


def run_anchor_calibration_suite(
    *,
    pack_dir: str | Path,
    out_dir: str | Path,
    profile_ids: Sequence[str] = DEFAULT_PROFILE_IDS,
    repository_root: str | Path = REPOSITORY_ROOT,
    profile_getter: Callable[[str], ProductionBoundaryBackendProfile] = get_production_boundary_backend_profile,
    prediction_executor: Callable[..., dict[str, Any]] = execute_human_gold_prediction_scope,
) -> dict[str, Any]:
    pack_root = Path(pack_dir).resolve()
    destination = Path(out_dir).resolve()
    repository = Path(repository_root).resolve()
    lock_path = pack_root / "selection.lock.json"
    if not lock_path.is_file():
        raise HumanBoundaryAnchorCalibrationSuiteError("human-anchor pack has no selection.lock.json")
    if destination.exists():
        raise FileExistsError("anchor calibration suite output must be a new path")
    staging = destination.with_name(destination.name + ".staging")
    failed = destination.with_name(destination.name + ".failed")
    if staging.exists() or failed.exists():
        raise FileExistsError("stale anchor calibration staging/failed evidence exists")

    unique_profile_ids = tuple(str(value or "").strip() for value in profile_ids)
    if any(not value for value in unique_profile_ids) or len(set(unique_profile_ids)) != len(unique_profile_ids):
        raise HumanBoundaryAnchorCalibrationSuiteError("backend profile IDs must be unique/non-empty")
    if len(unique_profile_ids) < 2:
        raise HumanBoundaryAnchorCalibrationSuiteError("production authority requires at least two backend profiles")

    # Fail closed before model resolution when human review is incomplete.
    human_gold = build_human_boundary_anchor_artifact(lock_path)
    lock = _load_lock(lock_path)
    selection = lock.get("selection")
    if not isinstance(selection, Mapping):
        raise HumanBoundaryAnchorCalibrationSuiteError("anchor lock has no selection")
    outer = selection["populations"]["outer"]
    internal = selection["populations"]["internal"]
    final_audio = Path(str(lock["inputs"]["final_audio_path"]))
    language = str(human_gold["language_scope"])

    resolved_profiles: list[dict[str, Any]] = []
    for profile_id in unique_profile_ids:
        resolved = profile_getter(profile_id).resolve(repository, language=language)
        if str(resolved.get("profile_id") or "") != profile_id:
            raise HumanBoundaryAnchorCalibrationSuiteError("resolved backend profile ID mismatch")
        resolved_profiles.append(resolved)
    if len({row["backend_id"] for row in resolved_profiles}) < 2:
        raise HumanBoundaryAnchorCalibrationSuiteError("profiles do not identify two distinct backends")
    if len({row["correlation_group"] for row in resolved_profiles}) < 2:
        raise HumanBoundaryAnchorCalibrationSuiteError("profiles do not identify two independent correlation groups")
    if len({row["family"] for row in resolved_profiles}) < 2:
        raise HumanBoundaryAnchorCalibrationSuiteError("profiles do not occupy two evidence families")

    staging.mkdir(parents=True, exist_ok=False)
    try:
        _atomic_json(staging / "human_anchor_gold.json", human_gold)
        rows: list[dict[str, Any]] = []
        profiles_seen: list[dict[str, Any]] = []
        for resolved in resolved_profiles:
            profile_id = str(resolved["profile_id"])
            profiles_seen.append(
                {
                    "profile_id": profile_id,
                    "backend_id": resolved["backend_id"],
                    "backend_version": resolved["backend_version"],
                    "family": resolved["family"],
                    "correlation_group": resolved["correlation_group"],
                    "model_id": resolved["model_id"],
                    "model_revision": resolved["model_revision"],
                    "implementation_revision": resolved["implementation_revision"],
                    "adapter_contract_revision": resolved["adapter_contract_revision"],
                    "language": resolved["language"],
                    "audio_basis": resolved["audio_basis"],
                    "window_policy_id": resolved["window_policy_id"],
                }
            )
            config = ExternalHumanGoldPredictionConfig(
                command=resolved["command"],
                family=resolved["family"],
                correlation_group=resolved["correlation_group"],
                backend_id=resolved["backend_id"],
                backend_version=resolved["backend_version"],
                model_id=resolved["model_id"],
                model_revision=resolved["model_revision"],
                language=resolved["language"],
                implementation_revision=resolved["implementation_revision"],
                adapter_contract_revision=resolved["adapter_contract_revision"],
                audio_basis=resolved["audio_basis"],
                profile_id=profile_id,
                window_policy_id=resolved["window_policy_id"],
                timeout_seconds=900.0,
            )
            profile_dir = staging / profile_id
            profile_dir.mkdir()
            for boundary_kind in BOUNDARY_KINDS:
                population = internal if boundary_kind == "internal" else outer
                prediction = prediction_executor(
                    human_gold_artifact=human_gold,
                    locked_cases=population,
                    final_audio_path=final_audio,
                    boundary_kind=boundary_kind,
                    config=config,
                )
                prediction_path = profile_dir / f"{boundary_kind}.predictions.json"
                _atomic_json(prediction_path, prediction)
                calibration = build_production_calibration_from_prediction_artifact(
                    human_gold_artifact=human_gold,
                    prediction_artifact=prediction,
                )
                calibration_path = profile_dir / f"{boundary_kind}.calibration.json"
                _atomic_json(calibration_path, calibration)
                rows.append(_summary_row(profile_id=profile_id, prediction=prediction, calibration=calibration))

        if len(rows) != len(unique_profile_ids) * len(BOUNDARY_KINDS):
            raise HumanBoundaryAnchorCalibrationSuiteError("anchor calibration suite scope count is incomplete")
        authority_ready = all(row["production_authoritative"] for row in rows)
        suite: dict[str, Any] = {
            "schema_version": SUITE_SCHEMA_VERSION,
            "mode": "lightweight_human_anchor_calibration_only_no_subtitle_mutation",
            "selection_lock_sha256": human_gold["selection_lock_sha256"],
            "human_gold_artifact_sha256": human_gold["artifact_sha256"],
            "final_audio_sha256": human_gold["final_audio_sha256"],
            "language_scope": language,
            "backend_profiles": profiles_seen,
            "scope_count": len(rows),
            "production_authority_ready": authority_ready,
            "subtitle_mutation_performed": False,
            "scopes": rows,
        }
        suite["suite_sha256"] = sha256_json(suite)
        _atomic_json(staging / "suite.summary.json", suite)
        staging.replace(destination)
        return suite
    except Exception as exc:
        failure = {
            "schema_version": SUITE_SCHEMA_VERSION,
            "mode": "runtime_failure_no_subtitle_mutation",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "subtitle_mutation_performed": False,
        }
        try:
            _atomic_json(staging / "SUITE.FAILED.json", failure)
            staging.replace(failed)
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--backend-profile",
        action="append",
        dest="backend_profiles",
        choices=tuple(sorted(PRODUCTION_BOUNDARY_BACKEND_PROFILES)),
    )
    args = parser.parse_args()
    suite = run_anchor_calibration_suite(
        pack_dir=args.pack_dir,
        out_dir=args.out_dir,
        profile_ids=tuple(args.backend_profiles or DEFAULT_PROFILE_IDS),
    )
    print(
        json.dumps(
            {
                "out_dir": str(args.out_dir),
                "scope_count": suite["scope_count"],
                "production_authority_ready": suite["production_authority_ready"],
                "suite_sha256": suite["suite_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if suite["production_authority_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
