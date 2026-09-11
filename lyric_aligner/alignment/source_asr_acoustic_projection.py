"""Independent source-ASR -> source/mix-acoustic onset evidence.

The source recording is transcribed without canonical text.  Canonical lyrics are
used only after inference to identify exact, globally unique English word runs.
Those observed source onsets are then projected to the exact final mix through
bounded source/mix acoustic retrieval.  Editor timing and an unpromoted LRC
clock may bound search, but neither is allowed to supply the predicted onset.

This module only builds/reconciles shadow evidence.  It does not grant release
or subtitle timing authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from lyric_aligner.text.alignment_lexical import AlignmentLexicalError, english_units
from lyric_aligner.alignment.acoustic_eligibility import timing_evidence_qualified

SOURCE_ASR_ONSET_POLICY_ID_V1 = "whole-source-exact-canonical-onset-2026-09-10-v1"
SOURCE_ASR_ONSET_POLICY_ID = "whole-source-unique-line-prefix-onset-2026-09-10-v2"
SOURCE_ASR_ACOUSTIC_POLICY_ID_V1 = "source-asr-acoustic-projection-2026-09-10-v1"
SOURCE_ASR_ACOUSTIC_POLICY_ID = "source-asr-acoustic-projection-2026-09-10-v2"
SOURCE_ASR_ACOUSTIC_SCHEMA_VERSION_V1 = "source-asr-acoustic-projection-1.0"
SOURCE_ASR_ACOUSTIC_SCHEMA_VERSION = "source-asr-acoustic-projection-1.1"
MIN_PREFIX_LEXICAL_UNITS = 3
MIN_PREFIX_SIGNAL_CHARACTERS = 12
MIN_WORD_PROBABILITY = 0.70
MAX_ADJACENT_GAP_MS = 1500
SOURCE_CONTEXT_BEFORE_MS = 5000
SOURCE_CONTEXT_AFTER_MS = 7000
_SHA = set("0123456789abcdef")
_ALLOWED_PUNCTUATION = set(" \t\r\n.,!?;:()[]{}\\\"/\\-–—_+&@#$%^*=~`|<>")
_SUPPORTED_PROJECTION_CONTRACTS = {
    (SOURCE_ASR_ACOUSTIC_SCHEMA_VERSION_V1, SOURCE_ASR_ACOUSTIC_POLICY_ID_V1),
    (SOURCE_ASR_ACOUSTIC_SCHEMA_VERSION, SOURCE_ASR_ACOUSTIC_POLICY_ID),
}


class SourceAsrAcousticProjectionError(ValueError):
    """Raised when the frozen source/mix evidence lineage cannot be replayed."""


def _sha_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _json_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _require_sha(value: object, label: str) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(char not in _SHA for char in digest):
        raise SourceAsrAcousticProjectionError(f"{label} must be lowercase SHA-256")
    return digest


def _english_complete(value: object) -> list[str] | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).replace("’", "'")
    for char in normalized:
        if char.isascii() and (char.isalnum() or char == "'"):
            continue
        if char in _ALLOWED_PUNCTUATION:
            continue
        return None
    try:
        return english_units(normalized)
    except AlignmentLexicalError:
        return None


def _signal_characters(units: Sequence[str]) -> int:
    return sum(sum(char.isalnum() for char in unit) for unit in units)


def _occurrences(stream: Sequence[str | None], target: Sequence[str]) -> list[int]:
    if not target:
        return []
    size = len(target)
    return [
        start
        for start in range(len(stream) - size + 1)
        if list(stream[start : start + size]) == list(target)
    ]


def _canonical_index(lines: Sequence[Mapping[str, Any]]) -> tuple[dict[int, dict[str, Any]], list[str | None]]:
    if isinstance(lines, (str, bytes)) or not isinstance(lines, Sequence) or not lines:
        raise SourceAsrAcousticProjectionError("canonical timeline lines must be a non-empty sequence")
    by_index: dict[int, dict[str, Any]] = {}
    flattened: list[str | None] = []
    for row in lines:
        if not isinstance(row, Mapping):
            raise SourceAsrAcousticProjectionError("canonical timeline line is invalid")
        try:
            index = int(row["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceAsrAcousticProjectionError("canonical line index is invalid") from exc
        if index in by_index:
            raise SourceAsrAcousticProjectionError("canonical line index is duplicated")
        text = str(row.get("text") or "")
        units = _english_complete(text)
        by_index[index] = {"text": text, "units": units}
        flattened.extend(units if units is not None else [None])
    return by_index, flattened


def exact_source_onsets(
    *,
    canonical_lines: Sequence[Mapping[str, Any]],
    selected_line_indices: Sequence[int],
    source_observation: Mapping[str, Any],
    expected_source_audio_sha256: str,
) -> dict[int, dict[str, Any]]:
    """Return strict source-ASR onsets for a preselected canonical population.

    The onset authority comes only from a line-start prefix. Starting at the
    first canonical word, the shortest prefix with at least
    ``MIN_PREFIX_LEXICAL_UNITS`` and ``MIN_PREFIX_SIGNAL_CHARACTERS`` is
    considered, then progressively extended. A prefix is accepted only when it
    is unique in both the full canonical token stream and the full observed
    source-ASR token stream. This tolerates ASR errors later in a lyric line
    without ever using an arbitrary middle fragment to back-project the line
    onset. Every contributing observed word still passes the original
    probability/timing/gap gates.
    """

    expected_sha = _require_sha(expected_source_audio_sha256, "source audio SHA")
    if source_observation.get("status") != "observed":
        raise SourceAsrAcousticProjectionError("source observation is not observed")
    if source_observation.get("audio_basis") != "source":
        raise SourceAsrAcousticProjectionError("source observation audio_basis must be source")
    if str(source_observation.get("source_audio_sha256") or "") != expected_sha:
        raise SourceAsrAcousticProjectionError("source observation/source audio SHA mismatch")
    observation_key = _require_sha(
        source_observation.get("cache_key_sha256"), "source observation cache key"
    )
    words_raw = source_observation.get("words")
    if not isinstance(words_raw, Sequence) or isinstance(words_raw, (str, bytes)):
        raise SourceAsrAcousticProjectionError("source observation words must be a sequence")

    canonical_by_index, canonical_stream = _canonical_index(canonical_lines)
    observed: list[dict[str, Any]] = []
    observed_stream: list[str | None] = []
    for position, raw in enumerate(words_raw):
        if not isinstance(raw, Mapping):
            observed.append({"raw": None, "unit": None, "position": position})
            observed_stream.append(None)
            continue
        units = _english_complete(raw.get("text"))
        unit = units[0] if units is not None and len(units) == 1 else None
        observed.append({"raw": raw, "unit": unit, "position": position})
        observed_stream.append(unit)

    selected = [int(value) for value in selected_line_indices]
    if len(selected) != len(set(selected)):
        raise SourceAsrAcousticProjectionError("selected canonical line indices are duplicated")
    results: dict[int, dict[str, Any]] = {}
    for index in selected:
        canonical = canonical_by_index.get(index)
        if canonical is None:
            raise SourceAsrAcousticProjectionError(f"selected canonical line {index} is absent")
        units = canonical["units"]
        row: dict[str, Any] = {
            "canonical_line_index": index,
            "canonical_text_sha256": _sha_text(canonical["text"]),
            "source_observation_cache_key_sha256": observation_key,
            "source_audio_sha256": expected_sha,
            "policy_id": SOURCE_ASR_ONSET_POLICY_ID,
            "status": "rejected",
        }
        if units is None:
            row["reason"] = "canonical_line_not_complete_english_lexical_sequence"
        else:
            row["canonical_line_lexical_unit_count"] = len(units)
            candidate_lengths = [
                length
                for length in range(MIN_PREFIX_LEXICAL_UNITS, len(units) + 1)
                if _signal_characters(units[:length]) >= MIN_PREFIX_SIGNAL_CHARACTERS
            ]
            if not candidate_lengths:
                row["reason"] = "canonical_line_below_minimum_prefix_evidence"
            else:
                canonical_unique_seen = False
                observed_unique_seen = False
                validation_reason: str | None = None
                prefix_attempts: list[dict[str, Any]] = []
                for length in candidate_lengths:
                    prefix = units[:length]
                    canonical_hits = _occurrences(canonical_stream, prefix)
                    attempt: dict[str, Any] = {
                        "lexical_unit_count": length,
                        "signal_characters": _signal_characters(prefix),
                        "canonical_sequence_match_count": len(canonical_hits),
                    }
                    prefix_attempts.append(attempt)
                    if len(canonical_hits) != 1:
                        continue
                    canonical_unique_seen = True
                    observed_hits = _occurrences(observed_stream, prefix)
                    attempt["observed_sequence_match_count"] = len(observed_hits)
                    if len(observed_hits) != 1:
                        continue
                    observed_unique_seen = True
                    start = observed_hits[0]
                    selected_words = observed[start : start + length]
                    previous_end: int | None = None
                    probabilities: list[float] = []
                    valid = True
                    reason = ""
                    first_ms = last_ms = 0
                    for offset, item in enumerate(selected_words):
                        raw = item["raw"]
                        if not isinstance(raw, Mapping):
                            valid, reason = False, "observed_sequence_contains_barrier"
                            break
                        try:
                            word_start = int(raw["start_ms"])
                            word_end = int(raw["end_ms"])
                            probability = float(raw["probability"])
                        except (KeyError, TypeError, ValueError):
                            valid, reason = False, "observed_word_timing_or_probability_invalid"
                            break
                        if (
                            not math.isfinite(probability)
                            or probability < MIN_WORD_PROBABILITY
                            or probability > 1.0
                            or word_start < 0
                            or word_end <= word_start
                        ):
                            valid, reason = False, "observed_word_timing_or_probability_below_threshold"
                            break
                        if previous_end is not None:
                            gap = word_start - previous_end
                            if gap < 0:
                                valid, reason = False, "observed_word_time_order_or_overlap_invalid"
                                break
                            if gap > MAX_ADJACENT_GAP_MS:
                                valid, reason = False, "observed_word_adjacent_gap_exceeded"
                                break
                        if offset == 0:
                            first_ms = word_start
                        last_ms = word_end
                        previous_end = word_end
                        probabilities.append(probability)
                    if not valid:
                        validation_reason = reason
                        break
                    row.update(
                        status="accepted",
                        reason=None,
                        source_start_ms=first_ms,
                        source_end_ms=last_ms,
                        lexical_unit_count=length,
                        canonical_prefix_lexical_unit_count=length,
                        prefix_signal_characters=_signal_characters(prefix),
                        matched_prefix_sha256=_sha_text(" ".join(prefix)),
                        match_mode=(
                            "unique_full_line"
                            if length == len(units)
                            else "unique_line_start_prefix"
                        ),
                        canonical_sequence_match_count=1,
                        observed_sequence_match_count=1,
                        mean_word_probability=round(sum(probabilities) / len(probabilities), 6),
                        minimum_word_probability=round(min(probabilities), 6),
                        observed_word_start_index=start,
                        observed_word_end_index=start + length,
                    )
                    break
                row["prefix_attempts"] = prefix_attempts
                if row["status"] != "accepted":
                    if validation_reason is not None:
                        row["reason"] = validation_reason
                    elif not canonical_unique_seen:
                        row["reason"] = "canonical_sequence_not_unique_in_full_canonical"
                    elif not observed_unique_seen:
                        row["reason"] = "observed_sequence_not_unique_in_full_source_observation"
                    else:
                        row["reason"] = "source_prefix_evidence_not_accepted"
        results[index] = row
    return results


def build_source_asr_acoustic_plan(
    *,
    task_fingerprint_sha256: str,
    selection_lock: Mapping[str, Any],
    alignment_plan: Mapping[str, Any],
    track_assets: Mapping[str, Any],
    timelines_by_ordinal: Mapping[int, Mapping[str, Any]],
    source_observations_by_ordinal: Mapping[int, Mapping[str, Any]],
    mix_audio_sha256: str,
) -> dict[str, Any]:
    """Build acoustic jobs whose source onset comes only from source-ASR evidence."""

    fingerprint = _require_sha(task_fingerprint_sha256, "task fingerprint")
    mix_sha = _require_sha(mix_audio_sha256, "mix audio SHA")
    if str(selection_lock.get("task_fingerprint_sha256") or "") != fingerprint:
        raise SourceAsrAcousticProjectionError("selection lock belongs to another task")
    if selection_lock.get("policy_id") != "targeted-semantic-failed-expansion-v1":
        raise SourceAsrAcousticProjectionError("unexpected targeted selection policy")
    selected_ordinals = selection_lock.get("selected_ordinals")
    selection = selection_lock.get("selection")
    if not isinstance(selected_ordinals, list) or not isinstance(selection, Mapping):
        raise SourceAsrAcousticProjectionError("targeted selection lock is invalid")
    if len({int(value) for value in selected_ordinals}) != len(selected_ordinals):
        raise SourceAsrAcousticProjectionError("duplicate selected ordinal")

    jobs_raw = alignment_plan.get("jobs")
    if not isinstance(jobs_raw, list):
        raise SourceAsrAcousticProjectionError("alignment plan has no jobs")
    plan_by_id: dict[str, Mapping[str, Any]] = {}
    for job in jobs_raw:
        if not isinstance(job, Mapping):
            continue
        job_id = str(job.get("job_id") or "")
        if not job_id or job_id in plan_by_id:
            raise SourceAsrAcousticProjectionError("alignment plan job identity is invalid")
        plan_by_id[job_id] = job

    assets = track_assets.get("assets")
    occurrences = track_assets.get("occurrences")
    if not isinstance(assets, list) or not isinstance(occurrences, list):
        raise SourceAsrAcousticProjectionError("track assets payload is invalid")
    asset_by_track = {
        str(row.get("track_id") or ""): row for row in assets if isinstance(row, Mapping)
    }
    occurrence_by_ordinal = {
        int(row["ordinal"]): row
        for row in occurrences
        if isinstance(row, Mapping) and row.get("ordinal") is not None
    }
    resolutions = track_assets.get("resolution")
    if not isinstance(resolutions, list):
        raise SourceAsrAcousticProjectionError("track assets lack canonical resolution")
    resolution_by_track = {str(row.get("track_id") or ""): row for row in resolutions}
    if (len(asset_by_track) != len(assets) or len(occurrence_by_ordinal) != len(occurrences)
            or len(resolution_by_track) != len(resolutions)):
        raise SourceAsrAcousticProjectionError("duplicate asset/occurrence/resolution identity")

    evidence_jobs: list[dict[str, Any]] = []
    source_onset_ledger: list[dict[str, Any]] = []
    seen_selection_jobs: set[str] = set()
    for ordinal_raw in selected_ordinals:
        ordinal = int(ordinal_raw)
        selected_rows = selection.get(str(ordinal))
        if not isinstance(selected_rows, list) or not selected_rows:
            raise SourceAsrAcousticProjectionError(f"selection lock lacks ordinal {ordinal}")
        occurrence = occurrence_by_ordinal.get(ordinal)
        timeline = timelines_by_ordinal.get(ordinal)
        observation = source_observations_by_ordinal.get(ordinal)
        if occurrence is None or timeline is None or observation is None:
            raise SourceAsrAcousticProjectionError(f"ordinal {ordinal} lacks asset/timeline/source observation")
        track_id = str(occurrence.get("track_id") or "")
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        asset = asset_by_track.get(track_id)
        if asset is None:
            raise SourceAsrAcousticProjectionError(f"ordinal {ordinal} lacks bound track asset")
        result = timeline.get("result")
        lines = result.get("lines") if isinstance(result, Mapping) else None
        if not isinstance(lines, list):
            raise SourceAsrAcousticProjectionError(f"ordinal {ordinal} timeline lacks canonical lines")
        canonical_selection = resolution_by_track.get(track_id, {}).get("canonical_selection")
        if not isinstance(canonical_selection, list) or not canonical_selection:
            raise SourceAsrAcousticProjectionError("missing full canonical selection")
        canonical_sha = _require_sha(asset.get("canonical_selection_sha256"), "canonical selection SHA")
        if (_json_sha(canonical_selection) != canonical_sha
                or result.get("canonical_selection_sha256") != canonical_sha
                or timeline.get("task_fingerprint_sha256") != fingerprint
                or timeline.get("occurrence_id") != occurrence_id
                or timeline.get("track_id") != track_id):
            raise SourceAsrAcousticProjectionError("timeline/full canonical identity mismatch")
        full_lines = [{"canonical_line_index": index, "text": row["text"]}
                      for index, row in enumerate(canonical_selection)]
        selected_indices = [int(row["canonical_line_index"]) for row in selected_rows]
        onsets = exact_source_onsets(
            canonical_lines=full_lines,
            selected_line_indices=selected_indices,
            source_observation=observation,
            expected_source_audio_sha256=str(asset.get("source_audio_sha256") or ""),
        )
        timeline_by_index = {
            int(line["canonical_line_index"]): line for line in lines if isinstance(line, Mapping)
        }
        for selected_row in selected_rows:
            if not isinstance(selected_row, Mapping):
                raise SourceAsrAcousticProjectionError("selection lock row is invalid")
            source_job_id = str(selected_row.get("job_id") or "")
            if not source_job_id or source_job_id in seen_selection_jobs:
                raise SourceAsrAcousticProjectionError("duplicate/empty frozen selection job identity")
            seen_selection_jobs.add(source_job_id)
            plan_job = plan_by_id.get(source_job_id)
            if plan_job is None:
                raise SourceAsrAcousticProjectionError(f"frozen job {source_job_id} missing from alignment plan")
            line_index = int(selected_row["canonical_line_index"])
            if (
                int(selected_row.get("ordinal", -1)) != ordinal
                or str(selected_row.get("occurrence_id") or "") != occurrence_id
                or int(plan_job.get("ordinal", -1)) != ordinal
                or str(plan_job.get("occurrence_id") or "") != occurrence_id
                or plan_job.get("track_id") != track_id
                or plan_job.get("canonical_selection_sha256") != canonical_sha
                or int(plan_job.get("canonical_line_index", -1)) != line_index
                or str(plan_job.get("canonical_text_sha256") or "")
                != str(selected_row.get("canonical_text_sha256") or "")
            ):
                raise SourceAsrAcousticProjectionError("frozen targeted identity differs from alignment plan")
            timeline_line = timeline_by_index.get(line_index)
            if timeline_line is None or _sha_text(str(timeline_line.get("text") or "")) != str(selected_row.get("canonical_text_sha256") or ""):
                raise SourceAsrAcousticProjectionError("frozen targeted canonical text differs from current timeline")
            if _sha_text(str(full_lines[line_index]["text"])) != str(selected_row.get("canonical_text_sha256") or ""):
                raise SourceAsrAcousticProjectionError("frozen targeted text differs from full canonical selection")
            onset = dict(onsets[line_index])
            source_onset_ledger.append({"ordinal": ordinal, "occurrence_id": occurrence_id, **onset})
            if onset["status"] != "accepted":
                continue
            mix_window = plan_job.get("mix_window_ms")
            if not isinstance(mix_window, list) or len(mix_window) != 2:
                raise SourceAsrAcousticProjectionError("frozen alignment job lacks mix window")
            source_start = int(onset["source_start_ms"])
            source_end = int(onset["source_end_ms"])
            source_domain = observation.get("search_domain")
            source_duration_ms = (
                int(source_domain.get("end_ms"))
                if isinstance(source_domain, Mapping) and source_domain.get("end_ms") is not None
                else None
            )
            if source_duration_ms is None or source_duration_ms <= source_end:
                raise SourceAsrAcousticProjectionError(
                    f"ordinal {ordinal} source observation lacks a valid full-source duration"
                )
            source_window = [
                max(0, source_start - SOURCE_CONTEXT_BEFORE_MS),
                min(source_duration_ms, source_end + SOURCE_CONTEXT_AFTER_MS),
            ]
            identity = {
                "policy_id": SOURCE_ASR_ACOUSTIC_POLICY_ID,
                "task_fingerprint_sha256": fingerprint,
                "ordinal": ordinal,
                "occurrence_id": occurrence_id,
                "track_id": track_id,
                "canonical_line_index": line_index,
                "canonical_text_sha256": str(selected_row["canonical_text_sha256"]),
                "frozen_selection_job_id": source_job_id,
                "source_audio_sha256": str(asset["source_audio_sha256"]),
                "mix_audio_sha256": mix_sha,
                "source_observation_cache_key_sha256": str(observation["cache_key_sha256"]),
                "source_asr_start_ms": source_start,
                "source_asr_end_ms": source_end,
                "mix_window_ms": [int(mix_window[0]), int(mix_window[1])],
                "source_window_ms": source_window,
            }
            evidence_job = {
                "job_id": _json_sha(identity),
                "source_selection_job_id": source_job_id,
                "ordinal": ordinal,
                "source_ordinal": ordinal - 1,
                "occurrence_id": occurrence_id,
                "track_id": track_id,
                "canonical_line_index": line_index,
                "canonical_text_sha256": str(selected_row["canonical_text_sha256"]),
                "source_audio_sha256": str(asset["source_audio_sha256"]),
                "mix_audio_sha256": mix_sha,
                "source_observation_cache_key_sha256": str(observation["cache_key_sha256"]),
                "source_asr_start_ms": source_start,
                "source_asr_end_ms": source_end,
                "expected_source_time_ms": source_start,
                "mix_window_ms": [int(mix_window[0]), int(mix_window[1])],
                "source_window_ms": source_window,
                "rate_prior": None,
                "requested_capabilities": ["source_local_acoustic_match", "source_asr_acoustic_projection"],
                "execution_state": "planned_not_executed",
                "source_onset_policy_id": SOURCE_ASR_ONSET_POLICY_ID,
                "projection_policy_id": SOURCE_ASR_ACOUSTIC_POLICY_ID,
            }
            region_window = plan_job.get("region_mix_window_ms")
            region_id = plan_job.get("region_id")
            if isinstance(region_window, list) and len(region_window) == 2:
                evidence_job["region_mix_window_ms"] = [int(region_window[0]), int(region_window[1])]
            if region_id is not None:
                evidence_job["region_id"] = str(region_id)
            evidence_jobs.append(evidence_job)

    if len(source_onset_ledger) != int(selection_lock.get("selected_job_count", -1)):
        raise SourceAsrAcousticProjectionError("source onset ledger does not cover frozen selection")
    return {
        "schema_version": SOURCE_ASR_ACOUSTIC_SCHEMA_VERSION,
        "policy_id": SOURCE_ASR_ACOUSTIC_POLICY_ID,
        "task_fingerprint_sha256": fingerprint,
        "mode": "plan_only",
        "backend_execution_performed": False,
        "automatic_timing_change_allowed": False,
        "timing_authority": "shadow_only",
        "frozen_selection_policy_id": str(selection_lock.get("policy_id") or ""),
        "frozen_selection_job_count": len(source_onset_ledger),
        "accepted_source_onset_count": len(evidence_jobs),
        "rejected_source_onset_count": len(source_onset_ledger) - len(evidence_jobs),
        "constants": {
            "minimum_prefix_lexical_units": MIN_PREFIX_LEXICAL_UNITS,
            "minimum_prefix_signal_characters": MIN_PREFIX_SIGNAL_CHARACTERS,
            "minimum_word_probability": MIN_WORD_PROBABILITY,
            "maximum_adjacent_gap_ms": MAX_ADJACENT_GAP_MS,
            "source_context_before_ms": SOURCE_CONTEXT_BEFORE_MS,
            "source_context_after_ms": SOURCE_CONTEXT_AFTER_MS,
            "source_rate_prior": None,
            "editor_timing_used_for_prediction": False,
            "source_clock_used_for_predicted_onset": False,
        },
        "source_onset_ledger": source_onset_ledger,
        "jobs": evidence_jobs,
    }


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _abs_metrics(values: Sequence[int]) -> dict[str, float | int | None]:
    absolute = sorted(abs(int(value)) for value in values)
    if not absolute:
        return {"count": 0, "median_abs_ms": None, "p90_abs_ms": None, "worst_abs_ms": None}
    middle = len(absolute) // 2
    median = (
        float(absolute[middle])
        if len(absolute) % 2
        else (absolute[middle - 1] + absolute[middle]) / 2.0
    )
    return {
        "count": len(absolute),
        "median_abs_ms": median,
        "p90_abs_ms": round(float(_percentile(absolute, 0.90)), 3),
        "worst_abs_ms": float(absolute[-1]),
    }


def evaluate_source_asr_acoustic_shadow(
    *,
    evidence: Mapping[str, Any],
    fusion: Mapping[str, Any],
    final_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare the new shadow projection with existing final-mix ASR and final SRT.

    This is descriptive only.  Agreement bins are reported at several fixed
    distances and do not grant production authority or select a threshold.
    """

    evidence_contract = (evidence.get("schema_version"), evidence.get("policy_id"))
    if evidence_contract not in _SUPPORTED_PROJECTION_CONTRACTS:
        raise SourceAsrAcousticProjectionError("shadow evaluation evidence policy mismatch")
    fusion_lines = fusion.get("lines")
    if not isinstance(fusion_lines, list):
        raise SourceAsrAcousticProjectionError("shadow evaluation fusion has no lines")
    asr_by_key: dict[tuple[str, int], int] = {}
    for line in fusion_lines:
        if not isinstance(line, Mapping):
            continue
        occurrence_id = str(line.get("occurrence_id") or "")
        line_index = line.get("canonical_line_index")
        if not occurrence_id or line_index is None:
            continue
        for family in line.get("families") or []:
            if not isinstance(family, Mapping) or family.get("family") != "asr":
                continue
            onset = family.get("canonical_onset")
            if isinstance(onset, Mapping):
                start = onset.get("start_ms")
                score = onset.get("support_score")
                if (
                    isinstance(start, int)
                    and not isinstance(start, bool)
                    and isinstance(score, (int, float))
                    and not isinstance(score, bool)
                    and math.isfinite(float(score))
                    and float(score) >= 0.72
                    and onset.get("basis") == "canonical_prefix_word_span"
                    and onset.get("canonical_start_covered") is True
                    and onset.get("canonical_match_ambiguous") is False
                ):
                    asr_by_key[(occurrence_id, int(line_index))] = start
            elif family.get("available") is True:
                boundary = family.get("boundary_ms")
                support = family.get("canonical_match_support_score")
                if (
                    isinstance(boundary, list)
                    and len(boundary) == 2
                    and isinstance(boundary[0], int)
                    and family.get("canonical_start_covered") is True
                    and family.get("boundary_basis") == "canonical_word_span"
                    and isinstance(support, (int, float))
                    and float(support) >= 0.72
                ):
                    asr_by_key[(occurrence_id, int(line_index))] = int(boundary[0])

    final_by_key: dict[tuple[str, int], int] = {}
    for row in final_rows:
        if not isinstance(row, Mapping):
            continue
        occurrence_id = str(row.get("occurrence_id") or "")
        line_index_raw = row.get("canonical_line_index")
        start_raw = row.get("start_ms")
        line_index_text = "" if line_index_raw is None else str(line_index_raw).strip()
        start_text = "" if start_raw is None else str(start_raw).strip()
        if not occurrence_id or not line_index_text or not start_text:
            continue
        try:
            line_index = int(line_index_text)
            start_int = int(start_text)
        except ValueError as exc:
            raise SourceAsrAcousticProjectionError(
                "final audit canonical timing identity is invalid"
            ) from exc
        key = (occurrence_id, line_index)
        previous = final_by_key.get(key)
        if previous is None or start_int < previous:
            final_by_key[key] = start_int

    per_track: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    jobs = evidence.get("jobs")
    if not isinstance(jobs, list):
        raise SourceAsrAcousticProjectionError("shadow evaluation evidence jobs are invalid")
    for job in jobs:
        if (not isinstance(job, Mapping)
                or not timing_evidence_qualified(job)):
            continue
        ordinal = int(job["ordinal"])
        key = (str(job["occurrence_id"]), int(job["canonical_line_index"]))
        source_mix = int(job["predicted_mix_start_ms"])
        final_asr = asr_by_key.get(key)
        final_srt = final_by_key.get(key)
        row = {
            "ordinal": ordinal,
            "occurrence_id": key[0],
            "canonical_line_index": key[1],
            "source_acoustic_start_ms": source_mix,
            "final_mix_asr_start_ms": final_asr,
            "final_srt_start_ms": final_srt,
            "source_minus_final_asr_ms": None if final_asr is None else source_mix - final_asr,
            "final_srt_minus_source_ms": None if final_srt is None else final_srt - source_mix,
            "final_srt_minus_final_asr_ms": None if final_srt is None or final_asr is None else final_srt - final_asr,
        }
        rows.append(row)
        track = per_track.setdefault(
            ordinal,
            {
                "ordinal": ordinal,
                "eligible_source_projection_count": 0,
                "source_vs_final_asr_deltas_ms": [],
                "final_vs_source_deltas_ms": [],
                "final_vs_final_asr_deltas_ms": [],
            },
        )
        track["eligible_source_projection_count"] += 1
        if row["source_minus_final_asr_ms"] is not None:
            track["source_vs_final_asr_deltas_ms"].append(row["source_minus_final_asr_ms"])
        if row["final_srt_minus_source_ms"] is not None:
            track["final_vs_source_deltas_ms"].append(row["final_srt_minus_source_ms"])
        if row["final_srt_minus_final_asr_ms"] is not None:
            track["final_vs_final_asr_deltas_ms"].append(row["final_srt_minus_final_asr_ms"])

    tracks: list[dict[str, Any]] = []
    for ordinal in sorted(per_track):
        raw = per_track[ordinal]
        source_asr = raw["source_vs_final_asr_deltas_ms"]
        final_source = raw["final_vs_source_deltas_ms"]
        final_asr = raw["final_vs_final_asr_deltas_ms"]
        tracks.append(
            {
                "ordinal": ordinal,
                "eligible_source_projection_count": raw["eligible_source_projection_count"],
                "source_vs_final_asr": _abs_metrics(source_asr),
                "final_vs_source": _abs_metrics(final_source),
                "final_vs_final_asr": _abs_metrics(final_asr),
                "source_final_asr_agreement_bins": {
                    str(limit): sum(abs(int(value)) <= limit for value in source_asr)
                    for limit in (250, 500, 750, 1500, 2500)
                },
            }
        )
    return {
        "schema_version": "source-asr-acoustic-shadow-evaluation-1.0",
        "policy_id": "source-asr-acoustic-shadow-descriptive-evaluation-2026-09-10-v1",
        "automatic_timing_change_allowed": False,
        "authority": "descriptive_only_no_threshold_selection",
        "eligible_job_count": len(rows),
        "rows": rows,
        "tracks": tracks,
    }


