"""Bounded source-time masking for the local HuBERTFA forced decoder.

The module deliberately contains no vendor or private-runtime imports at
module import time.  The adapter supplies the already-loaded vendor decoder
class to :func:`make_time_banded_decoder` only for the explicit experimental
``anchored-path-v1`` policy.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


HUBERTFA_TIME_BAND_DECODER_ID = "hubertfa-time-banded-destination-mask-v1"
HUBERTFA_TIME_BAND_POSTCHECK_ID = "frame_length_x_1.5_plus_0.0001-v1"
# This policy is deliberately separate from the legacy packet-qualified anchor
# contract.  It is the only policy under which ``exact_source_anchors`` may
# contribute a destination band.
EXACT_SOURCE_ANCHOR_POLICY_ID = "source-exact-word-run-legacy-bracket-v1"
HUBERTFA_EXACT_SOURCE_ANCHOR_POLICY_ID = EXACT_SOURCE_ANCHOR_POLICY_ID


class HuBERTFATimeBandError(ValueError):
    """A signed anchored-path record cannot be safely constrained."""


def _interval(value: object, *, label: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise HuBERTFATimeBandError(label + "_invalid")
    try:
        start, end = (int(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise HuBERTFATimeBandError(label + "_invalid") from exc
    if start >= end:
        raise HuBERTFATimeBandError(label + "_invalid")
    return start, end


def _sha256_identity(value: object, *, label: str) -> str:
    """Validate an externally bound SHA without manufacturing legacy IDs."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise HuBERTFATimeBandError(label)
    return value


