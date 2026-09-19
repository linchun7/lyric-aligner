"""Strict binding between final SRT, audit CSV, QA and release artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.srt import Cue, cue_id, normalized_text, parse_srt_strict, text_sha256


class FinalIntegrityError(ValueError):
    """Raised when final deliverables are not an exact, auditable set."""


def _parse_int(row: dict[str, str], names: tuple[str, ...], *, row_number: int) -> int:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            try:
                return int(value)
            except ValueError as exc:
                raise FinalIntegrityError(
                    f"audit row {row_number} has invalid integer {name}={value!r}"
                ) from exc
    raise FinalIntegrityError(
        f"audit row {row_number} is missing one of required columns {names}"
    )


def _audit_text(row: dict[str, str], *, row_number: int) -> str:
    for name in ("text", "final_text", "candidate"):
        if name in row and row[name] not in (None, ""):
            return str(row[name])
    raise FinalIntegrityError(
        f"audit row {row_number} is missing final text column (text/final_text/candidate)"
    )


def read_audit_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise FinalIntegrityError(f"audit CSV contains no rows: {path}")
    return rows


def _cue_signature(position: int, cue: Cue) -> dict[str, Any]:
    return {
        "position": position,
        "cue_number": cue.number,
        "start_ms": cue.start_ms,
        "end_ms": cue.end_ms,
        "text_sha256": text_sha256(cue.text),
        "cue_id": cue_id(position, cue),
    }


def validate_srt_report_binding(
    final_srt: Path,
    audit_csv: Path,
    *,
    expected_task_fingerprint: str | None = None,
) -> dict[str, Any]:
    cues = parse_srt_strict(final_srt)
    rows = read_audit_rows(audit_csv)
    issues: list[str] = []

    if len(cues) != len(rows):
        issues.append(f"SRT/report row count mismatch: srt={len(cues)}, report={len(rows)}")

    compare_count = min(len(cues), len(rows))
    signatures: list[dict[str, Any]] = []
    for index in range(compare_count):
        position = index + 1
        cue = cues[index]
        row = rows[index]
        signature = _cue_signature(position, cue)
        signatures.append(signature)

        start_ms = _parse_int(row, ("start_ms",), row_number=position)
        end_ms = _parse_int(row, ("end_ms",), row_number=position)
        report_text = _audit_text(row, row_number=position)
        if start_ms != cue.start_ms or end_ms != cue.end_ms:
            issues.append(
                f"row {position} timing mismatch: SRT={cue.start_ms}-{cue.end_ms}, "
                f"report={start_ms}-{end_ms}"
            )
        if normalized_text(report_text) != normalized_text(cue.text):
            issues.append(f"row {position} text mismatch")

        recorded_cue_id = str(row.get("cue_id") or "").strip()
        if recorded_cue_id and recorded_cue_id != signature["cue_id"]:
            issues.append(f"row {position} cue_id mismatch")
        recorded_text_hash = str(row.get("text_sha256") or "").strip()
        if recorded_text_hash and recorded_text_hash != signature["text_sha256"]:
            issues.append(f"row {position} text_sha256 mismatch")

    if expected_task_fingerprint is not None:
        fingerprints = {
            str(row.get("task_fingerprint_sha256") or "").strip()
            for row in rows
        }
        if fingerprints != {expected_task_fingerprint}:
            issues.append(
                "audit task fingerprint mismatch: "
                f"expected one value {expected_task_fingerprint}, got {sorted(fingerprints)}"
            )

    if issues:
        raise FinalIntegrityError("; ".join(issues))
    return {
        "cue_count": len(cues),
        "rows_bound": len(rows),
        "cue_signatures": signatures,
    }


def _is_json_int(value: object) -> bool:
    """Return True only for a JSON-style integer, never bool/float/string."""

    return isinstance(value, int) and not isinstance(value, bool)


def validate_qa_payload(
    qa_json: Path,
    *,
    expected_task_fingerprint: str,
    expected_algorithm_version: str,
    expected_calibration_profile_id: str | None = None,
    expected_calibration_profile_version: str | None = None,
) -> dict[str, Any]:
    payload = json.loads(qa_json.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise FinalIntegrityError("QA JSON must contain an object")
    issues: list[str] = []
    if payload.get("task_fingerprint_sha256") != expected_task_fingerprint:
        issues.append("QA task fingerprint mismatch")
    if str(payload.get("algorithm_version")) != expected_algorithm_version:
        issues.append(
            "QA algorithm version mismatch: "
            f"expected {expected_algorithm_version}, got {payload.get('algorithm_version')}"
        )
    if expected_calibration_profile_id is not None:
        actual = str(payload.get("calibration_profile_id") or "")
        if actual != expected_calibration_profile_id:
            issues.append(
                "QA calibration_profile_id mismatch: "
                f"expected {expected_calibration_profile_id}, got {actual or '<missing>'}"
            )
    if expected_calibration_profile_version is not None:
        actual = str(payload.get("calibration_profile_version") or "")
        if actual != expected_calibration_profile_version:
            issues.append(
                "QA calibration_profile_version mismatch: "
                f"expected {expected_calibration_profile_version}, got {actual or '<missing>'}"
            )
    for key in ("passed", "structurally_valid", "fully_reviewed", "publish_ready"):
        if payload.get(key) is not True:
            issues.append(f"QA {key} must be true")
    review_count = payload.get("review_candidate_count", 0)
    if not _is_json_int(review_count) or review_count != 0:
        issues.append("QA review_candidate_count must be integer 0")
    if issues:
        raise FinalIntegrityError("; ".join(issues))
    return payload


def build_release_artifact_manifest(
    *,
    final_srt: Path,
    audit_csv: Path,
    qa_json: Path,
    task_fingerprint_sha256: str,
    algorithm_version: str,
    git_commit: str = "",
    normalized_config: dict[str, Any] | None = None,
    upstream_artifact_ids: tuple[str, ...] = (),
    expected_calibration_profile_id: str | None = None,
    expected_calibration_profile_version: str | None = None,
) -> dict[str, Any]:
    binding = validate_srt_report_binding(
        final_srt,
        audit_csv,
        expected_task_fingerprint=task_fingerprint_sha256,
    )
    validate_qa_payload(
        qa_json,
        expected_task_fingerprint=task_fingerprint_sha256,
        expected_algorithm_version=algorithm_version,
        expected_calibration_profile_id=expected_calibration_profile_id,
        expected_calibration_profile_version=expected_calibration_profile_version,
    )
    return build_artifact_manifest(
        task_fingerprint_sha256=task_fingerprint_sha256,
        stage="release",
        algorithm_version=algorithm_version,
        outputs=(
            ("final_srt", final_srt),
            ("audit_csv", audit_csv),
            ("qa_json", qa_json),
        ),
        normalized_config=normalized_config,
        producer={"git_commit": git_commit} if git_commit else {},
        upstream_artifact_ids=upstream_artifact_ids,
        evidence={
            "srt_report_binding": {
                "cue_count": binding["cue_count"],
                "rows_bound": binding["rows_bound"],
                "cue_signatures": binding["cue_signatures"],
            }
        },
    )
