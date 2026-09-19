#!/usr/bin/env python3
"""Create locked outer/internal human boundary audit packs before model scoring."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import soundfile as sf

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from lyric_aligner.evaluation.human_boundary_gold import (
    HUMAN_GOLD_SELECTION_SCHEMA_VERSION,
    candidate_from_report_row,
    select_gold_candidates,
)
from redo_karaoke_pipeline import normalized_text, parse_lrc


from lyric_aligner.alignment.window_policy import FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS


PACK_SCHEMA_VERSION = "human-boundary-gold-pack-lock-2.1"
CLIP_CONTEXT_MS = FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def track_name_from_lrc(path: Path) -> str:
    stem = path.stem.strip()
    if " - " in stem:
        _, title = stem.split(" - ", 1)
        if title.strip():
            return title.strip()
    return stem


def load_lyric_map(lyric_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    result: dict[str, list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    for path in sorted(lyric_dir.glob("*.lrc")):
        track = track_name_from_lrc(path)
        if track in result:
            raise ValueError(f"duplicate canonical lyric track title: {track}")
        lines = parse_lrc(path)
        result[track] = [
            {"index": int(line.index), "time_ms": int(line.time_ms), "text": str(line.text)}
            for line in lines
        ]
        hashes[path.name] = sha256_file(path)
    if not result:
        raise ValueError("no canonical LRC files found")
    return result, hashes


def load_report(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("boundary report contains no rows")
    return rows


def report_fragment_ownership_issues(
    rows: list[dict[str, str]],
    lyric_map: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Reject source reports that duplicate whole canonical events across editor fragments.

    Human-gold selection must not ask a listener to label a full canonical line
    when the owning editor cue contains only one fragment.  This preflight is
    intentionally exact/fail-closed: it flags only cases whose editor fragments
    reconstruct the canonical stream exactly while the report duplicated the
    whole event into adjacent cells.
    """

    canonical_by_track: dict[str, dict[int, str]] = {
        track: {int(line["index"]): str(line["text"]) for line in lines}
        for track, lines in lyric_map.items()
    }

    def indices(row: dict[str, str]) -> list[int]:
        values = [part.strip() for part in str(row.get("lrc_indices") or "").split(";")]
        if not values or any(not value.isdigit() for value in values):
            return []
        return [int(value) for value in values]

    ordered = sorted(
        [row for row in rows if str(row.get("kind") or "") == "existing"],
        key=lambda row: (int(row["start_ms"]), int(row["end_ms"])),
    )
    issues: list[dict[str, Any]] = []

    for left, right in zip(ordered, ordered[1:]):
        track = str(left.get("track") or "")
        if not track or track != str(right.get("track") or ""):
            continue
        gap_ms = int(right["start_ms"]) - int(left["end_ms"])
        if gap_ms > 1500:
            continue
        shared = sorted(set(indices(left)) & set(indices(right)))
        for lrc_index in shared:
            canonical = canonical_by_track.get(track, {}).get(lrc_index)
            if not canonical:
                continue
            canonical_norm = normalized_text(canonical)
            left_text = normalized_text(str(left.get("text") or ""))
            right_text = normalized_text(str(right.get("text") or ""))
            left_original = normalized_text(str(left.get("original") or ""))
            right_original = normalized_text(str(right.get("original") or ""))
            if (
                left_text == canonical_norm
                and right_text == canonical_norm
                and left_original
                and right_original
                and left_original != canonical_norm
                and right_original != canonical_norm
                and left_original + right_original == canonical_norm
            ):
                issues.append(
                    {
                        "category": "exact_fragmented_editor_ownership_duplication",
                        "track": track,
                        "lrc_index": lrc_index,
                        "left_cue": int(left["original_cue"]),
                        "right_cue": int(right["original_cue"]),
                        "canonical_text": canonical,
                    }
                )

    for left, middle, right in zip(ordered, ordered[1:], ordered[2:]):
        track = str(left.get("track") or "")
        if not track or track != str(middle.get("track") or "") or track != str(right.get("track") or ""):
            continue
        left_indices = indices(left)
        middle_indices = indices(middle)
        right_indices = indices(right)
        if len(left_indices) != 1 or len(middle_indices) != 1 or len(right_indices) != 1:
            continue
        current_index = left_indices[0]
        if middle_indices[0] != current_index or right_indices[0] != current_index + 1:
            continue
        current = canonical_by_track.get(track, {}).get(current_index)
        following = canonical_by_track.get(track, {}).get(current_index + 1)
        if not current or not following:
            continue
        if normalized_text(str(left.get("text") or "")) != normalized_text(current):
            continue
        if normalized_text(str(middle.get("text") or "")) != normalized_text(current):
            continue
        if normalized_text(str(right.get("text") or "")) != normalized_text(following):
            continue
        original_stream = "".join(
            normalized_text(str(row.get("original") or ""))
            for row in (left, middle, right)
        )
        canonical_stream = normalized_text(current + " " + following)
        if original_stream and original_stream == canonical_stream:
            issues.append(
                {
                    "category": "exact_cross_event_editor_ownership_duplication",
                    "track": track,
                    "lrc_indices": [current_index, current_index + 1],
                    "left_cue": int(left["original_cue"]),
                    "middle_cue": int(middle["original_cue"]),
                    "right_cue": int(right["original_cue"]),
                    "canonical_text": current + " " + following,
                }
            )

    return issues