def _json_sha(value: object, *, label: str) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HuBERTFATimeBandError(label) from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _exact_anchor_proof_side(value: object, *, label: str) -> tuple[int, float, float]:
    """Validate one required worker legacy-bracket proof side."""
    if not isinstance(value, Mapping):
        raise HuBERTFATimeBandError(label)
    try:
        side_index = int(value["canonical_line_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HuBERTFATimeBandError(label) from exc
    side_interval = value.get("source_interval_ms")
    if not isinstance(side_interval, (list, tuple)) or len(side_interval) != 2:
        raise HuBERTFATimeBandError(label)
    try:
        side_start, side_end = (float(item) for item in side_interval)
    except (TypeError, ValueError) as exc:
        raise HuBERTFATimeBandError(label) from exc
    if (not math.isfinite(side_start) or not math.isfinite(side_end)
            or side_start >= side_end):
        raise HuBERTFATimeBandError(label)
    proof_ids = value.get("proof_ids")
    if (not isinstance(proof_ids, list) or not proof_ids
            or any(not isinstance(item, str) or not item for item in proof_ids)):
        raise HuBERTFATimeBandError(label)
    return side_index, side_start, side_end


def _validate_exact_anchor_input_identity(
    value: object,
    *,
    expected_source_observation_sha256: object,
    expected_source_audio_sha256: object,
) -> tuple[Mapping[str, Any], dict[int, tuple[float, float, tuple[str, ...]]]]:
    """Validate the worker report identity used to bind each anchor SHA."""
    if not isinstance(value, Mapping):
        raise HuBERTFATimeBandError("exact_source_anchor_input_identity_invalid")
    required = {
        "canonical_lines_sha256", "observed_words_sha256", "source_observation_sha256",
        "source_audio_sha256", "source_domain_ms", "policy_constants",
        "qualified_anchors", "identity_sha256",
    }
    if not required.issubset(value):
        raise HuBERTFATimeBandError("exact_source_anchor_input_identity_invalid")
    identity = _sha256_identity(value.get("identity_sha256"),
                                label="exact_source_anchor_input_identity_invalid")
    payload = dict(value)
    payload.pop("identity_sha256", None)
    if _json_sha(payload, label="exact_source_anchor_input_identity_invalid") != identity:
        raise HuBERTFATimeBandError("exact_source_anchor_input_identity_mismatch")
    for key in ("canonical_lines_sha256", "observed_words_sha256",
                "source_observation_sha256", "source_audio_sha256"):
        _sha256_identity(value.get(key), label="exact_source_anchor_input_identity_invalid")
    if (value.get("source_observation_sha256") != expected_source_observation_sha256
            or value.get("source_audio_sha256") != expected_source_audio_sha256):
        raise HuBERTFATimeBandError("exact_source_anchor_input_identity_mismatch")
    if not isinstance(value.get("policy_constants"), Mapping):
        raise HuBERTFATimeBandError("exact_source_anchor_input_identity_invalid")
    qualified_anchors = value.get("qualified_anchors")
    if not isinstance(qualified_anchors, list):
        raise HuBERTFATimeBandError("exact_source_anchor_input_identity_invalid")
    proof_index: dict[int, tuple[float, float, tuple[str, ...]]] = {}
    for item in qualified_anchors:
        side_index, side_start, side_end = _exact_anchor_proof_side(
            item, label="exact_source_anchor_input_identity_invalid")
        proof_ids = tuple(str(proof_id) for proof_id in item["proof_ids"])
        if side_index in proof_index:
            raise HuBERTFATimeBandError("exact_source_anchor_input_identity_invalid")
        proof_index[side_index] = (side_start, side_end, proof_ids)
    return value, proof_index


def _validate_exact_source_anchor(
    anchor: Mapping[str, Any],
    *,
    expected_index: int,
    expected_words: Sequence[str],
    source_window: tuple[int, int],
    source_domain: tuple[float, float],
    input_identity_sha256: str,
    legacy_proofs: Mapping[int, tuple[float, float, tuple[str, ...]]],
) -> tuple[int, float, float]:
    """Validate one worker-produced exact word-run anchor.

    Exact anchors intentionally carry observation evidence instead of the old
    packet/candidate IDs.  The evidence is checked here, but it is never
    converted into a legacy identity.  ``legacy_bracket`` is retained as a
    proof report and may lie outside the cropped decoder window.
    """
    try:
        index = int(anchor["canonical_line_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HuBERTFATimeBandError("exact_source_anchor_invalid") from exc
    if index != expected_index:
        raise HuBERTFATimeBandError("exact_source_anchor_line_index_invalid")
    exact_interval = anchor.get("source_interval_ms")
    if not isinstance(exact_interval, (list, tuple)) or len(exact_interval) != 2:
        raise HuBERTFATimeBandError("exact_source_anchor_interval_invalid")
    try:
        start, end = (float(item) for item in exact_interval)
    except (TypeError, ValueError) as exc:
        raise HuBERTFATimeBandError("exact_source_anchor_interval_invalid") from exc
    if (not math.isfinite(start) or not math.isfinite(end) or start >= end):
        raise HuBERTFATimeBandError("exact_source_anchor_interval_invalid")
    if not source_window[0] <= start < end <= source_window[1]:
        raise HuBERTFATimeBandError("exact_source_anchor_outside_window")

    lexical_units = anchor.get("lexical_units")
    if (not isinstance(lexical_units, list)
            or any(not isinstance(unit, str) for unit in lexical_units)
            or lexical_units != list(expected_words)):
        raise HuBERTFATimeBandError("exact_source_anchor_lexical_units_mismatch")

    observed_indices = anchor.get("observed_word_indices")
    if (not isinstance(observed_indices, list) or not observed_indices
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0
                   for item in observed_indices)
            or any(left >= right for left, right in zip(observed_indices, observed_indices[1:]))):
        raise HuBERTFATimeBandError("exact_source_anchor_observed_indices_invalid")
    probabilities = anchor.get("probabilities")
    if (not isinstance(probabilities, list) or len(probabilities) != len(observed_indices)
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(float(item)) or not 0.0 <= float(item) <= 1.0
                   for item in probabilities)):
        raise HuBERTFATimeBandError("exact_source_anchor_probabilities_invalid")

    # The worker binds this identity over the complete input identity and the
    # anchor payload.  Do not invent a packet/candidate identity for this
    # experimental evidence channel.
    identity = anchor.get("anchor_identity_sha256")
    if identity is None:
        raise HuBERTFATimeBandError("exact_source_anchor_identity_invalid")
    identity = _sha256_identity(identity, label="exact_source_anchor_identity_invalid")
    anchor_payload = dict(anchor)
    anchor_payload.pop("anchor_identity_sha256", None)
    expected_identity = _json_sha({
        "input_identity_sha256": input_identity_sha256,
        "anchor": anchor_payload,
    }, label="exact_source_anchor_identity_invalid")
    if identity != expected_identity:
        raise HuBERTFATimeBandError("exact_source_anchor_identity_mismatch")

    bracket = anchor.get("legacy_bracket")
    if not isinstance(bracket, Mapping):
        raise HuBERTFATimeBandError("exact_source_anchor_legacy_bracket_invalid")
    left = bracket.get("left")
    right = bracket.get("right")
    left_index, left_start, left_end = _exact_anchor_proof_side(
        left, label="exact_source_anchor_legacy_bracket_invalid")
    right_index, right_start, right_end = _exact_anchor_proof_side(
        right, label="exact_source_anchor_legacy_bracket_invalid")
    left_proofs = tuple(str(proof_id) for proof_id in left["proof_ids"])
    right_proofs = tuple(str(proof_id) for proof_id in right["proof_ids"])
    for side_index, side_start, side_end, proof_ids in (
        (left_index, left_start, left_end, left_proofs),
        (right_index, right_start, right_end, right_proofs),
    ):
        expected = legacy_proofs.get(side_index)
        if expected is None or expected != (side_start, side_end, proof_ids):
            raise HuBERTFATimeBandError("exact_source_anchor_legacy_bracket_invalid")
    if (left_index >= index or right_index <= index
            or not source_domain[0] <= left_start < left_end <= source_domain[1]
            or not source_domain[0] <= right_start < right_end <= source_domain[1]
            or left_end > start or end > right_start):
        raise HuBERTFATimeBandError("exact_source_anchor_legacy_bracket_invalid")
    return index, start, end


def record_word_bands(
    record: Mapping[str, Any],
    word_seq: Sequence[str],
    *,
    source_pad_ms: int,
) -> dict[int, tuple[float, float]]:
    """Map signed qualified source anchors to lexical word-state bands.

    The signed record is the complete contract: only anchors explicitly
    carried in its context are used.  A source line with no qualified anchor
    remains unbounded; that is intentional for the outer-only diagnostic.
    """
    if source_pad_ms < 0:
        raise HuBERTFATimeBandError("source_pad_invalid")
    units = record.get("segment_lexical_units")
    context = record.get("context")
    if not isinstance(units, list) or not isinstance(context, Mapping):
        raise HuBERTFATimeBandError("time_band_record_fields_invalid")
    expected_words = [str(unit) for segment in units for unit in segment]
    if list(word_seq) != expected_words:
        raise HuBERTFATimeBandError("band_word_order_mismatch")
    segment_indices = context.get("canonical_segment_indices")
    anchors = context.get("qualified_source_anchors")
    exact_key_present = "exact_source_anchors" in context
    if (not isinstance(segment_indices, list) or len(segment_indices) != len(units)
            or not isinstance(anchors, list)):
        raise HuBERTFATimeBandError("time_band_context_invalid")
    exact_anchors: list[Any] | None = None
    exact_input_identity_sha256: str | None = None
    exact_input_identity: Mapping[str, Any] | None = None
    exact_legacy_proofs: dict[int, tuple[float, float, tuple[str, ...]]] | None = None
    exact_source_domain: tuple[float, float] | None = None
    if exact_key_present:
        if context.get("exact_source_anchor_policy_id") != EXACT_SOURCE_ANCHOR_POLICY_ID:
            raise HuBERTFATimeBandError("exact_source_anchor_policy_invalid")
        exact_anchors = context.get("exact_source_anchors")
        if not isinstance(exact_anchors, list):
            raise HuBERTFATimeBandError("exact_source_anchor_list_invalid")
        if not anchors and not exact_anchors:
            raise HuBERTFATimeBandError("time_band_context_invalid")
        exact_input_identity, exact_legacy_proofs = _validate_exact_anchor_input_identity(
            context.get("exact_source_anchor_input_identity"),
            expected_source_observation_sha256=context.get("source_observation_sha256"),
            expected_source_audio_sha256=record.get("source_audio_sha256"))
        exact_input_identity_sha256 = str(exact_input_identity["identity_sha256"])
        identity_domain = exact_input_identity.get("source_domain_ms")
        if (not isinstance(identity_domain, (list, tuple)) or len(identity_domain) != 2):
            raise HuBERTFATimeBandError("exact_source_anchor_domain_invalid")
        try:
            identity_domain_start, identity_domain_end = (float(item) for item in identity_domain)
        except (TypeError, ValueError) as exc:
            raise HuBERTFATimeBandError("exact_source_anchor_domain_invalid") from exc
        if (not math.isfinite(identity_domain_start) or not math.isfinite(identity_domain_end)
                or identity_domain_start >= identity_domain_end):
            raise HuBERTFATimeBandError("exact_source_anchor_domain_invalid")
        raw_domain_end = record.get("source_search_domain_end_ms")
        try:
            domain_end = float(raw_domain_end)
        except (TypeError, ValueError) as exc:
            raise HuBERTFATimeBandError("exact_source_anchor_domain_invalid") from exc
        if not math.isfinite(domain_end) or domain_end <= 0:
            raise HuBERTFATimeBandError("exact_source_anchor_domain_invalid")
        if domain_end != identity_domain_end:
            raise HuBERTFATimeBandError("exact_source_anchor_domain_mismatch")
        exact_source_domain = (identity_domain_start, identity_domain_end)
        if context.get("exact_source_anchor_evidence_sha256") is not None:
            _sha256_identity(context.get("exact_source_anchor_evidence_sha256"),
                             label="exact_source_anchor_evidence_identity_invalid")
    elif not anchors:
        raise HuBERTFATimeBandError("time_band_context_invalid")
    try:
        canonical_indices = [int(item) for item in segment_indices]
    except (TypeError, ValueError) as exc:
        raise HuBERTFATimeBandError("time_band_context_invalid") from exc
    if len(set(canonical_indices)) != len(canonical_indices):
        raise HuBERTFATimeBandError("time_band_context_invalid")
    if any(left >= right for left, right in zip(canonical_indices, canonical_indices[1:])):
        raise HuBERTFATimeBandError("time_band_context_invalid")
    window_start, window_end = _interval(record.get("source_window_ms"), label="source_window")
    if (exact_source_domain is not None
            and not exact_source_domain[0] <= window_start < window_end <= exact_source_domain[1]):
        raise HuBERTFATimeBandError("exact_source_anchor_domain_mismatch")
    segment_offsets: dict[int, tuple[int, int]] = {}
    offset = 0
    for index, segment in zip(canonical_indices, units):
        if not isinstance(segment, list) or not segment:
            raise HuBERTFATimeBandError("time_band_context_invalid")
        segment_offsets[index] = (offset, offset + len(segment))
        offset += len(segment)
    if offset != len(expected_words):
        raise HuBERTFATimeBandError("band_word_order_mismatch")

    bands: dict[int, tuple[float, float]] = {}
    parsed_anchors: list[tuple[int, float, float, str]] = []
    previous_index = None
    previous_end = None
    seen: set[int] = set()
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            raise HuBERTFATimeBandError("qualified_source_anchor_invalid")
        try:
            index = int(anchor["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HuBERTFATimeBandError("qualified_source_anchor_invalid") from exc
        if index in seen or index not in segment_offsets:
            raise HuBERTFATimeBandError("qualified_source_anchor_invalid")
        if not isinstance(anchor.get("packet_cache_key_sha256"), str) or not isinstance(anchor.get("candidate_id"), str):
            raise HuBERTFATimeBandError("qualified_source_anchor_identity_invalid")
        if anchor.get("qualification") not in {
            "selected_full_context", "all_optimal_duplicate_promotion",
        }:
            raise HuBERTFATimeBandError("qualified_source_anchor_qualification_invalid")
        start, end = _interval(anchor.get("source_interval_ms"), label="qualified_source_anchor_interval")
        if previous_index is not None and (index <= previous_index or previous_end is None or previous_end > start):
            raise HuBERTFATimeBandError("qualified_source_anchor_order_or_overlap_invalid")
        if not window_start <= start < end <= window_end:
            raise HuBERTFATimeBandError("qualified_source_anchor_outside_window")
        lower = max(window_start, start - source_pad_ms)
        upper = min(window_end, end + source_pad_ms)
        if lower >= upper:
            raise HuBERTFATimeBandError("qualified_source_anchor_band_invalid")
        local_band = ((lower - window_start) / 1000.0, (upper - window_start) / 1000.0)
        word_start, word_end = segment_offsets[index]
        bands.update({word_index: local_band for word_index in range(word_start, word_end)})
        parsed_anchors.append((index, start, end, "qualified"))
        seen.add(index)
        previous_index, previous_end = index, end

    # Exact word-run anchors are an additive experimental evidence channel.
    # They are policy-gated and never receive packet_cache_key/candidate IDs.
    if exact_key_present:
        assert exact_anchors is not None
        assert exact_source_domain is not None
        assert exact_input_identity_sha256 is not None
        assert exact_legacy_proofs is not None
        exact_seen: set[int] = set()
        exact_previous_index = None
        exact_previous_end = None
        for anchor in exact_anchors:
            if not isinstance(anchor, Mapping):
                raise HuBERTFATimeBandError("exact_source_anchor_invalid")
            try:
                index = int(anchor["canonical_line_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HuBERTFATimeBandError("exact_source_anchor_invalid") from exc
            if index not in segment_offsets:
                raise HuBERTFATimeBandError("exact_source_anchor_line_index_invalid")
            if index in exact_seen:
                raise HuBERTFATimeBandError("exact_source_anchor_duplicate_index")
            if exact_previous_index is not None and index <= exact_previous_index:
                raise HuBERTFATimeBandError("exact_source_anchor_order_or_overlap_invalid")
            index, start, end = _validate_exact_source_anchor(
                anchor, expected_index=index,
                expected_words=expected_words[segment_offsets[index][0]:segment_offsets[index][1]],
                source_window=(window_start, window_end),
                source_domain=exact_source_domain,
                input_identity_sha256=exact_input_identity_sha256,
                legacy_proofs=exact_legacy_proofs,
            )
            if exact_previous_end is not None and exact_previous_end > start:
                raise HuBERTFATimeBandError("exact_source_anchor_order_or_overlap_invalid")
            word_start, word_end = segment_offsets[index]
            lower = max(window_start, start - source_pad_ms)
            upper = min(window_end, end + source_pad_ms)
            if lower >= upper:
                raise HuBERTFATimeBandError("exact_source_anchor_band_invalid")
            local_band = ((lower - window_start) / 1000.0, (upper - window_start) / 1000.0)
            bands.update({word_index: local_band for word_index in range(word_start, word_end)})
            parsed_anchors.append((index, start, end, "exact"))
            exact_seen.add(index)
            exact_previous_index, exact_previous_end = index, end

        # A new exact anchor may not duplicate or overlap a legacy qualified
        # anchor.  Check in canonical order so the old list's order contract
        # remains untouched while the combined destination bands stay sound.
        ordered = sorted(parsed_anchors, key=lambda item: item[0])
        for previous, current in zip(ordered, ordered[1:]):
            if previous[0] == current[0]:
                raise HuBERTFATimeBandError("source_anchor_duplicate_index")
            if previous[2] > current[1]:
                raise HuBERTFATimeBandError("source_anchor_order_or_overlap_invalid")
    return bands


def validate_word_bands(
    words: Sequence[Mapping[str, Any]],
    expected_words: Sequence[str],
    word_bands: Mapping[int, tuple[float, float]],
    *,
    frame_length: float,
) -> None:
    """Reject postprocessed lexical words outside their hard decoded bands."""
    if not math.isfinite(frame_length) or frame_length <= 0:
        raise HuBERTFATimeBandError("time_band_frame_length_invalid")
    lexical = [word for word in words if str(word.get("text") or "") != "SP"]
    if [str(word.get("text") or "") for word in lexical] != list(expected_words):
        raise HuBERTFATimeBandError("band_word_order_mismatch")
    tolerance = 1.5 * frame_length + 0.0001
    for index, (lower, upper) in word_bands.items():
        if index < 0 or index >= len(lexical):
            raise HuBERTFATimeBandError("band_word_index_invalid")
        try:
            start, end = float(lexical[index]["start"]), float(lexical[index]["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HuBERTFATimeBandError("band_word_time_invalid") from exc
        if (not math.isfinite(start) or not math.isfinite(end) or start > end
                or start < lower - tolerance or end > upper + tolerance):
            raise HuBERTFATimeBandError("word_time_band_postcheck_failed")


def make_time_banded_decoder(base_decoder_cls: type):
    """Return a local subclass with the prototype's exact masked recurrence."""
    # Keep NumPy optional for ordinary import/isolated request validation.
    import numpy as np

    class TimeBandedDecoder(base_decoder_cls):
        def set_word_bands(self, words, bands):
            self.expected_words = list(words)
            self.word_bands = dict(bands)

        def decode(self, *args, **kwargs):
            words = kwargs.get("word_seq")
            if words != self.expected_words:
                raise HuBERTFATimeBandError("band_word_order_mismatch")
            mapping = kwargs["ph_idx_to_word_idx"]
            phones = kwargs["ph_seq"]
            if len(mapping) != len(phones):
                raise HuBERTFATimeBandError("band_phone_mapping_mismatch")
            self.state_bands = np.array([
                self.word_bands.get(int(index), (-np.inf, np.inf))
                if self.vocab["vocab"][phone] != 0 else (-np.inf, np.inf)
                for phone, index in zip(phones, mapping)
            ], dtype=float)
            if np.any(self.state_bands[:, 0] >= self.state_bands[:, 1]):
                raise HuBERTFATimeBandError("invalid_word_time_band")
            return super().decode(*args, **kwargs)

        def forward_pass(self, T, S, prob_log, edge_prob, curr_ph_max_prob_log,
                         dp, ph_seq_id, prob3_pad_len=2):
            if self.state_bands.shape != (S, 2):
                raise HuBERTFATimeBandError("band_state_count_mismatch")
            times = np.arange(T) * self.frame_length
            allowed = ((times[None, :] >= self.state_bands[:, :1]) &
                       (times[None, :] <= self.state_bands[:, 1:]))
            dp[~allowed[:, 0], 0] = -np.inf
            curr_ph_max_prob_log[~allowed[:, 0]] = -np.inf
            backtrack_s = np.full_like(dp, -1, dtype=np.int32)
            edge_log = np.log(edge_prob + 1e-6)
            not_edge_log = np.log(1 - edge_prob + 1e-6)
            mask_reset = ph_seq_id == 0
            prob1 = np.empty(S, dtype=np.float32)
            prob2 = np.full(S, -np.inf, dtype=np.float32)
            prob3 = np.full(S, -np.inf, dtype=np.float32)
            indices = np.arange(prob3_pad_len, S)
            prior = np.clip(indices - prob3_pad_len + 1, 0, S - 1)
            jump_allowed = (prior >= S - 1) | (ph_seq_id[prior] == 0)
            for t in range(1, T):
                emission = prob_log[:, t]
                previous = dp[:, t - 1]
                prob1[:] = previous + emission + not_edge_log[t]
                prob2[1:] = (previous[:S - 1] + emission[:S - 1] + edge_log[t]
                             + curr_ph_max_prob_log[:S - 1] * (T / S))
                jump = (previous[:S - prob3_pad_len] + emission[:S - prob3_pad_len]
                        + edge_log[t] + curr_ph_max_prob_log[:S - prob3_pad_len] * (T / S))
                prob3[indices] = np.where(jump_allowed, jump, -np.inf)
                candidates = np.vstack((prob1, prob2, prob3))
                moves = np.argmax(candidates, axis=0)
                dp[:, t] = candidates[moves, np.arange(S)]
                backtrack_s[:, t] = moves
                dp[~allowed[:, t], t] = -np.inf
                stay = moves == 0
                np.maximum(curr_ph_max_prob_log, emission, out=curr_ph_max_prob_log, where=stay)
                np.copyto(curr_ph_max_prob_log, emission, where=~stay)
                curr_ph_max_prob_log[~np.isfinite(dp[:, t])] = -np.inf
                curr_ph_max_prob_log[mask_reset] = 0.0
                prob2[1:] = -np.inf
                prob3[indices] = -np.inf
            terminals = [S - 1]
            if S > 1 and ph_seq_id[-1] == 0:
                terminals.append(S - 2)
            if not any(np.isfinite(dp[state, -1]) for state in terminals):
                raise HuBERTFATimeBandError("no_feasible_source_time_banded_path")
            return dp, backtrack_s, curr_ph_max_prob_log

    return TimeBandedDecoder


__all__ = [
    "EXACT_SOURCE_ANCHOR_POLICY_ID",
    "HUBERTFA_TIME_BAND_DECODER_ID",
    "HUBERTFA_EXACT_SOURCE_ANCHOR_POLICY_ID",
    "HUBERTFA_TIME_BAND_POSTCHECK_ID",
    "HuBERTFATimeBandError",
    "make_time_banded_decoder",
    "record_word_bands",
    "validate_word_bands",
]
