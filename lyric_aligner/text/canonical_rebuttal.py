"""Auditable semantic rebuttal of already-resolved canonical lyric text.

This layer exists because canonical source files can themselves contain transcription,
spacing or lexical errors.  A foundation model may propose a correction, but the
proposal is never allowed to change song/occurrence ownership or subtitle timing.
Lexical corrections require explicitly bound supporting evidence and remain shadow-only
until a separate promotion policy is calibrated.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.text_repair import (
    _normalize_for_match,
    parse_srt_text,
    render_repaired_srt,
    timeline_signature,
)

POLICY_SCHEMA = "canonical-semantic-rebuttal-policy-1.0"
SHADOW_SCHEMA = "canonical-semantic-rebuttal-shadow-1.0"
POLICY_ID = "resolved-canonical-semantic-rebuttal-1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_sha(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _required_text(payload: Mapping[str, Any], field: str, *, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} requires non-empty {field}")
    return value.strip()


def _canonical_index(rows: Sequence[Mapping[str, str]]) -> dict[tuple[str, int], Mapping[str, str]]:
    result: dict[tuple[str, int], Mapping[str, str]] = {}
    for position, row in enumerate(rows, start=1):
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        if not occurrence_id:
            raise ValueError(f"canonical row {position} is missing occurrence_id")
        try:
            line_index = int(row["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"canonical row {position} has invalid canonical_line_index") from exc
        if line_index < 0:
            raise ValueError(f"canonical row {position} has negative canonical_line_index")
        key = (occurrence_id, line_index)
        if key in result:
            raise ValueError("canonical audit contains duplicate occurrence/line identity")
        text = str(row.get("text") or "")
        if not _normalize_for_match(text):
            raise ValueError(f"canonical row {position} has blank lexical text")
        result[key] = row
    return result


def classify_variant_observation(
    observation_text: str,
    *,
    canonical_variant: str,
    corrected_variant: str,
) -> str:
    """Deterministically classify whether raw observed text contains either lexical variant."""
    observed = _normalize_for_match(observation_text)
    canonical = _normalize_for_match(canonical_variant)
    corrected = _normalize_for_match(corrected_variant)
    if not observed or not canonical or not corrected or canonical == corrected:
        raise ValueError("variant observation requires two distinct non-empty lexical variants")
    canonical_present = canonical in observed
    corrected_present = corrected in observed
    if canonical_present and not corrected_present:
        return "supports_canonical_text"
    if corrected_present and not canonical_present:
        return "supports_corrected_text"
    return "ambiguous"


def _validate_evidence(
    evidence: Any, *, context: str
) -> tuple[list[dict[str, Any]], set[str], set[str], set[str]]:
    if not isinstance(evidence, list):
        raise ValueError(f"{context} evidence must be a list")
    normalized: list[dict[str, Any]] = []
    families: set[str] = set()
    supporting_families: set[str] = set()
    opposing_families: set[str] = set()
    seen_ids: set[str] = set()
    for position, raw in enumerate(evidence, start=1):
        item_context = f"{context} evidence {position}"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{item_context} must be an object")
        evidence_id = _required_text(raw, "evidence_id", context=item_context)
        family = _required_text(raw, "family", context=item_context)
        if evidence_id in seen_ids:
            raise ValueError(f"{context} has duplicate evidence_id")
        seen_ids.add(evidence_id)
        digest = _required_text(raw, "sha256", context=item_context).lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"{item_context} has invalid sha256")
        role = _required_text(raw, "role", context=item_context)
        if role not in {
            "supports_corrected_text",
            "supports_canonical_text",
            "ambiguous",
            "provenance_only",
        }:
            raise ValueError(f"{item_context} has invalid role")
        observation_fields = (
            raw.get("observation_text"),
            raw.get("canonical_variant"),
            raw.get("corrected_variant"),
        )
        has_observation_contract = any(value not in (None, "") for value in observation_fields)
        observation_payload: dict[str, str] = {}
        if has_observation_contract:
            observation_text = _required_text(raw, "observation_text", context=item_context)
            canonical_variant = _required_text(raw, "canonical_variant", context=item_context)
            corrected_variant = _required_text(raw, "corrected_variant", context=item_context)
            computed_role = classify_variant_observation(
                observation_text,
                canonical_variant=canonical_variant,
                corrected_variant=corrected_variant,
            )
            if role != computed_role:
                raise ValueError(
                    f"{item_context} claimed role {role!r} disagrees with raw observation {computed_role!r}"
                )
            observation_payload = {
                "observation_text": observation_text,
                "canonical_variant": canonical_variant,
                "corrected_variant": corrected_variant,
            }
        normalized.append(
            {
                "evidence_id": evidence_id,
                "family": family,
                "sha256": digest,
                "role": role,
                "note": str(raw.get("note") or "").strip(),
                **observation_payload,
            }
        )
        families.add(family)
        if role == "supports_corrected_text":
            supporting_families.add(family)
        elif role == "supports_canonical_text":
            opposing_families.add(family)
    return normalized, families, supporting_families, opposing_families


def load_rebuttal_policy(
    policy_path: Path,
    *,
    canonical_evaluation_audit: Path,
    expected_task_fingerprint: str | None = None,
) -> dict[str, Any]:
    payload = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("canonical rebuttal policy must be an object")
    if payload.get("schema_version") != POLICY_SCHEMA or payload.get("policy_id") != POLICY_ID:
        raise ValueError("canonical rebuttal policy identity mismatch")
    audit_sha = sha256_file(canonical_evaluation_audit)
    if payload.get("canonical_evaluation_audit_sha256") != audit_sha:
        raise ValueError("canonical rebuttal policy is bound to a different canonical evaluation audit")
    task_fingerprint = _required_text(payload, "task_fingerprint_sha256", context="policy")
    if expected_task_fingerprint is not None and task_fingerprint != expected_task_fingerprint:
        raise ValueError("canonical rebuttal policy belongs to another task")
    min_families = payload.get("min_independent_evidence_families", 2)
    if isinstance(min_families, bool) or not isinstance(min_families, int) or min_families < 1:
        raise ValueError("min_independent_evidence_families must be a positive integer")
    model_identity = payload.get("model_identity")
    if not isinstance(model_identity, Mapping):
        raise ValueError("canonical rebuttal policy requires model_identity")
    normalized_model = {
        field: _required_text(model_identity, field, context="model_identity")
        for field in ("provider", "model_id", "prompt_policy_id")
    }

    canonical_rows = _read_csv(canonical_evaluation_audit)
    canonical = _canonical_index(canonical_rows)
    raw_overrides = payload.get("overrides")
    if not isinstance(raw_overrides, list):
        raise ValueError("canonical rebuttal policy overrides must be a list")
    normalized_overrides: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for position, raw in enumerate(raw_overrides, start=1):
        context = f"override {position}"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{context} must be an object")
        occurrence_id = _required_text(raw, "occurrence_id", context=context)
        line_index = raw.get("canonical_line_index")
        if isinstance(line_index, bool) or not isinstance(line_index, int) or line_index < 0:
            raise ValueError(f"{context} has invalid canonical_line_index")
        key = (occurrence_id, line_index)
        if key in seen:
            raise ValueError(f"duplicate canonical rebuttal identity: {key}")
        seen.add(key)
        row = canonical.get(key)
        if row is None:
            raise ValueError(f"{context} references unknown canonical identity")
        track_id = _required_text(raw, "track_id", context=context)
        row_track = str(row.get("track_id") or "").strip()
        if row_track and track_id != row_track:
            raise ValueError(f"{context} track_id differs from canonical evaluation")
        expected_text = _required_text(raw, "expected_text", context=context)
        canonical_text = str(row.get("text") or "")
        if expected_text != canonical_text:
            raise ValueError(f"{context} expected_text differs from canonical evaluation")
        corrected_text = _required_text(raw, "corrected_text", context=context)
        if corrected_text == expected_text:
            raise ValueError(f"{context} correction is a no-op")
        computed_kind = (
            "presentation_only"
            if _normalize_for_match(expected_text) == _normalize_for_match(corrected_text)
            else "lexical_rebuttal"
        )
        declared_kind = _required_text(raw, "kind", context=context)
        if declared_kind != computed_kind:
            raise ValueError(f"{context} kind does not match normalized text change")
        confidence = _required_text(raw, "confidence", context=context).lower()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"{context} has invalid confidence")
        status = _required_text(raw, "status", context=context)
        if status not in {"candidate", "authorized", "rejected"}:
            raise ValueError(f"{context} has invalid status")
        evidence, families, supporting_families, opposing_families = _validate_evidence(
            raw.get("evidence", []), context=context
        )
        authorized = status == "authorized"
        if authorized:
            if confidence != "high":
                raise ValueError(f"{context} authorized correction must be high confidence")
            if len(supporting_families) < min_families:
                raise ValueError(
                    f"{context} authorized correction lacks independent supporting evidence families"
                )
            if opposing_families:
                raise ValueError(
                    f"{context} authorized correction has direct evidence supporting canonical text"
                )
        normalized_overrides.append(
            {
                "occurrence_id": occurrence_id,
                "track_id": track_id,
                "canonical_line_index": line_index,
                "expected_text": expected_text,
                "corrected_text": corrected_text,
                "kind": computed_kind,
                "confidence": confidence,
                "status": status,
                "reason": _required_text(raw, "reason", context=context),
                "reviewer": _required_text(raw, "reviewer", context=context),
                "evidence": evidence,
                "evidence_families": sorted(families),
                "supporting_evidence_families": sorted(supporting_families),
                "opposing_evidence_families": sorted(opposing_families),
                "authorized": authorized,
            }
        )

    normalized = {
        "schema_version": POLICY_SCHEMA,
        "policy_id": POLICY_ID,
        "task_fingerprint_sha256": task_fingerprint,
        "canonical_evaluation_audit_sha256": audit_sha,
        "min_independent_evidence_families": min_families,
        "model_identity": normalized_model,
        "overrides": normalized_overrides,
    }
    normalized["normalized_policy_sha256"] = _stable_sha(normalized)
    return normalized


def build_truth_overlay(
    *,
    canonical_evaluation_audit: Path,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    rows = _read_csv(canonical_evaluation_audit)
    canonical = _canonical_index(rows)
    corrections: dict[tuple[str, int], Mapping[str, Any]] = {
        (str(item["occurrence_id"]), int(item["canonical_line_index"])): item
        for item in policy["overrides"]
        if item["status"] != "rejected"
    }
    overlay_rows: list[dict[str, Any]] = []
    candidate_count = 0
    authorized_count = 0
    lexical_authorized_count = 0
    presentation_authorized_count = 0
    for row in rows:
        key = (str(row["occurrence_id"]), int(row["canonical_line_index"]))
        correction = corrections.get(key)
        truth_text = str(row["text"])
        status = "canonical"
        if correction is not None:
            candidate_count += 1
            status = str(correction["status"])
            if correction["authorized"]:
                truth_text = str(correction["corrected_text"])
                authorized_count += 1
                if correction["kind"] == "lexical_rebuttal":
                    lexical_authorized_count += 1
                else:
                    presentation_authorized_count += 1
        overlay_rows.append(
            {
                "occurrence_id": row["occurrence_id"],
                "track_id": row.get("track_id", ""),
                "canonical_line_index": int(row["canonical_line_index"]),
                "canonical_text": row["text"],
                "truth_text": truth_text,
                "truth_normalized": _normalize_for_match(truth_text),
                "truth_status": status,
                "correction_kind": correction["kind"] if correction is not None else None,
            }
        )
    return {
        "schema_version": "canonical-semantic-truth-overlay-1.0",
        "policy_id": POLICY_ID,
        "canonical_evaluation_audit_sha256": sha256_file(canonical_evaluation_audit),
        "normalized_policy_sha256": policy["normalized_policy_sha256"],
        "canonical_row_count": len(canonical),
        "candidate_override_count": candidate_count,
        "authorized_override_count": authorized_count,
        "authorized_lexical_rebuttal_count": lexical_authorized_count,
        "authorized_presentation_only_count": presentation_authorized_count,
        "rows": overlay_rows,
    }


def _parse_line_claims(row: Mapping[str, str]) -> list[int]:
    raw_multi = row.get("canonical_line_indices")
    if raw_multi not in (None, ""):
        parsed = json.loads(str(raw_multi))
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("final audit has invalid canonical_line_indices")
        values = [int(value) for value in parsed]
        if any(value < 0 for value in values) or len(set(values)) != len(values):
            raise ValueError("final audit has invalid canonical_line_indices")
        return values
    raw_single = row.get("canonical_line_index")
    if raw_single in (None, ""):
        return []
    return [int(raw_single)]


def materialize_rebuttal_shadow(
    *,
    source_srt_text: str,
    final_audit_rows: Sequence[Mapping[str, str]],
    policy: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    parts, cues = parse_srt_text(source_srt_text)
    if len(cues) != len(final_audit_rows):
        raise ValueError("source SRT cue count differs from final audit")
    by_identity: dict[tuple[str, int], list[int]] = defaultdict(list)
    for position, (cue, row) in enumerate(zip(cues, final_audit_rows)):
        if cue.text != str(row.get("text") or ""):
            raise ValueError(f"source SRT text differs from final audit at position {position + 1}")
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        for line_index in _parse_line_claims(row):
            by_identity[(occurrence_id, line_index)].append(position)

    replacements: dict[int, str] = {}
    applied: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for item in policy["overrides"]:
        if not item["authorized"] or item["kind"] != "lexical_rebuttal":
            continue
        key = (str(item["occurrence_id"]), int(item["canonical_line_index"]))
        positions = by_identity.get(key, [])
        if len(positions) != 1:
            deferred.append({
                "identity": [key[0], key[1]],
                "reason": "canonical_line_not_owned_by_exactly_one_final_cue",
                "positions": [position + 1 for position in positions],
            })
            continue
        position = positions[0]
        claims = _parse_line_claims(final_audit_rows[position])
        if claims != [key[1]]:
            deferred.append({
                "identity": [key[0], key[1]],
                "reason": "final_cue_has_multi_line_ownership",
                "positions": [position + 1],
            })
            continue
        if cues[position].text != item["expected_text"]:
            raise ValueError("authorized rebuttal expected_text differs from bound final cue")
        replacements[cues[position].ordinal] = str(item["corrected_text"])
        applied.append({
            "occurrence_id": key[0],
            "canonical_line_index": key[1],
            "cue_ordinal": cues[position].ordinal,
            "kind": item["kind"],
            "reason": item["reason"],
        })

    rendered = render_repaired_srt(parts, cues, replacements)
    _, output_cues = parse_srt_text(rendered)
    if len(output_cues) != len(cues) or timeline_signature(output_cues) != timeline_signature(cues):
        raise AssertionError("canonical rebuttal shadow changed cue count, numbering or timing")
    return rendered, {
        "schema_version": SHADOW_SCHEMA,
        "policy_id": POLICY_ID,
        "publish_ready": False,
        "timing_authority_used": False,
        "normalized_policy_sha256": policy["normalized_policy_sha256"],
        "authorized_lexical_rebuttal_count": sum(
            item["authorized"] and item["kind"] == "lexical_rebuttal"
            for item in policy["overrides"]
        ),
        "materialized_count": len(applied),
        "deferred_count": len(deferred),
        "timeline_unchanged": True,
        "applied": applied,
        "deferred": deferred,
        "meaning": (
            "authorized semantic corrections are shadow-only; timing and ownership are immutable, "
            "and production promotion requires a separately frozen evaluation/promotion policy"
        ),
    }