def safe_clip_name(index: int, case_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", case_id):
        raise ValueError("case_id is not a SHA-256 digest")
    return f"{index:02d}_{case_id[:16]}.wav"


def write_clip(
    *,
    audio_path: Path,
    audio_info: Any,
    clip_path: Path,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    samplerate = int(audio_info.samplerate)
    total_frames = int(audio_info.frames)
    requested_start_ms = max(0, int(start_ms) - CLIP_CONTEXT_MS)
    requested_end_ms = int(end_ms) + CLIP_CONTEXT_MS
    start_frame = max(0, int(round(requested_start_ms * samplerate / 1000.0)))
    end_frame = min(total_frames, int(round(requested_end_ms * samplerate / 1000.0)))
    if end_frame <= start_frame:
        raise ValueError("audit clip interval is empty")
    data, actual_rate = sf.read(
        str(audio_path),
        start=start_frame,
        stop=end_frame,
        dtype="float32",
        always_2d=True,
    )
    if not len(data):
        raise ValueError("audit clip decoded no audio")
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(clip_path), data, actual_rate, subtype="PCM_16")
    clip_start_ms = int(round(start_frame * 1000.0 / samplerate))
    clip_end_ms = int(round(end_frame * 1000.0 / samplerate))
    return {
        "clip_start_ms": clip_start_ms,
        "clip_end_ms": clip_end_ms,
        "clip_sha256": sha256_file(clip_path),
    }


def _base_audit_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": record["case_id"],
        "partition": record["partition"],
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


def write_outer_audit_sheet(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "partition",
        "track",
        "cue_number",
        "clip_relpath",
        "clip_start_ms",
        "clip_end_ms",
        "editor_start_ms_reference_only",
        "editor_end_ms_reference_only",
        "canonical_text",
        "segment_count",
        "gold_start_clip_ms",
        "gold_start_uncertainty_ms",
        "gold_end_clip_ms",
        "gold_end_uncertainty_ms",
        "auditor_notes",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    **_base_audit_row(record),
                    "gold_start_clip_ms": "",
                    "gold_start_uncertainty_ms": "",
                    "gold_end_clip_ms": "",
                    "gold_end_uncertainty_ms": "",
                    "auditor_notes": "",
                }
            )
    temporary.replace(path)


