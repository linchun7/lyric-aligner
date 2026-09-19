"""Model-reviewed, display-only sensitive-word gate for every Safe Final.

Deterministic rules only surface review hints. A model must scan the complete
frozen SRT and explicitly decide every hinted cue. Only high-confidence MASK
decisions can change viewer-facing text, and the materializer derives the mask
from exact task-bound terms instead of accepting free replacement lyrics.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lyric_aligner.qa.semantic_sync import parse_song_windows
from lyric_aligner.review.canonical_splices import _bound, _read, _require, file_ref
from lyric_aligner.srt import Cue, parse_srt_strict
from lyric_aligner.text.display_policy import find_strong_profanity

SCHEMA_VERSION = "semantic-sensitive-review-1.0"
PACK_KIND = "semantic-sensitive-pack"
REVIEW_KIND = "semantic-sensitive-decisions"
AUDIT_KIND = "semantic-sensitive-finalization"

_CONTEXTUAL_TERM_RE = re.compile(
    r"(?<![A-Za-z])(?:sexy|shot|bullet|kill(?:er|ers|ing|ed|s)?|killa|damn)(?![A-Za-z])",
    flags=re.IGNORECASE,
)
_PREMASKED_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_]|[^\x00-\x7f])\*(?!\*)")


def _format_ms(value: int) -> str:
    hour, remain = divmod(value, 3_600_000)
    minute, remain = divmod(remain, 60_000)
    second, millis = divmod(remain, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _timing(cue: Cue) -> str:
    return f"{_format_ms(cue.start_ms)} --> {_format_ms(cue.end_ms)}"


def _song_context(
    song_list: Path | None,
    *,
    content_end_ms: int,
) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
    if song_list is None:
        return None, []
    song_list = song_list.resolve()
    _require(song_list.is_file(), "song list does not exist")
    windows = parse_song_windows(song_list, content_end_ms=content_end_ms)
    return file_ref(song_list), [
        {
            "song_index": window.ordinal,
            "start_ms": window.start_ms,
            "end_ms": window.end_ms,
            "label": window.label,
        }
        for window in windows
    ]


def _song_for_cue(
    songs: list[dict[str, Any]],
    start_ms: int,
) -> dict[str, Any] | None:
    current = None
    for song in songs:
        if int(song["start_ms"]) > start_ms:
            break
        current = song
    return dict(current) if current is not None else None


def _hints(text: str) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for row in find_strong_profanity(text):
        hints.append(
            {
                "kind": "strong_profanity_candidate",
                "start": row["start"],
                "end": row["end"],
                "text": row["text"],
                "automatic_replacement_allowed": False,
            }
        )
    for match in _CONTEXTUAL_TERM_RE.finditer(text):
        hints.append(
            {
                "kind": "context_required_term",
                "start": match.start(),
                "end": match.end(),
                "text": match.group(0),
                "automatic_replacement_allowed": False,
            }
        )
    for match in _PREMASKED_RE.finditer(text):
        hints.append(
            {
                "kind": "pre_masked_text",
                "start": match.start(),
                "end": match.end(),
                "text": match.group(0),
                "automatic_replacement_allowed": False,
            }
        )
    hints.sort(key=lambda row: (int(row["start"]), int(row["end"]), str(row["kind"])))
    return hints


def build_semantic_sensitive_pack(
    source_srt: Path,
    *,
    song_list: Path | None = None,
) -> dict[str, Any]:
    """Build a full-SRT model review pack; no text is changed here."""

    source_srt = source_srt.resolve()
    _require(source_srt.is_file(), "source SRT does not exist")
    cues = parse_srt_strict(source_srt)
    _require(bool(cues), "source SRT contains no cues")
    song_ref, songs = _song_context(
        song_list,
        content_end_ms=max(cue.end_ms for cue in cues) + 1,
    )

    rows = []
    required: list[int] = []
    hint_count = 0
    for ordinal, cue in enumerate(cues):
        hints = _hints(cue.text)
        hint_count += len(hints)
        if hints:
            required.append(cue.number)
        rows.append(
            {
                "cue_ordinal": ordinal,
                "cue_number": cue.number,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "timing": _timing(cue),
                "song": _song_for_cue(songs, cue.start_ms),
                "text": cue.text,
                "hints": hints,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": PACK_KIND,
        "source_srt": file_ref(source_srt),
        "song_list": song_ref,
        "songs": songs,
        "full_scan_required": True,
        "cue_count": len(cues),
        "candidate_hint_count": hint_count,
        "required_decision_cue_numbers": required,
        "cues": rows,
        "review_contract": {
            "reviewer_type": "model",
            "full_scan_completed": True,
            "scanned_cue_count": len(cues),
            "actions": ["keep", "mask", "review"],
            "mask_materialization": "exact term -> first character plus '*'",
            "rules": [
                "Read every cue, not only mechanically hinted candidates.",
                "Hints are routing only and never authorize automatic replacement.",
                "Judge wording in lyric/song/context; preserve legitimate titles, mixed-language lyrics and ad-libs.",
                "Every required_decision_cue_number needs exactly one explicit decision.",
                "Add a mask/review decision for any unhinted issue found during the full scan.",
                "MASK may only identify exact terms already present in expected_text; no free lyric rewriting.",
                "REVIEW blocks finalization until resolved.",
            ],
        },
    }


def _nth_interval(text: str, term: str, occurrence: int) -> tuple[int, int]:
    _require(isinstance(term, str) and len(term) >= 1 and term.strip() == term, "invalid mask term")
    _require(type(occurrence) is int and occurrence >= 1, "invalid mask term occurrence")
    start = 0
    found = -1
    for _ in range(occurrence):
        found = text.find(term, start)
        _require(found >= 0, f"mask term occurrence not found: {term!r} #{occurrence}")
        start = found + len(term)
    return found, found + len(term)


def _materialize_mask(text: str, terms: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    _require(isinstance(terms, list) and bool(terms), "MASK requires mask_terms")
    intervals: list[tuple[int, int, str, int]] = []
    for row in terms:
        _require(
            isinstance(row, dict) and set(row) == {"text", "occurrence"},
            "mask term must contain only text/occurrence",
        )
        term = row["text"]
        occurrence = row["occurrence"]
        a, b = _nth_interval(text, term, occurrence)
        intervals.append((a, b, term, occurrence))

    intervals.sort()
    for left, right in zip(intervals, intervals[1:]):
        _require(left[1] <= right[0], "overlapping semantic sensitive mask terms")

    output = text
    materialized = []
    for a, b, term, occurrence in reversed(intervals):
        replacement = term[0] + "*"
        output = output[:a] + replacement + output[b:]
        materialized.append(
            {
                "start": a,
                "end": b,
                "source_text": term,
                "occurrence": occurrence,
                "display_text": replacement,
            }
        )
    materialized.reverse()
    _require(output != text, "MASK decision produced no display change")
    return output, materialized


def materialize_semantic_masks(
    text: str,
    terms: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Public deterministic materializer for an already validated model decision."""

    return _materialize_mask(text, terms)


