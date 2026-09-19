"""Read-only structural evidence audit for production tasks.

This layer promotes fresh-blind-passed detector logic only into diagnostic QA.  It
never grants timing, content-extent, review-resolution, segmentation, or release
authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from lyric_aligner.evaluation.structural_detectors import (
    DEFAULT_STRUCTURAL_DETECTOR_POLICY,
    StructuralDetectorPolicy,
    detect_detached_tail_events,
    detect_editor_reorder_events,
)
from lyric_aligner.srt import parse_srt_strict

EDITOR_SOURCE_MAP_SCHEMA = "v4-editor-source-map-1.0"
EDITOR_SOURCE_MAP_AUTHORITY = "source_occurrence_verified"


class StructuralEvidenceAuditError(ValueError):
    """Raised when structural evidence inputs are malformed or unauthoritative."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_string(value: object, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise StructuralEvidenceAuditError(f"{field} must be a SHA-256 hex string")
    return text


def editor_source_map_bound_artifact(path: Path, *, repository_root: Path) -> Path:
    """Resolve the upstream mapping artifact so audit outputs cannot overwrite it."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StructuralEvidenceAuditError(f"cannot read editor source map: {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise StructuralEvidenceAuditError("editor source map must contain a JSON object")
    source_rel = str(payload.get("source_mapping_artifact_path") or "").strip()
    if not source_rel:
        raise StructuralEvidenceAuditError("editor source map is missing source_mapping_artifact_path")
    source_path = (repository_root / source_rel).resolve()
    try:
        source_path.relative_to(repository_root.resolve())
    except ValueError as exc:
        raise StructuralEvidenceAuditError(
            "source mapping artifact must stay inside the repository"
        ) from exc
    return source_path


def load_editor_source_map(
    path: Path,
    *,
    editor_srt: Path,
    expected_task_fingerprint: str,
    repository_root: Path,
) -> frozenset[int]:
    """Load a fail-closed editor cue mapping authority artifact."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StructuralEvidenceAuditError(f"cannot read editor source map: {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise StructuralEvidenceAuditError("editor source map must contain a JSON object")
    if payload.get("schema_version") != EDITOR_SOURCE_MAP_SCHEMA:
        raise StructuralEvidenceAuditError("editor source map schema_version mismatch")
    if str(payload.get("task_fingerprint_sha256") or "") != expected_task_fingerprint:
        raise StructuralEvidenceAuditError("editor source map belongs to another task")
    if payload.get("mapping_authority") != EDITOR_SOURCE_MAP_AUTHORITY:
        raise StructuralEvidenceAuditError("editor source map lacks source/occurrence authority")
    if _sha256_string(payload.get("editor_srt_sha256"), field="editor_srt_sha256") != file_sha256(editor_srt):
        raise StructuralEvidenceAuditError("editor source map editor SRT SHA mismatch")
    source_sha = _sha256_string(
        payload.get("source_mapping_artifact_sha256"),
        field="source_mapping_artifact_sha256",
    )
    source_path = editor_source_map_bound_artifact(
        path,
        repository_root=repository_root,
    )
    if not source_path.is_file():
        raise StructuralEvidenceAuditError(
            f"source mapping artifact does not exist: {source_path}"
        )
    if file_sha256(source_path) != source_sha:
        raise StructuralEvidenceAuditError("source mapping artifact SHA mismatch")

    cues = parse_srt_strict(editor_srt)
    cue_count = payload.get("cue_count")
    if isinstance(cue_count, bool) or not isinstance(cue_count, int):
        raise StructuralEvidenceAuditError("editor source map cue_count must be an integer")
    if cue_count != len(cues):
        raise StructuralEvidenceAuditError("editor source map cue_count does not match editor SRT")

    raw_positions = payload.get("mapped_cue_positions")
    if not isinstance(raw_positions, list):
        raise StructuralEvidenceAuditError("editor source map mapped_cue_positions must be a list")
    positions: list[int] = []
    for value in raw_positions:
        if isinstance(value, bool) or not isinstance(value, int):
            raise StructuralEvidenceAuditError("editor source map positions must be integers")
        positions.append(value)
    if positions != sorted(set(positions)):
        raise StructuralEvidenceAuditError("editor source map positions must be unique and sorted")
    if any(position < 0 or position >= len(cues) for position in positions):
        raise StructuralEvidenceAuditError("editor source map contains an out-of-range position")
    return frozenset(positions)


def audit_structural_evidence(
    *,
    editor_srt: Path,
    audio_path: Path,
    expected_task_fingerprint: str,
    repository_root: Path,
    editor_source_map: Path | None = None,
    policy: StructuralDetectorPolicy = DEFAULT_STRUCTURAL_DETECTOR_POLICY,
) -> dict[str, Any]:
    """Run diagnostic structural detectors without mutating production authority."""

    policy.validate()
    if not editor_srt.is_file():
        raise StructuralEvidenceAuditError(f"editor SRT does not exist: {editor_srt}")
    if not audio_path.is_file():
        raise StructuralEvidenceAuditError(f"audio does not exist: {audio_path}")

    reorder_events: list[dict[str, Any]] = []
    if editor_source_map is None:
        reorder_status = "not_run_missing_source_mapping_authority"
    else:
        mapped_positions = load_editor_source_map(
            editor_source_map,
            editor_srt=editor_srt,
            expected_task_fingerprint=expected_task_fingerprint,
            repository_root=repository_root,
        )
        reorder_events = detect_editor_reorder_events(
            editor_srt,
            mapped_cue_positions=mapped_positions,
            policy=policy,
        )
        reorder_status = "evaluated"

    detached_tail_events = detect_detached_tail_events(audio_path, policy=policy)
    events = sorted(
        [*reorder_events, *detached_tail_events],
        key=lambda row: (
            float(row["start_ms"]),
            float(row["end_ms"]),
            str(row["kind"]),
        ),
    )
    return {
        "authority": "diagnostic_only",
        "automatic_timing_change_allowed": False,
        "automatic_content_end_change_allowed": False,
        "automatic_review_resolution_allowed": False,
        "release_gate_eligible": False,
        "publish_ready": False,
        "event_count": len(events),
        "events": events,
        "reorder": {
            "status": reorder_status,
            "event_count": len(reorder_events),
        },
        "detached_tail": {
            "status": "evaluated",
            "event_count": len(detached_tail_events),
        },
    }
