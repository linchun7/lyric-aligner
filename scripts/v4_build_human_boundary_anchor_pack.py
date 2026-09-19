#!/usr/bin/env python3
"""Project a 24-clip production human-anchor pack from a locked 60-clip benchmark.

This does not re-sample from backend outcomes.  The source full pack is first
cryptographically/provenance validated, then a deterministic 12 outer + 12
internal subset is projected from its already pre-model-locked order.  Existing
human marks are copied only as values-to-recheck; they never affect membership.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.human_boundary_anchor import (
    ANCHOR_TOTAL_BOUNDARY_POINTS,
    HUMAN_ANCHOR_AUDIT_UI_REVISION,
    HUMAN_ANCHOR_AUDIT_UX_REVISION,
    HUMAN_ANCHOR_REPLACEMENT_SELECTION_SCHEMA_VERSION,
    HUMAN_ANCHOR_SELECTION_SCHEMA_VERSION,
    HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
    legacy_uncertainty_to_clarity,
    project_locked_anchor_selection,
)
from scripts.v4_ingest_human_boundary_gold import sha256_file, verify_lock


ANCHOR_PACK_SCHEMA_VERSION = "human-boundary-anchor-pack-lock-1.0"
ANCHOR_REPLACEMENT_PACK_SCHEMA_VERSION = "human-boundary-anchor-pack-lock-1.1"
ANCHOR_PACK_SCHEMA_VERSIONS = {ANCHOR_PACK_SCHEMA_VERSION, ANCHOR_REPLACEMENT_PACK_SCHEMA_VERSION}
ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION = "human-boundary-anchor-audit-recheck-1.0"
ANCHOR_QUESTION_INVALIDATION_SCHEMA_VERSION = "human-boundary-anchor-question-invalidations-1.0"
ANCHOR_QUESTION_INVALID_REASON_CODES = {"target_boundary_outside_locked_clip"}
ANCHOR_UI_REVISION = HUMAN_ANCHOR_AUDIT_UI_REVISION
ANCHOR_UI_UX_REVISION = HUMAN_ANCHOR_AUDIT_UX_REVISION


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _load_json(path: Path, *, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists() and default is not None:
        return dict(default)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _validate_question_invalidations(
    path: Path,
    *,
    source_lock: Mapping[str, Any],
    source_anchor_lock: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload = _load_json(path)
    if payload.get("schema_version") != ANCHOR_QUESTION_INVALIDATION_SCHEMA_VERSION:
        raise ValueError("unsupported human-anchor question-invalidation schema")
    claimed = str(payload.get("invalidation_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("invalidation_sha256", None)
    if len(claimed) != 64 or claimed != sha256_json(unsigned):
        raise ValueError("human-anchor question-invalidation SHA is invalid")
    if str(payload.get("source_anchor_lock_sha256") or "") != str(source_anchor_lock.get("lock_sha256") or ""):
        raise ValueError("question invalidations do not bind the superseded anchor lock")
    entries = payload.get("invalidations")
    if not isinstance(entries, list) or not entries:
        raise ValueError("question invalidations must contain at least one record")
    baseline = project_locked_anchor_selection(source_lock)
    selected_population = {
        str(row["case_id"]): purpose
        for purpose in ("outer", "internal")
        for row in baseline["populations"][purpose]
    }
    invalid_ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"case_id", "population", "reason_code"}:
            raise ValueError("question invalidation records have unsupported fields")
        case_id = str(entry.get("case_id") or "").strip()
        population = str(entry.get("population") or "").strip()
        reason = str(entry.get("reason_code") or "").strip()
        if selected_population.get(case_id) != population:
            raise ValueError("question invalidation is not a selected case/population")
        if reason not in ANCHOR_QUESTION_INVALID_REASON_CODES:
            raise ValueError("question invalidation reason is not structurally admissible")
        invalid_ids.append(case_id)
    if len(set(invalid_ids)) != len(invalid_ids):
        raise ValueError("question invalidations contain duplicate case IDs")
    return payload, tuple(sorted(invalid_ids))


def _validate_carry_forward_pack(
    pack: Path,
    *,
    source_lock: Mapping[str, Any],
) -> tuple[dict[str, Any], set[str], dict[str, dict[str, str]]]:
    root = pack.resolve()
    lock = _load_json(root / "selection.lock.json")
    if lock.get("schema_version") not in ANCHOR_PACK_SCHEMA_VERSIONS:
        raise ValueError("unsupported carry-forward anchor pack schema")
    claimed = str(lock.get("lock_sha256") or "")
    unsigned = dict(lock)
    unsigned.pop("lock_sha256", None)
    if len(claimed) != 64 or claimed != sha256_json(unsigned):
        raise ValueError("carry-forward anchor pack lock SHA is invalid")
    source = lock.get("source_full_pack")
    if not isinstance(source, Mapping) or str(source.get("selection_lock_sha256") or "") != str(source_lock.get("lock_sha256") or ""):
        raise ValueError("carry-forward pack comes from another full selection lock")
    invalid_ids = tuple(str(value) for value in lock.get("question_invalid_case_ids", []))
    expected = project_locked_anchor_selection(source_lock, question_invalid_case_ids=invalid_ids)
    if lock.get("selection") != expected:
        raise ValueError("carry-forward anchor selection is not deterministic")
    recheck = _load_json(root / "HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json")
    if recheck.get("schema_version") != ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION:
        raise ValueError("carry-forward recheck sentinel schema is invalid")
    if str(recheck.get("required_ui_revision") or "") != ANCHOR_UI_REVISION:
        raise ValueError("carry-forward pack requires another audit UI revision")
    if str(recheck.get("required_ui_ux_revision") or "") != ANCHOR_UI_UX_REVISION:
        raise ValueError("carry-forward pack requires another audit UI UX revision")
    if str(recheck.get("last_confirmed_ui_revision") or "") != ANCHOR_UI_REVISION:
        raise ValueError("carry-forward pack lacks required audit UI confirmation")
    if str(recheck.get("last_confirmed_ui_ux_revision") or "") != ANCHOR_UI_UX_REVISION:
        raise ValueError("carry-forward pack lacks required audit UI UX confirmation")
    pending = {str(value) for value in recheck.get("pending_case_ids", [])}
    confirmed = {str(value) for value in recheck.get("confirmed_case_ids", [])}
    selected_ids = {
        str(row["case_id"])
        for purpose in ("outer", "internal")
        for row in expected["populations"][purpose]
    }
    if pending or confirmed != selected_ids:
        raise ValueError("carry-forward pack is not fully human-confirmed")
    rows: dict[str, dict[str, str]] = {}
    for purpose in ("outer", "internal"):
        for row in _read_csv(root / purpose / "human_audit.csv"):
            case_id = str(row.get("case_id") or "")
            if case_id in rows:
                raise ValueError("carry-forward audit contains duplicate case IDs")
            rows[case_id] = row
    return lock, confirmed, rows


def _source_audit_index(source_root: Path, purpose: str) -> dict[str, dict[str, str]]:
    rows = _read_csv(source_root / purpose / "human_audit.csv")
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if not case_id or case_id in result:
            raise ValueError(f"source {purpose} audit case IDs are invalid")
        result[case_id] = row
    return result


def _copy_clip(source_root: Path, destination_root: Path, record: Mapping[str, Any]) -> None:
    relative = Path(str(record.get("clip_relpath") or ""))
    if relative.is_absolute() or ".." in relative.parts or not str(relative):
        raise ValueError("anchor clip path is unsafe")
    source = (source_root / relative).resolve()
    target = (destination_root / relative).resolve()
    source.relative_to(source_root.resolve())
    target.relative_to(destination_root.resolve())
    if not source.is_file():
        raise FileNotFoundError(f"source clip missing: {source}")
    if sha256_file(source) != str(record.get("clip_sha256") or ""):
        raise ValueError("source clip SHA differs from locked record")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if sha256_file(target) != str(record.get("clip_sha256") or ""):
        raise ValueError("copied anchor clip SHA mismatch")


def _base_row(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": record["case_id"],
        "partition": record["partition"],
        "source_benchmark_partition": record["source_benchmark_partition"],
        "track": record["track"],
        "cue_number": record["cue_number"],
        "clip_relpath": record["clip_relpath"],
        "clip_start_ms": record["clip_start_ms"],
        "clip_end_ms": record["clip_end_ms"],
        "editor_start_ms_reference_only": record["start_ms"],
        "editor_end_ms_reference_only": record["end_ms"],
        "canonical_text": record["canonical_text"],
        "segment_count": record["segment_count"],
    }


def _legacy_complete(purpose: str, row: Mapping[str, Any]) -> bool:
    keys = (
        ("gold_start_clip_ms", "gold_start_uncertainty_ms", "gold_end_clip_ms", "gold_end_uncertainty_ms")
        if purpose == "outer"
        else ("gold_internal_clip_ms", "gold_internal_uncertainty_ms")
    )
    return all(str(row.get(key) or "").strip() for key in keys)


def _audit_rows(
    *,
    purpose: str,
    records: list[dict[str, Any]],
    source_index: Mapping[str, Mapping[str, Any]],
    carry_index: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    prefilled: set[str] = set()
    if purpose == "outer":
        fields = [
            "case_id", "partition", "source_benchmark_partition", "track", "cue_number",
            "clip_relpath", "clip_start_ms", "clip_end_ms",
            "editor_start_ms_reference_only", "editor_end_ms_reference_only",
            "canonical_text", "segment_count",
            "gold_start_clip_ms", "gold_start_clarity",
            "gold_end_clip_ms", "gold_end_clarity", "auditor_notes",
        ]
    else:
        fields = [
            "case_id", "partition", "source_benchmark_partition", "track", "cue_number",
            "clip_relpath", "clip_start_ms", "clip_end_ms",
            "editor_start_ms_reference_only", "editor_end_ms_reference_only",
            "canonical_text", "segment_count", "internal_boundary_index",
            "internal_left_text", "internal_right_text",
            "gold_internal_clip_ms", "gold_internal_clarity", "auditor_notes",
        ]

    carry = carry_index or {}
    for record in records:
        case_id = str(record["case_id"])
        source = source_index.get(case_id)
        if source is None:
            raise ValueError(f"source audit row missing for selected case {case_id}")
        carried = carry.get(case_id)
        base = _base_row(record)
        if purpose == "outer":
            carry_keys = (
                "gold_start_clip_ms", "gold_start_clarity",
                "gold_end_clip_ms", "gold_end_clarity",
            )
        else:
            carry_keys = ("gold_internal_clip_ms", "gold_internal_clarity")
        carried_complete = carried is not None and all(
            str(carried.get(key) or "").strip() for key in carry_keys
        )
        complete = carried_complete or _legacy_complete(purpose, source)
        if complete:
            prefilled.add(case_id)
        if purpose == "outer":
            row = {
                **base,
                "gold_start_clip_ms": (
                    str(carried.get("gold_start_clip_ms") or "") if carried_complete
                    else str(source.get("gold_start_clip_ms") or "") if complete else ""
                ),
                "gold_start_clarity": (
                    str(carried.get("gold_start_clarity") or "") if carried_complete
                    else legacy_uncertainty_to_clarity(source.get("gold_start_uncertainty_ms")) if complete else ""
                ),
                "gold_end_clip_ms": (
                    str(carried.get("gold_end_clip_ms") or "") if carried_complete
                    else str(source.get("gold_end_clip_ms") or "") if complete else ""
                ),
                "gold_end_clarity": (
                    str(carried.get("gold_end_clarity") or "") if carried_complete
                    else legacy_uncertainty_to_clarity(source.get("gold_end_uncertainty_ms")) if complete else ""
                ),
                "auditor_notes": str(
                    carried.get("auditor_notes") if carried_complete else source.get("auditor_notes") or ""
                ),
            }
        else:
            boundary_index = int(record["internal_boundary_index"])
            segments = record["segments"]
            row = {
                **base,
                "internal_boundary_index": boundary_index,
                "internal_left_text": segments[boundary_index - 1]["text"],
                "internal_right_text": segments[boundary_index]["text"],
                "gold_internal_clip_ms": (
                    str(carried.get("gold_internal_clip_ms") or "") if carried_complete
                    else str(source.get("gold_internal_clip_ms") or "") if complete else ""
                ),
                "gold_internal_clarity": (
                    str(carried.get("gold_internal_clarity") or "") if carried_complete
                    else legacy_uncertainty_to_clarity(source.get("gold_internal_uncertainty_ms")) if complete else ""
                ),
                "auditor_notes": str(
                    carried.get("auditor_notes") if carried_complete else source.get("auditor_notes") or ""
                ),
            }
        rows.append(row)
    return fields, rows, prefilled


def _recheck_membership(
    *,
    selected_ids: set[str],
    prefilled_ids: set[str],
    source_pending: set[str],
    source_confirmed: set[str],
    carry_confirmed: set[str],
    replacement_ids: set[str],
) -> tuple[list[str], list[str]]:
    confirmed = sorted(
        ((source_confirmed | carry_confirmed) & selected_ids) - replacement_ids
    )
    pending = sorted(
        ((source_pending - carry_confirmed) & selected_ids)
        | (prefilled_ids - set(confirmed))
        | replacement_ids
    )
    if set(confirmed) & set(pending):
        raise ValueError("human-anchor pending/confirmed membership overlaps")
    return confirmed, pending


def build_anchor_pack(
    source_pack: Path,
    out_dir: Path,
    *,
    question_invalidations: Path | None = None,
    carry_forward_anchor_pack: Path | None = None,
) -> dict[str, Any]:
    source_root = source_pack.resolve()
    destination = out_dir.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("anchor output directory must be new/empty")
    destination.mkdir(parents=True, exist_ok=True)

    source_lock_path = source_root / "selection.lock.json"
    full_lock = _load_json(source_lock_path)
    # Full verification proves exact source report/audio/lyrics/clips and the old
    # deterministic 30+30 selection before we project the smaller authority pack.
    verify_lock(full_lock, source_lock_path)
    if (question_invalidations is None) != (carry_forward_anchor_pack is None):
        raise ValueError("question invalidations and carry-forward anchor pack are required together")
    invalidation_payload: dict[str, Any] | None = None
    invalid_ids: tuple[str, ...] = ()
    carry_lock: dict[str, Any] | None = None
    carry_confirmed: set[str] = set()
    carry_rows: dict[str, dict[str, str]] = {}
    old_selected_ids: set[str] = set()
    if carry_forward_anchor_pack is not None and question_invalidations is not None:
        carry_lock, carry_confirmed, carry_rows = _validate_carry_forward_pack(
            carry_forward_anchor_pack,
            source_lock=full_lock,
        )
        old_selected_ids = {
            str(row["case_id"])
            for purpose in ("outer", "internal")
            for row in carry_lock["selection"]["populations"][purpose]
        }
        invalidation_payload, invalid_ids = _validate_question_invalidations(
            question_invalidations,
            source_lock=full_lock,
            source_anchor_lock=carry_lock,
        )
    selection = project_locked_anchor_selection(
        full_lock,
        question_invalid_case_ids=invalid_ids,
    )
    expected_schema = (
        HUMAN_ANCHOR_REPLACEMENT_SELECTION_SCHEMA_VERSION
        if invalid_ids
        else HUMAN_ANCHOR_SELECTION_SCHEMA_VERSION
    )
    if selection.get("schema_version") != expected_schema:
        raise ValueError("anchor selection schema mismatch")

    source_recheck = _load_json(
        source_root / "HUMAN_AUDIT_RECHECK_REQUIRED.json",
        default={"pending_case_ids": [], "confirmed_case_ids": []},
    )
    source_pending = {str(value) for value in source_recheck.get("pending_case_ids", [])}
    source_confirmed = {str(value) for value in source_recheck.get("confirmed_case_ids", [])}

    selected_ids: set[str] = set()
    prefilled_ids: set[str] = set()
    for purpose in ("outer", "internal"):
        records = [dict(row) for row in selection["populations"][purpose]]
        source_index = _source_audit_index(source_root, purpose)
        fields, audit_rows, prefilled = _audit_rows(
            purpose=purpose,
            records=records,
            source_index=source_index,
            carry_index=carry_rows,
        )
        _write_csv(destination / purpose / "human_audit.csv", fields, audit_rows)
        for record in records:
            _copy_clip(source_root, destination, record)
            selected_ids.add(str(record["case_id"]))
        prefilled_ids.update(prefilled)

    # Any legacy prefilled value not explicitly confirmed by the repaired UI must
    # be rechecked.  This preserves the current 7 rows without silently promoting
    # the six rows known to have been saved with the old broken UI.
    replacement_ids = selected_ids - old_selected_ids if invalid_ids else set()
    confirmed, pending = _recheck_membership(
        selected_ids=selected_ids,
        prefilled_ids=prefilled_ids,
        source_pending=source_pending,
        source_confirmed=source_confirmed,
        carry_confirmed=carry_confirmed,
        replacement_ids=replacement_ids,
    )
    recheck = {
        "schema_version": ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
        "required_ui_revision": ANCHOR_UI_REVISION,
        "required_ui_ux_revision": ANCHOR_UI_UX_REVISION,
        "reason": "Legacy marks are retained as initial candidates only. Recheck values not confirmed by the repaired audit UI before production authority.",
        "pending_case_ids": pending,
        "confirmed_case_ids": confirmed,
    }
    atomic_json(destination / "HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json", recheck)
    if invalidation_payload is not None:
        atomic_json(destination / "QUESTION_INVALIDATIONS.json", invalidation_payload)

    lock: dict[str, Any] = {
        "schema_version": (
            ANCHOR_REPLACEMENT_PACK_SCHEMA_VERSION if invalid_ids else ANCHOR_PACK_SCHEMA_VERSION
        ),
        "selection": selection,
        "selection_created_from_pre_model_locked_benchmark": True,
        "backend_prediction_fields_present": False,
        "source_full_pack": {
            "selection_lock_path": str(source_lock_path),
            "selection_lock_file_sha256": sha256_file(source_lock_path),
            "selection_lock_sha256": full_lock["lock_sha256"],
        },
        "inputs": dict(full_lock["inputs"]),
        "audit_interaction_policy": {
            "ui_revision": ANCHOR_UI_REVISION,
            "candidate_role": "machine_or_legacy_initialization_only_never_gold",
            "human_decisions": ["too_early", "correct", "too_late"],
            "final_clarity_classes": ["clear", "ambiguous"],
            "uncertainty_policy_id": HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
        },
        "summary": {
            "outer_cue_count": len(selection["populations"]["outer"]),
            "internal_cue_count": len(selection["populations"]["internal"]),
            "human_boundary_point_count": ANCHOR_TOTAL_BOUNDARY_POINTS,
            "legacy_prefilled_selected_count": len(prefilled_ids),
            "legacy_confirmed_selected_count": len(confirmed),
            "legacy_recheck_selected_count": len(pending),
        },
    }
    if invalid_ids:
        if carry_lock is None or invalidation_payload is None:
            raise AssertionError("replacement anchor provenance is incomplete")
        lock["question_invalid_case_ids"] = list(invalid_ids)
        lock["question_invalidation_sha256"] = invalidation_payload["invalidation_sha256"]
        lock["question_invalidation_file_sha256"] = sha256_file(
            destination / "QUESTION_INVALIDATIONS.json"
        )
        lock["supersedes_anchor_lock_sha256"] = carry_lock["lock_sha256"]
        lock["summary"]["question_invalidated_count"] = len(invalid_ids)
        lock["summary"]["replacement_pending_count"] = len(replacement_ids)
        lock["summary"]["carried_forward_confirmed_count"] = len(
            set(confirmed) & old_selected_ids
        )
    lock["lock_sha256"] = sha256_json(lock)
    atomic_json(destination / "selection.lock.json", lock)
    (destination / "README-AUDIT.txt").write_text(
        "Production human-anchor audit pack (24 clips / 36 boundary points).\n\n"
        "This is a deterministic projection of the already pre-model-locked 60-clip benchmark.\n"
        "Backend/model predictions must never change anchor membership. Machine consensus is only an initial listening candidate.\n"
        "Human interaction: listen around the candidate, answer too early / correct / too late until satisfied, then choose clear or ambiguous.\n"
        "clear maps to a conservative 50ms perceptual half-width; ambiguous maps to 100ms. The listener never estimates milliseconds of uncertainty.\n"
        "Legacy marks are retained only as candidates; cases listed in HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json remain non-authoritative until rechecked.\n",
        encoding="utf-8",
    )
    return lock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pack", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--question-invalidations", type=Path)
    parser.add_argument("--carry-forward-anchor-pack", type=Path)
    args = parser.parse_args()
    lock = build_anchor_pack(
        args.source_pack,
        args.out_dir,
        question_invalidations=args.question_invalidations,
        carry_forward_anchor_pack=args.carry_forward_anchor_pack,
    )
    print(json.dumps({"out_dir": str(args.out_dir), "lock_sha256": lock["lock_sha256"], **lock["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
