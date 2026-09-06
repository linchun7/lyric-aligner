#!/usr/bin/env python3
"""Validate locked outer/internal human audits and materialize 90 boundary-gold rows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import soundfile as sf

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.human_boundary_gold import (
    HUMAN_GOLD_SELECTION_SCHEMA_VERSION,
    candidate_from_report_row,
    select_gold_candidates,
)
from lyric_aligner.timeline.boundary_calibration import BoundaryCalibrationPolicy
from scripts.v4_build_human_boundary_gold_pack import (
    CLIP_CONTEXT_MS,
    load_lyric_map,
    load_report,
    safe_clip_name,
)

PACK_SCHEMA_VERSION = "human-boundary-gold-pack-lock-2.1"
GOLD_SCHEMA_VERSION = "human-boundary-gold-2.1"
EXPECTED_CUES_PER_POPULATION = 30
EXPECTED_BOUNDARY_POINTS = 90
PACK_INVALIDATION_SENTINEL = "HUMAN_GOLD_PACK_INVALIDATED.json"
AUDIT_RECHECK_SENTINEL = "HUMAN_AUDIT_RECHECK_REQUIRED.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_required_int(row: Mapping[str, Any], key: str) -> int:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"human audit field {key} is blank")
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"human audit field {key} must be integer milliseconds") from exc
    if result < 0:
        raise ValueError(f"human audit field {key} must be nonnegative")
    return result


def _safe_child(root: Path, relative: str) -> Path:
    rel = Path(str(relative or ""))
    if not str(relative or "").strip() or rel.is_absolute() or ".." in rel.parts:
        raise ValueError("locked relative path is unsafe")
    root_resolved = root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("locked relative path escapes gold-pack root") from exc
    return target


def _verify_selection(selection: Mapping[str, Any], *, purpose: str) -> None:
    if selection.get("schema_version") != HUMAN_GOLD_SELECTION_SCHEMA_VERSION:
        raise ValueError("unsupported human-gold selection schema")
    if selection.get("purpose") != purpose:
        raise ValueError("human-gold selection purpose mismatch")
    claimed = str(selection.get("selection_sha256") or "")
    without_hash = dict(selection)
    without_hash.pop("selection_sha256", None)
    if len(claimed) != 64 or claimed != sha256_json(without_hash):
        raise ValueError("human-gold selection SHA is invalid")
    records = selection.get("records")
    if not isinstance(records, list) or len(records) != EXPECTED_CUES_PER_POPULATION:
        raise ValueError("production selection must contain exactly 30 cues")
    ids = [str(row.get("case_id") or "") for row in records if isinstance(row, Mapping)]
    if len(ids) != EXPECTED_CUES_PER_POPULATION or len(set(ids)) != EXPECTED_CUES_PER_POPULATION:
        raise ValueError("selection case IDs must be unique/non-empty")
    if purpose == "outer":
        if any(row.get("boundary_kinds") != ["start", "end"] for row in records):
            raise ValueError("outer selection boundary-kind contract is invalid")
    else:
        if any(row.get("boundary_kinds") != ["internal"] for row in records):
            raise ValueError("internal selection boundary-kind contract is invalid")
        if any(int(row.get("segment_count") or 0) < 2 for row in records):
            raise ValueError("internal selection contains a non-multisegment cue")
        if any(row.get("internal_boundary_index") is None for row in records):
            raise ValueError("internal selection lacks canonical boundary identity")


def _verify_clip_against_final_mix(
    *,
    root: Path,
    purpose: str,
    index: int,
    materialized: Mapping[str, Any],
    audio_path: Path,
    audio_info: Any,
) -> None:
    expected_relpath = f"{purpose}/clips/{safe_clip_name(index, str(materialized.get('case_id') or ''))}"
    if str(materialized.get("clip_relpath") or "") != expected_relpath:
        raise ValueError("human audit clip path does not match deterministic pack layout")
    samplerate = int(audio_info.samplerate)
    total_frames = int(audio_info.frames)
    start_ms = int(materialized.get("start_ms"))
    end_ms = int(materialized.get("end_ms"))
    start_frame = max(0, int(round(max(0, start_ms - CLIP_CONTEXT_MS) * samplerate / 1000.0)))
    end_frame = min(
        total_frames,
        int(round((end_ms + CLIP_CONTEXT_MS) * samplerate / 1000.0)),
    )
    if end_frame <= start_frame:
        raise ValueError("deterministic human audit clip interval is empty")
    expected_start_ms = int(round(start_frame * 1000.0 / samplerate))
    expected_end_ms = int(round(end_frame * 1000.0 / samplerate))
    if (
        int(materialized.get("clip_start_ms")) != expected_start_ms
        or int(materialized.get("clip_end_ms")) != expected_end_ms
    ):
        raise ValueError("human audit clip interval does not match final-mix derivation")

    clip = _safe_child(root, expected_relpath)
    if not clip.is_file():
        raise ValueError("human audit clip is missing")
    actual_sha = sha256_file(clip)
    if actual_sha != str(materialized.get("clip_sha256") or ""):
        raise ValueError("human audit clip identity mismatch")
    data, actual_rate = sf.read(
        str(audio_path),
        start=start_frame,
        stop=end_frame,
        dtype="float32",
        always_2d=True,
    )
    if not len(data):
        raise ValueError("deterministic audit clip decoded no final-mix audio")
    with tempfile.TemporaryDirectory(prefix="human-gold-clip-verify-") as temporary:
        regenerated = Path(temporary) / "expected.wav"
        sf.write(str(regenerated), data, actual_rate, subtype="PCM_16")
        if sha256_file(regenerated) != actual_sha:
            raise ValueError("human audit clip bytes do not match deterministic final-mix slice")


def _verify_population(
    population: Mapping[str, Any],
    *,
    purpose: str,
    root: Path,
    expected_selection: Mapping[str, Any],
    audio_path: Path,
    audio_info: Any,
) -> tuple[list[Mapping[str, Any]], Path]:
    if population.get("purpose") != purpose:
        raise ValueError("human-gold population purpose mismatch")
    selection = population.get("selection")
    if not isinstance(selection, Mapping):
        raise ValueError("human-gold population has no embedded selection")
    _verify_selection(selection, purpose=purpose)
    if dict(selection) != dict(expected_selection):
        raise ValueError("embedded human-gold selection does not match fresh deterministic selection")
    if population.get("selection_schema_version") != selection.get("schema_version"):
        raise ValueError("population selection schema identity mismatch")
    if population.get("selection_policy_id") != selection.get("policy_id"):
        raise ValueError("population selection policy identity mismatch")
    if population.get("selection_sha256") != selection.get("selection_sha256"):
        raise ValueError("population selection SHA identity mismatch")

    selected_records = selection["records"]
    records = population.get("records")
    if not isinstance(records, list) or len(records) != len(selected_records):
        raise ValueError("materialized population record count mismatch")
    for index, (selected, materialized) in enumerate(zip(selected_records, records), start=1):
        if not isinstance(selected, Mapping) or not isinstance(materialized, Mapping):
            raise ValueError("population record must be an object")
        for key, value in selected.items():
            if materialized.get(key) != value:
                raise ValueError(f"materialized population changed selected field {key}")
        _verify_clip_against_final_mix(
            root=root,
            purpose=purpose,
            index=index,
            materialized=materialized,
            audio_path=audio_path,
            audio_info=audio_info,
        )

    expected_audit_relpath = f"{purpose}/human_audit.csv"
    if str(population.get("audit_relpath") or "") != expected_audit_relpath:
        raise ValueError("human audit CSV path does not match deterministic pack layout")
    audit_path = _safe_child(root, expected_audit_relpath)
    if not audit_path.is_file():
        raise ValueError("human audit CSV is missing")
    return records, audit_path


def _verify_input_provenance(lock: Mapping[str, Any]) -> dict[str, Path]:
    inputs = lock.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("human-gold lock has no input provenance")
    language_scope = str(inputs.get("language_scope") or "").strip().lower()
    if (
        not language_scope
        or language_scope != str(inputs.get("language_scope") or "")
        or not re.fullmatch(r"[a-z0-9-]+", language_scope)
    ):
        raise ValueError("human-gold language scope is missing or non-canonical")
    report = Path(str(inputs.get("report_path") or ""))
    audio = Path(str(inputs.get("final_audio_path") or ""))
    lyric_dir = Path(str(inputs.get("canonical_lyrics_dir") or ""))
    if not report.is_file() or sha256_file(report) != str(inputs.get("report_sha256") or ""):
        raise ValueError("source boundary report changed after gold selection")
    if not audio.is_file() or sha256_file(audio) != str(inputs.get("final_audio_sha256") or ""):
        raise ValueError("final mix changed after gold selection")
    hashes = inputs.get("canonical_lyric_sha256_by_name")
    if not lyric_dir.is_dir() or not isinstance(hashes, Mapping) or not hashes:
        raise ValueError("canonical lyric provenance is incomplete")
    current_names = {path.name for path in lyric_dir.glob("*.lrc")}
    if current_names != set(str(name) for name in hashes):
        raise ValueError("canonical lyric file set changed after gold selection")
    for name, expected_sha in hashes.items():
        path = lyric_dir / str(name)
        if not path.is_file() or sha256_file(path) != str(expected_sha):
            raise ValueError("canonical lyric file changed after gold selection")
    return {"report": report, "audio": audio, "lyric_dir": lyric_dir}


def verify_lock(lock: Mapping[str, Any], lock_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if lock.get("schema_version") != PACK_SCHEMA_VERSION:
        raise ValueError("unsupported human-gold pack schema")
    claimed = str(lock.get("lock_sha256") or "")
    without_hash = dict(lock)
    without_hash.pop("lock_sha256", None)
    if len(claimed) != 64 or claimed != sha256_json(without_hash):
        raise ValueError("human-gold pack lock hash is invalid")
    if lock.get("selection_created_before_backend_scoring") is not True:
        raise ValueError("selection lock does not attest pre-model selection")
    if lock.get("backend_prediction_fields_present") is not False:
        raise ValueError("selection lock is contaminated by backend predictions")
    try:
        population_case_overlap_count = int(lock["population_case_overlap_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("outer/internal gold overlap count is invalid") from exc
    if population_case_overlap_count != 0:
        raise ValueError("outer/internal gold populations are not declared disjoint")
    paths = _verify_input_provenance(lock)

    lyric_map, _ = load_lyric_map(paths["lyric_dir"])
    report_rows = load_report(paths["report"])
    candidates = []
    for row in report_rows:
        candidate = candidate_from_report_row(row, lyric_lines_by_track=lyric_map)
        if candidate is not None:
            candidates.append(candidate)
    if int(lock.get("candidate_pool_count") or -1) != len(candidates):
        raise ValueError("human-gold candidate pool count does not match current deterministic reconstruction")
    expected_outer = select_gold_candidates(candidates, purpose="outer")
    expected_outer_ids = {str(row["case_id"]) for row in expected_outer["records"]}
    expected_internal = select_gold_candidates(
        candidates,
        purpose="internal",
        excluded_case_ids=expected_outer_ids,
    )

    populations = lock.get("populations")
    if not isinstance(populations, Mapping) or set(populations) != {"outer", "internal"}:
        raise ValueError("human-gold lock must contain outer and internal populations")
    audio_info = sf.info(str(paths["audio"]))
    outer_records, outer_audit = _verify_population(
        populations["outer"],
        purpose="outer",
        root=lock_path.parent,
        expected_selection=expected_outer,
        audio_path=paths["audio"],
        audio_info=audio_info,
    )
    internal_records, internal_audit = _verify_population(
        populations["internal"],
        purpose="internal",
        root=lock_path.parent,
        expected_selection=expected_internal,
        audio_path=paths["audio"],
        audio_info=audio_info,
    )
    outer_ids = {str(row["case_id"]) for row in outer_records}
    internal_ids = {str(row["case_id"]) for row in internal_records}
    if outer_ids & internal_ids:
        raise ValueError("outer/internal gold populations overlap")
    summary = lock.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("human-gold lock summary is missing")
    if int(summary.get("outer_cue_count") or -1) != EXPECTED_CUES_PER_POPULATION:
        raise ValueError("outer cue count is not production size")
    if int(summary.get("internal_cue_count") or -1) != EXPECTED_CUES_PER_POPULATION:
        raise ValueError("internal cue count is not production size")
    if int(summary.get("human_boundary_point_count") or -1) != EXPECTED_BOUNDARY_POINTS:
        raise ValueError("human boundary point count is not production size")
    return {
        "records": outer_records,
        "audit_path": outer_audit,
    }, {
        "records": internal_records,
        "audit_path": internal_audit,
    }


def _load_audit(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        forbidden = ("backend", "prediction", "model_id", "model_revision")
        if any(term in header.casefold() for header in headers for term in forbidden):
            raise ValueError("human audit CSV must not contain backend prediction columns")
        rows = list(reader)
    return rows


def _verify_common_audit_identity(audit: Mapping[str, Any], locked: Mapping[str, Any]) -> None:
    immutable_columns = (
        ("case_id", "case_id"),
        ("partition", "partition"),
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
    for csv_key, lock_key in immutable_columns:
        if str(audit.get(csv_key) or "") != str(locked.get(lock_key) or ""):
            raise ValueError(f"human audit changed locked field {csv_key}")


def _outer_gold(records: list[Mapping[str, Any]], audit_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    if len(records) != len(audit_rows):
        raise ValueError("outer audit row count changed after selection lock")
    max_uncertainty = BoundaryCalibrationPolicy().max_gold_uncertainty_ms
    output: list[dict[str, Any]] = []
    for audit, locked in zip(audit_rows, records):
        _verify_common_audit_identity(audit, locked)
        clip_start = int(locked["clip_start_ms"])
        clip_end = int(locked["clip_end_ms"])
        duration = clip_end - clip_start
        start_local = parse_required_int(audit, "gold_start_clip_ms")
        end_local = parse_required_int(audit, "gold_end_clip_ms")
        start_uncertainty = parse_required_int(audit, "gold_start_uncertainty_ms")
        end_uncertainty = parse_required_int(audit, "gold_end_uncertainty_ms")
        if start_local > duration or end_local > duration:
            raise ValueError("outer human gold point falls outside audit clip")
        if start_local >= end_local:
            raise ValueError("outer human gold must satisfy start < end")
        if max(start_uncertainty, end_uncertainty) > max_uncertainty:
            raise ValueError("outer human gold uncertainty exceeds production policy")
        for kind, local_ms, uncertainty in (
            ("start", start_local, start_uncertainty),
            ("end", end_local, end_uncertainty),
        ):
            output.append(
                {
                    "id": f"{locked['case_id']}:{kind}",
                    "kind": "boundary",
                    "boundary_kind": kind,
                    "gold_ms": clip_start + local_ms,
                    "gold_uncertainty_ms": uncertainty,
                    "partition": locked["partition"],
                    "track": locked["track"],
                    "case_id": locked["case_id"],
                    "cue_number": int(locked["cue_number"]),
                    "population": "outer",
                }
            )
    return output


def _internal_gold(records: list[Mapping[str, Any]], audit_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    if len(records) != len(audit_rows):
        raise ValueError("internal audit row count changed after selection lock")
    max_uncertainty = BoundaryCalibrationPolicy().max_gold_uncertainty_ms
    output: list[dict[str, Any]] = []
    for audit, locked in zip(audit_rows, records):
        _verify_common_audit_identity(audit, locked)
        boundary_index = int(locked["internal_boundary_index"])
        segments = locked["segments"]
        if not 1 <= boundary_index < len(segments):
            raise ValueError("locked internal boundary index is invalid")
        if str(audit.get("internal_boundary_index") or "") != str(boundary_index):
            raise ValueError("human audit changed internal boundary index")
        if str(audit.get("internal_left_text") or "") != str(segments[boundary_index - 1]["text"]):
            raise ValueError("human audit changed internal left segment text")
        if str(audit.get("internal_right_text") or "") != str(segments[boundary_index]["text"]):
            raise ValueError("human audit changed internal right segment text")
        clip_start = int(locked["clip_start_ms"])
        clip_end = int(locked["clip_end_ms"])
        duration = clip_end - clip_start
        local_ms = parse_required_int(audit, "gold_internal_clip_ms")
        uncertainty = parse_required_int(audit, "gold_internal_uncertainty_ms")
        if not 0 < local_ms < duration:
            raise ValueError("internal human gold point falls outside locked audit clip")
        absolute_ms = clip_start + local_ms
        if uncertainty > max_uncertainty:
            raise ValueError("internal human gold uncertainty exceeds production policy")
        output.append(
            {
                "id": f"{locked['case_id']}:internal",
                "kind": "boundary",
                "boundary_kind": "internal",
                "gold_ms": absolute_ms,
                "gold_uncertainty_ms": uncertainty,
                "partition": locked["partition"],
                "track": locked["track"],
                "case_id": locked["case_id"],
                "cue_number": int(locked["cue_number"]),
                "population": "internal",
                "internal_boundary_index": boundary_index,
            }
        )
    return output


def build_human_boundary_gold_artifact(lock_path: Path) -> dict[str, Any]:
    invalidation_path = lock_path.parent / PACK_INVALIDATION_SENTINEL
    if invalidation_path.exists():
        try:
            invalidation = json.loads(invalidation_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            invalidation = {}
        reason = str(invalidation.get("reason") or "pack was explicitly invalidated")
        raise ValueError(
            f"human-gold pack is invalidated by {PACK_INVALIDATION_SENTINEL}: {reason}"
        )
    recheck_path = lock_path.parent / AUDIT_RECHECK_SENTINEL
    if recheck_path.exists():
        try:
            recheck = json.loads(recheck_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"human audit recheck sentinel is unreadable: {AUDIT_RECHECK_SENTINEL}") from exc
        pending = recheck.get("pending_case_ids") if isinstance(recheck, Mapping) else None
        if not isinstance(pending, list):
            raise ValueError("human audit recheck sentinel has invalid pending_case_ids")
        normalized = [str(value or "").strip() for value in pending]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("human audit recheck sentinel contains invalid/duplicate case IDs")
        if normalized:
            raise ValueError(
                f"human audit still requires {len(normalized)} UI rechecks before calibration"
            )
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    if not isinstance(lock, Mapping):
        raise ValueError("selection lock must be an object")
    outer, internal = verify_lock(lock, lock_path)
    outer_audit = _load_audit(outer["audit_path"])
    internal_audit = _load_audit(internal["audit_path"])
    gold_records = _outer_gold(outer["records"], outer_audit) + _internal_gold(
        internal["records"], internal_audit
    )
    if len(gold_records) != EXPECTED_BOUNDARY_POINTS:
        raise ValueError("human-gold ingest did not produce exactly 90 boundary points")
    ids = [str(row["id"]) for row in gold_records]
    if len(set(ids)) != len(ids):
        raise ValueError("human-gold boundary IDs are not unique")

    artifact: dict[str, Any] = {
        "schema_version": GOLD_SCHEMA_VERSION,
        "authority": "human_audited_final_mix_acoustic_boundary",
        "language_scope": lock["inputs"]["language_scope"],
        "selection_lock_sha256": lock["lock_sha256"],
        "selection_lock_file_sha256": sha256_file(lock_path),
        "outer_audit_csv_sha256": sha256_file(outer["audit_path"]),
        "internal_audit_csv_sha256": sha256_file(internal["audit_path"]),
        "final_audio_sha256": lock["inputs"]["final_audio_sha256"],
        "record_count": len(gold_records),
        "boundary_kind_counts": {
            kind: sum(row["boundary_kind"] == kind for row in gold_records)
            for kind in ("start", "end", "internal")
        },
        "population_counts": {
            population: sum(row["population"] == population for row in gold_records)
            for population in ("outer", "internal")
        },
        "records": gold_records,
    }
    if artifact["boundary_kind_counts"] != {"start": 30, "end": 30, "internal": 30}:
        raise ValueError("human-gold boundary-kind counts are invalid")
    artifact["gold_sha256"] = sha256_json(gold_records)
    artifact["artifact_sha256"] = sha256_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    artifact = build_human_boundary_gold_artifact(args.lock)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.out, artifact)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "record_count": artifact["record_count"],
                "boundary_kind_counts": artifact["boundary_kind_counts"],
                "artifact_sha256": artifact["artifact_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
