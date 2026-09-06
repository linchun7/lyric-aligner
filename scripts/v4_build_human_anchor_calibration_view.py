#!/usr/bin/env python3
"""Build a physically calibration-only view of locked Human Anchor gold.

The source gold may contain holdout/internal records. This builder verifies the source
artifact, emits only outer/calibration start+end rows, and records source provenance.
The output is safe for calibration evaluators that must not read holdout timing before
policy freeze.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "human-boundary-gold-calibration-view-1.0"
AUTHORITY = "derived_locked_human_anchor_calibration_only_no_holdout"


class HumanAnchorCalibrationViewError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_verified_source(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise HumanAnchorCalibrationViewError("source human gold root must be object")
    claimed = str(payload.get("artifact_sha256") or "")
    without_hash = dict(payload)
    without_hash.pop("artifact_sha256", None)
    if len(claimed) != 64 or claimed != _sha_json(without_hash):
        raise HumanAnchorCalibrationViewError("source human gold artifact SHA mismatch")
    for key in ("selection_lock_sha256", "final_audio_sha256", "outer_audit_csv_sha256"):
        value = str(payload.get(key) or "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise HumanAnchorCalibrationViewError(f"source human gold lacks valid {key}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise HumanAnchorCalibrationViewError("source human gold records must be list")
    return payload


def build_calibration_view(source: Mapping[str, Any]) -> dict[str, Any]:
    records = source.get("records")
    if not isinstance(records, list):
        raise HumanAnchorCalibrationViewError("source human gold records must be list")
    selected = [
        dict(row)
        for row in records
        if isinstance(row, Mapping)
        and row.get("population") == "outer"
        and row.get("partition") == "calibration"
        and row.get("boundary_kind") in {"start", "end"}
    ]
    if len(selected) != 16:
        raise HumanAnchorCalibrationViewError("expected exactly 16 outer calibration boundary rows")
    by_case: dict[str, set[str]] = {}
    ids: set[str] = set()
    for row in selected:
        record_id = str(row.get("id") or "")
        case_id = str(row.get("case_id") or "")
        kind = str(row.get("boundary_kind") or "")
        if not record_id or record_id in ids or not case_id:
            raise HumanAnchorCalibrationViewError("calibration view IDs must be unique/non-empty")
        ids.add(record_id)
        by_case.setdefault(case_id, set()).add(kind)
        if row.get("partition") != "calibration" or row.get("population") != "outer":
            raise HumanAnchorCalibrationViewError("calibration view selection leaked another partition")
    if len(by_case) != 8 or any(kinds != {"start", "end"} for kinds in by_case.values()):
        raise HumanAnchorCalibrationViewError("calibration view must contain 8 complete outer cases")

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "source_human_gold_schema_version": source.get("schema_version"),
        "source_human_gold_artifact_sha256": source.get("artifact_sha256"),
        "selection_lock_sha256": source.get("selection_lock_sha256"),
        "final_audio_sha256": source.get("final_audio_sha256"),
        "outer_audit_csv_sha256": source.get("outer_audit_csv_sha256"),
        "uncertainty_policy_id": source.get("uncertainty_policy_id"),
        "production_policy": source.get("production_policy"),
        "partition": "calibration",
        "population": "outer",
        "record_count": len(selected),
        "unique_case_count": len(by_case),
        "records": selected,
        "contains_holdout_records": False,
        "contains_internal_records": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = _load_verified_source(args.source)
    artifact = build_calibration_view(source)
    if args.out.exists():
        raise FileExistsError(f"calibration view output already exists: {args.out}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.out)
    print(json.dumps({
        "out": str(args.out),
        "record_count": artifact["record_count"],
        "unique_case_count": artifact["unique_case_count"],
        "artifact_sha256": artifact["artifact_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
