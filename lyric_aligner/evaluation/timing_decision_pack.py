"""Freeze decision-sensitive timing cases before any human truth is read.

The pack compares two already-materialized subtitle audits by stable canonical identity,
not cue number or text.  It selects changed boundaries plus deterministic unchanged
controls.  The output intentionally contains no gold labels and grants no production
authority.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.text_repair import _normalize_for_match

SCHEMA_VERSION = "timing-decision-pack-1.0"
GOLD_SCHEMA_VERSION = "timing-decision-human-gold-1.0"
POLICY_ID = "canonical-identity-decision-sensitive-selection-1.0"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _int(raw: Any, field: str, position: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"row {position} has invalid {field}") from exc
    if value < 0:
        raise ValueError(f"row {position} has negative {field}")
    return value


def _single_line_identity(row: Mapping[str, str], position: int) -> tuple[str, int] | None:
    occurrence_id = str(row.get("occurrence_id") or "").strip()
    if not occurrence_id:
        return None
    multi_raw = row.get("canonical_line_indices")
    if multi_raw not in (None, ""):
        try:
            parsed = json.loads(str(multi_raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"row {position} has invalid canonical_line_indices") from exc
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"row {position} has invalid canonical_line_indices")
        values = [int(value) for value in parsed]
        if len(values) != 1:
            return None
        line_index = values[0]
    else:
        raw = row.get("canonical_line_index")
        if raw in (None, ""):
            return None
        line_index = _int(raw, "canonical_line_index", position)
    return occurrence_id, line_index


def _index_rows(rows: Sequence[Mapping[str, str]]) -> tuple[dict[tuple[str, int], Mapping[str, str]], set[tuple[str, int]]]:
    grouped: dict[tuple[str, int], list[Mapping[str, str]]] = {}
    for position, row in enumerate(rows, start=1):
        identity = _single_line_identity(row, position)
        if identity is None:
            continue
        grouped.setdefault(identity, []).append(row)
    unique = {key: values[0] for key, values in grouped.items() if len(values) == 1}
    ambiguous = {key for key, values in grouped.items() if len(values) != 1}
    return unique, ambiguous


def _opaque_id(task_fingerprint: str, occurrence_id: str, line_index: int, boundary_kind: str) -> str:
    return hashlib.sha256(
        f"{task_fingerprint}|{occurrence_id}|{line_index}|{boundary_kind}".encode("utf-8")
    ).hexdigest()


def _control_rank(case_id: str) -> str:
    return hashlib.sha256(("control|" + case_id).encode("ascii")).hexdigest()


def build_timing_decision_pack(
    *,
    old_audit: Path,
    hybrid_audit: Path,
    final_mix_sha256: str,
    min_changed_delta_ms: int = 100,
    unchanged_control_count: int = 20,
    clip_margin_ms: int = 2500,
) -> dict[str, Any]:
    if isinstance(min_changed_delta_ms, bool) or not isinstance(min_changed_delta_ms, int) or min_changed_delta_ms < 1:
        raise ValueError("min_changed_delta_ms must be a positive integer")
    if isinstance(unchanged_control_count, bool) or not isinstance(unchanged_control_count, int) or unchanged_control_count < 0:
        raise ValueError("unchanged_control_count must be a nonnegative integer")
    if isinstance(clip_margin_ms, bool) or not isinstance(clip_margin_ms, int) or clip_margin_ms < 250:
        raise ValueError("clip_margin_ms must be at least 250")
    if len(final_mix_sha256) != 64 or any(char not in "0123456789abcdef" for char in final_mix_sha256.lower()):
        raise ValueError("final_mix_sha256 must be a lowercase-compatible SHA-256")

    old_rows = _read_csv(old_audit)
    hybrid_rows = _read_csv(hybrid_audit)
    old_index, old_ambiguous = _index_rows(old_rows)
    hybrid_index, hybrid_ambiguous = _index_rows(hybrid_rows)
    shared = sorted(set(old_index) & set(hybrid_index))
    hybrid_task_values = {
        str(row.get("task_fingerprint_sha256") or "").strip()
        for row in hybrid_rows
        if str(row.get("task_fingerprint_sha256") or "").strip()
    }
    old_task_values = {
        str(row.get("task_fingerprint_sha256") or "").strip()
        for row in old_rows
        if str(row.get("task_fingerprint_sha256") or "").strip()
    }
    if len(hybrid_task_values) != 1 or len(old_task_values) != 1:
        raise ValueError("old and hybrid audits must each contain exactly one task fingerprint")
    task_fingerprint = next(iter(hybrid_task_values))
    if next(iter(old_task_values)) != task_fingerprint:
        raise ValueError("old and hybrid audits belong to different tasks")

    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for occurrence_id, line_index in shared:
        old_row = old_index[(occurrence_id, line_index)]
        new_row = hybrid_index[(occurrence_id, line_index)]
        old_track = str(old_row.get("track_id") or "").strip()
        new_track = str(new_row.get("track_id") or "").strip()
        if old_track and new_track and old_track != new_track:
            continue
        track_id = new_track or old_track
        old_text = str(old_row.get("text") or "")
        new_text = str(new_row.get("text") or "")
        if not old_text or not new_text or _normalize_for_match(old_text) != _normalize_for_match(new_text):
            continue
        target_text = new_text
        for boundary_kind, field in (("start", "start_ms"), ("end", "end_ms")):
            old_ms = _int(old_row.get(field), field, line_index + 1)
            hybrid_ms = _int(new_row.get(field), field, line_index + 1)
            case_id = _opaque_id(task_fingerprint, occurrence_id, line_index, boundary_kind)
            delta = hybrid_ms - old_ms
            case = {
                "id": case_id,
                "track_hash": hashlib.sha256(track_id.encode("utf-8")).hexdigest() if track_id else None,
                "target_text": target_text,
                "target_text_sha256": hashlib.sha256(target_text.encode("utf-8")).hexdigest(),
                "boundary_kind": boundary_kind,
                "old_final_ms": old_ms,
                "hybrid_ms": hybrid_ms,
                "absolute_change_ms": abs(delta),
                "signed_change_ms": delta,
                "clip_start_ms": max(0, min(old_ms, hybrid_ms) - clip_margin_ms),
                "clip_end_ms": max(old_ms, hybrid_ms) + clip_margin_ms,
                "private_identity": {
                    "occurrence_id": occurrence_id,
                    "canonical_line_index": line_index,
                },
            }
            if abs(delta) >= min_changed_delta_ms:
                case["selection_reason"] = "decision_changed_above_threshold"
                changed.append(case)
            elif delta == 0:
                case["selection_reason"] = "deterministic_unchanged_control"
                unchanged.append(case)

    unchanged.sort(key=lambda item: (_control_rank(str(item["id"])), str(item["id"])))
    controls = unchanged[:unchanged_control_count]
    selected = sorted(changed + controls, key=lambda item: str(item["id"]))
    pack: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "purpose": "pre_gold_frozen_decision_validation_selection_never_production_authority",
        "gold_read": False,
        "task_fingerprint_sha256": task_fingerprint,
        "final_mix_sha256": final_mix_sha256.lower(),
        "old_audit_sha256": _sha256_file(old_audit),
        "hybrid_audit_sha256": _sha256_file(hybrid_audit),
        "selection_config": {
            "min_changed_delta_ms": min_changed_delta_ms,
            "unchanged_control_count": unchanged_control_count,
            "clip_margin_ms": clip_margin_ms,
            "single_canonical_line_identity_only": True,
        },
        "shared_unique_identity_count": len(shared),
        "old_ambiguous_identity_count": len(old_ambiguous),
        "hybrid_ambiguous_identity_count": len(hybrid_ambiguous),
        "changed_case_count": len(changed),
        "control_case_count": len(controls),
        "selected_case_count": len(selected),
        "cases": selected,
    }
    bare = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    pack["selection_lock_sha256"] = hashlib.sha256(bare).hexdigest()
    return pack


def verify_selection_lock(pack: Mapping[str, Any]) -> str:
    if pack.get("schema_version") != SCHEMA_VERSION or pack.get("policy_id") != POLICY_ID:
        raise ValueError("timing decision pack identity mismatch")
    if pack.get("gold_read") is not False:
        raise ValueError("timing decision pack must be frozen before gold is read")
    expected = pack.get("selection_lock_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("timing decision pack is missing selection lock")
    bare = dict(pack)
    bare.pop("selection_lock_sha256", None)
    actual = hashlib.sha256(
        json.dumps(bare, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if actual != expected:
        raise ValueError("timing decision pack selection lock mismatch")
    return expected


def merge_timing_decision_gold(
    pack: Mapping[str, Any],
    gold: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Bind complete human gold to a pre-gold locked pack for offline evaluation."""
    lock = verify_selection_lock(pack)
    if gold.get("schema_version") != GOLD_SCHEMA_VERSION:
        raise ValueError("timing decision gold schema mismatch")
    if gold.get("selection_lock_sha256") != lock:
        raise ValueError("timing decision gold belongs to another selection lock")
    partition = gold.get("partition")
    if partition not in {"development", "calibration", "blind", "holdout", "regression"}:
        raise ValueError("timing decision gold partition is invalid")
    raw_gold = gold.get("records")
    raw_invalid = gold.get("invalid_records", [])
    if not isinstance(raw_gold, list) or not isinstance(raw_invalid, list):
        raise ValueError("timing decision gold records/invalid_records must be lists")
    cases = pack.get("cases")
    if not isinstance(cases, list):
        raise ValueError("timing decision pack cases must be a list")
    case_by_id: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("timing decision pack contains a non-object case")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in case_by_id:
            raise ValueError("timing decision pack has invalid or duplicate case id")
        case_by_id[case_id] = case

    gold_by_id: dict[str, Mapping[str, Any]] = {}
    for row in raw_gold:
        if not isinstance(row, Mapping):
            raise ValueError("timing decision gold contains a non-object record")
        record_id = row.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in gold_by_id:
            raise ValueError("timing decision gold has invalid or duplicate id")
        if record_id not in case_by_id:
            raise ValueError("timing decision gold contains an unknown case id")
        gold_ms = row.get("gold_ms")
        uncertainty_ms = row.get("uncertainty_ms", 0)
        if isinstance(gold_ms, bool) or not isinstance(gold_ms, int) or gold_ms < 0:
            raise ValueError("timing decision gold_ms must be a nonnegative integer")
        if (
            isinstance(uncertainty_ms, bool)
            or not isinstance(uncertainty_ms, int)
            or uncertainty_ms < 0
        ):
            raise ValueError("timing decision uncertainty_ms must be a nonnegative integer")
        gold_by_id[record_id] = {
            "id": record_id,
            "gold_ms": gold_ms,
            "uncertainty_ms": uncertainty_ms,
        }
    invalid_by_id: dict[str, str] = {}
    for row in raw_invalid:
        if not isinstance(row, Mapping):
            raise ValueError("timing decision invalid gold contains a non-object record")
        record_id = row.get("id")
        if (
            not isinstance(record_id, str)
            or not record_id
            or record_id in invalid_by_id
            or record_id in gold_by_id
        ):
            raise ValueError("timing decision invalid gold has duplicate/conflicting id")
        if record_id not in case_by_id:
            raise ValueError("timing decision invalid gold contains an unknown case id")
        reason = str(row.get("reason") or "").strip()
        if not reason:
            raise ValueError("timing decision invalid gold requires a reason")
        invalid_by_id[record_id] = reason
    accounted = set(gold_by_id) | set(invalid_by_id)
    if accounted != set(case_by_id):
        missing = sorted(set(case_by_id) - accounted)
        raise ValueError(f"timing decision gold is incomplete; missing {len(missing)} cases")

    records: list[dict[str, Any]] = []
    for case_id in sorted(gold_by_id):
        case = case_by_id[case_id]
        truth = gold_by_id[case_id]
        record: dict[str, Any] = {
            "id": case_id,
            "track": str(case.get("track_hash") or case_id),
            "partition": partition,
            "boundary_kind": case["boundary_kind"],
            "gold_ms": truth["gold_ms"],
            "uncertainty_ms": truth["uncertainty_ms"],
            "old_final_ms": case["old_final_ms"],
            "hybrid_ms": case["hybrid_ms"],
        }
        if case.get("editor_ms") is not None:
            record["editor_ms"] = case["editor_ms"]
        alternatives = case.get("alternatives")
        if isinstance(alternatives, Mapping) and alternatives:
            record["alternatives"] = dict(alternatives)
        records.append(record)
    return records
