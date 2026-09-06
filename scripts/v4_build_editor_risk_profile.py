#!/usr/bin/env python3
"""Build an exact-provenance editor boundary risk profile from Human Anchor gold.

This is a measurement/evaluation stage.  It never reads or writes subtitle timing
artifacts and never grants candidate timing authority.  The output can later be
validated by ``evaluation.risk_provenance`` before an expected-loss comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json, sha256_file
from lyric_aligner.evaluation.editor_risk_profile import build_editor_risk_profile_artifact


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def run(
    *,
    selection_lock_path: Path,
    human_gold_path: Path,
    boundary_kind: str,
    population: str,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(f"editor risk output already exists: {output_path}")
    selection_lock = _load_json(selection_lock_path, label="selection lock")
    human_gold = _load_json(human_gold_path, label="human gold")
    artifact = build_editor_risk_profile_artifact(
        selection_lock=selection_lock,
        selection_lock_file_sha256=sha256_file(selection_lock_path),
        human_gold_artifact=human_gold,
        boundary_kind=boundary_kind,
        population=population,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, artifact)
    if sha256_file(output_path) == "":
        raise AssertionError("editor risk artifact was not persisted")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-lock", required=True, type=Path)
    parser.add_argument("--human-gold", required=True, type=Path)
    parser.add_argument("--boundary-kind", required=True, choices=("start", "end"))
    parser.add_argument("--population", default="holdout", choices=("calibration", "holdout"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    artifact = run(
        selection_lock_path=args.selection_lock,
        human_gold_path=args.human_gold,
        boundary_kind=args.boundary_kind,
        population=args.population,
        output_path=args.out,
    )
    profile = artifact["profile"]
    print(
        json.dumps(
            {
                "artifact_sha256": artifact["artifact_sha256"],
                "boundary_kind": artifact["boundary_kind"],
                "population": artifact["population"],
                "sample_count": profile["sample_count"],
                "distinct_track_count": profile["distinct_track_count"],
                "mean_effective_error_ms": profile["mean_effective_error_ms"],
                "p90_effective_error_ms": profile["p90_effective_error_ms"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
