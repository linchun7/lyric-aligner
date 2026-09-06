#!/usr/bin/env python3
"""Validate a 24-clip human-anchor pack and materialize 36 authority points."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.human_boundary_anchor import (
    ANCHOR_BOUNDARY_POINTS_PER_KIND,
    ANCHOR_INTERNAL_CUE_COUNT,
    ANCHOR_OUTER_CUE_COUNT,
    ANCHOR_TOTAL_BOUNDARY_POINTS,
    HUMAN_ANCHOR_GOLD_AUTHORITY,
    HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
    HUMAN_ANCHOR_PRODUCTION_POLICY,
    HUMAN_ANCHOR_SELECTION_SCHEMA_VERSIONS,
    HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
    clarity_to_uncertainty_ms,
    project_locked_anchor_selection,
)
from scripts.v4_build_human_boundary_anchor_pack import (
    ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
    ANCHOR_PACK_SCHEMA_VERSIONS,
    ANCHOR_QUESTION_INVALIDATION_SCHEMA_VERSION,
    ANCHOR_QUESTION_INVALID_REASON_CODES,
    ANCHOR_REPLACEMENT_PACK_SCHEMA_VERSION,
    ANCHOR_UI_REVISION,
    ANCHOR_UI_UX_REVISION,
    sha256_json,
)
from scripts.v4_ingest_human_boundary_gold import sha256_file, verify_lock


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"audit CSV has no header: {path}")
        headers = list(reader.fieldnames)
        forbidden = ("backend", "prediction", "model_id", "model_revision")
        if any(term in header.casefold() for header in headers for term in forbidden):
            raise ValueError("human anchor audit CSV must not contain backend prediction columns")
        return headers, [dict(row) for row in reader]


def _safe_child(root: Path, relative: str) -> Path:
    rel = Path(str(relative or ""))
    if not str(relative or "").strip() or rel.is_absolute() or ".." in rel.parts:
        raise ValueError("locked anchor relative path is unsafe")
    target = (root / rel).resolve()
    target.relative_to(root.resolve())
    return target


def _parse_required_int(row: Mapping[str, Any], key: str) -> int:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"human anchor field {key} is blank")
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"human anchor field {key} must be integer milliseconds") from exc
    if result < 0:
        raise ValueError(f"human anchor field {key} must be nonnegative")
    return result


def _verify_lock(lock: Mapping[str, Any], lock_path: Path) -> dict[str, Any]:
    if lock.get("schema_version") not in ANCHOR_PACK_SCHEMA_VERSIONS:
        raise ValueError("unsupported human-anchor pack schema")
    claimed = str(lock.get("lock_sha256") or "")
    unsigned = dict(lock)
    unsigned.pop("lock_sha256", None)
    if len(claimed) != 64 or claimed != sha256_json(unsigned):
        raise ValueError("human-anchor pack lock SHA is invalid")
    if lock.get("selection_created_from_pre_model_locked_benchmark") is not True:
        raise ValueError("human-anchor selection lacks pre-model locked source attestation")
    if lock.get("backend_prediction_fields_present") is not False:
        raise ValueError("human-anchor selection lock is contaminated by backend predictions")

    source = lock.get("source_full_pack")
    if not isinstance(source, Mapping):
        raise ValueError("human-anchor source full-pack provenance is missing")
    source_lock_path = Path(str(source.get("selection_lock_path") or ""))
    if not source_lock_path.is_file() or sha256_file(source_lock_path) != str(source.get("selection_lock_file_sha256") or ""):
        raise ValueError("source full-pack selection lock file changed")
    source_lock = _load_json(source_lock_path)
    if str(source_lock.get("lock_sha256") or "") != str(source.get("selection_lock_sha256") or ""):
        raise ValueError("source full-pack semantic lock SHA changed")
    verify_lock(source_lock, source_lock_path)

    invalid_ids_raw = lock.get("question_invalid_case_ids", [])
    if not isinstance(invalid_ids_raw, list):
        raise ValueError("human-anchor question-invalid case IDs are invalid")
    invalid_ids = tuple(str(value or "").strip() for value in invalid_ids_raw)
    if bool(invalid_ids) != (lock.get("schema_version") == ANCHOR_REPLACEMENT_PACK_SCHEMA_VERSION):
        raise ValueError("human-anchor replacement schema/provenance mismatch")
    expected = project_locked_anchor_selection(
        source_lock,
        question_invalid_case_ids=invalid_ids,
    )
    selection = lock.get("selection")
    if not isinstance(selection, Mapping) or selection.get("schema_version") not in HUMAN_ANCHOR_SELECTION_SCHEMA_VERSIONS:
        raise ValueError("human-anchor selection is missing/unsupported")
    if dict(selection) != expected:
        raise ValueError("human-anchor selection differs from deterministic locked projection")
    if invalid_ids:
        invalidation_path = lock_path.parent / "QUESTION_INVALIDATIONS.json"
        if (
            not invalidation_path.is_file()
            or sha256_file(invalidation_path) != str(lock.get("question_invalidation_file_sha256") or "")
        ):
            raise ValueError("human-anchor question-invalidation file changed")
        invalidation = _load_json(invalidation_path)
        claimed_invalidation = str(invalidation.get("invalidation_sha256") or "")
        unsigned_invalidation = dict(invalidation)
        unsigned_invalidation.pop("invalidation_sha256", None)
        if (
            invalidation.get("schema_version") != ANCHOR_QUESTION_INVALIDATION_SCHEMA_VERSION
            or claimed_invalidation != sha256_json(unsigned_invalidation)
            or claimed_invalidation != str(lock.get("question_invalidation_sha256") or "")
            or str(invalidation.get("source_anchor_lock_sha256") or "")
            != str(lock.get("supersedes_anchor_lock_sha256") or "")
        ):
            raise ValueError("human-anchor question-invalidation provenance is invalid")
        entries = invalidation.get("invalidations")
        if not isinstance(entries, list):
            raise ValueError("human-anchor question invalidations are invalid")
        verified_ids: list[str] = []
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {"case_id", "population", "reason_code"}:
                raise ValueError("human-anchor question invalidation fields are invalid")
            if str(entry.get("reason_code") or "") not in ANCHOR_QUESTION_INVALID_REASON_CODES:
                raise ValueError("human-anchor question invalidation reason is invalid")
            verified_ids.append(str(entry.get("case_id") or ""))
        if sorted(verified_ids) != sorted(invalid_ids):
            raise ValueError("human-anchor question invalidation IDs differ from the lock")

    inputs = lock.get("inputs")
    if not isinstance(inputs, Mapping) or dict(inputs) != dict(source_lock.get("inputs") or {}):
        raise ValueError("human-anchor input provenance differs from source full pack")
    summary = lock.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("human-anchor lock summary is missing")
    if int(summary.get("outer_cue_count") or -1) != ANCHOR_OUTER_CUE_COUNT:
        raise ValueError("human-anchor outer count is invalid")
    if int(summary.get("internal_cue_count") or -1) != ANCHOR_INTERNAL_CUE_COUNT:
        raise ValueError("human-anchor internal count is invalid")
    if int(summary.get("human_boundary_point_count") or -1) != ANCHOR_TOTAL_BOUNDARY_POINTS:
        raise ValueError("human-anchor boundary point count is invalid")

    root = lock_path.parent
    for purpose in ("outer", "internal"):
        for record in selection["populations"][purpose]:
            clip = _safe_child(root, str(record["clip_relpath"]))
            if not clip.is_file() or sha256_file(clip) != str(record.get("clip_sha256") or ""):
                raise ValueError("human-anchor clip identity differs from locked source clip")
    return source_lock


def _verify_recheck_complete(root: Path, selected_ids: set[str]) -> dict[str, Any]:
    path = root / "HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json"
    payload = _load_json(path)
    if payload.get("schema_version") != ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION:
        raise ValueError("unsupported human-anchor recheck sentinel schema")
    if str(payload.get("required_ui_revision") or "") != ANCHOR_UI_REVISION:
        raise ValueError("human-anchor audit requires another UI revision")
    if str(payload.get("required_ui_ux_revision") or "") != ANCHOR_UI_UX_REVISION:
        raise ValueError("human-anchor audit requires another UI UX revision")
    if str(payload.get("last_confirmed_ui_revision") or "") != ANCHOR_UI_REVISION:
        raise ValueError("human-anchor audit has not been confirmed with the required UI revision")
    if str(payload.get("last_confirmed_ui_ux_revision") or "") != ANCHOR_UI_UX_REVISION:
        raise ValueError("human-anchor audit has not been confirmed with the required UI UX revision")
    pending = payload.get("pending_case_ids")
    confirmed = payload.get("confirmed_case_ids")
    if not isinstance(pending, list) or not isinstance(confirmed, list):
        raise ValueError("human-anchor recheck sentinel lists are invalid")
    pending_ids = [str(value or "").strip() for value in pending]
    confirmed_ids = [str(value or "").strip() for value in confirmed]
    if any(not value for value in pending_ids + confirmed_ids):
        raise ValueError("human-anchor recheck sentinel contains blank IDs")
    if len(set(pending_ids)) != len(pending_ids) or len(set(confirmed_ids)) != len(confirmed_ids):
        raise ValueError("human-anchor recheck sentinel contains duplicate IDs")
    if set(pending_ids) & set(confirmed_ids):
        raise ValueError("human-anchor pending/confirmed IDs overlap")
    if not set(pending_ids) <= selected_ids or not set(confirmed_ids) <= selected_ids:
        raise ValueError("human-anchor recheck sentinel references an unselected case")
    if pending_ids:
        raise ValueError(f"human anchor audit still requires {len(pending_ids)} UI rechecks")
    confirmed_set = set(confirmed_ids)
    if confirmed_set != selected_ids:
        missing = len(selected_ids - confirmed_set)
        raise ValueError(
            f"human anchor audit lacks UI human-confirmed records for {missing} selected cases"
        )
    return payload


def _verify_common(audit: Mapping[str, Any], locked: Mapping[str, Any]) -> None:
    pairs = (
        ("case_id", "case_id"),
        ("partition", "partition"),
        ("source_benchmark_partition", "source_benchmark_partition"),
        ("track", "track"),
        ("cue_number", "cue_number"),
        ("clip_relpath", "clip_relpath"),
        ("clip_start_ms", "clip_start_ms"),
        ("clip_end_ms", "clip_end_ms"),
        ("editor_start_ms_reference_only", "start_ms"),
        ("editor_end_ms_reference_only", "end_ms"),
        ("canonical_text", "canonical_text"),
        ("segment_count", "segment_count"),
    )
    for csv_key, lock_key in pairs:
        if str(audit.get(csv_key) or "") != str(locked.get(lock_key) or ""):
            raise ValueError(f"human-anchor audit changed locked field {csv_key}")


def _outer_records(locked: list[dict[str, Any]], audit_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    if len(locked) != ANCHOR_OUTER_CUE_COUNT or len(audit_rows) != len(locked):
        raise ValueError("outer human-anchor audit count mismatch")
    output: list[dict[str, Any]] = []
    for audit, record in zip(audit_rows, locked):
        _verify_common(audit, record)
        clip_start = int(record["clip_start_ms"])
        clip_end = int(record["clip_end_ms"])
        duration = clip_end - clip_start
        start = _parse_required_int(audit, "gold_start_clip_ms")
        end = _parse_required_int(audit, "gold_end_clip_ms")
        if not 0 <= start < end <= duration:
            raise ValueError("outer human-anchor boundaries must lie in clip and satisfy start < end")
        for kind, local_ms, clarity_key in (
            ("start", start, "gold_start_clarity"),
            ("end", end, "gold_end_clarity"),
        ):
            clarity = str(audit.get(clarity_key) or "").strip().lower()
            uncertainty = clarity_to_uncertainty_ms(clarity)
            output.append(
                {
                    "id": f"{record['case_id']}:{kind}",
                    "kind": "boundary",
                    "boundary_kind": kind,
                    "gold_ms": clip_start + local_ms,
                    "gold_uncertainty_ms": uncertainty,
                    "gold_clarity": clarity,
                    "partition": record["partition"],
                    "track": record["track"],
                    "case_id": record["case_id"],
                    "cue_number": int(record["cue_number"]),
                    "population": "outer",
                }
            )
    return output


def _internal_records(locked: list[dict[str, Any]], audit_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    if len(locked) != ANCHOR_INTERNAL_CUE_COUNT or len(audit_rows) != len(locked):
        raise ValueError("internal human-anchor audit count mismatch")
    output: list[dict[str, Any]] = []
    for audit, record in zip(audit_rows, locked):
        _verify_common(audit, record)
        index = int(record["internal_boundary_index"])
        segments = record["segments"]
        if str(audit.get("internal_boundary_index") or "") != str(index):
            raise ValueError("human-anchor audit changed internal boundary index")
        if str(audit.get("internal_left_text") or "") != str(segments[index - 1]["text"]):
            raise ValueError("human-anchor audit changed internal left text")
        if str(audit.get("internal_right_text") or "") != str(segments[index]["text"]):
            raise ValueError("human-anchor audit changed internal right text")
        local = _parse_required_int(audit, "gold_internal_clip_ms")
        clip_start = int(record["clip_start_ms"])
        clip_end = int(record["clip_end_ms"])
        if not 0 < local < clip_end - clip_start:
            raise ValueError("internal human-anchor boundary lies outside locked clip")
        absolute = clip_start + local
        clarity = str(audit.get("gold_internal_clarity") or "").strip().lower()
        uncertainty = clarity_to_uncertainty_ms(clarity)
        output.append(
            {
                "id": f"{record['case_id']}:internal",
                "kind": "boundary",
                "boundary_kind": "internal",
                "gold_ms": absolute,
                "gold_uncertainty_ms": uncertainty,
                "gold_clarity": clarity,
                "partition": record["partition"],
                "track": record["track"],
                "case_id": record["case_id"],
                "cue_number": int(record["cue_number"]),
                "population": "internal",
                "internal_boundary_index": index,
            }
        )
    return output


def build_human_boundary_anchor_artifact(lock_path: Path) -> dict[str, Any]:
    lock = _load_json(lock_path)
    source_lock = _verify_lock(lock, lock_path)
    selection = lock["selection"]
    selected_ids = {
        str(row["case_id"])
        for purpose in ("outer", "internal")
        for row in selection["populations"][purpose]
    }
    recheck = _verify_recheck_complete(lock_path.parent, selected_ids)
    recheck_path = lock_path.parent / "HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json"

    _, outer_audit = _read_csv(lock_path.parent / "outer" / "human_audit.csv")
    _, internal_audit = _read_csv(lock_path.parent / "internal" / "human_audit.csv")
    records = _outer_records(selection["populations"]["outer"], outer_audit)
    records += _internal_records(selection["populations"]["internal"], internal_audit)
    if len(records) != ANCHOR_TOTAL_BOUNDARY_POINTS:
        raise ValueError("human-anchor ingest did not produce exactly 36 points")
    if len({str(row["id"]) for row in records}) != len(records):
        raise ValueError("human-anchor boundary IDs are not unique")
    kind_counts = {
        kind: sum(row["boundary_kind"] == kind for row in records)
        for kind in ("start", "end", "internal")
    }
    if kind_counts != {"start": 12, "end": 12, "internal": 12}:
        raise ValueError("human-anchor boundary-kind counts are invalid")

    artifact: dict[str, Any] = {
        "schema_version": HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
        "authority": HUMAN_ANCHOR_GOLD_AUTHORITY,
        "language_scope": lock["inputs"]["language_scope"],
        "selection_lock_sha256": lock["lock_sha256"],
        "selection_lock_file_sha256": sha256_file(lock_path),
        "source_full_selection_lock_sha256": source_lock["lock_sha256"],
        "outer_audit_csv_sha256": sha256_file(lock_path.parent / "outer" / "human_audit.csv"),
        "internal_audit_csv_sha256": sha256_file(lock_path.parent / "internal" / "human_audit.csv"),
        "audit_confirmation": {
            "recheck_file_sha256": sha256_file(recheck_path),
            "recheck_schema_version": recheck["schema_version"],
            "required_ui_revision": recheck["required_ui_revision"],
            "required_ui_ux_revision": recheck["required_ui_ux_revision"],
            "last_confirmed_ui_revision": recheck["last_confirmed_ui_revision"],
            "last_confirmed_ui_ux_revision": recheck["last_confirmed_ui_ux_revision"],
            "pending_case_count": len(recheck["pending_case_ids"]),
            "confirmed_case_count": len(recheck["confirmed_case_ids"]),
        },
        "final_audio_sha256": lock["inputs"]["final_audio_sha256"],
        "uncertainty_policy_id": HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
        "production_policy": HUMAN_ANCHOR_PRODUCTION_POLICY.to_dict() if hasattr(HUMAN_ANCHOR_PRODUCTION_POLICY, "to_dict") else HUMAN_ANCHOR_PRODUCTION_POLICY.__dict__,
        "record_count": len(records),
        "boundary_kind_counts": kind_counts,
        "population_counts": {"outer": 24, "internal": 12},
        "records": records,
    }
    # Dataclass __dict__ is canonical here and contains only scalar policy fields.
    artifact["production_policy"] = dict(HUMAN_ANCHOR_PRODUCTION_POLICY.__dict__)
    artifact["gold_sha256"] = sha256_json(records)
    artifact["artifact_sha256"] = sha256_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    artifact = build_human_boundary_anchor_artifact(args.lock)
    if args.out.exists():
        raise FileExistsError("human-anchor gold output must be a new path")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "record_count": artifact["record_count"], "artifact_sha256": artifact["artifact_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
