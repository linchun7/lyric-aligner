#!/usr/bin/env python3
"""Build production calibration for the pre-frozen joint internal selector."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.selective_consensus_calibration import (
    INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION,
    build_internal_selective_consensus_calibration,
    internal_selective_consensus_calibration_is_authoritative,
    sha256_json,
)
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    alignment_units,
)


def load_json(path: Path, *, label: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def verified_lock(path: Path) -> dict:
    lock = load_json(path, label="anchor selection lock")
    claimed = str(lock.get("lock_sha256") or "")
    unsigned = dict(lock)
    unsigned.pop("lock_sha256", None)
    if len(claimed) != 64 or claimed != sha256_json(unsigned):
        raise ValueError("anchor selection lock SHA is invalid")
    return lock


def build_selector_priors(lock: dict) -> dict[str, int]:
    selection = lock.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("anchor selection lock has no selection object")
    populations = selection.get("populations")
    if not isinstance(populations, dict) or not isinstance(populations.get("internal"), list):
        raise ValueError("anchor selection lock has no internal population")
    records = populations["internal"]
    if len(records) != 12:
        raise ValueError("joint selector calibration requires exactly 12 locked internal cases")
    language = str((lock.get("inputs") or {}).get("language_scope") or "").strip().lower()
    if not language:
        raise ValueError("anchor selection language scope is missing")
    priors: dict[str, int] = {}
    for record in records:
        case_id = str(record.get("case_id") or "")
        segments = record.get("segments")
        if len(case_id) != 64 or not isinstance(segments, list) or len(segments) < 2:
            raise ValueError("locked internal case identity/segments are invalid")
        boundary_index = int(record.get("internal_boundary_index") or 0)
        if not 1 <= boundary_index < len(segments):
            raise ValueError("locked internal boundary index is invalid")
        units = [alignment_units(language, str(segment.get("text") or "")) for segment in segments]
        left_count = sum(len(row) for row in units[:boundary_index])
        total_count = sum(len(row) for row in units)
        if not 0 < left_count < total_count:
            raise ValueError("locked internal lexical-ratio prior is invalid")
        start_ms = int(record["start_ms"])
        end_ms = int(record["end_ms"])
        if not 0 <= start_ms < end_ms:
            raise ValueError("locked internal editor interval is invalid")
        priors[f"{case_id}:internal"] = int(
            round(start_ms + (end_ms - start_ms) * left_count / total_count)
        )
    if len(priors) != 12:
        raise ValueError("joint selector prior IDs are duplicated")
    return priors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite-dir", type=Path, required=True)
    parser.add_argument("--anchor-lock", type=Path, required=True)
    parser.add_argument("--blind-scope-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    suite_dir = args.suite_dir.resolve()
    summary = load_json(suite_dir / "suite.summary.json", label="calibration suite summary")
    profiles = summary.get("backend_profiles")
    if not isinstance(profiles, list) or len(profiles) != 2:
        raise ValueError("joint selector builder requires exactly two backend profiles")
    profile_ids = sorted(str(row.get("profile_id") or "") for row in profiles if isinstance(row, dict))
    if len(profile_ids) != 2 or any(not value for value in profile_ids):
        raise ValueError("calibration suite backend profile IDs are invalid")
    source_calibrations = [
        load_json(
            suite_dir / profile_id / "internal.calibration.json",
            label=f"{profile_id} internal calibration",
        )
        for profile_id in profile_ids
    ]
    blind_scope_root = args.blind_scope_root.resolve()
    source_blind_scope_artifacts = [
        load_json(
            blind_scope_root / f"{profile_id}__internal.json",
            label=f"{profile_id} frozen blind internal scope",
        )
        for profile_id in profile_ids
    ]
    human_gold_artifact = load_json(
        suite_dir / "human_anchor_gold.json",
        label="human anchor gold",
    )
    lock = verified_lock(args.anchor_lock.resolve())
    priors = build_selector_priors(lock)
    artifact = build_internal_selective_consensus_calibration(
        suite_summary=summary,
        human_gold_artifact=human_gold_artifact,
        source_calibrations=source_calibrations,
        source_blind_scope_artifacts=source_blind_scope_artifacts,
        selector_priors_ms=priors,
        selector_prior_provenance={
            "selection_lock_sha256": str(lock["lock_sha256"]),
            "lexical_id": ALIGNMENT_LEXICAL_ID,
            "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
            "prior_definition": INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION,
            "selector_frozen_before_final_replacement_gold": True,
        },
    )
    if not internal_selective_consensus_calibration_is_authoritative(
        artifact,
        expected_final_audio_sha256=str(summary.get("final_audio_sha256") or ""),
        expected_source_suite_sha256=str(summary.get("suite_sha256") or ""),
        source_calibrations=source_calibrations,
    ):
        raise RuntimeError("built joint selector calibration is not authoritative")
    if args.out.exists():
        raise FileExistsError("joint selector calibration output must be a new path")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.out)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "artifact_sha256": artifact["artifact_sha256"],
                "calibration_summary": artifact["summary"]["calibration"],
                "holdout_summary": artifact["summary"]["holdout"],
                "passed": artifact["passed"],
                "subtitle_mutation_performed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