def _validate_review(
    pack_path: Path,
    review_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path, list[Cue], dict[int, dict[str, Any]]]:
    pack_path = pack_path.resolve()
    review_path = review_path.resolve()
    pack = _read(pack_path)
    review = _read(review_path)

    _require(pack.get("schema_version") == SCHEMA_VERSION and pack.get("kind") == PACK_KIND, "unsupported semantic sensitive pack")
    source_srt = _bound(pack["source_srt"], pack_path.parent)
    song_list = _bound(pack["song_list"], pack_path.parent) if pack.get("song_list") else None
    rebuilt = build_semantic_sensitive_pack(source_srt, song_list=song_list)
    _require(
        json.dumps(pack, ensure_ascii=False, sort_keys=True, allow_nan=False)
        == json.dumps(rebuilt, ensure_ascii=False, sort_keys=True, allow_nan=False),
        "stale or modified semantic sensitive pack",
    )

    expected_top = {
        "schema_version",
        "kind",
        "pack_sha256",
        "source_srt_sha256",
        "reviewer_type",
        "reviewer_model",
        "full_scan_completed",
        "scanned_cue_count",
        "decisions",
    }
    _require(set(review) == expected_top, "invalid semantic sensitive review fields")
    _require(review["schema_version"] == SCHEMA_VERSION and review["kind"] == REVIEW_KIND, "unsupported semantic sensitive review")
    _require(review["pack_sha256"] == file_ref(pack_path)["sha256"], "review belongs to another semantic sensitive pack")
    _require(review["source_srt_sha256"] == pack["source_srt"]["sha256"], "review source SRT hash mismatch")
    _require(review["reviewer_type"] == "model", "semantic sensitive review must be performed by a model")
    _require(isinstance(review["reviewer_model"], str) and review["reviewer_model"].strip(), "reviewer_model required")
    _require(review["full_scan_completed"] is True, "full semantic sensitive SRT scan is required")
    _require(
        type(review["scanned_cue_count"]) is int
        and review["scanned_cue_count"] == pack["cue_count"],
        "semantic sensitive scanned_cue_count must equal pack cue_count",
    )
    _require(isinstance(review["decisions"], list), "semantic sensitive decisions must be a list")

    cues = parse_srt_strict(source_srt)
    by_number = {cue.number: cue for cue in cues}
    _require(len(by_number) == len(cues), "duplicate cue numbers")
    required = set(pack["required_decision_cue_numbers"])
    decisions: dict[int, dict[str, Any]] = {}

    decision_fields = {
        "cue_number",
        "timing",
        "expected_text",
        "action",
        "mask_terms",
        "reason",
        "confidence",
    }
    for row in review["decisions"]:
        _require(isinstance(row, dict) and set(row) == decision_fields, "invalid semantic sensitive decision fields")
        number = row["cue_number"]
        _require(type(number) is int and number in by_number and number not in decisions, "unknown or duplicate semantic sensitive cue")
        cue = by_number[number]
        _require(row["timing"] == _timing(cue), "semantic sensitive decision timing mismatch")
        _require(row["expected_text"] == cue.text, "semantic sensitive decision expected_text mismatch")
        action = row["action"]
        _require(action in {"keep", "mask", "review"}, "invalid semantic sensitive action")
        _require(isinstance(row["reason"], str) and row["reason"].strip(), "semantic sensitive decision reason required")
        _require(row["confidence"] in {"low", "medium", "high"}, "invalid semantic sensitive confidence")
        _require(isinstance(row["mask_terms"], list), "mask_terms must be a list")

        if action == "mask":
            _require(row["confidence"] == "high", "MASK requires high confidence")
            _materialize_mask(cue.text, row["mask_terms"])
        else:
            _require(not row["mask_terms"], "KEEP/REVIEW cannot carry mask_terms")
            if action == "keep":
                _require(row["confidence"] == "high", "KEEP on a required candidate requires high confidence")
        if number not in required:
            _require(action in {"mask", "review"}, "unhinted KEEP decisions are unnecessary and rejected")
        decisions[number] = row

    _require(required <= set(decisions), "incomplete semantic sensitive candidate decisions")
    return pack, review, source_srt, cues, decisions


