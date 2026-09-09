"""Audit viewer-facing subtitle text against preserved canonical truth and explicit transforms."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping

from lyric_aligner.text.display_policy import mask_strong_profanity
from lyric_aligner.text_repair import _normalize_for_match, read_srt

SCHEMA_VERSION = "viewer-lexical-floor-audit-1.0"
POLICY_ID = "viewer-text-truth-and-presentation-floor-1.0"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _single_identity(row: Mapping[str, str]) -> tuple[str, int] | None:
    occurrence_id = str(row.get("occurrence_id") or "").strip()
    if not occurrence_id:
        return None
    multi_raw = row.get("canonical_line_indices")
    if multi_raw not in (None, ""):
        try:
            parsed = json.loads(str(multi_raw))
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, list) or len(parsed) != 1:
            return None
        try:
            line_index = int(parsed[0])
        except (TypeError, ValueError):
            return None
        return occurrence_id, line_index
    raw = row.get("canonical_line_index")
    if raw in (None, ""):
        return None
    try:
        return occurrence_id, int(raw)
    except (TypeError, ValueError):
        return None


def _authorized_overlay(path: Path | None) -> dict[tuple[str, int], str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "canonical-semantic-truth-overlay-1.0":
        raise ValueError("viewer lexical audit received invalid canonical truth overlay")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("canonical truth overlay rows must be a list")
    result: dict[tuple[str, int], str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("canonical truth overlay contains non-object row")
        if row.get("truth_status") != "authorized":
            continue
        key = (str(row.get("occurrence_id") or ""), int(row["canonical_line_index"]))
        truth_text = str(row.get("truth_text") or "")
        if not key[0] or not _normalize_for_match(truth_text):
            raise ValueError("authorized canonical truth overlay row is incomplete")
        result[key] = truth_text
    return result


def audit_viewer_lexical_floor(
    *,
    final_srt: Path,
    final_audit: Path,
    canonical_truth_overlay: Path | None = None,
) -> dict[str, Any]:
    rows = _read_csv(final_audit)
    _, _, _, cues = read_srt(final_srt)
    if len(rows) != len(cues):
        raise ValueError("viewer SRT cue count differs from audit row count")
    overlay = _authorized_overlay(canonical_truth_overlay)
    srt_audit_mismatch_count = 0
    presentation_only_change_count = 0
    sensitive_mask_change_count = 0
    authorized_lexical_rebuttal_count = 0
    unauthorized_lexical_change_count = 0
    unchanged_count = 0
    diagnostics: list[dict[str, Any]] = []

    for position, (cue, row) in enumerate(zip(cues, rows), start=1):
        source_text = str(row.get("canonical_text") or row.get("text") or "")
        display_text = str(row.get("display_text") or row.get("text") or source_text)
        if cue.text != display_text:
            srt_audit_mismatch_count += 1
            diagnostics.append({"position": position, "kind": "srt_display_text_mismatch"})
            continue
        if display_text == source_text:
            unchanged_count += 1
            continue
        reasons = str(row.get("display_change_reasons") or "")
        source_norm = _normalize_for_match(source_text)
        display_norm = _normalize_for_match(display_text)
        if source_norm == display_norm:
            presentation_only_change_count += 1
            continue
        identity = _single_identity(row)
        truth_text = overlay.get(identity) if identity is not None else None
        truth_base = truth_text if truth_text is not None else source_text
        truth_norm = _normalize_for_match(truth_base)
        if "strong_profanity_mask" in reasons:
            expected_masked, mask_count = mask_strong_profanity(truth_base)
            if mask_count and _normalize_for_match(expected_masked) == display_norm:
                sensitive_mask_change_count += 1
                if truth_text is not None and truth_norm != source_norm:
                    authorized_lexical_rebuttal_count += 1
                continue
        if (
            "model_override:" in reasons
            and truth_text is not None
            and truth_norm == display_norm
        ):
            authorized_lexical_rebuttal_count += 1
            continue
        unauthorized_lexical_change_count += 1
        diagnostics.append(
            {
                "position": position,
                "kind": "unauthorized_viewer_lexical_change",
                "identity": list(identity) if identity is not None else None,
                "has_model_override": "model_override:" in reasons,
                "has_sensitive_mask": "strong_profanity_mask" in reasons,
            }
        )

    status = (
        "complete"
        if srt_audit_mismatch_count == 0 and unauthorized_lexical_change_count == 0
        else "failed"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "status": status,
        "final_cue_count": len(cues),
        "srt_audit_mismatch_count": srt_audit_mismatch_count,
        "unchanged_count": unchanged_count,
        "presentation_only_change_count": presentation_only_change_count,
        "sensitive_mask_change_count": sensitive_mask_change_count,
        "authorized_lexical_rebuttal_count": authorized_lexical_rebuttal_count,
        "unauthorized_lexical_change_count": unauthorized_lexical_change_count,
        "canonical_truth_overlay_used": canonical_truth_overlay is not None,
        "timing_evaluated": False,
        "diagnostics": diagnostics,
        "meaning": (
            "viewer text may differ from canonical only through presentation-equivalent formatting, "
            "explicit sensitive masking, or a hash-bound authorized canonical semantic rebuttal"
        ),
    }
