"""English direct-final-mix HuBERTFA shadow evidence contracts.

This module deliberately creates *uncalibrated* observer input/output only.  It
uses current final subtitle ownership to route a bounded final-mix window, but it
does not treat that timing as truth.  No result produced here is timing authority
until the exact observer/model/window policy has passed an independent final-mix
human-gold calibration/holdout protocol.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.audio.forced_alignment import CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID
from lyric_aligner.text.alignment_lexical import english_units
from lyric_aligner.alignment.window_policy import (
    FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS,
    FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID,
    full_sequence_alignment_window_ms,
)

ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_SCHEMA = "english-final-mix-hubertfa-shadow-1.3"
ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID = (
    "english-final-mix-hubertfa-shadow-continuous-context-2026-09-11-v4"
)
ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID = "hubertfa_english_final_mix_contextual_shadow_v2"
ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP = "hubertfa_onnx_en_contextual_shadow_v1"
ENGLISH_FINAL_MIX_HUBERTFA_REQUEST_PROTOCOL = "english-final-mix-hubertfa-request-1.3"
ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS = FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS


class EnglishFinalMixHuBERTFAShadowError(ValueError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text.casefold()):
        raise EnglishFinalMixHuBERTFAShadowError(f"{label} must be SHA-256")
    return text.casefold()


def _validate_file_binding(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise EnglishFinalMixHuBERTFAShadowError(f"{label} must be a {{path,sha256}} binding")
    path = Path(str(value.get("path") or "")).resolve()
    expected_sha = _require_sha256(value.get("sha256"), f"{label} SHA")
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise EnglishFinalMixHuBERTFAShadowError(f"{label} file identity mismatch")
    return {"path": str(path), "sha256": expected_sha}


def _validate_file_manifest(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise EnglishFinalMixHuBERTFAShadowError(f"{label} must be a nonempty file manifest")
    output: dict[str, str] = {}
    for raw_path, raw_sha in value.items():
        path = Path(str(raw_path or "")).resolve()
        expected_sha = _require_sha256(raw_sha, f"{label} SHA")
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise EnglishFinalMixHuBERTFAShadowError(f"{label} file identity mismatch: {path}")
        output[str(path)] = expected_sha
    return dict(sorted(output.items()))


def _indices(value: Any) -> list[int]:
    if isinstance(value, list):
        raw = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EnglishFinalMixHuBERTFAShadowError("final audit canonical_line_indices is invalid JSON") from exc
    if not isinstance(raw, list):
        raise EnglishFinalMixHuBERTFAShadowError("final audit canonical_line_indices must be a list")
    output: list[int] = []
    for item in raw:
        if type(item) is not int or item < 0:
            raise EnglishFinalMixHuBERTFAShadowError("final audit canonical_line_indices contains invalid index")
        output.append(item)
    return output


def _audit_owners(
    rows: Sequence[Mapping[str, Any]],
    *,
    occurrence_id: str,
    canonical_line_index: int,
    canonical_text_sha256: str,
) -> list[Mapping[str, Any]]:
    owners: list[Mapping[str, Any]] = []
    expected_text_sha = _require_sha256(canonical_text_sha256, "canonical context text SHA")
    for row in rows:
        if str(row.get("occurrence_id") or "") != occurrence_id:
            continue
        explicit_indices = _indices(row.get("canonical_line_indices"))
        if canonical_line_index in explicit_indices:
            owners.append(row)
            continue
        # Direct line-LRC/canonical rows in older audit shapes may leave the
        # plural ownership field empty while still carrying one exact
        # canonical_line_index.  Accept that narrow fallback only when the row
        # text itself is byte-identical to the resolved canonical text.  This
        # avoids upgrading partial/multi-line ownership from a scalar hint.
        if explicit_indices:
            continue
        raw_index = row.get("canonical_line_index")
        try:
            scalar_index = int(raw_index) if str(raw_index or "").strip() else None
        except (TypeError, ValueError):
            scalar_index = None
        if (
            scalar_index == canonical_line_index
            and str(row.get("text_sha256") or "") == expected_text_sha
        ):
            owners.append(row)
    return owners


def _interval(rows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    if not rows:
        raise EnglishFinalMixHuBERTFAShadowError("final audit has no owner for canonical line")
    try:
        start = min(int(row["start_ms"]) for row in rows)
        end = max(int(row["end_ms"]) for row in rows)
    except (KeyError, TypeError, ValueError) as exc:
        raise EnglishFinalMixHuBERTFAShadowError("final audit owner interval is invalid") from exc
    if start < 0 or end <= start:
        raise EnglishFinalMixHuBERTFAShadowError("final audit owner interval is invalid")
    return start, end


def _asset_indexes(
    track_assets: Mapping[str, Any],
) -> tuple[
    dict[int, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
]:
    assets_raw = track_assets.get("assets")
    occurrences_raw = track_assets.get("occurrences")
    resolutions_raw = track_assets.get("resolution")
    if (
        not isinstance(assets_raw, list)
        or not isinstance(occurrences_raw, list)
        or not isinstance(resolutions_raw, list)
    ):
        raise EnglishFinalMixHuBERTFAShadowError("track assets are incomplete")
    assets: dict[str, Mapping[str, Any]] = {}
    for row in assets_raw:
        if not isinstance(row, Mapping):
            raise EnglishFinalMixHuBERTFAShadowError("track asset row must be an object")
        track_id = str(row.get("track_id") or "")
        if not track_id or track_id in assets:
            raise EnglishFinalMixHuBERTFAShadowError("track asset IDs are invalid/duplicated")
        assets[track_id] = row
    occurrences: dict[int, Mapping[str, Any]] = {}
    for row in occurrences_raw:
        if not isinstance(row, Mapping):
            raise EnglishFinalMixHuBERTFAShadowError("track occurrence row must be an object")
        try:
            ordinal = int(row["ordinal"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EnglishFinalMixHuBERTFAShadowError("track occurrence ordinal is invalid") from exc
        if ordinal <= 0 or ordinal in occurrences:
            raise EnglishFinalMixHuBERTFAShadowError("track occurrence ordinals are invalid/duplicated")
        occurrences[ordinal] = row
    resolutions: dict[str, Mapping[str, Any]] = {}
    for row in resolutions_raw:
        if not isinstance(row, Mapping):
            raise EnglishFinalMixHuBERTFAShadowError("track resolution row must be an object")
        track_id = str(row.get("track_id") or "")
        if not track_id or track_id in resolutions:
            raise EnglishFinalMixHuBERTFAShadowError("track resolution IDs are invalid/duplicated")
        resolutions[track_id] = row
    return occurrences, assets, resolutions


def _selection_rows(selection_lock: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if selection_lock.get("policy_id") != "targeted-semantic-failed-expansion-v1":
        raise EnglishFinalMixHuBERTFAShadowError("unexpected targeted selection policy")
    selection = selection_lock.get("selection")
    if not isinstance(selection, Mapping):
        raise EnglishFinalMixHuBERTFAShadowError("selection lock has no selection map")
    rows: list[Mapping[str, Any]] = []
    for ordinal_key, group in selection.items():
        if not isinstance(group, list):
            raise EnglishFinalMixHuBERTFAShadowError("selection lock group must be a list")
        for row in group:
            if not isinstance(row, Mapping):
                raise EnglishFinalMixHuBERTFAShadowError("selection lock row must be an object")
            if int(row.get("ordinal") or -1) != int(ordinal_key):
                raise EnglishFinalMixHuBERTFAShadowError("selection lock ordinal key mismatch")
            rows.append(row)
    expected = int(selection_lock.get("selected_job_count") or -1)
    if expected != len(rows):
        raise EnglishFinalMixHuBERTFAShadowError("selection lock selected_job_count mismatch")
    job_ids = [str(row.get("job_id") or "") for row in rows]
    if not all(job_ids) or len(set(job_ids)) != len(job_ids):
        raise EnglishFinalMixHuBERTFAShadowError("selection lock job IDs are invalid/duplicated")
    return rows


def build_shadow_plan(
    *,
    selection_lock: Mapping[str, Any],
    selection_lock_sha256: str,
    track_assets: Mapping[str, Any],
    track_assets_sha256: str,
    final_audit_rows: Sequence[Mapping[str, Any]],
    final_audit_sha256: str,
    final_audio_path: str | Path,
    final_audio_sha256: str,
    mix_duration_ms: int,
) -> dict[str, Any]:
    """Build a fixed-denominator English HuBERTFA final-mix shadow plan.

    Current final timing is used only to derive a bounded routing window.  The
    predicted onset itself comes from forced alignment inside that window.
    """
    fingerprint = _require_sha256(selection_lock.get("task_fingerprint_sha256"), "selection lock task fingerprint")
    selection_lock_sha256 = _require_sha256(selection_lock_sha256, "selection_lock_sha256")
    track_assets_sha256 = _require_sha256(track_assets_sha256, "track_assets_sha256")
    final_audit_sha256 = _require_sha256(final_audit_sha256, "final_audit_sha256")
    final_audio_sha256 = _require_sha256(final_audio_sha256, "final_audio_sha256")
    final_audio = Path(final_audio_path).resolve()
    if not final_audio.is_file() or sha256_file(final_audio) != final_audio_sha256:
        raise EnglishFinalMixHuBERTFAShadowError("final mix identity mismatch")
    if type(mix_duration_ms) is not int or mix_duration_ms <= 0:
        raise EnglishFinalMixHuBERTFAShadowError("mix duration is invalid")

    occurrences, assets, resolutions = _asset_indexes(track_assets)
    targets: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for selected in _selection_rows(selection_lock):
        job_id = str(selected["job_id"])
        ordinal = int(selected["ordinal"])
        occurrence = occurrences.get(ordinal)
        if occurrence is None:
            raise EnglishFinalMixHuBERTFAShadowError("selected ordinal is absent from track assets")
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        if occurrence_id != str(selected.get("occurrence_id") or ""):
            raise EnglishFinalMixHuBERTFAShadowError("selection/track-assets occurrence identity mismatch")
        track_id = str(occurrence.get("track_id") or "")
        asset = assets.get(track_id)
        if asset is None:
            raise EnglishFinalMixHuBERTFAShadowError("occurrence track asset is missing")
        lyric_path = Path(str(asset.get("canonical_lyric_path") or "")).resolve()
        expected_lyric_sha = str(asset.get("canonical_lyric_sha256") or "")
        if not lyric_path.is_file() or sha256_file(lyric_path) != expected_lyric_sha:
            raise EnglishFinalMixHuBERTFAShadowError("canonical lyric identity mismatch")
        resolution = resolutions.get(track_id)
        if resolution is None:
            raise EnglishFinalMixHuBERTFAShadowError("track canonical resolution is missing")
        canonical_selection = resolution.get("canonical_selection")
        if not isinstance(canonical_selection, list) or not canonical_selection:
            raise EnglishFinalMixHuBERTFAShadowError("track canonical selection is missing")
        if any(not isinstance(row, Mapping) or not isinstance(row.get("text"), str) for row in canonical_selection):
            raise EnglishFinalMixHuBERTFAShadowError("track canonical selection contains invalid row")
        canonical_selection_sha = str(asset.get("canonical_selection_sha256") or "")
        if len(canonical_selection_sha) != 64 or sha256_json(canonical_selection) != canonical_selection_sha:
            raise EnglishFinalMixHuBERTFAShadowError("canonical selection identity mismatch")
        index = int(selected["canonical_line_index"])
        if not 0 <= index < len(canonical_selection):
            raise EnglishFinalMixHuBERTFAShadowError("selected canonical line index is out of range")
        target_text = str(canonical_selection[index]["text"])
        expected_text_sha = str(selected.get("canonical_text_sha256") or "")
        if _text_sha(target_text) != expected_text_sha:
            raise EnglishFinalMixHuBERTFAShadowError("selected canonical text SHA mismatch")

        if index <= 0 or index >= len(canonical_selection) - 1:
            targets.append(
                {
                    "job_id": job_id,
                    "ordinal": ordinal,
                    "occurrence_id": occurrence_id,
                    "track_id": track_id,
                    "canonical_line_index": index,
                    "canonical_text_sha256": expected_text_sha,
                    "status": "rejected",
                    "reason": "insufficient_bilateral_canonical_context",
                }
            )
            continue
        context_indices = [index - 1, index, index + 1]
        context_owner_rows: list[Mapping[str, Any]] = []
        context_intervals: list[tuple[int, int]] = []
        for context_index in context_indices:
            owners = _audit_owners(
                final_audit_rows,
                occurrence_id=occurrence_id,
                canonical_line_index=context_index,
                canonical_text_sha256=_text_sha(str(canonical_selection[context_index]["text"])),
            )
            if not owners:
                # Preserve the frozen denominator.  Missing routing ownership is
                # an explicit rejected target, never silently dropped.
                targets.append(
                    {
                        "job_id": job_id,
                        "ordinal": ordinal,
                        "occurrence_id": occurrence_id,
                        "track_id": track_id,
                        "canonical_line_index": index,
                        "canonical_text_sha256": expected_text_sha,
                        "status": "rejected",
                        "reason": f"missing_final_ownership_for_context_line:{context_index}",
                    }
                )
                context_owner_rows = []
                context_intervals = []
                break
            context_owner_rows.extend(owners)
            context_intervals.append(_interval(owners))
        if not context_owner_rows:
            continue
        adjacent_context_gaps_ms = [
            max(0, right[0] - left[1])
            for left, right in zip(context_intervals, context_intervals[1:])
        ]
        if any(
            gap_ms > ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS
            for gap_ms in adjacent_context_gaps_ms
        ):
            targets.append(
                {
                    "job_id": job_id,
                    "ordinal": ordinal,
                    "occurrence_id": occurrence_id,
                    "track_id": track_id,
                    "canonical_line_index": index,
                    "canonical_text_sha256": expected_text_sha,
                    "status": "rejected",
                    "reason": "context_temporal_discontinuity",
                    "adjacent_context_gaps_ms": adjacent_context_gaps_ms,
                    "max_allowed_adjacent_context_gap_ms": ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
                }
            )
            continue
        target_owners = _audit_owners(
            final_audit_rows,
            occurrence_id=occurrence_id,
            canonical_line_index=index,
            canonical_text_sha256=expected_text_sha,
        )
        target_start, target_end = _interval(target_owners)
        context_start, context_end = _interval(context_owner_rows)
        window = full_sequence_alignment_window_ms(
            cue_start_ms=context_start,
            cue_end_ms=context_end,
            mix_duration_ms=mix_duration_ms,
        )
        segment_units = [english_units(str(canonical_selection[value]["text"])) for value in context_indices]
        if any(not units for units in segment_units):
            targets.append(
                {
                    "job_id": job_id,
                    "ordinal": ordinal,
                    "occurrence_id": occurrence_id,
                    "track_id": track_id,
                    "canonical_line_index": index,
                    "canonical_text_sha256": expected_text_sha,
                    "status": "rejected",
                    "reason": "context_contains_no_english_lexical_units",
                }
            )
            continue
        target_segment_index = 1
        lexical_sha = sha256_json(segment_units)
        record_identity = {
            "policy_id": ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
            "observer_id": ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
            "task_fingerprint_sha256": fingerprint,
            "source_selection_job_id": job_id,
            "ordinal": ordinal,
            "occurrence_id": occurrence_id,
            "track_id": track_id,
            "canonical_line_index": index,
            "canonical_text_sha256": expected_text_sha,
            "canonical_selection_sha256": canonical_selection_sha,
            "final_audio_sha256": final_audio_sha256,
            "lexical_units_sha256": lexical_sha,
            "context_canonical_line_indices": context_indices,
            "target_segment_index": target_segment_index,
            "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
            "context_continuity_max_gap_ms": ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
            "adjacent_context_gaps_ms": adjacent_context_gaps_ms,
            "mix_window_ms": window,
        }
        record_id = sha256_json(record_identity)
        record = {
            "record_id": record_id,
            "source_selection_job_id": job_id,
            "ordinal": ordinal,
            "occurrence_id": occurrence_id,
            "track_id": track_id,
            "canonical_line_index": index,
            "canonical_text_sha256": expected_text_sha,
            "canonical_lyric_sha256": expected_lyric_sha,
            "canonical_selection_sha256": canonical_selection_sha,
            "language": "en",
            "segment_lexical_units": segment_units,
            "lexical_units_sha256": lexical_sha,
            "context_canonical_line_indices": context_indices,
            "target_segment_index": target_segment_index,
            "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
            "context_continuity_max_gap_ms": ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
            "adjacent_context_gaps_ms": adjacent_context_gaps_ms,
            "routing_final_interval_ms": [target_start, target_end],
            "context_final_interval_ms": [context_start, context_end],
            "mix_window_ms": window,
            "routing_timing_is_truth": False,
        }
        records.append(record)
        targets.append(
            {
                "job_id": job_id,
                "ordinal": ordinal,
                "occurrence_id": occurrence_id,
                "track_id": track_id,
                "canonical_line_index": index,
                "canonical_text_sha256": expected_text_sha,
                "status": "prepared",
                "record_id": record_id,
                "routing_final_start_ms": target_start,
                "mix_window_ms": window,
            }
        )

    selected_count = int(selection_lock["selected_job_count"])
    if len(targets) != selected_count:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan did not account for every selected target")
    prepared_ids = [row["record_id"] for row in records]
    if len(set(prepared_ids)) != len(prepared_ids):
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan record IDs are duplicated")
    plan = {
        "schema_version": ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_SCHEMA,
        "policy_id": ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
        "observer_id": ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
        "correlation_group": ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP,
        "task_fingerprint_sha256": fingerprint,
        "authority": "shadow_only_uncalibrated",
        "automatic_timing_change_allowed": False,
        "routing_timing_is_truth": False,
        "window_policy_id": FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID,
        "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
        "context_continuity_max_gap_ms": ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
        "selected_target_count": selected_count,
        "prepared_record_count": len(records),
        "rejected_target_count": selected_count - len(records),
        "targets": targets,
        "records": records,
        "bindings": {
            "selection_lock_sha256": selection_lock_sha256,
            "track_assets_sha256": track_assets_sha256,
            "final_audit_sha256": final_audit_sha256,
            "final_audio_path": str(final_audio),
            "final_audio_sha256": final_audio_sha256,
        },
    }
    plan["plan_sha256"] = sha256_json(plan)
    return plan


def build_adapter_request(plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("schema_version") != ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_SCHEMA:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan schema mismatch")
    if plan.get("policy_id") != ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan policy mismatch")
    if plan.get("observer_id") != ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan observer mismatch")
    if plan.get("correlation_group") != ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan correlation group mismatch")
    if plan.get("authority") != "shadow_only_uncalibrated" or plan.get("automatic_timing_change_allowed") is not False:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan may not claim timing authority")
    if plan.get("window_policy_id") != FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan window policy mismatch")
    if plan.get("alignment_method_id") != CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan alignment method mismatch")
    if plan.get("context_continuity_max_gap_ms") != ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan context continuity policy mismatch")
    supplied_plan_sha = _require_sha256(plan.get("plan_sha256"), "shadow plan SHA")
    unsigned_plan = dict(plan)
    unsigned_plan.pop("plan_sha256", None)
    if sha256_json(unsigned_plan) != supplied_plan_sha:
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan SHA mismatch")
    records = plan.get("records")
    if not isinstance(records, list):
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan records must be a list")
    bindings = plan.get("bindings")
    if not isinstance(bindings, Mapping):
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan bindings are missing")
    request = {
        "protocol_version": ENGLISH_FINAL_MIX_HUBERTFA_REQUEST_PROTOCOL,
        "policy_id": ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
        "observer_id": ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
        "correlation_group": ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP,
        "authority": "shadow_only_uncalibrated",
        "automatic_timing_change_allowed": False,
        "plan_sha256": str(plan.get("plan_sha256") or ""),
        "task_fingerprint_sha256": str(plan.get("task_fingerprint_sha256") or ""),
        "final_audio_path": str(bindings.get("final_audio_path") or ""),
        "final_audio_sha256": str(bindings.get("final_audio_sha256") or ""),
        "window_policy_id": FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID,
        "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
        "context_continuity_max_gap_ms": ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
        "record_count": len(records),
        "records": records,
    }
    request["request_sha256"] = sha256_json(request)
    return request


def bind_adapter_response(
    plan: Mapping[str, Any], request: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    expected_request = build_adapter_request(plan)
    if dict(request) != expected_request:
        raise EnglishFinalMixHuBERTFAShadowError("shadow request no longer matches the bound plan")
    if response.get("protocol_version") != ENGLISH_FINAL_MIX_HUBERTFA_REQUEST_PROTOCOL:
        raise EnglishFinalMixHuBERTFAShadowError("shadow response protocol mismatch")
    for key in (
        "policy_id",
        "observer_id",
        "correlation_group",
        "plan_sha256",
        "request_sha256",
        "task_fingerprint_sha256",
        "final_audio_sha256",
        "window_policy_id",
        "alignment_method_id",
        "context_continuity_max_gap_ms",
        "record_count",
    ):
        expected = request.get(key)
        if response.get(key) != expected:
            raise EnglishFinalMixHuBERTFAShadowError(f"shadow response {key} mismatch")
    if response.get("authority") != "shadow_only_uncalibrated":
        raise EnglishFinalMixHuBERTFAShadowError("shadow response authority mismatch")
    if response.get("automatic_timing_change_allowed") is not False:
        raise EnglishFinalMixHuBERTFAShadowError("shadow response may not allow timing mutation")

    runtime_bundle: dict[str, Any] = {}
    for key in (
        "model",
        "model_config",
        "model_version",
        "model_vocab",
        "dictionary",
        "adapter",
        "existing_mandarin_adapter_dependency",
    ):
        runtime_bundle[key] = _validate_file_binding(response.get(key), f"shadow response {key}")
    runtime_bundle["vendor_files"] = _validate_file_manifest(
        response.get("vendor_files"), "shadow response vendor_files"
    )
    provider_runtime = response.get("provider_runtime")
    if not isinstance(provider_runtime, Mapping):
        raise EnglishFinalMixHuBERTFAShadowError("shadow response provider runtime is missing")
    providers = provider_runtime.get("actual_providers")
    if providers != ["CPUExecutionProvider"]:
        raise EnglishFinalMixHuBERTFAShadowError("shadow response must use the CPU execution provider")
    python_executable = Path(str(provider_runtime.get("python_executable") or "")).resolve()
    if not python_executable.is_file() or not str(provider_runtime.get("python_version") or "").strip():
        raise EnglishFinalMixHuBERTFAShadowError("shadow response Python runtime identity is invalid")
    libraries = provider_runtime.get("libraries")
    if not isinstance(libraries, Mapping) or any(
        not str(libraries.get(name) or "").strip() for name in ("onnxruntime", "librosa", "soundfile")
    ):
        raise EnglishFinalMixHuBERTFAShadowError("shadow response runtime library identity is incomplete")
    device_utils = _validate_file_binding(
        provider_runtime.get("vendor_device_utils"), "shadow response vendor device utils"
    )
    runtime_bundle["provider_runtime"] = {
        "python_executable": str(python_executable),
        "python_version": str(provider_runtime["python_version"]),
        "actual_providers": list(providers),
        "libraries": {name: str(libraries[name]) for name in ("onnxruntime", "librosa", "soundfile")},
        "vendor_device_utils": device_utils,
    }

    records = response.get("records")
    if not isinstance(records, list):
        raise EnglishFinalMixHuBERTFAShadowError("shadow response records must be a list")
    expected_records = plan.get("records")
    if not isinstance(expected_records, list):
        raise EnglishFinalMixHuBERTFAShadowError("shadow plan records must be a list")
    if len(records) != len(expected_records) or int(response.get("record_count") or -1) != len(records):
        raise EnglishFinalMixHuBERTFAShadowError("shadow response record count mismatch")
    expected_ids = [str(row["record_id"]) for row in expected_records]
    actual_ids = [str(row.get("record_id") or "") for row in records if isinstance(row, Mapping)]
    if len(actual_ids) != len(records) or actual_ids != expected_ids:
        raise EnglishFinalMixHuBERTFAShadowError("shadow response record order/identity mismatch")

    for expected, returned in zip(expected_records, records):
        if not isinstance(returned, Mapping):
            raise EnglishFinalMixHuBERTFAShadowError("shadow response record must be an object")
        if returned.get("mix_window_ms") != expected.get("mix_window_ms"):
            raise EnglishFinalMixHuBERTFAShadowError("shadow response exact mix window mismatch")
        if returned.get("lexical_units_sha256") != expected.get("lexical_units_sha256"):
            raise EnglishFinalMixHuBERTFAShadowError("shadow response lexical identity mismatch")
        if returned.get("alignment_method_id") != CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID:
            raise EnglishFinalMixHuBERTFAShadowError("shadow response alignment method mismatch")
        status = str(returned.get("status") or "")
        if status not in {"aligned", "unaligned"}:
            raise EnglishFinalMixHuBERTFAShadowError("shadow response record status is invalid")
        if status == "unaligned":
            if returned.get("predicted_onset_ms") is not None or returned.get("predicted_interval_ms") is not None:
                raise EnglishFinalMixHuBERTFAShadowError("unaligned shadow record may not contain timing")
            if not str(returned.get("reason") or ""):
                raise EnglishFinalMixHuBERTFAShadowError("unaligned shadow record requires a reason")
            continue
        predicted = returned.get("predicted_onset_ms")
        interval = returned.get("predicted_interval_ms")
        if type(predicted) is not int or not isinstance(interval, list) or len(interval) != 2 or any(type(value) is not int for value in interval):
            raise EnglishFinalMixHuBERTFAShadowError("aligned shadow record timing is invalid")
        interval_start, interval_end = interval
        window_start, window_end = [int(value) for value in expected["mix_window_ms"]]
        if predicted != interval_start or not window_start <= interval_start < interval_end <= window_end:
            raise EnglishFinalMixHuBERTFAShadowError("aligned shadow interval lies outside the exact mix window")
        _require_sha256(returned.get("aligned_words_sha256"), "aligned words SHA")
        if str(returned.get("reason") or ""):
            raise EnglishFinalMixHuBERTFAShadowError("aligned shadow record may not contain a rejection reason")

    bound = dict(response)
    bound["observer_runtime_bundle_sha256"] = sha256_json(runtime_bundle)
    return bound


def evaluate_shadow(plan: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """Describe frozen-denominator coverage and disagreement; current final is not truth."""
    plan_records = {
        str(row["record_id"]): row for row in plan.get("records", []) if isinstance(row, Mapping)
    }
    response_records = response.get("records")
    if not isinstance(response_records, list):
        raise EnglishFinalMixHuBERTFAShadowError("evaluation response records must be a list")
    response_by_id = {
        str(row.get("record_id") or ""): row
        for row in response_records
        if isinstance(row, Mapping)
    }
    if len(response_by_id) != len(response_records):
        raise EnglishFinalMixHuBERTFAShadowError("evaluation response record IDs are invalid/duplicated")
    targets = plan.get("targets")
    if not isinstance(targets, list):
        raise EnglishFinalMixHuBERTFAShadowError("evaluation plan targets must be a list")

    rows: list[dict[str, Any]] = []
    per_ordinal: dict[int, list[int]] = {}
    rejection_reason_counts: dict[str, int] = {}
    aligned_count = 0
    rejected_before_adapter_count = 0
    unaligned_count = 0

    for target in targets:
        if not isinstance(target, Mapping):
            raise EnglishFinalMixHuBERTFAShadowError("evaluation target must be an object")
        ordinal = int(target["ordinal"])
        base = {
            "job_id": str(target["job_id"]),
            "ordinal": ordinal,
            "occurrence_id": str(target["occurrence_id"]),
            "track_id": str(target["track_id"]),
            "canonical_line_index": int(target["canonical_line_index"]),
            "canonical_text_sha256": str(target["canonical_text_sha256"]),
        }
        if target.get("status") == "rejected":
            reason = str(target.get("reason") or "plan_rejected_without_reason")
            rejected_before_adapter_count += 1
            rejection_reason_counts[reason] = rejection_reason_counts.get(reason, 0) + 1
            rows.append(
                {
                    **base,
                    "record_id": None,
                    "status": "rejected_before_adapter",
                    "reason": reason,
                    "routing_final_start_ms": None,
                    "predicted_onset_ms": None,
                    "predicted_interval_ms": None,
                }
            )
            continue
        if target.get("status") != "prepared":
            raise EnglishFinalMixHuBERTFAShadowError("evaluation target status is invalid")
        record_id = str(target.get("record_id") or "")
        source = plan_records.get(record_id)
        result = response_by_id.get(record_id)
        if source is None or result is None:
            raise EnglishFinalMixHuBERTFAShadowError("evaluation prepared target lacks bound plan/response record")
        status = str(result.get("status") or "")
        reason = str(result.get("reason") or "")
        row = {
            **base,
            "record_id": record_id,
            "status": status,
            "reason": reason,
            "routing_final_start_ms": int(source["routing_final_interval_ms"][0]),
            "predicted_onset_ms": result.get("predicted_onset_ms"),
            "predicted_interval_ms": result.get("predicted_interval_ms"),
            "mix_window_ms": list(source["mix_window_ms"]),
        }
        if status == "aligned":
            predicted = int(result["predicted_onset_ms"])
            aligned_count += 1
            delta = predicted - int(row["routing_final_start_ms"])
            row["signed_disagreement_vs_current_final_ms"] = delta
            row["absolute_disagreement_vs_current_final_ms"] = abs(delta)
            window_start, window_end = [int(value) for value in source["mix_window_ms"]]
            row["distance_to_mix_window_start_ms"] = predicted - window_start
            row["distance_to_mix_window_end_ms"] = window_end - predicted
            row["min_mix_window_edge_distance_ms"] = min(
                predicted - window_start,
                window_end - predicted,
            )
            per_ordinal.setdefault(ordinal, []).append(abs(delta))
        else:
            unaligned_count += 1
            rejection_reason_counts[reason] = rejection_reason_counts.get(reason, 0) + 1
        rows.append(row)

    if len(rows) != int(plan.get("selected_target_count") or -1):
        raise EnglishFinalMixHuBERTFAShadowError("evaluation did not preserve the frozen selected denominator")

    def percentile_nearest(values: Sequence[int], q: float) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
        return int(ordered[index])

    tracks: list[dict[str, Any]] = []
    ordinals = sorted({int(row["ordinal"]) for row in targets if isinstance(row, Mapping)})
    for ordinal in ordinals:
        target_rows = [row for row in rows if int(row["ordinal"]) == ordinal]
        values = per_ordinal.get(ordinal, [])
        local_reasons: dict[str, int] = {}
        for row in target_rows:
            if row["status"] == "aligned":
                continue
            reason = str(row.get("reason") or "")
            local_reasons[reason] = local_reasons.get(reason, 0) + 1
        selected = len(target_rows)
        prepared = sum(row["status"] != "rejected_before_adapter" for row in target_rows)
        aligned = sum(row["status"] == "aligned" for row in target_rows)
        tracks.append(
            {
                "ordinal": ordinal,
                "selected_count": selected,
                "prepared_count": prepared,
                "aligned_count": aligned,
                "rejected_before_adapter_count": sum(
                    row["status"] == "rejected_before_adapter" for row in target_rows
                ),
                "unaligned_count": sum(row["status"] == "unaligned" for row in target_rows),
                "aligned_fraction_of_selected": aligned / selected if selected else 0.0,
                "median_abs_disagreement_vs_current_final_ms": percentile_nearest(values, 0.5),
                "p90_abs_disagreement_vs_current_final_ms": percentile_nearest(values, 0.9),
                "max_abs_disagreement_vs_current_final_ms": max(values) if values else None,
                "rejection_reason_counts": dict(sorted(local_reasons.items())),
            }
        )
    return {
        "schema_version": "english-final-mix-hubertfa-shadow-evaluation-1.1",
        "policy_id": ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
        "observer_id": ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
        "correlation_group": ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP,
        "authority": "descriptive_only_current_final_is_not_truth",
        "automatic_timing_change_allowed": False,
        "selected_target_count": int(plan.get("selected_target_count") or 0),
        "prepared_record_count": int(plan.get("prepared_record_count") or 0),
        "rejected_before_adapter_count": rejected_before_adapter_count,
        "aligned_record_count": aligned_count,
        "unaligned_record_count": unaligned_count,
        "aligned_fraction_of_selected": (
            aligned_count / int(plan["selected_target_count"])
            if int(plan.get("selected_target_count") or 0) > 0
            else 0.0
        ),
        "rejection_reason_counts": dict(sorted(rejection_reason_counts.items())),
        "rows": rows,
        "tracks": tracks,
        "bindings": {
            "plan_sha256": str(plan.get("plan_sha256") or ""),
            "request_sha256": str(response.get("request_sha256") or ""),
            "observer_runtime_bundle_sha256": str(response.get("observer_runtime_bundle_sha256") or ""),
        },
    }


__all__ = (
    "ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_SCHEMA",
    "ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID",
    "ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS",
    "ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID",
    "ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP",
    "ENGLISH_FINAL_MIX_HUBERTFA_REQUEST_PROTOCOL",
    "EnglishFinalMixHuBERTFAShadowError",
    "sha256_file",
    "sha256_json",
    "build_shadow_plan",
    "build_adapter_request",
    "bind_adapter_response",
    "evaluate_shadow",
)
