"""Experimental global occurrence consensus over complete local packet lattices.

This resolves occurrence identity, not acoustic boundary accuracy.  It never
changes an existing local selection or relaxes local context qualification.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from lyric_aligner.alignment.source_packets import BOUNDED_TARGET_POLICY, SOURCE_PACKET_BOUNDED_POLICY_ID

SOURCE_SEQUENCE_POLICY_ID = "source-sequence-2026-09-08-v1-duplicate-only-all-optimal"
MAX_VERTICES = 2048
MAX_PAIR_CHECKS = 2_000_000


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _before(left: tuple, right: tuple) -> bool:
    # Canonical coordinates, observed character coordinates, AND real time
    # must agree.  Cue playback position is deliberately absent.
    return left[1] <= right[0] and left[3] <= right[2] and left[5] <= right[4]


def mandatory_chain_vertices(vertices: Sequence[tuple]) -> tuple[set[int], int]:
    """Return vertices present in every maximum chain, with bounded O(n²) DP."""
    n = len(vertices)
    if n > MAX_VERTICES or n * (n - 1) // 2 > MAX_PAIR_CHECKS:
        raise ValueError("source_sequence_resource_limit")
    order = sorted(range(n), key=lambda i: vertices[i])
    lengths: dict[int, int] = {}
    dominators: dict[int, int] = {}
    for offset, current in enumerate(order):
        best, common = 0, 0
        for previous in order[:offset]:
            if not _before(vertices[previous], vertices[current]):
                continue
            length = lengths[previous]
            if length > best:
                best, common = length, dominators[previous]
            elif length == best:
                common &= dominators[previous]
        lengths[current] = best + 1
        dominators[current] = common | (1 << current)
    longest = max(lengths.values(), default=0)
    ends = [dominators[i] for i in order if lengths[i] == longest]
    common = ends[0] if ends else 0
    for bits in ends[1:]:
        common &= bits
    return {i for i in range(n) if common & (1 << i)}, longest


def _vertex(packet: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple | None:
    if not candidate.get("context_alignment", {}).get("independent_context_support"):
        return None
    if any(candidate.get("source_interval_" + side + "_reason") !=
           "observed_positive_duration_word" for side in ("start", "end")):
        return None
    interval = candidate.get("source_interval_ms", [])
    if len(interval) != 2 or any(isinstance(t, bool) or not isinstance(t, (int, float)) or
                                  not math.isfinite(t) for t in interval):
        return None
    if not 0 <= interval[0] < interval[1]:
        return None
    ranges = packet["target_cue"]["canonical_character_ranges"]
    if not ranges or not packet.get("coverage", {}).get("target_domain_is_contiguous"):
        return None
    domain = sorted((r["canonical_line_index"], r["start_char"], r["end_char"]) for r in ranges)
    if any(a < 0 or b <= a for _, a, b in domain):
        return None
    observed = [m["observed_stream_character_index"]
                for char in candidate.get("target_cue_character_attribution", [])
                for m in char.get("observed_matches", [])]
    if not observed:
        return None
    return ((domain[0][0], domain[0][1]), (domain[-1][0], domain[-1][2]),
            min(observed), max(observed) + 1, *interval)


def resolve_source_sequence(packets: Sequence[Mapping[str, Any]], *, source_observation_sha256: str) -> dict[str, Any]:
    """Return promotions by stable packet cache key; inputs are untouched.

    Callers must enumerate the complete candidate list, not a top-k ledger.
    The caller binds the actual observation artifact digest; packets also
    carry that digest in their envelope so cross-observation batches fail.
    """
    if len(source_observation_sha256) != 64 or any(c not in '0123456789abcdef' for c in source_observation_sha256):
        raise ValueError('source observation SHA256 is required')
    identity = {"policy_id": SOURCE_SEQUENCE_POLICY_ID, 'source_observation_sha256': source_observation_sha256,
                "limits": [MAX_VERTICES, MAX_PAIR_CHECKS],
                "packet_digests": sorted(_digest(packet) for packet in packets)}
    result = {"schema_version": 'source-sequence-1.0', "policy_id": SOURCE_SEQUENCE_POLICY_ID,
              'source_observation_sha256': source_observation_sha256, "authority": "experimental_only",
              "automatic_production_mutation_allowed": False,
              "cache_key_sha256": _digest(identity), "promotions": [],
              "status": "complete", "vertex_count": 0, "maximum_chain_length": 0}
    if any(p.get('source_observation_sha256') != source_observation_sha256 for p in packets):
        return {**result, 'status': 'mixed_source_observation'}
    if any(p.get('policy_id') != SOURCE_PACKET_BOUNDED_POLICY_ID or
           p.get('cache_identity', {}).get('target_alignment_policy') != BOUNDED_TARGET_POLICY
           for p in packets):
        return {**result, "status": "unsupported_packet_policy"}
    identities = {_digest({key: p.get("cache_identity", {}).get(key) for key in
                   ("source_audio_sha256", "lyric_version", "source_search_domain", "model_id",
                    "normalization_id", "target_alignment_policy")}) for p in packets}
    if len(identities) > 1:
        return {**result, "status": "mixed_source_identity"}
    if any(p.get("candidates_truncated") is not False or
           p.get("selection_reason") == "source_packet_resource_limit" or
           p.get("coverage", {}).get("candidate_count_before_truncation") != len(p.get("candidates", []))
           for p in packets):
        return {**result, "status": "incomplete_lattice"}
    domains = sorted(set(tuple(sorted((r['canonical_line_index'], r['start_char'], r['end_char'])
                    for r in p['target_cue']['canonical_character_ranges'])) for p in packets))
    if any(not d for d in domains) or any((a[-1][0], a[-1][2]) > (b[0][0], b[0][1])
                                        for a, b in zip(domains, domains[1:])):
        return {**result, "status": "overlapping_canonical_domains"}
    vertices: list[tuple] = []
    indices: dict[tuple, int] = {}
    entries: list[tuple[int, Mapping[str, Any], int]] = []
    anchors: list[tuple] = []
    for index, packet in enumerate(packets):
        if packet.get('selected_candidate_id') is not None and not any(
                c.get('candidate_id') == packet['selected_candidate_id'] for c in packet.get('candidates', [])):
            return {**result, "status": "invalid_existing_anchor"}
        for candidate in packet.get("candidates", []):
            vertex = _vertex(packet, candidate)
            if candidate.get("candidate_id") == packet.get("selected_candidate_id"):
                if vertex is None:
                    return {**result, "status": "invalid_existing_anchor"}
                anchors.append(vertex)
            if vertex is None:
                continue
            if vertex not in indices:
                indices[vertex] = len(vertices)
                vertices.append(vertex)
                if len(vertices) > MAX_VERTICES:
                    return {**result, "status": "source_sequence_resource_limit"}
            entries.append((index, candidate, indices[vertex]))
    if any(a != b and not _before(a, b) and not _before(b, a)
           for i, a in enumerate(anchors) for b in anchors[:i]):
        return {**result, "status": "preexisting_selected_conflict"}
    result["vertex_count"] = len(vertices)
    try:
        mandatory, longest = mandatory_chain_vertices(vertices)
    except ValueError:
        return {**result, "status": "source_sequence_resource_limit"}
    result["maximum_chain_length"] = longest
    for index, candidate, vertex_index in entries:
        packet = packets[index]
        if packet.get("selected_candidate_id") is not None or packet.get("selection_reason") != "ambiguous_canonical_packet_identity":
            continue
        vertex = vertices[vertex_index]
        if vertex_index in mandatory and all(vertex == anchor or _before(vertex, anchor) or
                                             _before(anchor, vertex) for anchor in anchors):
            result["promotions"].append({"packet_cache_key_sha256": packet['cache_key_sha256'],
                "canonical_domain": packet['target_cue']['canonical_character_ranges'],
                "candidate_id": candidate["candidate_id"],
                "reason": "canonical_duplicate_resolved_by_all_optimal_source_order"})
    # Equivalent duplicate candidate records are not separate evidence.
    grouped: dict[str, dict[str, dict]] = {}
    for promotion in result["promotions"]:
        grouped.setdefault(promotion['packet_cache_key_sha256'], {})[promotion['candidate_id']] = promotion
    # Topologically equivalent records can carry different rank identities;
    # choosing a stable representative does not change their time or evidence.
    result["promotions"] = [items[min(items)] for _, items in sorted(grouped.items())]
    return result