def validate_semantic_sensitive_review(
    pack_path: Path,
    review_path: Path,
    *,
    expected_source_srt: Path | None = None,
    require_resolved: bool = True,
) -> dict[str, Any]:
    """Validate model review lineage for either lightweight or artifact-aware materialization."""

    pack, review, source_srt, cues, decisions = _validate_review(pack_path, review_path)
    if expected_source_srt is not None:
        _require(
            source_srt == expected_source_srt.resolve(),
            "semantic sensitive review belongs to another source SRT",
        )
    unresolved = sorted(
        number for number, row in decisions.items() if row["action"] == "review"
    )
    if require_resolved:
        _require(not unresolved, f"semantic sensitive review unresolved for cues: {unresolved}")
    return {
        "pack": pack,
        "review": review,
        "source_srt": source_srt,
        "cues": cues,
        "decisions": decisions,
        "unresolved": unresolved,
    }


def finalize_semantic_sensitive_review(
    pack_path: Path,
    review_path: Path,
    *,
    out_srt: Path,
    out_audit: Path,
) -> dict[str, Any]:
    """Apply only model-approved masks; unresolved REVIEW entries fail closed."""

    validated = validate_semantic_sensitive_review(pack_path, review_path)
    pack = validated["pack"]
    review = validated["review"]
    source_srt = validated["source_srt"]
    cues = validated["cues"]
    decisions = validated["decisions"]

    out_srt = out_srt.resolve()
    out_audit = out_audit.resolve()
    _require(out_srt != source_srt, "semantic sensitive final must not overwrite source SRT")
    _require(not out_srt.exists() and not out_audit.exists(), "semantic sensitive outputs are create-only")
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    out_audit.parent.mkdir(parents=True, exist_ok=True)

    final_cues: list[Cue] = []
    changes: list[dict[str, Any]] = []
    masked_term_count = 0
    for cue in cues:
        row = decisions.get(cue.number)
        text = cue.text
        if row and row["action"] == "mask":
            text, masks = _materialize_mask(cue.text, row["mask_terms"])
            masked_term_count += len(masks)
            changes.append(
                {
                    "cue_number": cue.number,
                    "timing": _timing(cue),
                    "before": cue.text,
                    "after": text,
                    "reason": row["reason"],
                    "confidence": row["confidence"],
                    "masks": masks,
                }
            )
        final_cues.append(Cue(cue.number, cue.start_ms, cue.end_ms, text))

    blocks = [
        f"{cue.number}\n{_timing(cue)}\n{cue.text}"
        for cue in final_cues
    ]
    out_srt.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")

    reparsed = parse_srt_strict(out_srt)
    _require(
        [(c.number, c.start_ms, c.end_ms) for c in reparsed]
        == [(c.number, c.start_ms, c.end_ms) for c in cues],
        "semantic sensitive final changed cue topology/timing",
    )

    audit = {
        "schema_version": SCHEMA_VERSION,
        "kind": AUDIT_KIND,
        "pack": file_ref(pack_path),
        "review": file_ref(review_path),
        "source_srt": file_ref(source_srt),
        "final_srt": file_ref(out_srt),
        "reviewer_type": review["reviewer_type"],
        "reviewer_model": review["reviewer_model"],
        "full_scan_completed": True,
        "scanned_cue_count": review["scanned_cue_count"],
        "cue_count": len(cues),
        "required_candidate_count": len(pack["required_decision_cue_numbers"]),
        "decision_count": len(decisions),
        "masked_cue_count": len(changes),
        "masked_term_count": masked_term_count,
        "unresolved_review_count": 0,
        "timeline_unchanged": True,
        "cue_count_unchanged": True,
        "mask_only_display_changes": True,
        "semantic_sensitive_review_required": False,
        "formal_safe_final_ready": True,
        "changes": changes,
    }
    out_audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit
