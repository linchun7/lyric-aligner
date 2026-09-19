"""Prepare bounded all-or-none adjacent-cue HuBERTFA source windows.

This is an experimental preparation surface.  It consumes an already frozen
all-canonical packet lattice and never rewrites its ledger or source sequence.
The existing anchored-path adapter remains the decoder; this module only builds
two-output records that a caller may later make atomic in interval selection.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.alignment.source_context_hubertfa import (
    SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES,
    SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS,
    SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS,
    SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
    SOURCE_CONTEXT_WINDOW_PAD_MS,
    HUBERTFA_TIME_BAND_DECODER_ID,
    HUBERTFA_TIME_BAND_POSTCHECK_ID,
    SourceContextHuBERTFAError,
    _block_anchor_detail,
    _dictionary_keys,
    _english_text_complete,
    _full_single_line_range,
    _positive_interval,
    _qualified_anchor_candidate,
    json_sha,
)
from lyric_aligner.text.alignment_lexical import alignment_units


SOURCE_JOINT_CONTEXT_POLICY_ID = "atomic-adjacent-pair-v1"
SOURCE_JOINT_EXACT_CONTEXT_POLICY_ID = "atomic-adjacent-pair-exact-source-v1"
SOURCE_JOINT_CONTEXT_SCHEMA_VERSION = "source-joint-context-1.0"


def _frozen_lattice(prepared: Mapping[str, Any]) -> tuple[
    list[int], dict[int, str], dict[int, Mapping[str, Any]], dict[str, str], tuple[int, int]
]:
    """Validate the fixed lattice once, without rebuilding or changing it."""

    if prepared.get("context_policy") != SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        raise SourceContextHuBERTFAError("joint pairs require anchored-path prepared context")
    if prepared.get("policy_id") != SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID:
        raise SourceContextHuBERTFAError("joint pairs require anchored-path lexical-only no-AP policy")
    canonical_rows = prepared.get("canonical_lines")
    if not isinstance(canonical_rows, list) or not canonical_rows:
        raise SourceContextHuBERTFAError("joint pairs require canonical lines")
    try:
        line_order = [int(row["canonical_line_index"]) for row in canonical_rows if isinstance(row, Mapping)]
        lines = {int(row["canonical_line_index"]): str(row["text"]) for row in canonical_rows if isinstance(row, Mapping)}
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceContextHuBERTFAError("joint pairs canonical lines are invalid") from exc
    if len(line_order) != len(canonical_rows) or len(lines) != len(line_order) or any(
        earlier >= later for earlier, later in zip(line_order, line_order[1:])
    ):
        raise SourceContextHuBERTFAError("joint pairs require increasing unique canonical lines")

    packet_rows = prepared.get("packets")
    if not isinstance(packet_rows, list):
        raise SourceContextHuBERTFAError("joint pairs require complete canonical packet lattice")
    packets: dict[int, Mapping[str, Any]] = {}
    for row in packet_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("packet"), Mapping):
            raise SourceContextHuBERTFAError("joint pairs require complete canonical packet lattice")
        try:
            index = int(row["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceContextHuBERTFAError("joint pairs packet lattice index is invalid") from exc
        if index in packets:
            raise SourceContextHuBERTFAError("joint pairs packet lattice index is duplicated")
        packets[index] = row["packet"]
    if set(packets) != set(line_order):
        raise SourceContextHuBERTFAError("joint pairs require complete canonical packet lattice")

    sequence = prepared.get("source_sequence")
    if not isinstance(sequence, Mapping):
        raise SourceContextHuBERTFAError("joint pairs require frozen source sequence")
    promotions: dict[str, str] = {}
    for item in sequence.get("promotions", []):
        if not isinstance(item, Mapping):
            raise SourceContextHuBERTFAError("joint pairs source sequence promotion is invalid")
        packet_key, candidate_id = item.get("packet_cache_key_sha256"), item.get("candidate_id")
        if not isinstance(packet_key, str) or not isinstance(candidate_id, str):
            raise SourceContextHuBERTFAError("joint pairs source sequence promotion is invalid")
        promotions[packet_key] = candidate_id

    source_domain = prepared.get("source_search_domain")
    domain = _positive_interval([
        source_domain.get("start_ms") if isinstance(source_domain, Mapping) else None,
        source_domain.get("end_ms") if isinstance(source_domain, Mapping) else None,
    ])
    if domain is None:
        domain = _positive_interval(prepared.get("source_search_domain_ms"))
    if domain is None:
        raise SourceContextHuBERTFAError("joint pairs require source search domain")
    return line_order, lines, packets, promotions, domain


def _pair_positions(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    try:
        left, right = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    return (left, right) if left < right else None


def _rejected(pair: Any, reason: str) -> dict[str, Any]:
    return {"pair": list(pair) if isinstance(pair, (tuple, list)) else pair,
            "status": "rejected", "reason": reason}


def prepare_joint_pairs(
    prepared: Mapping[str, Any],
    pairs: Sequence[Sequence[int]],
    *,
    cue_specs: Sequence[Mapping[str, Any]],
    dictionary_path: Path,
    observed_words: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prepare exactly two adjacent cue targets in one bounded path record.

    ``cue_specs`` carries the original canonical ownership ranges; the frozen
    ledger's target index is accepted only after those ranges prove the same
    complete single canonical line.  Each pair receives its nearest
    *qualified* exterior anchors once.  A failed pair is rejected in place; a
    farther anchor is never tried as a rescue.
    """

    if not isinstance(prepared, Mapping):
        raise SourceContextHuBERTFAError("joint pairs prepared payload must be a mapping")
    if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
        raise SourceContextHuBERTFAError("joint pairs must be a sequence")
    if isinstance(cue_specs, (str, bytes)) or not isinstance(cue_specs, Sequence):
        raise SourceContextHuBERTFAError("joint pairs require raw cue specs")
    line_order, lines, packets, promotions, domain = _frozen_lattice(prepared)
    source_observation_sha256 = prepared.get("source_observation_sha256")
    if not isinstance(source_observation_sha256, str) or len(source_observation_sha256) != 64:
        raise SourceContextHuBERTFAError("joint pairs require source observation SHA")
    ledger_rows = prepared.get("ledger")
    if not isinstance(ledger_rows, list):
        raise SourceContextHuBERTFAError("joint pairs require prepared ledger")
    ledger: dict[int, Mapping[str, Any]] = {}
    for entry in ledger_rows:
        if not isinstance(entry, Mapping):
            raise SourceContextHuBERTFAError("joint pairs ledger entry is invalid")
        try:
            position = int(entry["position"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceContextHuBERTFAError("joint pairs ledger position is invalid") from exc
        if position in ledger:
            raise SourceContextHuBERTFAError("joint pairs ledger position is duplicated")
        ledger[position] = entry
    raw_cues: dict[int, Mapping[str, Any]] = {}
    for cue_spec in cue_specs:
        if not isinstance(cue_spec, Mapping):
            raise SourceContextHuBERTFAError("joint pairs raw cue spec is invalid")
        try:
            position = int(cue_spec["position"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceContextHuBERTFAError("joint pairs raw cue position is invalid") from exc
        if position in raw_cues:
            raise SourceContextHuBERTFAError("joint pairs raw cue position is duplicated")
        raw_cues[position] = cue_spec

    anchors: dict[int, dict[str, Any]] = {}
    for index in line_order:
        candidate, qualification, _reason = _qualified_anchor_candidate(packets[index], promotions)
        if candidate is not None and qualification is not None:
            anchors[index] = _block_anchor_detail(index, packets[index], candidate, qualification)
    legacy_anchors = dict(anchors)
    exact_report = None
    proposal_policy = SOURCE_JOINT_CONTEXT_POLICY_ID
    if observed_words is not None:
        from lyric_aligner.alignment.source_exact_anchors import build_exact_source_anchors
        exact_report = build_exact_source_anchors(
            prepared["canonical_lines"], observed_words,
            source_observation_sha256=source_observation_sha256,
            source_audio_sha256=prepared["source_audio_sha256"],
            source_domain_ms=domain, qualified_anchors=list(legacy_anchors.values()))
        proposal_policy = SOURCE_JOINT_EXACT_CONTEXT_POLICY_ID
        for anchor in exact_report["anchors"]:
            # Existing qualified packet evidence takes precedence. New evidence
            # never masquerades as a packet or promotes another new anchor.
            anchors.setdefault(int(anchor["canonical_line_index"]), anchor)
    anchor_ordinals = [ordinal for ordinal, index in enumerate(line_order) if index in anchors]
    dictionary = _dictionary_keys(dictionary_path)
    records: list[dict[str, Any]] = []
    pair_results: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()

    for raw_pair in pairs:
        pair = _pair_positions(raw_pair)
        if pair is None:
            pair_results.append(_rejected(raw_pair, "pair_positions_invalid"))
            continue
        if pair in seen_pairs:
            pair_results.append(_rejected(pair, "duplicate_pair_request"))
            continue
        seen_pairs.add(pair)
        left_position, right_position = pair
        if right_position != left_position + 1:
            pair_results.append(_rejected(pair, "cue_positions_not_adjacent"))
            continue
        left_entry, right_entry = ledger.get(left_position), ledger.get(right_position)
        if left_entry is None or right_entry is None:
            pair_results.append(_rejected(pair, "cue_position_missing_from_prepared_ledger"))
            continue
        left_cue, right_cue = raw_cues.get(left_position), raw_cues.get(right_position)
        if left_cue is None or right_cue is None:
            pair_results.append(_rejected(pair, "cue_position_missing_from_raw_specs"))
            continue
        try:
            left_index, right_index = int(left_entry["target_index"]), int(right_entry["target_index"])
        except (KeyError, TypeError, ValueError):
            pair_results.append(_rejected(pair, "cue_not_proven_complete_single_line"))
            continue
        left_ranges, right_ranges = left_cue.get("canonical_character_ranges"), right_cue.get("canonical_character_ranges")
        if (not isinstance(left_ranges, list) or not isinstance(right_ranges, list)
                or _full_single_line_range(left_ranges, lines) != left_index
                or _full_single_line_range(right_ranges, lines) != right_index):
            pair_results.append(_rejected(pair, "cue_not_proven_complete_single_line"))
            continue
        if left_index not in packets or right_index not in packets:
            pair_results.append(_rejected(pair, "cue_not_proven_complete_single_line"))
            continue
        if (not isinstance(left_entry.get("source_packet"), Mapping)
                or not isinstance(right_entry.get("source_packet"), Mapping)
                or left_entry["source_packet"].get("cache_key_sha256") != packets[left_index].get("cache_key_sha256")
                or right_entry["source_packet"].get("cache_key_sha256") != packets[right_index].get("cache_key_sha256")):
            pair_results.append(_rejected(pair, "cue_not_proven_complete_single_line"))
            continue
        left_ordinal, right_ordinal = line_order.index(left_index), line_order.index(right_index)
        if right_ordinal != left_ordinal + 1:
            pair_results.append(_rejected(pair, "cue_canonical_lines_not_adjacent"))
            continue
        left_anchor_candidates = [ordinal for ordinal in anchor_ordinals if ordinal < left_ordinal]
        right_anchor_candidates = [ordinal for ordinal in anchor_ordinals if ordinal > right_ordinal]
        if not left_anchor_candidates or not right_anchor_candidates:
            pair_results.append(_rejected(pair, "nearest_qualified_outer_anchor_missing"))
            continue
        # These are the only two permissible anchors for this request.  Later
        # checks must reject this pair rather than widening to a farther pair.
        left_anchor_ordinal, right_anchor_ordinal = left_anchor_candidates[-1], right_anchor_candidates[0]
        outer_indices = (line_order[left_anchor_ordinal], line_order[right_anchor_ordinal])
        outer = [anchors[outer_indices[0]], anchors[outer_indices[1]]]
        left_interval, right_interval = outer[0]["source_interval_ms"], outer[1]["source_interval_ms"]
        if not (left_interval[0] < left_interval[1] <= right_interval[0] < right_interval[1]):
            pair_results.append(_rejected(pair, "outer_anchor_order_or_overlap_invalid"))
            continue
        segment_indices = line_order[left_anchor_ordinal:right_anchor_ordinal + 1]
        interior = segment_indices[1:-1]
        if len(interior) > SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES:
            pair_results.append(_rejected(pair, "interior_line_cap_exceeded"))
            continue
        ordered_qualified = [anchors[index] for index in segment_indices if index in anchors]
        previous_end = None
        contradictory = False
        for item in ordered_qualified:
            interval = item["source_interval_ms"]
            if previous_end is not None and previous_end > interval[0]:
                contradictory = True
                break
            previous_end = interval[1]
        if contradictory:
            pair_results.append(_rejected(pair, "qualified_interior_anchor_order_or_overlap_invalid"))
            continue
        low, high = left_interval[0], right_interval[1]
        window = [max(domain[0], low - SOURCE_CONTEXT_WINDOW_PAD_MS),
                  min(domain[1], high + SOURCE_CONTEXT_WINDOW_PAD_MS)]
        if (not window[0] <= low < high <= window[1]
                or window[1] - window[0] > SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS):
            pair_results.append(_rejected(pair, "source_window_cap_exceeded"))
            continue
        texts = [lines[index] for index in segment_indices]
        if not _english_text_complete(texts):
            pair_results.append(_rejected(pair, "provider_text_not_verified_en"))
            continue
        try:
            units = [alignment_units("en", text) for text in texts]
        except Exception as exc:
            pair_results.append(_rejected(pair, "canonical_lexical_preparation_failed:" + type(exc).__name__))
            continue
        unknown = sorted({unit for segment in units for unit in segment if unit not in dictionary})
        if unknown:
            pair_results.append(_rejected(pair, "dictionary_coverage_missing:" + ",".join(unknown[:12])))
            continue
        total_units = sum(len(segment) for segment in units)
        if total_units > SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS:
            pair_results.append(_rejected(pair, "lexical_unit_cap_exceeded"))
            continue
        outputs = [
            {"position": left_position, "canonical_line_index": left_index,
             "target_segment_index": segment_indices.index(left_index)},
            {"position": right_position, "canonical_line_index": right_index,
             "target_segment_index": segment_indices.index(right_index)},
        ]
        context = {
            "context_policy": SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
            "joint_proposal_policy_id": proposal_policy,
            "pair_positions": list(pair),
            "pair_canonical_indices": [left_index, right_index],
            "outer_anchor_candidates": outer,
            "qualified_source_anchors": [anchor for anchor in ordered_qualified
                if int(anchor["canonical_line_index"]) in legacy_anchors],
            "canonical_segment_indices": segment_indices,
            "canonical_interior_indices": interior,
            "source_window_ms": window,
            "source_observation_sha256": source_observation_sha256,
            "total_lexical_units": total_units,
            "time_banded_decoder_id": HUBERTFA_TIME_BAND_DECODER_ID,
            "time_banded_postcheck": HUBERTFA_TIME_BAND_POSTCHECK_ID,
            "time_banded_source_pad_ms": SOURCE_CONTEXT_WINDOW_PAD_MS,
        }
        if exact_report is not None:
            context["exact_source_anchor_policy_id"] = exact_report["policy_id"]
            context["exact_source_anchors"] = [anchor for anchor in ordered_qualified
                if int(anchor["canonical_line_index"]) not in legacy_anchors]
            context["exact_source_anchor_evidence_sha256"] = json_sha(exact_report)
            context["exact_source_anchor_input_identity"] = exact_report["input_identity"]
        record_payload = {
            "occurrence_id": prepared.get("occurrence_id"),
            "joint_proposal_policy_id": proposal_policy,
            "context_policy": SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
            "language": "en",
            "source_audio_path": prepared.get("source_audio_path"),
            "source_audio_sha256": prepared.get("source_audio_sha256"),
            "source_search_domain_end_ms": domain[1],
            "source_window_ms": window,
            "segment_text": texts,
            "segment_lexical_units": units,
            "lexical_units_sha256": json_sha(units),
            "target_outputs": outputs,
            "context": context,
        }
        proposal_id = json_sha(record_payload)
        record = {"record_id": proposal_id, "proposal_id": proposal_id, **record_payload}
        records.append(record)
        pair_results.append({"pair": list(pair), "status": "ready", "proposal_id": proposal_id,
                             "record_id": proposal_id})

    result = {
        "schema_version": SOURCE_JOINT_CONTEXT_SCHEMA_VERSION,
        "joint_proposal_policy_id": proposal_policy,
        "authority": "experimental_only_not_production_authority",
        "automatic_production_mutation_allowed": False,
        "context_policy": SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
        "occurrence_id": prepared.get("occurrence_id"),
        "source_observation_sha256": source_observation_sha256,
        "records": records,
        "pair_results": pair_results,
    }
    if exact_report is not None:
        result["exact_source_anchor_evidence"] = exact_report
    return result


__all__ = [
    "SOURCE_JOINT_CONTEXT_POLICY_ID",
    "SOURCE_JOINT_CONTEXT_SCHEMA_VERSION",
    "prepare_joint_pairs",
]