def write_internal_audit_sheet(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "partition",
        "track",
        "cue_number",
        "clip_relpath",
        "clip_start_ms",
        "clip_end_ms",
        "editor_start_ms_reference_only",
        "editor_end_ms_reference_only",
        "canonical_text",
        "segment_count",
        "internal_boundary_index",
        "internal_left_text",
        "internal_right_text",
        "gold_internal_clip_ms",
        "gold_internal_uncertainty_ms",
        "auditor_notes",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            boundary_index = int(record["internal_boundary_index"])
            segments = record["segments"]
            writer.writerow(
                {
                    **_base_audit_row(record),
                    "internal_boundary_index": boundary_index,
                    "internal_left_text": segments[boundary_index - 1]["text"],
                    "internal_right_text": segments[boundary_index]["text"],
                    "gold_internal_clip_ms": "",
                    "gold_internal_uncertainty_ms": "",
                    "auditor_notes": "",
                }
            )
    temporary.replace(path)


def materialize_population(
    *,
    root: Path,
    purpose: str,
    selection: dict[str, Any],
    audio_path: Path,
    audio_info: Any,
) -> dict[str, Any]:
    population_dir = root / purpose
    clips_dir = population_dir / "clips"
    population_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for index, record in enumerate(selection["records"], start=1):
        clip_name = safe_clip_name(index, str(record["case_id"]))
        clip_path = clips_dir / clip_name
        clip_meta = write_clip(
            audio_path=audio_path,
            audio_info=audio_info,
            clip_path=clip_path,
            start_ms=int(record["start_ms"]),
            end_ms=int(record["end_ms"]),
        )
        clip_meta["clip_relpath"] = str(clip_path.relative_to(root)).replace("\\", "/")
        records.append({**record, **clip_meta})

    audit_path = population_dir / "human_audit.csv"
    if purpose == "outer":
        write_outer_audit_sheet(audit_path, records)
    elif purpose == "internal":
        write_internal_audit_sheet(audit_path, records)
    else:
        raise ValueError("unknown human-gold population purpose")
    return {
        "selection_schema_version": selection["schema_version"],
        "selection_policy_id": selection["policy_id"],
        "selection_sha256": selection["selection_sha256"],
        "selection": selection,
        "purpose": purpose,
        "audit_relpath": str(audit_path.relative_to(root)).replace("\\", "/"),
        "summary": {
            "selected_count": len(records),
            "calibration_count": sum(row["partition"] == "calibration" for row in records),
            "holdout_count": sum(row["partition"] == "holdout" for row in records),
            "distinct_track_count": selection["distinct_track_count"],
            "calibration_distinct_track_count": selection["calibration_distinct_track_count"],
            "holdout_distinct_track_count": selection["holdout_distinct_track_count"],
            "single_segment_count": selection["single_segment_count"],
            "multi_segment_count": selection["multi_segment_count"],
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--lyrics-dir", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    language_scope = str(args.language or "").strip().lower()
    if not language_scope or not re.fullmatch(r"[a-z0-9-]+", language_scope):
        raise ValueError("language scope must be a non-empty lowercase BCP-47-like token")

    for path, label in (
        (args.report, "report"),
        (args.lyrics_dir, "lyrics directory"),
        (args.audio, "final audio"),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise FileExistsError(
            "human-gold output directory must be new/empty; never regenerate a lock in place"
        )
    args.out_dir.mkdir(parents=True, exist_ok=True)

    lyric_map, lyric_hashes = load_lyric_map(args.lyrics_dir)
    report_rows = load_report(args.report)
    ownership_issues = report_fragment_ownership_issues(report_rows, lyric_map)
    if ownership_issues:
        first = ownership_issues[0]
        raise ValueError(
            "boundary report is unsafe for human-gold selection: "
            f"{len(ownership_issues)} exact editor text-ownership duplication issue(s); "
            f"first={first}"
        )
    candidates = []
    for row in report_rows:
        candidate = candidate_from_report_row(row, lyric_lines_by_track=lyric_map)
        if candidate is not None:
            candidates.append(candidate)

    outer_selection = select_gold_candidates(candidates, purpose="outer")
    outer_ids = {str(row["case_id"]) for row in outer_selection["records"]}
    internal_selection = select_gold_candidates(
        candidates,
        purpose="internal",
        excluded_case_ids=outer_ids,
    )
    internal_ids = {str(row["case_id"]) for row in internal_selection["records"]}
    if outer_selection.get("schema_version") != HUMAN_GOLD_SELECTION_SCHEMA_VERSION:
        raise ValueError("outer selection schema mismatch")
    if internal_selection.get("schema_version") != HUMAN_GOLD_SELECTION_SCHEMA_VERSION:
        raise ValueError("internal selection schema mismatch")
    if outer_ids & internal_ids:
        raise ValueError("outer/internal gold populations must be cue-disjoint")

    info = sf.info(str(args.audio))
    outer = materialize_population(
        root=args.out_dir,
        purpose="outer",
        selection=outer_selection,
        audio_path=args.audio,
        audio_info=info,
    )
    internal = materialize_population(
        root=args.out_dir,
        purpose="internal",
        selection=internal_selection,
        audio_path=args.audio,
        audio_info=info,
    )

    lock: dict[str, Any] = {
        "schema_version": PACK_SCHEMA_VERSION,
        "selection_created_before_backend_scoring": True,
        "backend_prediction_fields_present": False,
        "population_case_overlap_count": 0,
        "inputs": {
            "language_scope": language_scope,
            "report_path": str(args.report.resolve()),
            "report_sha256": sha256_file(args.report),
            "final_audio_path": str(args.audio.resolve()),
            "final_audio_sha256": sha256_file(args.audio),
            "canonical_lyrics_dir": str(args.lyrics_dir.resolve()),
            "canonical_lyric_sha256_by_name": lyric_hashes,
        },
        "candidate_pool_count": len(candidates),
        "populations": {"outer": outer, "internal": internal},
        "summary": {
            "outer_cue_count": len(outer["records"]),
            "internal_cue_count": len(internal["records"]),
            "human_boundary_point_count": len(outer["records"]) * 2 + len(internal["records"]),
        },
    }
    lock["lock_sha256"] = sha256_json(lock)
    atomic_json(args.out_dir / "selection.lock.json", lock)
    atomic_text(
        args.out_dir / "README-AUDIT.txt",
        "Human acoustic boundary audit pack.\n\n"
        "The outer and internal populations were selected and partitioned before backend scoring and are cue-disjoint.\n"
        "Do not regenerate or reselect after viewing SOFA/HuBERTFA/ASR predictions.\n\n"
        "OUTER: fill outer/human_audit.csv. Times are clip-local milliseconds (0 = clip start).\n"
        "  gold_start_clip_ms = first audible lexical onset of canonical_text; exclude breath/instrumental onset.\n"
        "  gold_end_clip_ms = last audible lexical offset of canonical_text; exclude reverb/instrument tail.\n\n"
        "INTERNAL: fill internal/human_audit.csv.\n"
        "  gold_internal_clip_ms = first audible lexical onset of internal_right_text.\n\n"
        "For every point, *_uncertainty_ms is a perceptual half-width and must be <=100ms for production authority.\n"
        "Editor start/end columns are routing references only and are NOT gold.\n"
        "Do not consult backend predictions while auditing.\n",
    )
    print(
        json.dumps(
            {
                "out_dir": str(args.out_dir),
                "lock_sha256": lock["lock_sha256"],
                "candidate_pool_count": len(candidates),
                **lock["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
