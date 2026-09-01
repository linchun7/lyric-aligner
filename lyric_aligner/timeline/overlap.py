"""Recompose canonical occurrence timelines for confirmed cross-track overlap.

The overlap layer never invents lyric text. It merges the existing primary
canonical timeline with canonical lyrics re-projected through boundary-local
Source-to-Mix mappings, clipped strictly to human-confirmed overlap intervals.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Iterable


class OverlapRecompositionError(ValueError):
    """Raised when confirmed-overlap evidence cannot be safely materialized."""


@dataclass(frozen=True)
class ConfirmedOverlapRegion:
    candidate_id: str
    left_occurrence_id: str
    right_occurrence_id: str
    start_ms: int
    end_ms: int
    issue_id: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise OverlapRecompositionError("confirmed overlap region requires candidate_id")
        if not self.left_occurrence_id or not self.right_occurrence_id:
            raise OverlapRecompositionError("confirmed overlap region requires both occurrences")
        if self.left_occurrence_id == self.right_occurrence_id:
            raise OverlapRecompositionError("confirmed overlap occurrences must be distinct")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise OverlapRecompositionError("confirmed overlap interval is invalid")

    @property
    def region_id(self) -> str:
        payload = {
            "candidate_id": self.candidate_id,
            "left_occurrence_id": self.left_occurrence_id,
            "right_occurrence_id": self.right_occurrence_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "region_id": self.region_id}


def region_from_issue(issue: dict[str, Any]) -> ConfirmedOverlapRegion:
    if issue.get("decision_action") != "confirmed_overlap":
        raise OverlapRecompositionError("review issue is not confirmed_overlap")
    if not bool(issue.get("requires_recomposition")):
        raise OverlapRecompositionError("confirmed overlap issue is missing recomposition flag")
    issue_id = str(issue.get("issue_id") or "").strip()
    if not issue_id:
        raise OverlapRecompositionError("confirmed overlap issue is missing issue_id")
    candidate_id = str(issue.get("candidate_id") or "").strip()
    interval = issue.get("confirmed_interval")
    if not isinstance(interval, list) or len(interval) != 2:
        try:
            interval = [float(issue["interval_start"]), float(issue["interval_end"])]
        except (KeyError, TypeError, ValueError) as exc:
            raise OverlapRecompositionError(
                "confirmed overlap issue is missing a materialized interval"
            ) from exc
    try:
        start_ms = int(round(float(interval[0]) * 1000.0))
        end_ms = int(round(float(interval[1]) * 1000.0))
    except (TypeError, ValueError) as exc:
        raise OverlapRecompositionError("confirmed overlap interval is invalid") from exc
    return ConfirmedOverlapRegion(
        candidate_id=candidate_id,
        left_occurrence_id=str(issue.get("left_occurrence_id") or ""),
        right_occurrence_id=str(issue.get("right_occurrence_id") or ""),
        start_ms=start_ms,
        end_ms=end_ms,
        issue_id=issue_id,
    )


def _clip_line_to_region(line: dict[str, Any], region: ConfirmedOverlapRegion) -> dict[str, Any] | None:
    try:
        raw_start = int(line["mix_start_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OverlapRecompositionError("projected overlap line has invalid mix_start_ms") from exc
    raw_end = line.get("mix_end_ms")
    if raw_end is None:
        end = region.end_ms
    else:
        try:
            end = int(raw_end)
        except (TypeError, ValueError) as exc:
            raise OverlapRecompositionError("projected overlap line has invalid mix_end_ms") from exc
    start = max(raw_start, region.start_ms)
    end = min(end, region.end_ms)
    if end <= start:
        return None

    clipped = deepcopy(line)
    clipped["mix_start_ms"] = start
    clipped["mix_end_ms"] = end
    clipped["overlap_region_id"] = region.region_id
    clipped["overlap_candidate_id"] = region.candidate_id
    clipped["overlap_clip"] = True

    tokens = clipped.get("tokens")
    if isinstance(tokens, list):
        clipped_tokens: list[dict[str, Any]] = []
        for token in tokens:
            try:
                token_start = int(token["mix_start_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            token_end_raw = token.get("mix_end_ms")
            token_end = region.end_ms if token_end_raw is None else int(token_end_raw)
            token_start = max(token_start, region.start_ms)
            token_end = min(token_end, region.end_ms)
            if token_end > token_start:
                clipped_tokens.append(
                    {
                        **token,
                        "mix_start_ms": token_start,
                        "mix_end_ms": token_end,
                    }
                )
        clipped["tokens"] = clipped_tokens
    return clipped


def clip_projected_result_to_region(
    projected_result: dict[str, Any],
    region: ConfirmedOverlapRegion,
) -> list[dict[str, Any]]:
    lines = projected_result.get("lines")
    if not isinstance(lines, list):
        raise OverlapRecompositionError("projected overlap result has no line list")
    output: list[dict[str, Any]] = []
    for line in lines:
        clipped = _clip_line_to_region(line, region)
        if clipped is not None:
            output.append(clipped)
    return output


def _line_interval(line: dict[str, Any]) -> tuple[int, int]:
    try:
        start = int(line["mix_start_ms"])
        end_raw = line.get("mix_end_ms")
        end = start + 1 if end_raw is None else int(end_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise OverlapRecompositionError("timeline line has invalid mix interval") from exc
    return start, end


def _merge_compatible_line(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    if int(left.get("canonical_line_index", -1)) != int(right.get("canonical_line_index", -2)):
        return None
    if str(left.get("text") or "") != str(right.get("text") or ""):
        return None
    left_start, left_end = _line_interval(left)
    right_start, right_end = _line_interval(right)
    if right_start > left_end + 250 or left_start > right_end + 250:
        return None

    merged = deepcopy(left)
    merged["mix_start_ms"] = min(left_start, right_start)
    merged["mix_end_ms"] = max(left_end, right_end)
    region_ids = {
        str(value)
        for value in (
            left.get("overlap_region_id"),
            right.get("overlap_region_id"),
        )
        if value
    }
    candidate_ids = {
        str(value)
        for value in (
            left.get("overlap_candidate_id"),
            right.get("overlap_candidate_id"),
        )
        if value
    }
    if region_ids:
        merged["overlap_region_ids"] = sorted(region_ids)
    if candidate_ids:
        merged["overlap_candidate_ids"] = sorted(candidate_ids)
    merged["overlap_recomposed"] = True
    return merged


def merge_primary_with_overlap_lines(
    primary_result: dict[str, Any],
    overlap_lines: Iterable[dict[str, Any]],
    *,
    regions: Iterable[ConfirmedOverlapRegion],
) -> dict[str, Any]:
    """Return one occurrence timeline expanded only by confirmed overlap evidence."""

    result = deepcopy(primary_result)
    window = result.get("window")
    if not isinstance(window, dict):
        raise OverlapRecompositionError("primary timeline has no finite occurrence window")
    try:
        base_start = int(window["start_ms"])
        base_end = int(window["end_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OverlapRecompositionError("primary timeline window is invalid") from exc

    base_lines = result.get("lines")
    if not isinstance(base_lines, list):
        raise OverlapRecompositionError("primary timeline lines must be a list")
    combined = [deepcopy(line) for line in base_lines]
    for incoming in overlap_lines:
        incoming = deepcopy(incoming)
        merged = False
        for index, existing in enumerate(combined):
            candidate = _merge_compatible_line(existing, incoming)
            if candidate is not None:
                combined[index] = candidate
                merged = True
                break
        if not merged:
            combined.append(incoming)

    combined.sort(
        key=lambda line: (
            int(line.get("mix_start_ms", -1)),
            int(line.get("canonical_line_index", -1)),
            int(line.get("mix_end_ms") or 2**31 - 1),
        )
    )
    region_rows = list(regions)
    coverage = result.get("projection_coverage")
    if coverage is not None:
        if not isinstance(coverage, dict):
            raise OverlapRecompositionError("primary timeline has invalid projection_coverage")
        if str(coverage.get("status") or "") == "bounded_terminal_disconnect":
            try:
                authority_end_ms = int(coverage["mix_end_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise OverlapRecompositionError(
                    "primary timeline has invalid bounded projection authority"
                ) from exc
            if any(region.end_ms > authority_end_ms for region in region_rows):
                raise OverlapRecompositionError(
                    "confirmed overlap extends beyond primary timeline projection authority"
                )
    if region_rows:
        base_start = min(base_start, *(region.start_ms for region in region_rows))
        base_end = max(base_end, *(region.end_ms for region in region_rows))
    result["window"] = {"start_ms": base_start, "end_ms": base_end}
    result["lines"] = combined
    result["line_count"] = len(combined)
    result["overlap_recomposition"] = {
        "region_ids": [region.region_id for region in region_rows],
        "candidate_ids": [region.candidate_id for region in region_rows],
    }
    return result
