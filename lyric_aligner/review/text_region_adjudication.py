"""Deterministic region-level adjudication for unresolved text reviews.

This layer resolves only a canonical gap that is uniquely bracketed by already
resolved neighbouring cues.  It keeps cue topology/timing fixed and repartitions
canonical text only on whitespace word boundaries.  It is deliberately narrower
than general semantic/model review.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.text.canonical_lyrics import parse_canonical_lyrics
from lyric_aligner.text_repair import SubtitleCue, _normalize_for_match, _pair_score

SCHEMA_VERSION = "text-region-adjudication-1.0"
POLICY_ID = "exact-bracket-canonical-gap-repartition-1.0"


@dataclass(frozen=True)
class Policy:
    min_region_similarity: float = 0.78
    min_mean_local_similarity: float = 0.70
    min_local_similarity: float = 0.55
    min_length_ratio: float = 0.75
    max_length_ratio: float = 1.35
    multi_line_min_region_similarity: float = 0.90
    multi_line_min_length_ratio: float = 0.88
    multi_line_max_length_ratio: float = 1.15
    multi_line_min_canonical_coverage: float = 0.90
    multi_line_min_line_coverage: float = 0.70
    short_line_exact_max_chars: int = 6
    transition_guard_ms: int = 10_000
    max_region_cues: int = 8
    max_gap_lines: int = 8


_BLOCKED_REASONS = {
    "segmentation_would_empty_existing_cue",
    "cue_boundary_splits_canonical_latin_word",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _span(row: Mapping[str, Any] | None) -> tuple[int, int] | None:
    if not isinstance(row, Mapping):
        return None
    raw = row.get("canonical_span")
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    try:
        start, end = int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    return start, end


def _canonical_rows(manifest: Mapping[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in manifest.get("canonical_lyrics", []):
        path = Path(str(item["path"]))
        if not path.is_absolute():
            path = repo_root / path
        for line in parse_canonical_lyrics(path):
            rows.append(
                {
                    "source_ordinal": int(item["ordinal"]),
                    "parser_index": int(line.index),
                    "text": line.text,
                }
            )
    return rows


def _regions(rows: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    ordered = sorted(rows, key=lambda row: int(row["cue_ordinal"]))
    output: list[list[Mapping[str, Any]]] = []
    for row in ordered:
        cue = int(row["cue_ordinal"])
        if not output or cue != int(output[-1][-1]["cue_ordinal"]) + 1:
            output.append([row])
        else:
            output[-1].append(row)
    return output


def _anchor_row(
    cue_ordinal: int,
    *,
    smart_by_cue: Mapping[int, Mapping[str, Any]],
    safe_by_cue: Mapping[int, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    safe = safe_by_cue.get(cue_ordinal)
    if safe and safe.get("resolution") in {
        "apply_canonical_text",
        "keep_editor_presentation_equivalent",
        "ownership_repartition",
    }:
        return safe
    smart = smart_by_cue.get(cue_ordinal)
    if smart and smart.get("action") != "review" and _span(smart) is not None:
        return smart
    return None


def _transition_risk(
    cues: Sequence[SubtitleCue],
    cue_ordinals: Sequence[int],
    transition_boundaries_ms: Sequence[int],
    policy: Policy,
) -> bool:
    first = cues[min(cue_ordinals)]
    last = cues[max(cue_ordinals)]
    # Timing parsing is intentionally reused from the immutable SRT strings.
    def bounds(cue: SubtitleCue) -> tuple[int, int]:
        left, right = [part.strip() for part in cue.timing.split("-->", 1)]
        def ms(value: str) -> int:
            hh, mm, rest = value.replace(".", ",").split(":")
            ss, milli = rest.split(",")
            return (int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(milli)
        return ms(left), ms(right)
    start, _ = bounds(first)
    _, end = bounds(last)
    return any(
        start - policy.transition_guard_ms <= boundary <= end + policy.transition_guard_ms
        for boundary in transition_boundaries_ms
    )


def _word_tokens(text: str) -> list[str]:
    return [token for token in text.split() if token]


def _sequence_coverage(reference: str, observed: str) -> float:
    if not reference:
        return 0.0
    matcher = difflib.SequenceMatcher(None, reference, observed, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(reference)


def _multi_line_coverage_ok(
    *,
    gap_rows: Sequence[Mapping[str, Any]],
    canonical_norm: str,
    editor_norm: str,
    region_similarity: float,
    length_ratio: float,
    policy: Policy,
) -> tuple[bool, float, list[float], str | None]:
    if len(gap_rows) <= 1:
        return True, _sequence_coverage(canonical_norm, editor_norm), [], None
    if region_similarity < policy.multi_line_min_region_similarity:
        return False, 0.0, [], "multi_line_region_similarity_below_policy"
    if not (
        policy.multi_line_min_length_ratio
        <= length_ratio
        <= policy.multi_line_max_length_ratio
    ):
        return False, 0.0, [], "multi_line_length_ratio_outside_policy"
    canonical_coverage = _sequence_coverage(canonical_norm, editor_norm)
    if canonical_coverage < policy.multi_line_min_canonical_coverage:
        return False, canonical_coverage, [], "multi_line_canonical_coverage_below_policy"
    line_coverages: list[float] = []
    for row in gap_rows:
        line_norm = _normalize_for_match(str(row.get("text") or ""))
        if not line_norm:
            return False, canonical_coverage, line_coverages, "multi_line_empty_canonical_line"
        coverage = _sequence_coverage(line_norm, editor_norm)
        line_coverages.append(coverage)
        if len(line_norm) <= policy.short_line_exact_max_chars:
            if line_norm not in editor_norm:
                return False, canonical_coverage, line_coverages, "multi_line_short_line_not_observed"
        elif coverage < policy.multi_line_min_line_coverage:
            return False, canonical_coverage, line_coverages, "multi_line_line_coverage_below_policy"
    return True, canonical_coverage, line_coverages, None


def _partition_words(
    cue_texts: Sequence[str],
    canonical_text: str,
    policy: Policy,
) -> tuple[list[str], list[float]] | None:
    tokens = _word_tokens(canonical_text)
    cue_count = len(cue_texts)
    if cue_count == 0 or len(tokens) < cue_count:
        return None

    @lru_cache(maxsize=None)
    def solve(cue_index: int, token_index: int):
        if cue_index == cue_count:
            return (0.0, (), ()) if token_index == len(tokens) else None
        remaining_cues = cue_count - cue_index - 1
        best = None
        max_end = len(tokens) - remaining_cues
        source_norm = _normalize_for_match(cue_texts[cue_index])
        if not source_norm:
            return None
        for end in range(token_index + 1, max_end + 1):
            candidate = " ".join(tokens[token_index:end])
            target_norm = _normalize_for_match(candidate)
            if not target_norm:
                continue
            score = _pair_score(source_norm, target_norm)
            ratio = len(target_norm) / max(1, len(source_norm))
            # Local constraints stop a globally plausible region from assigning a
            # completely unrelated phrase to one cue just to make the stream fit.
            if score < policy.min_local_similarity or ratio < 0.45 or ratio > 2.2:
                continue
            local_cost = (1.0 - score) + 0.15 * abs(math.log(ratio))
            tail = solve(cue_index + 1, end)
            if tail is None:
                continue
            candidate_result = (
                local_cost + tail[0],
                (candidate, *tail[1]),
                (score, *tail[2]),
            )
            if best is None or candidate_result[0] < best[0]:
                best = candidate_result
        return best

    result = solve(0, 0)
    if result is None:
        return None
    assignments = list(result[1])
    scores = [float(value) for value in result[2]]
    if min(scores) < policy.min_local_similarity:
        return None
    if sum(scores) / len(scores) < policy.min_mean_local_similarity:
        return None
    if _normalize_for_match(" ".join(assignments)) != _normalize_for_match(canonical_text):
        return None
    return assignments, scores


def adjudicate_regions(
    *,
    repo_root: Path,
    manifest: Mapping[str, Any],
    smart: Mapping[str, Any],
    safe_report: Mapping[str, Any],
    cues: Sequence[SubtitleCue],
    transition_boundaries_ms: Sequence[int],
    policy: Policy | None = None,
) -> dict[str, Any]:
    policy = policy or Policy()
    canonical = _canonical_rows(manifest, repo_root)
    smart_rows = [row for row in smart.get("text_decisions", []) if isinstance(row, Mapping)]
    smart_by_cue = {int(row["cue_ordinal"]): row for row in smart_rows}
    safe_rows = [row for row in safe_report.get("decisions", []) if isinstance(row, Mapping)]
    safe_by_cue = {int(row["cue_ordinal"]): row for row in safe_rows}
    unresolved = [
        smart_by_cue[int(row["cue_ordinal"])]
        for row in safe_rows
        if row.get("resolution") == "unresolved_manual"
        and int(row["cue_ordinal"]) in smart_by_cue
    ]
    if len(unresolved) != int(safe_report.get("remaining_manual", -1)):
        raise ValueError("safe report unresolved population does not replay")

    replacements: dict[int, str] = {}
    decisions: list[dict[str, Any]] = []
    for region in _regions(unresolved):
        cue_ordinals = [int(row["cue_ordinal"]) for row in region]
        decision: dict[str, Any] = {
            "cue_ordinals": cue_ordinals,
            "resolution": "unresolved_manual",
            "reason": None,
            "canonical_gap": None,
            "region_similarity": None,
            "length_ratio": None,
            "canonical_coverage": None,
            "canonical_line_coverages": [],
            "local_scores": [],
            "timing_mutation_performed": False,
        }
        if len(region) > policy.max_region_cues:
            decision["reason"] = "region_too_large"
            decisions.append(decision); continue
        if any(str(row.get("reason") or "") in _BLOCKED_REASONS for row in region):
            decision["reason"] = "blocked_review_reason"
            decisions.append(decision); continue
        left = _anchor_row(cue_ordinals[0] - 1, smart_by_cue=smart_by_cue, safe_by_cue=safe_by_cue)
        right = _anchor_row(cue_ordinals[-1] + 1, smart_by_cue=smart_by_cue, safe_by_cue=safe_by_cue)
        left_span, right_span = _span(left), _span(right)
        if left_span is None or right_span is None:
            decision["reason"] = "resolved_bracket_missing"
            decisions.append(decision); continue
        gap_start, gap_end = left_span[1], right_span[0]
        if gap_start < 0 or gap_end <= gap_start or gap_end > len(canonical):
            decision["reason"] = "canonical_gap_empty_or_invalid"
            decisions.append(decision); continue
        if gap_end - gap_start > policy.max_gap_lines:
            decision["reason"] = "canonical_gap_too_large"
            decisions.append(decision); continue
        gap_rows = canonical[gap_start:gap_end]
        source_ids = {int(row["source_ordinal"]) for row in gap_rows}
        if len(source_ids) != 1:
            decision["reason"] = "canonical_gap_crosses_source"
            decisions.append(decision); continue
        source_ordinal = next(iter(source_ids))
        # The bordering lines, when present, must be from the same source as the gap.
        border_indices = [gap_start - 1, gap_end]
        if any(
            index < 0 or index >= len(canonical) or int(canonical[index]["source_ordinal"]) != source_ordinal
            for index in border_indices
        ):
            decision["reason"] = "bracket_crosses_source_boundary"
            decisions.append(decision); continue
        if _transition_risk(cues, cue_ordinals, transition_boundaries_ms, policy):
            decision["reason"] = "near_unresolved_transition"
            decisions.append(decision); continue
        # Smart proposals may be ambiguous, but they must not point to another source.
        proposal_sources: set[int] = set()
        proposal_outside_gap = False
        for row in region:
            span = _span(row)
            if span is None:
                continue
            if span[0] < len(canonical):
                proposal_sources.add(int(canonical[span[0]]["source_ordinal"]))
            if span[0] < gap_start or span[1] > gap_end:
                proposal_outside_gap = True
        if proposal_sources and proposal_sources != {source_ordinal}:
            decision["reason"] = "smart_proposal_source_conflict"
            decisions.append(decision); continue
        if proposal_outside_gap:
            decision["reason"] = "smart_proposal_outside_exact_gap"
            decisions.append(decision); continue

        canonical_text = " ".join(str(row["text"]).strip() for row in gap_rows if str(row["text"]).strip())
        editor_texts = [cues[cue].text for cue in cue_ordinals]
        editor_stream = " ".join(editor_texts)
        region_similarity = _pair_score(
            _normalize_for_match(editor_stream), _normalize_for_match(canonical_text)
        )
        editor_norm = _normalize_for_match(editor_stream)
        canonical_norm = _normalize_for_match(canonical_text)
        if not editor_norm or not canonical_norm:
            decision["reason"] = "empty_normalized_stream"
            decisions.append(decision); continue
        length_ratio = len(editor_norm) / len(canonical_norm)
        decision["canonical_gap"] = [gap_start, gap_end]
        decision["source_ordinal"] = source_ordinal
        decision["region_similarity"] = region_similarity
        decision["length_ratio"] = length_ratio
        if region_similarity < policy.min_region_similarity:
            decision["reason"] = "region_similarity_below_policy"
            decisions.append(decision); continue
        if not (policy.min_length_ratio <= length_ratio <= policy.max_length_ratio):
            decision["reason"] = "region_length_ratio_outside_policy"
            decisions.append(decision); continue
        coverage_ok, canonical_coverage, line_coverages, coverage_reason = _multi_line_coverage_ok(
            gap_rows=gap_rows,
            canonical_norm=canonical_norm,
            editor_norm=editor_norm,
            region_similarity=region_similarity,
            length_ratio=length_ratio,
            policy=policy,
        )
        decision["canonical_coverage"] = canonical_coverage
        decision["canonical_line_coverages"] = line_coverages
        if not coverage_ok:
            decision["reason"] = coverage_reason
            decisions.append(decision); continue
        partition = _partition_words(editor_texts, canonical_text, policy)
        if partition is None:
            decision["reason"] = "safe_word_partition_unavailable"
            decisions.append(decision); continue
        assignments, local_scores = partition
        if len(assignments) != len(cue_ordinals) or any(not text.strip() for text in assignments):
            decision["reason"] = "invalid_partition_shape"
            decisions.append(decision); continue
        if _normalize_for_match(" ".join(assignments)) != canonical_norm:
            decision["reason"] = "partition_stream_not_conservative"
            decisions.append(decision); continue
        decision.update(
            {
                "resolution": "ownership_repartition",
                "reason": "exact_resolved_bracket_and_conservative_word_partition",
                "local_scores": local_scores,
                "assignment_text_sha256": [
                    hashlib.sha256(text.encode("utf-8")).hexdigest() for text in assignments
                ],
            }
        )
        for cue, text in zip(cue_ordinals, assignments):
            replacements[cue] = text
        decisions.append(decision)

    resolved_cues = sorted(replacements)
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy": policy.__dict__,
        "before_manual": len(unresolved),
        "resolved_region_count": sum(row["resolution"] == "ownership_repartition" for row in decisions),
        "resolved_cue_count": len(resolved_cues),
        "remaining_manual": len(unresolved) - len(resolved_cues),
        "resolved_cue_ordinals": resolved_cues,
        "decisions": decisions,
        "replacements": replacements,
        "timing_mutation_performed": False,
    }


__all__ = (
    "POLICY_ID",
    "SCHEMA_VERSION",
    "Policy",
    "adjudicate_regions",
)
