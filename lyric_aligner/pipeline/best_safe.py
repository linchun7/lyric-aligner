"""Evidence-gated Best-Safe subtitle composition.

Best-Safe is a product layer above Smart/Pro/Max.  It never changes Smart's
contract.  Smart cue topology and timing are the product floor.  Higher-mode
results remain candidates unless a *specific boundary* has independent timing
authority; track-level semantic success never grants whole-track timing or
segmentation authority.  Text/display repair is deliberately independent from
timing authority so the fallback can gain safe lexical quality without
regressing an already-good editor/Smart timeline.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.io.path_safety import validate_artifact_output_tree
from lyric_aligner.srt import Cue, cue_id, parse_srt_strict, text_sha256
from lyric_aligner.text.display_policy import mask_strong_profanity
from lyric_aligner.text_repair import _normalize_for_match, _pair_score, parse_canonical_files
from lyric_aligner.review.text_region_adjudication import (
    Policy as TextRegionPolicy,
    _multi_line_coverage_ok,
    _partition_words,
)


POLICY_ID = "best-safe-smart-timing-floor-1.1"
ALGORITHM_VERSION = "1.1.0"
SCHEMA_VERSION = "best-safe-production-1.1"


class BestSafeError(ValueError):
    """Raised when Best-Safe cannot prove a safe composition."""


@dataclass(frozen=True)
class SongBoundary:
    ordinal: int
    start_ms: int
    name: str


@dataclass
class SelectedCue:
    start_ms: int
    end_ms: int
    text: str
    track_ordinal: int
    track: str
    origin: str
    source_position: int | None = None
    source_cue_number: int | None = None
    source_occurrence_id: str = ""
    timing_basis: str = ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise BestSafeError(f"expected JSON object: {path}")
    return value


def _song_boundaries(path: Path) -> list[SongBoundary]:
    rows: list[SongBoundary] = []
    pattern = re.compile(r"^(\d{1,2}):(\d{2})\s+(.+)$")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        minute, second, name = match.groups()
        rows.append(
            SongBoundary(
                ordinal=len(rows) + 1,
                start_ms=(int(minute) * 60 + int(second)) * 1000,
                name=name.strip(),
            )
        )
    if not rows:
        raise BestSafeError("song list contains no parseable MM:SS rows")
    if any(right.start_ms <= left.start_ms for left, right in zip(rows, rows[1:])):
        raise BestSafeError("song list starts are not strictly increasing")
    return rows


def _track_for_start(start_ms: int, songs: Sequence[SongBoundary]) -> SongBoundary:
    chosen = songs[0]
    for row in songs:
        if row.start_ms <= start_ms:
            chosen = row
        else:
            break
    return chosen


def _smart_identity_map(
    *,
    smart_report: Path | None,
    canonical_lyrics: Sequence[Path] | None,
    cue_count: int,
    track_count: int,
) -> tuple[dict[int, int], dict[int, str]]:
    if smart_report is None or canonical_lyrics is None:
        return {}, {}
    if not smart_report.is_file():
        raise BestSafeError(f"Smart report does not exist: {smart_report}")
    if not canonical_lyrics or any(not path.is_file() for path in canonical_lyrics):
        raise BestSafeError("Best-Safe canonical_lyrics must all exist when Smart identity binding is enabled")
    canonical = parse_canonical_files(canonical_lyrics)
    payload = _load_json(smart_report)
    identity: dict[int, int] = {}
    single_line_presentation: dict[int, str] = {}
    for row in payload.get("text_decisions", []):
        if not isinstance(row, Mapping) or row.get("cue_ordinal") is None:
            continue
        cue_ordinal = int(row["cue_ordinal"])
        if cue_ordinal < 0 or cue_ordinal >= cue_count:
            raise BestSafeError(f"Smart decision cue_ordinal out of range: {cue_ordinal}")
        span = row.get("canonical_span")
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            continue
        start, end = int(span[0]), int(span[1])
        if start < 0 or end <= start or end > len(canonical):
            continue
        sources = {canonical[index].source_ordinal for index in range(start, end)}
        if len(sources) != 1:
            continue
        track_ordinal = next(iter(sources)) + 1
        if not 1 <= track_ordinal <= track_count:
            raise BestSafeError(f"Smart canonical source maps outside song list: {track_ordinal}")
        previous = identity.setdefault(cue_ordinal, track_ordinal)
        if previous != track_ordinal:
            raise BestSafeError(f"Smart cue {cue_ordinal} has conflicting canonical source identities")
        if end - start == 1:
            presentation = canonical[start].text
            old_presentation = single_line_presentation.setdefault(cue_ordinal, presentation)
            if old_presentation != presentation:
                raise BestSafeError(f"Smart cue {cue_ordinal} has conflicting canonical presentation")
    return identity, single_line_presentation


_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")


def _latin_tokens(text: str) -> list[str]:
    return [token.casefold() for token in _LATIN_TOKEN_RE.findall(text)]


def _multi_cue_latin_ownership_preserved(before: Sequence[str], after: Sequence[str]) -> bool:
    """Reject cross-cue Latin token insertion/deletion/repartition.

    Multi-cue lexical repair may substitute words inside a frozen cue, but it
    must not move/add/drop Latin tokens across cue boundaries without timing
    authority.  Equal-length replace blocks are allowed; insert/delete or a
    changed per-cue token count is not.
    """
    if len(before) != len(after):
        return False
    for old_text, new_text in zip(before, after):
        old_tokens = _latin_tokens(old_text)
        new_tokens = _latin_tokens(new_text)
        if not old_tokens and not new_tokens:
            continue
        if len(old_tokens) != len(new_tokens):
            return False
        matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in {"insert", "delete"}:
                return False
            if tag == "replace" and (i2 - i1) != (j2 - j1):
                return False
    return True


def _decision_span(row: Mapping[str, Any] | None) -> tuple[int, int] | None:
    if not isinstance(row, Mapping):
        return None
    span = row.get("canonical_span")
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return None
    try:
        start, end = int(span[0]), int(span[1])
    except (TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    return start, end


def _apply_text_adjudication(
    *,
    cue_texts: Sequence[str],
    smart_srt: Path,
    smart_report: Path | None,
    canonical_lyrics: Sequence[Path] | None,
    proposal_path: Path | None,
    task_fingerprint: str,
) -> tuple[list[str], dict[str, Any]]:
    output = list(cue_texts)
    if proposal_path is None:
        return output, {
            "enabled": False,
            "reviewer_model": "",
            "proposal_count": 0,
            "applied_region_count": 0,
            "applied_cue_count": 0,
            "rejected_region_count": 0,
            "applied": [],
            "rejected": [],
        }
    if smart_report is None or canonical_lyrics is None:
        raise BestSafeError("Best-Safe text adjudication requires Smart report and canonical lyrics")
    if not proposal_path.is_file():
        raise BestSafeError(f"Best-Safe text adjudication file does not exist: {proposal_path}")
    payload = _load_json(proposal_path)
    if payload.get("schema_version") != "best-safe-text-adjudication-1.0":
        raise BestSafeError("unsupported Best-Safe text adjudication schema")
    if str(payload.get("task_fingerprint_sha256") or "") != task_fingerprint:
        raise BestSafeError("Best-Safe text adjudication belongs to another task")
    if str(payload.get("smart_srt_sha256") or "") != _sha256(smart_srt):
        raise BestSafeError("Best-Safe text adjudication is stale for the supplied Smart SRT")
    if str(payload.get("smart_report_sha256") or "") != _sha256(smart_report):
        raise BestSafeError("Best-Safe text adjudication is stale for the supplied Smart report")
    expected_canonical_hashes = [_sha256(path) for path in canonical_lyrics]
    if payload.get("canonical_lyrics_sha256") != expected_canonical_hashes:
        raise BestSafeError("Best-Safe text adjudication is stale for the supplied canonical lyrics")
    reviewer_model = str(payload.get("reviewer_model") or "").strip()
    if not reviewer_model:
        raise BestSafeError("Best-Safe text adjudication requires reviewer_model")

    smart_payload = _load_json(smart_report)
    smart_rows = [row for row in smart_payload.get("text_decisions", []) if isinstance(row, Mapping)]
    by_cue = {
        int(row["cue_ordinal"]): row
        for row in smart_rows
        if row.get("cue_ordinal") is not None
    }
    smart_review_cues = {
        int(row["cue_ordinal"])
        for row in smart_rows
        if row.get("cue_ordinal") is not None and row.get("action") == "review"
    }
    canonical = parse_canonical_files(canonical_lyrics)
    proposals = payload.get("proposals")
    if not isinstance(proposals, list):
        raise BestSafeError("Best-Safe text adjudication proposals must be a list")
    model_keep = payload.get("model_keep_smart_regions", [])
    if not isinstance(model_keep, list):
        raise BestSafeError("Best-Safe model_keep_smart_regions must be a list")
    review_region_count_raw = payload.get("review_region_count")
    if review_region_count_raw is not None and int(review_region_count_raw) != len(proposals) + len(model_keep):
        raise BestSafeError("Best-Safe text adjudication review region accounting mismatch")
    used_region_ids: set[int] = set()
    model_keep_cues: set[int] = set()
    normalized_keep: list[dict[str, Any]] = []
    for position, keep in enumerate(model_keep, start=1):
        if not isinstance(keep, Mapping):
            raise BestSafeError(f"model keep region {position} must be an object")
        region_id = int(keep.get("region_id") or 0)
        raw_cues = keep.get("cue_ordinals")
        if region_id <= 0 or region_id in used_region_ids:
            raise BestSafeError(f"model keep region {position} has invalid/duplicate region_id")
        if not isinstance(raw_cues, list) or not raw_cues:
            raise BestSafeError(f"model keep region {position} requires cue_ordinals")
        cue_ordinals = [int(value) for value in raw_cues]
        if cue_ordinals != list(range(cue_ordinals[0], cue_ordinals[-1] + 1)):
            raise BestSafeError(f"model keep region {position} cue_ordinals are not contiguous")
        if any(cue < 0 or cue >= len(output) for cue in cue_ordinals):
            raise BestSafeError(f"model keep region {position} cue ordinal is out of range")
        if any(by_cue.get(cue) is None or by_cue[cue].get("action") != "review" for cue in cue_ordinals):
            raise BestSafeError(f"model keep region {position} targets a non-review Smart cue")
        if model_keep_cues.intersection(cue_ordinals):
            raise BestSafeError(f"model keep region {position} overlaps another keep region")
        used_region_ids.add(region_id)
        model_keep_cues.update(cue_ordinals)
        normalized_keep.append(
            {
                "region_id": region_id,
                "cue_ordinals": cue_ordinals,
                "classification": str(keep.get("classification") or ""),
                "reason": str(keep.get("reason") or "model_keep_smart"),
            }
        )
    used_cues: set[int] = set()
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for position, proposal in enumerate(proposals, start=1):
        if not isinstance(proposal, Mapping):
            raise BestSafeError(f"text proposal {position} must be an object")
        if any(key in proposal for key in ("text", "replacement_text", "assignments", "start_ms", "end_ms")):
            raise BestSafeError(f"text proposal {position} contains free-form text/timing fields")
        if str(proposal.get("action") or "") != "apply_canonical_gap":
            raise BestSafeError(f"text proposal {position} has unsupported action")
        if str(proposal.get("confidence") or "").strip().lower() != "high":
            raise BestSafeError(f"text proposal {position} must be high confidence")
        raw_cues = proposal.get("cue_ordinals")
        span = proposal.get("canonical_span")
        expected = proposal.get("expected_texts")
        if not isinstance(raw_cues, list) or not raw_cues:
            raise BestSafeError(f"text proposal {position} requires cue_ordinals")
        cue_ordinals = [int(value) for value in raw_cues]
        if cue_ordinals != list(range(cue_ordinals[0], cue_ordinals[-1] + 1)):
            raise BestSafeError(f"text proposal {position} cue_ordinals are not contiguous")
        if any(cue < 0 or cue >= len(output) for cue in cue_ordinals):
            raise BestSafeError(f"text proposal {position} cue ordinal is out of range")
        if used_cues.intersection(cue_ordinals):
            raise BestSafeError(f"text proposal {position} overlaps another proposal")
        if model_keep_cues.intersection(cue_ordinals):
            raise BestSafeError(f"text proposal {position} overlaps a model keep-Smart region")
        used_cues.update(cue_ordinals)
        if not isinstance(expected, list) or [str(value) for value in expected] != [cue_texts[cue] for cue in cue_ordinals]:
            raise BestSafeError(f"text proposal {position} expected_texts do not match Smart input")
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise BestSafeError(f"text proposal {position} requires canonical_span")
        gap_start, gap_end = int(span[0]), int(span[1])
        if gap_start < 0 or gap_end <= gap_start or gap_end > len(canonical):
            raise BestSafeError(f"text proposal {position} canonical_span is invalid")

        target_rows = [by_cue.get(cue) for cue in cue_ordinals]
        if any(row is None or row.get("action") != "review" for row in target_rows):
            raise BestSafeError(f"text proposal {position} targets a non-review Smart cue")
        left = by_cue.get(cue_ordinals[0] - 1)
        right = by_cue.get(cue_ordinals[-1] + 1)
        left_span, right_span = _decision_span(left), _decision_span(right)
        structural_reason: str | None = None
        if (
            left is None
            or right is None
            or left.get("action") == "review"
            or right.get("action") == "review"
            or left_span is None
            or right_span is None
        ):
            structural_reason = "resolved_neighbor_bracket_missing"
        elif left_span[1] != gap_start or right_span[0] != gap_end:
            structural_reason = "proposal_not_exact_resolved_canonical_gap"
        gap_sources = {canonical[index].source_ordinal for index in range(gap_start, gap_end)}
        if structural_reason is None and len(gap_sources) != 1:
            structural_reason = "canonical_gap_crosses_source"
        if structural_reason is not None:
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": structural_reason,
                }
            )
            continue

        gap_rows = [{"text": canonical[index].text} for index in range(gap_start, gap_end)]
        canonical_text = " ".join(row["text"] for row in gap_rows)
        editor_texts = [output[cue] for cue in cue_ordinals]
        editor_stream = " ".join(editor_texts)
        editor_norm = _normalize_for_match(editor_stream)
        canonical_norm = _normalize_for_match(canonical_text)
        policy = TextRegionPolicy()
        if not editor_norm or not canonical_norm:
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": "deterministic_empty_normalized_stream",
                }
            )
            continue
        if editor_norm == canonical_norm:
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": "deterministic_normalized_stream_already_equal_keep_smart_presentation",
                }
            )
            continue
        region_similarity = _pair_score(editor_norm, canonical_norm)
        length_ratio = len(editor_norm) / len(canonical_norm)
        if region_similarity < policy.min_region_similarity:
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": "deterministic_region_similarity_below_policy",
                    "region_similarity": region_similarity,
                    "length_ratio": length_ratio,
                }
            )
            continue
        if not (policy.min_length_ratio <= length_ratio <= policy.max_length_ratio):
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": "deterministic_region_length_ratio_outside_policy",
                    "region_similarity": region_similarity,
                    "length_ratio": length_ratio,
                }
            )
            continue
        coverage_ok, canonical_coverage, line_coverages, coverage_reason = _multi_line_coverage_ok(
            gap_rows=gap_rows,
            canonical_norm=canonical_norm,
            editor_norm=editor_norm,
            region_similarity=region_similarity,
            length_ratio=length_ratio,
            policy=policy,
        )
        if not coverage_ok:
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": f"deterministic_{coverage_reason}",
                    "region_similarity": region_similarity,
                    "length_ratio": length_ratio,
                    "canonical_coverage": canonical_coverage,
                    "canonical_line_coverages": line_coverages,
                }
            )
            continue
        partition = _partition_words(editor_texts, canonical_text, policy)
        if partition is None:
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": "deterministic_word_partition_rejected",
                }
            )
            continue
        assignments, local_scores = partition
        if (
            len(assignments) != len(cue_ordinals)
            or any(not text.strip() for text in assignments)
            or _normalize_for_match(" ".join(assignments)) != _normalize_for_match(canonical_text)
        ):
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": "deterministic_partition_stream_mismatch",
                }
            )
            continue
        before = [output[cue] for cue in cue_ordinals]
        if len(cue_ordinals) > 1 and not _multi_cue_latin_ownership_preserved(before, assignments):
            rejected.append(
                {
                    "proposal_index": position,
                    "cue_ordinals": cue_ordinals,
                    "canonical_span": [gap_start, gap_end],
                    "reason": "deterministic_multi_cue_latin_ownership_changed",
                    "before_latin_token_counts": [len(_latin_tokens(text)) for text in before],
                    "after_latin_token_counts": [len(_latin_tokens(text)) for text in assignments],
                }
            )
            continue
        for cue, text in zip(cue_ordinals, assignments):
            output[cue] = text
        applied.append(
            {
                "proposal_index": position,
                "cue_ordinals": cue_ordinals,
                "canonical_span": [gap_start, gap_end],
                "before": before,
                "after": assignments,
                "local_scores": [float(score) for score in local_scores],
                "reason": str(proposal.get("reason") or "bounded_model_hypothesis"),
            }
        )

    accounted_review_cues = model_keep_cues | used_cues
    if accounted_review_cues != smart_review_cues:
        missing = sorted(smart_review_cues - accounted_review_cues)
        extra = sorted(accounted_review_cues - smart_review_cues)
        raise BestSafeError(
            "Best-Safe text adjudication does not account for every Smart review cue "
            f"(missing={missing[:20]}, extra={extra[:20]})"
        )

    rejected_cues = {
        cue
        for row in rejected
        for cue in row.get("cue_ordinals", [])
    }
    applied_cues = {
        cue
        for row in applied
        for cue in row.get("cue_ordinals", [])
    }
    residual_cues = model_keep_cues | rejected_cues
    if applied_cues.intersection(residual_cues) or applied_cues | residual_cues != smart_review_cues:
        raise BestSafeError("Best-Safe text adjudication final review-cue accounting is inconsistent")
    return output, {
        "enabled": True,
        "reviewer_model": reviewer_model,
        "smart_review_cue_count": len(smart_review_cues),
        "accounted_review_cue_count": len(accounted_review_cues),
        "unaccounted_review_cue_count": 0,
        "review_coverage_complete": True,
        "review_region_count": (
            int(review_region_count_raw)
            if review_region_count_raw is not None
            else len(proposals) + len(normalized_keep)
        ),
        "proposal_count": len(proposals),
        "model_keep_smart_region_count": len(normalized_keep),
        "model_keep_smart_cue_count": len(model_keep_cues),
        "model_keep_smart_regions": normalized_keep,
        "applied_region_count": len(applied),
        "applied_cue_count": sum(len(row["cue_ordinals"]) for row in applied),
        "rejected_region_count": len(rejected),
        "residual_smart_region_count": len(normalized_keep) + len(rejected),
        "residual_smart_cue_count": len(residual_cues),
        "residual_smart_cue_ordinals": sorted(residual_cues),
        "applied": applied,
        "rejected": rejected,
    }


def _smart_groups(
    smart_srt: Path,
    songs: Sequence[SongBoundary],
    *,
    smart_report: Path | None = None,
    canonical_lyrics: Sequence[Path] | None = None,
    text_adjudication: Path | None = None,
    task_fingerprint: str,
) -> tuple[dict[int, list[SelectedCue]], dict[str, Any]]:
    cues = parse_srt_strict(smart_srt)
    text_values, text_adjudication_report = _apply_text_adjudication(
        cue_texts=[cue.text for cue in cues],
        smart_srt=smart_srt,
        smart_report=smart_report,
        canonical_lyrics=canonical_lyrics,
        proposal_path=text_adjudication,
        task_fingerprint=task_fingerprint,
    )
    identity, canonical_presentation = _smart_identity_map(
        smart_report=smart_report,
        canonical_lyrics=canonical_lyrics,
        cue_count=len(cues),
        track_count=len(songs),
    )
    groups: dict[int, list[SelectedCue]] = {row.ordinal: [] for row in songs}
    identity_bound = 0
    time_fallback = 0
    time_disagreements: list[dict[str, Any]] = []
    presentation_changes: list[dict[str, Any]] = []
    previous_track = 1
    for cue_ordinal, cue in enumerate(cues):
        time_track = _track_for_start(cue.start_ms, songs)
        track_ordinal = identity.get(cue_ordinal)
        if track_ordinal is None:
            track_ordinal = time_track.ordinal
            time_fallback += 1
        else:
            identity_bound += 1
            if track_ordinal != time_track.ordinal:
                time_disagreements.append(
                    {
                        "cue_ordinal": cue_ordinal,
                        "cue_number": cue.number,
                        "start_ms": cue.start_ms,
                        "end_ms": cue.end_ms,
                        "identity_track_ordinal": track_ordinal,
                        "time_track_ordinal": time_track.ordinal,
                        "text": cue.text,
                    }
                )
        if track_ordinal < previous_track:
            raise BestSafeError(
                f"Smart canonical track identity regresses at cue {cue.number}: {previous_track}->{track_ordinal}"
            )
        previous_track = track_ordinal
        track = songs[track_ordinal - 1]
        cue_text = text_values[cue_ordinal]
        canonical_text = canonical_presentation.get(cue_ordinal)
        if (
            canonical_text is not None
            and "*" in canonical_text
            and canonical_text != cue_text
            and _normalize_for_match(canonical_text) == _normalize_for_match(cue_text)
        ):
            presentation_changes.append(
                {
                    "cue_ordinal": cue_ordinal,
                    "cue_number": cue.number,
                    "before": cue_text,
                    "after": canonical_text,
                    "reason": "single_line_canonical_explicit_mask_restore",
                }
            )
            cue_text = canonical_text
        groups[track_ordinal].append(
            SelectedCue(
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue_text,
                track_ordinal=track_ordinal,
                track=track.name,
                origin="smart_safe",
                source_position=cue_ordinal + 1,
                source_cue_number=cue.number,
                timing_basis="smart_editor_prior",
            )
        )
    missing = [ordinal for ordinal, rows in groups.items() if not rows]
    if missing:
        raise BestSafeError(f"Smart base has no cues for tracks: {missing}")
    return groups, {
        "canonical_identity_enabled": bool(identity),
        "canonical_identity_bound_cue_count": identity_bound,
        "time_fallback_cue_count": time_fallback,
        "time_boundary_disagreement_count": len(time_disagreements),
        "time_boundary_disagreements": time_disagreements,
        "text_adjudication": text_adjudication_report,
        "canonical_presentation_change_count": len(presentation_changes),
        "canonical_presentation_changes": presentation_changes,
    }


def _read_max_audit(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise BestSafeError("Max audit contains no rows")
    return rows


def _max_groups(
    max_srt: Path,
    max_audit: Path,
    songs: Sequence[SongBoundary],
) -> tuple[dict[int, list[SelectedCue]], dict[str, int], str]:
    cues = parse_srt_strict(max_srt)
    audit = _read_max_audit(max_audit)
    if len(cues) != len(audit):
        raise BestSafeError(f"Max SRT/audit count mismatch: {len(cues)} != {len(audit)}")
    groups: dict[int, list[SelectedCue]] = {row.ordinal: [] for row in songs}
    occurrence_to_track: dict[str, int] = {}
    fingerprints: set[str] = set()
    for position, (cue, row) in enumerate(zip(cues, audit), start=1):
        try:
            ordinal = int(row.get("ordinal") or 0)
        except ValueError as exc:
            raise BestSafeError(f"Max audit row {position} has invalid ordinal") from exc
        if ordinal not in groups:
            raise BestSafeError(f"Max audit row {position} has unknown track ordinal {ordinal}")
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        if occurrence_id:
            old = occurrence_to_track.setdefault(occurrence_id, ordinal)
            if old != ordinal:
                raise BestSafeError(f"occurrence {occurrence_id} maps to multiple tracks")
        fingerprint = str(row.get("task_fingerprint_sha256") or "").strip()
        if fingerprint:
            fingerprints.add(fingerprint)
        groups[ordinal].append(
            SelectedCue(
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
                track_ordinal=ordinal,
                track=songs[ordinal - 1].name,
                origin="max_semantic_pass",
                source_position=position,
                source_cue_number=cue.number,
                source_occurrence_id=occurrence_id,
                timing_basis=str(row.get("timing_basis") or row.get("timing_format") or "").strip(),
            )
        )
    if len(fingerprints) != 1:
        raise BestSafeError(f"Max audit must contain exactly one task fingerprint, got {sorted(fingerprints)}")
    return groups, occurrence_to_track, next(iter(fingerprints))


def _semantic_track_statuses(
    path: Path,
    *,
    expected_track_count: int,
    expected_fingerprint: str,
) -> tuple[set[int], set[int], dict[int, dict[str, Any]]]:
    payload = _load_json(path)
    fingerprint = str(payload.get("task_fingerprint_sha256") or "")
    if fingerprint != expected_fingerprint:
        raise BestSafeError("semantic sync belongs to another task fingerprint")
    final_sync = payload.get("final_sync")
    if not isinstance(final_sync, Mapping):
        raise BestSafeError("semantic sync lacks final_sync")
    tracks = final_sync.get("tracks")
    if not isinstance(tracks, list):
        raise BestSafeError("semantic sync final_sync.tracks is missing")
    status: dict[int, dict[str, Any]] = {}
    for row in tracks:
        if not isinstance(row, Mapping):
            continue
        ordinal = int(row.get("ordinal") or 0)
        if ordinal <= 0 or ordinal > expected_track_count:
            raise BestSafeError(f"semantic sync has invalid track ordinal {ordinal}")
        status[ordinal] = dict(row)
    if set(status) != set(range(1, expected_track_count + 1)):
        raise BestSafeError("semantic sync does not cover every song-list track exactly once")
    passed = {
        ordinal
        for ordinal, row in status.items()
        if row.get("passed") is True and not list(row.get("errors") or [])
    }
    blocked = set(status) - passed
    return passed, blocked, status


def _transition_actions(
    path: Path,
    occurrence_to_track: Mapping[str, int],
    *,
    expected_fingerprint: str,
) -> dict[tuple[int, int], str]:
    payload = _load_json(path)
    if str(payload.get("task_fingerprint_sha256") or "") != expected_fingerprint:
        raise BestSafeError("transition authorization belongs to another task fingerprint")
    actions: dict[tuple[int, int], str] = {}
    for item in payload.get("review_items", []):
        if not isinstance(item, Mapping):
            continue
        issue = item.get("issue") or {}
        decision = item.get("decision") or {}
        if not isinstance(issue, Mapping) or not isinstance(decision, Mapping):
            continue
        left = occurrence_to_track.get(str(issue.get("left_occurrence_id") or ""))
        right = occurrence_to_track.get(str(issue.get("right_occurrence_id") or ""))
        if left is None or right is None:
            continue
        action = str(decision.get("action") or "").strip()
        if action not in {"resolved_clear", "confirmed_overlap"}:
            continue
        key = (left, right)
        previous = actions.setdefault(key, action)
        if previous != action:
            raise BestSafeError(f"conflicting transition actions for {key}: {previous} vs {action}")
    return actions


def _clone_rows(rows: Sequence[SelectedCue]) -> list[SelectedCue]:
    return [SelectedCue(**row.__dict__) for row in rows]


def _compose_groups(
    *,
    smart_groups: Mapping[int, Sequence[SelectedCue]],
    max_groups: Mapping[int, Sequence[SelectedCue]],
    passed_tracks: set[int],
    blocked_tracks: set[int],
    transition_actions: Mapping[tuple[int, int], str],
) -> tuple[dict[int, list[SelectedCue]], list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    """Return the immutable Smart topology/timing floor.

    Max/semantic/transition inputs remain lineage and candidate evidence, but a
    track-level PASS never grants timing or segmentation authority.  Boundary
    timing can only change later through an explicit, independently-authorized
    timing-promotion ledger.
    """
    del max_groups, passed_tracks, blocked_tracks, transition_actions
    selected = {ordinal: _clone_rows(rows) for ordinal, rows in smart_groups.items()}
    return selected, [], [], set()


def _flatten_rows(selected: Mapping[int, Sequence[SelectedCue]]) -> list[SelectedCue]:
    return [row for ordinal in sorted(selected) for row in selected[ordinal]]


def _timing_value(row: SelectedCue, boundary: str) -> int:
    if boundary == "start":
        return row.start_ms
    if boundary == "end":
        return row.end_ms
    raise BestSafeError(f"unsupported timing boundary: {boundary!r}")


def _set_timing_value(row: SelectedCue, boundary: str, value: int) -> None:
    if boundary == "start":
        row.start_ms = value
    elif boundary == "end":
        row.end_ms = value
    else:
        raise BestSafeError(f"unsupported timing boundary: {boundary!r}")


def _timing_truth_points(
    *,
    path: Path | None,
    smart_srt: Path,
    task_fingerprint: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if path is None:
        return {}, {"enabled": False, "point_count": 0, "points": []}
    if not path.is_file():
        raise BestSafeError(f"Best-Safe timing truth file does not exist: {path}")
    payload = _load_json(path)
    if payload.get("schema_version") != "best-safe-timing-truth-1.0":
        raise BestSafeError("unsupported Best-Safe timing truth schema")
    if str(payload.get("task_fingerprint_sha256") or "") != task_fingerprint:
        raise BestSafeError("Best-Safe timing truth belongs to another task")
    if str(payload.get("smart_srt_sha256") or "") != _sha256(smart_srt):
        raise BestSafeError("Best-Safe timing truth is stale for the supplied Smart SRT")
    smart_by_number = {cue.number: cue for cue in parse_srt_strict(smart_srt)}
    raw_points = payload.get("points")
    if not isinstance(raw_points, list):
        raise BestSafeError("Best-Safe timing truth points must be a list")
    points: dict[str, dict[str, Any]] = {}
    identities: set[tuple[int, str]] = set()
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_points, start=1):
        if not isinstance(raw, Mapping):
            raise BestSafeError(f"timing truth point {position} must be an object")
        point_id = str(raw.get("id") or "").strip()
        source_cue_number = int(raw.get("source_cue_number") or 0)
        track_ordinal = int(raw.get("track_ordinal") or 0)
        boundary = str(raw.get("boundary") or "").strip()
        truth_ms = int(raw.get("truth_ms") if raw.get("truth_ms") is not None else -1)
        tolerance_ms = int(raw.get("tolerance_ms") if raw.get("tolerance_ms") is not None else -1)
        authority = str(raw.get("authority") or "").strip()
        expected_text = str(raw.get("expected_text") or "")
        if not point_id or point_id in points:
            raise BestSafeError(f"timing truth point {position} has missing/duplicate id")
        if track_ordinal <= 0 or source_cue_number <= 0 or boundary not in {"start", "end"}:
            raise BestSafeError(f"timing truth point {point_id} has invalid cue identity/boundary")
        if truth_ms < 0 or tolerance_ms < 0:
            raise BestSafeError(f"timing truth point {point_id} has invalid truth/tolerance")
        if authority != "human_truth":
            raise BestSafeError(f"timing truth point {point_id} has unsupported authority {authority!r}")
        smart_cue = smart_by_number.get(source_cue_number)
        if smart_cue is None:
            raise BestSafeError(f"timing truth point {point_id} references unknown Smart cue {source_cue_number}")
        if expected_text and smart_cue.text != expected_text:
            raise BestSafeError(f"timing truth point {point_id} text does not match frozen Smart cue")
        identity = (source_cue_number, boundary)
        if identity in identities:
            raise BestSafeError(f"duplicate timing truth boundary identity: {identity}")
        identities.add(identity)
        item = {
            "id": point_id,
            "track_ordinal": track_ordinal,
            "source_cue_number": source_cue_number,
            "boundary": boundary,
            "truth_ms": truth_ms,
            "tolerance_ms": tolerance_ms,
            "authority": authority,
            "expected_text": expected_text,
            "note": str(raw.get("note") or ""),
        }
        points[point_id] = item
        normalized.append(item)
    return points, {"enabled": True, "point_count": len(normalized), "points": normalized}


def _apply_timing_floor(
    selected: Mapping[int, Sequence[SelectedCue]],
    *,
    smart_srt: Path,
    task_fingerprint: str,
    timing_truth: Path | None,
    timing_promotions: Path | None,
) -> dict[str, Any]:
    """Enforce Smart topology/timing and apply only human-truth-bound promotions."""
    smart_cues = parse_srt_strict(smart_srt)
    rows = _flatten_rows(selected)
    if len(rows) != len(smart_cues):
        raise BestSafeError(f"Best-Safe 1.1 topology changed Smart cue count: {len(rows)} != {len(smart_cues)}")
    for position, (row, smart) in enumerate(zip(rows, smart_cues), start=1):
        if row.source_position != position or row.source_cue_number != smart.number:
            raise BestSafeError(f"Best-Safe 1.1 Smart topology identity mismatch at position {position}")
        if (row.start_ms, row.end_ms) != (smart.start_ms, smart.end_ms):
            raise BestSafeError(f"Best-Safe 1.1 timing changed before authorized promotions at Smart cue {smart.number}")

    truth_by_id, truth_report = _timing_truth_points(
        path=timing_truth,
        smart_srt=smart_srt,
        task_fingerprint=task_fingerprint,
    )
    row_by_number = {row.source_cue_number: row for row in rows}
    authorized: dict[tuple[int, str], dict[str, Any]] = {}
    promotion_changes: list[dict[str, Any]] = []
    if timing_promotions is not None:
        if not timing_promotions.is_file():
            raise BestSafeError(f"Best-Safe timing promotion file does not exist: {timing_promotions}")
        payload = _load_json(timing_promotions)
        if payload.get("schema_version") != "best-safe-timing-promotions-1.0":
            raise BestSafeError("unsupported Best-Safe timing promotion schema")
        if str(payload.get("task_fingerprint_sha256") or "") != task_fingerprint:
            raise BestSafeError("Best-Safe timing promotions belong to another task")
        if str(payload.get("smart_srt_sha256") or "") != _sha256(smart_srt):
            raise BestSafeError("Best-Safe timing promotions are stale for the supplied Smart SRT")
        promotions = payload.get("promotions")
        if not isinstance(promotions, list):
            raise BestSafeError("Best-Safe timing promotions must be a list")
        smart_by_number = {cue.number: cue for cue in smart_cues}
        for position, raw in enumerate(promotions, start=1):
            if not isinstance(raw, Mapping):
                raise BestSafeError(f"timing promotion {position} must be an object")
            track_ordinal = int(raw.get("track_ordinal") or 0)
            source_cue_number = int(raw.get("source_cue_number") or 0)
            boundary = str(raw.get("boundary") or "").strip()
            expected_smart_ms = int(raw.get("expected_smart_ms") if raw.get("expected_smart_ms") is not None else -1)
            promoted_ms = int(raw.get("promoted_ms") if raw.get("promoted_ms") is not None else -1)
            authority_type = str(raw.get("authority_type") or "").strip()
            truth_id = str(raw.get("truth_id") or "").strip()
            expected_text = str(raw.get("expected_text") or "")
            reason = str(raw.get("reason") or "").strip()
            if track_ordinal <= 0 or source_cue_number <= 0 or boundary not in {"start", "end"}:
                raise BestSafeError(f"timing promotion {position} has invalid cue identity/boundary")
            if authority_type != "human_truth":
                raise BestSafeError(
                    f"timing promotion {position} authority {authority_type!r} is not independently verifiable in Best-Safe 1.1"
                )
            truth = truth_by_id.get(truth_id)
            if truth is None:
                raise BestSafeError(f"timing promotion {position} lacks matching human truth {truth_id!r}")
            if (
                truth["track_ordinal"] != track_ordinal
                or truth["source_cue_number"] != source_cue_number
                or truth["boundary"] != boundary
            ):
                raise BestSafeError(f"timing promotion {position} does not match timing truth identity")
            smart = smart_by_number.get(source_cue_number)
            row = row_by_number.get(source_cue_number)
            if smart is None or row is None or row.track_ordinal != track_ordinal:
                raise BestSafeError(f"timing promotion {position} does not map to one Smart-floor cue")
            smart_value = smart.start_ms if boundary == "start" else smart.end_ms
            if expected_smart_ms != smart_value:
                raise BestSafeError(f"timing promotion {position} expected Smart boundary is stale")
            if expected_text and smart.text != expected_text:
                raise BestSafeError(f"timing promotion {position} expected text is stale")
            if abs(promoted_ms - truth["truth_ms"]) > truth["tolerance_ms"]:
                raise BestSafeError(f"timing promotion {position} is outside its human-truth tolerance")
            key = (source_cue_number, boundary)
            if key in authorized:
                raise BestSafeError(f"duplicate timing promotion boundary identity: {key}")
            before = _timing_value(row, boundary)
            _set_timing_value(row, boundary, promoted_ms)
            row.timing_basis = "smart_editor_prior+human_truth_boundary"
            change = {
                "track_ordinal": track_ordinal,
                "source_cue_number": source_cue_number,
                "boundary": boundary,
                "before_ms": before,
                "after_ms": promoted_ms,
                "authority_type": authority_type,
                "truth_id": truth_id,
                "reason": reason,
            }
            authorized[key] = change
            promotion_changes.append(change)

    unsupported: list[dict[str, Any]] = []
    changed_boundaries: list[dict[str, Any]] = []
    for row, smart in zip(rows, smart_cues):
        for boundary, smart_value, final_value in (
            ("start", smart.start_ms, row.start_ms),
            ("end", smart.end_ms, row.end_ms),
        ):
            if final_value == smart_value:
                continue
            item = {
                "track_ordinal": row.track_ordinal,
                "source_cue_number": smart.number,
                "boundary": boundary,
                "smart_ms": smart_value,
                "final_ms": final_value,
                "delta_ms": final_value - smart_value,
            }
            changed_boundaries.append(item)
            if (smart.number, boundary) not in authorized:
                unsupported.append(item)
    if unsupported:
        raise BestSafeError(f"Best-Safe 1.1 has unsupported timing changes: {unsupported[:5]}")

    truth_checks: list[dict[str, Any]] = []
    truth_failures: list[dict[str, Any]] = []
    for truth in truth_by_id.values():
        row = row_by_number.get(truth["source_cue_number"])
        if row is None or row.track_ordinal != truth["track_ordinal"]:
            raise BestSafeError(f"timing truth {truth['id']} does not map to one final Smart-floor cue")
        actual = _timing_value(row, truth["boundary"])
        delta = actual - truth["truth_ms"]
        check = {
            "id": truth["id"],
            "track_ordinal": truth["track_ordinal"],
            "source_cue_number": truth["source_cue_number"],
            "boundary": truth["boundary"],
            "truth_ms": truth["truth_ms"],
            "actual_ms": actual,
            "delta_ms": delta,
            "tolerance_ms": truth["tolerance_ms"],
            "passed": abs(delta) <= truth["tolerance_ms"],
        }
        truth_checks.append(check)
        if not check["passed"]:
            truth_failures.append(check)
    if truth_failures:
        raise BestSafeError(f"Best-Safe 1.1 regresses human timing truth: {truth_failures[:5]}")

    return {
        "policy": "smart-topology-timing-floor-1.1",
        "smart_cue_count": len(smart_cues),
        "final_cue_count": len(rows),
        "topology_exact": True,
        "authorized_promotion_count": len(authorized),
        "timing_changed_boundary_count": len(changed_boundaries),
        "unsupported_timing_change_count": len(unsupported),
        "changed_boundaries": changed_boundaries,
        "promotion_changes": promotion_changes,
        "truth": truth_report,
        "truth_check_count": len(truth_checks),
        "truth_failure_count": len(truth_failures),
        "truth_checks": truth_checks,
    }


def _apply_sensitive_masks(
    selected: Mapping[int, Sequence[SelectedCue]],
    *,
    profile: str,
) -> dict[str, Any]:
    if profile not in {"none", "strong_profanity_v1"}:
        raise BestSafeError(f"unsupported Best-Safe mask profile: {profile!r}")
    if profile == "none":
        return {"profile": profile, "changed_cue_count": 0, "masked_term_count": 0, "changes": []}
    changes: list[dict[str, Any]] = []
    masked_term_count = 0
    for ordinal in sorted(selected):
        for row in selected[ordinal]:
            masked, count = mask_strong_profanity(row.text)
            if not count:
                continue
            before = row.text
            row.text = masked
            masked_term_count += count
            changes.append(
                {
                    "track_ordinal": ordinal,
                    "source_cue_number": row.source_cue_number,
                    "before": before,
                    "after": masked,
                    "masked_term_count": count,
                }
            )
    return {
        "profile": profile,
        "changed_cue_count": len(changes),
        "masked_term_count": masked_term_count,
        "changes": changes,
    }


def _apply_display_overrides(
    selected: Mapping[int, Sequence[SelectedCue]],
    *,
    path: Path | None,
    task_fingerprint: str,
) -> dict[str, Any]:
    if path is None:
        return {"enabled": False, "applied_count": 0, "reviewer_model": "", "changes": []}
    if not path.is_file():
        raise BestSafeError(f"Best-Safe display override file does not exist: {path}")
    payload = _load_json(path)
    if payload.get("schema_version") != "best-safe-display-overrides-1.0":
        raise BestSafeError("unsupported Best-Safe display override schema")
    if str(payload.get("task_fingerprint_sha256") or "") != task_fingerprint:
        raise BestSafeError("Best-Safe display overrides belong to another task")
    reviewer_model = str(payload.get("reviewer_model") or "").strip()
    if not reviewer_model:
        raise BestSafeError("Best-Safe display overrides require reviewer_model")
    changes: list[dict[str, Any]] = []
    used: set[tuple[int, int]] = set()
    for position, override in enumerate(payload.get("overrides", []), start=1):
        if not isinstance(override, Mapping):
            raise BestSafeError(f"display override {position} must be an object")
        if str(override.get("confidence") or "").strip().lower() != "high":
            raise BestSafeError(f"display override {position} must be high confidence")
        track_ordinal = int(override.get("track_ordinal") or 0)
        source_cue_number = int(override.get("source_cue_number") or 0)
        expected_text = str(override.get("expected_text") or "")
        display_text = str(override.get("display_text") or "")
        reason = str(override.get("reason") or "").strip()
        if track_ordinal not in selected or source_cue_number <= 0 or not expected_text or not display_text or not reason:
            raise BestSafeError(f"display override {position} is incomplete")
        if _normalize_for_match(expected_text) != _normalize_for_match(display_text):
            raise BestSafeError(f"display override {position} changes normalized lexical content")
        matches = [
            row
            for row in selected[track_ordinal]
            if row.source_cue_number == source_cue_number and row.text == expected_text
        ]
        if len(matches) != 1:
            raise BestSafeError(
                f"display override {position} must match exactly one selected cue; got {len(matches)}"
            )
        key = (track_ordinal, source_cue_number)
        if key in used:
            raise BestSafeError(f"duplicate Best-Safe display override identity: {key}")
        used.add(key)
        row = matches[0]
        row.text = display_text
        changes.append(
            {
                "track_ordinal": track_ordinal,
                "source_cue_number": source_cue_number,
                "before": expected_text,
                "after": display_text,
                "reason": reason,
            }
        )
    return {
        "enabled": True,
        "applied_count": len(changes),
        "reviewer_model": reviewer_model,
        "changes": changes,
    }


def _geometry(selected: Mapping[int, Sequence[SelectedCue]]) -> dict[str, Any]:
    issues: list[str] = []
    cross_track: list[dict[str, Any]] = []
    cue_count = 0
    for ordinal in sorted(selected):
        rows = selected[ordinal]
        cue_count += len(rows)
        previous_start = -1
        for position, row in enumerate(rows):
            if row.end_ms <= row.start_ms:
                issues.append(f"track {ordinal} cue {position + 1} has non-positive duration")
            if row.start_ms < previous_start:
                issues.append(f"track {ordinal} cue starts regress")
            previous_start = row.start_ms
        if ordinal < len(selected) and rows and selected[ordinal + 1]:
            overlap = max(0, rows[-1].end_ms - selected[ordinal + 1][0].start_ms)
            if overlap:
                cross_track.append(
                    {
                        "left_track": ordinal,
                        "right_track": ordinal + 1,
                        "overlap_ms": overlap,
                    }
                )
    return {
        "cue_count": cue_count,
        "issue_count": len(issues),
        "issues": issues,
        "cross_track_overlap_count": len(cross_track),
        "cross_track_overlaps": cross_track,
    }


def _write_srt(path: Path, selected: Mapping[int, Sequence[SelectedCue]]) -> list[SelectedCue]:
    flat = [row for ordinal in sorted(selected) for row in selected[ordinal]]
    blocks = []
    for position, row in enumerate(flat, start=1):
        def fmt(value: int) -> str:
            hour, rem = divmod(value, 3_600_000)
            minute, rem = divmod(rem, 60_000)
            second, milli = divmod(rem, 1000)
            return f"{hour:02d}:{minute:02d}:{second:02d},{milli:03d}"
        blocks.append(f"{position}\n{fmt(row.start_ms)} --> {fmt(row.end_ms)}\n{row.text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return flat


def _write_audit(
    path: Path,
    rows: Sequence[SelectedCue],
    *,
    task_fingerprint: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "position",
        "cue_number",
        "start_ms",
        "end_ms",
        "text",
        "track_ordinal",
        "track",
        "origin",
        "timing_basis",
        "source_position",
        "source_cue_number",
        "source_occurrence_id",
        "task_fingerprint_sha256",
        "cue_id",
        "text_sha256",
        "best_safe_policy_id",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for position, row in enumerate(rows, start=1):
            cue = Cue(position, row.start_ms, row.end_ms, row.text)
            writer.writerow(
                {
                    "position": position,
                    "cue_number": position,
                    "start_ms": row.start_ms,
                    "end_ms": row.end_ms,
                    "text": row.text,
                    "track_ordinal": row.track_ordinal,
                    "track": row.track,
                    "origin": row.origin,
                    "timing_basis": row.timing_basis,
                    "source_position": row.source_position or "",
                    "source_cue_number": row.source_cue_number or "",
                    "source_occurrence_id": row.source_occurrence_id,
                    "task_fingerprint_sha256": task_fingerprint,
                    "cue_id": cue_id(position, cue),
                    "text_sha256": text_sha256(row.text),
                    "best_safe_policy_id": POLICY_ID,
                }
            )


def build_best_safe(
    *,
    smart_srt: Path,
    max_srt: Path,
    max_audit: Path,
    semantic_sync: Path,
    song_list: Path,
    transition_authorization: Path,
    out_srt: Path,
    out_audit: Path,
    out_qa: Path,
    out_artifact: Path,
    expected_task_fingerprint: str | None = None,
    smart_report: Path | None = None,
    canonical_lyrics: Sequence[Path] | None = None,
    canonical_lyrics_root: Path | None = None,
    text_adjudication: Path | None = None,
    mask_profile: str = "strong_profanity_v1",
    display_overrides: Path | None = None,
    timing_truth: Path | None = None,
    timing_promotions: Path | None = None,
) -> dict[str, Any]:
    protected_inputs: dict[str, Path] = {
        "smart_srt": smart_srt,
        "max_srt": max_srt,
        "max_audit": max_audit,
        "semantic_sync": semantic_sync,
        "song_list": song_list,
        "transition_authorization": transition_authorization,
    }
    for label, path in protected_inputs.items():
        if not path.is_file():
            raise BestSafeError(f"required Best-Safe input missing ({label}): {path}")
    if smart_report is not None:
        if not smart_report.is_file():
            raise BestSafeError(f"Best-Safe Smart report missing: {smart_report}")
        protected_inputs["smart_report"] = smart_report
    if canonical_lyrics is not None:
        for index, path in enumerate(canonical_lyrics, start=1):
            if not path.is_file():
                raise BestSafeError(f"Best-Safe canonical lyric missing: {path}")
            protected_inputs[f"canonical_lyric_{index}"] = path
    if canonical_lyrics_root is not None:
        if not canonical_lyrics_root.is_dir():
            raise BestSafeError(f"Best-Safe canonical lyrics root is not a directory: {canonical_lyrics_root}")
        protected_inputs["canonical_lyrics_root"] = canonical_lyrics_root
    if text_adjudication is not None:
        protected_inputs["text_adjudication"] = text_adjudication
    if display_overrides is not None:
        protected_inputs["display_overrides"] = display_overrides
    if timing_truth is not None:
        protected_inputs["timing_truth"] = timing_truth
    if timing_promotions is not None:
        protected_inputs["timing_promotions"] = timing_promotions

    outputs = {
        "final_srt": out_srt,
        "audit_csv": out_audit,
        "qa_json": out_qa,
        "artifact_json": out_artifact,
    }
    output_parents = {path.resolve().parent for path in outputs.values()}
    if len(output_parents) != 1:
        raise BestSafeError("Best-Safe outputs must share one output directory")
    validate_artifact_output_tree(
        inputs=protected_inputs,
        output_dir=next(iter(output_parents)),
        outputs=outputs,
    )

    songs = _song_boundaries(song_list)
    max_groups, occurrence_to_track, task_fingerprint = _max_groups(max_srt, max_audit, songs)
    if expected_task_fingerprint and task_fingerprint != expected_task_fingerprint:
        raise BestSafeError("Max task fingerprint does not match expected task fingerprint")
    smart_groups, smart_identity = _smart_groups(
        smart_srt,
        songs,
        smart_report=smart_report,
        canonical_lyrics=canonical_lyrics,
        text_adjudication=text_adjudication,
        task_fingerprint=task_fingerprint,
    )
    passed, blocked, semantic_tracks = _semantic_track_statuses(
        semantic_sync,
        expected_track_count=len(songs),
        expected_fingerprint=task_fingerprint,
    )
    transitions = _transition_actions(
        transition_authorization,
        occurrence_to_track,
        expected_fingerprint=task_fingerprint,
    )
    selected, transition_adjustments, transition_fallbacks, demoted = _compose_groups(
        smart_groups=smart_groups,
        max_groups=max_groups,
        passed_tracks=passed,
        blocked_tracks=blocked,
        transition_actions=transitions,
    )
    timing_floor_result = _apply_timing_floor(
        selected,
        smart_srt=smart_srt,
        task_fingerprint=task_fingerprint,
        timing_truth=timing_truth,
        timing_promotions=timing_promotions,
    )
    sensitive_mask_result = _apply_sensitive_masks(selected, profile=mask_profile)
    display_result = _apply_display_overrides(
        selected,
        path=display_overrides,
        task_fingerprint=task_fingerprint,
    )
    geometry = _geometry(selected)
    if geometry["issue_count"]:
        raise BestSafeError("Best-Safe geometry failed: " + "; ".join(geometry["issues"]))

    final_rows = _write_srt(out_srt, selected)
    _write_audit(out_audit, final_rows, task_fingerprint=task_fingerprint)
    reparsed = parse_srt_strict(out_srt)
    if len(reparsed) != len(final_rows):
        raise BestSafeError("Best-Safe SRT did not replay after writing")

    origin_counts: dict[str, int] = {}
    for row in final_rows:
        origin_counts[row.origin] = origin_counts.get(row.origin, 0) + 1
    selected_track_modes = {
        ordinal: "smart_timing_floor"
        for ordinal in range(1, len(songs) + 1)
    }
    publish_ready = (
        timing_floor_result["topology_exact"] is True
        and timing_floor_result["unsupported_timing_change_count"] == 0
        and timing_floor_result["truth_failure_count"] == 0
    )
    qa: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "algorithm_version": ALGORITHM_VERSION,
        "task_fingerprint_sha256": task_fingerprint,
        "passed": publish_ready,
        "publish_ready": publish_ready,
        "authority_scope": "best_safe_smart_timing_floor_not_max_release",
        "meaning": (
            "publish_ready means Smart cue topology/timing were preserved except explicitly authorized boundary promotions, "
            "all supplied human timing truth passed, and text/display improvements passed their own deterministic gates; "
            "track-level Max semantic PASS remains candidate evidence only and does not grant whole-track timing authority"
        ),
        "cue_count": len(final_rows),
        "track_count": len(songs),
        "semantic_passed_track_ordinals": sorted(passed),
        "semantic_blocked_track_ordinals": sorted(blocked),
        "max_candidate_track_ordinals": sorted(passed),
        "demoted_track_ordinals": sorted(demoted),
        "selected_track_modes": {str(k): v for k, v in selected_track_modes.items()},
        "origin_cue_counts": origin_counts,
        "smart_track_identity": smart_identity,
        "timing_floor": timing_floor_result,
        "sensitive_mask": sensitive_mask_result,
        "display_overrides": display_result,
        "geometry": geometry,
        "transition_authority_mode": "diagnostic_only_no_smart_timing_mutation",
        "transition_adjustments": transition_adjustments,
        "transition_fallbacks": transition_fallbacks,
        "semantic_track_evidence": {
            str(ordinal): {
                "passed": semantic_tracks[ordinal].get("passed") is True,
                "evidence_basis": semantic_tracks[ordinal].get("evidence_basis"),
                "median_abs_delta_ms": semantic_tracks[ordinal].get("median_abs_delta_ms"),
                "p90_abs_delta_ms": semantic_tracks[ordinal].get("p90_abs_delta_ms"),
                "errors": list(semantic_tracks[ordinal].get("errors") or []),
            }
            for ordinal in sorted(semantic_tracks)
        },
        "inputs": {
            "smart_srt_sha256": _sha256(smart_srt),
            "smart_report_sha256": _sha256(smart_report) if smart_report is not None else None,
            "canonical_lyrics": (
                [{"path": str(path), "sha256": _sha256(path)} for path in canonical_lyrics]
                if canonical_lyrics is not None
                else []
            ),
            "text_adjudication_sha256": _sha256(text_adjudication) if text_adjudication is not None else None,
            "mask_profile": mask_profile,
            "max_srt_sha256": _sha256(max_srt),
            "max_audit_sha256": _sha256(max_audit),
            "semantic_sync_sha256": _sha256(semantic_sync),
            "song_list_sha256": _sha256(song_list),
            "transition_authorization_sha256": _sha256(transition_authorization),
            "display_overrides_sha256": _sha256(display_overrides) if display_overrides is not None else None,
            "timing_truth_sha256": _sha256(timing_truth) if timing_truth is not None else None,
            "timing_promotions_sha256": _sha256(timing_promotions) if timing_promotions is not None else None,
        },
    }
    out_qa.parent.mkdir(parents=True, exist_ok=True)
    out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=task_fingerprint,
        stage="best_safe",
        algorithm_version=ALGORITHM_VERSION,
        outputs=(("final_srt", out_srt), ("audit_csv", out_audit), ("qa_json", out_qa)),
        normalized_config={
            "policy_id": POLICY_ID,
            "timing_floor_policy": timing_floor_result["policy"],
        },
        evidence={
            "smart_srt_sha256": qa["inputs"]["smart_srt_sha256"],
            "text_adjudication_sha256": qa["inputs"]["text_adjudication_sha256"],
            "mask_profile": qa["inputs"]["mask_profile"],
            "max_srt_sha256": qa["inputs"]["max_srt_sha256"],
            "max_audit_sha256": qa["inputs"]["max_audit_sha256"],
            "semantic_sync_sha256": qa["inputs"]["semantic_sync_sha256"],
            "transition_authorization_sha256": qa["inputs"]["transition_authorization_sha256"],
            "display_overrides_sha256": qa["inputs"]["display_overrides_sha256"],
            "timing_truth_sha256": qa["inputs"]["timing_truth_sha256"],
            "timing_promotions_sha256": qa["inputs"]["timing_promotions_sha256"],
            "timing_floor_policy": timing_floor_result["policy"],
            "unsupported_timing_change_count": timing_floor_result["unsupported_timing_change_count"],
            "semantic_passed_track_ordinals": sorted(passed),
            "semantic_blocked_track_ordinals": sorted(blocked),
            "max_candidate_track_ordinals": sorted(passed),
            "demoted_track_ordinals": sorted(demoted),
        },
    )
    out_artifact.parent.mkdir(parents=True, exist_ok=True)
    out_artifact.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return qa


__all__ = (
    "ALGORITHM_VERSION",
    "BestSafeError",
    "POLICY_ID",
    "SCHEMA_VERSION",
    "build_best_safe",
)