def bind_source_asr_acoustic_results(
    *,
    plan: Mapping[str, Any],
    acoustic_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Join generic local-acoustic results to their independent source-ASR lineage."""

    plan_contract = (plan.get("schema_version"), plan.get("policy_id"))
    if plan_contract not in _SUPPORTED_PROJECTION_CONTRACTS:
        raise SourceAsrAcousticProjectionError("source-ASR acoustic plan policy mismatch")
    jobs = plan.get("jobs")
    results = acoustic_result.get("jobs")
    if not isinstance(jobs, list) or not isinstance(results, list):
        raise SourceAsrAcousticProjectionError("source-ASR acoustic plan/result jobs are invalid")
    plan_by_id = {str(row.get("job_id") or ""): row for row in jobs if isinstance(row, Mapping)}
    result_by_id = {str(row.get("job_id") or ""): row for row in results if isinstance(row, Mapping)}
    if (set(plan_by_id) != set(result_by_id) or "" in plan_by_id or "" in result_by_id
            or len(plan_by_id) != len(jobs) or len(result_by_id) != len(results)):
        raise SourceAsrAcousticProjectionError("source-ASR acoustic result job identity mismatch")
    bound: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job["job_id"])
        result = result_by_id[job_id]
        eligible = (
            timing_evidence_qualified(result)
            and result.get("mix_window_ms") == job.get("mix_window_ms")
        )
        bound.append(
            {
                "job_id": job_id,
                "source_selection_job_id": job["source_selection_job_id"],
                "ordinal": int(job["ordinal"]),
                "occurrence_id": job["occurrence_id"],
                "track_id": job["track_id"],
                "canonical_line_index": int(job["canonical_line_index"]),
                "canonical_text_sha256": job["canonical_text_sha256"],
                "source_audio_sha256": job["source_audio_sha256"],
                "mix_audio_sha256": job["mix_audio_sha256"],
                "source_observation_cache_key_sha256": job["source_observation_cache_key_sha256"],
                "source_asr_start_ms": int(job["source_asr_start_ms"]),
                "source_asr_end_ms": int(job["source_asr_end_ms"]),
                "predicted_mix_start_ms": int(result["predicted_mix_start_ms"]),
                "mix_window_ms": job.get("mix_window_ms"),
                "estimated_slope": result.get("estimated_slope"),
                "fused_score": result.get("fused_score"),
                "margin": result.get("margin"),
                "feature_agreement": result.get("feature_agreement"),
                "ambiguous": result.get("ambiguous"),
                "local_match_gate_passed": result.get("local_match_gate_passed"),
                "slope_search_boundary_hit": result.get("slope_search_boundary_hit"),
                "source_search_boundary_hit": result.get("source_search_boundary_hit"),
                "timing_fusion_evidence_eligible": eligible,
                "projection_within_mix_window": result.get("projection_within_mix_window"),
                "projection_extrapolation_ms": result.get("projection_extrapolation_ms"),
                "authority": "shadow_audio_evidence_only",
            }
        )
    return {
        "schema_version": str(plan["schema_version"]),
        "policy_id": str(plan["policy_id"]),
        "task_fingerprint_sha256": str(plan.get("task_fingerprint_sha256") or ""),
        "automatic_timing_change_allowed": False,
        "timing_authority": "shadow_only",
        "job_count": len(bound),
        "eligible_job_count": sum(bool(row["timing_fusion_evidence_eligible"]) for row in bound),
        "jobs": bound,
    }
