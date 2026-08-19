"""Deterministic canonical-lyric text repair with immutable SRT timing."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_UTF8_BOM = b"\xef\xbb\xbf"
_LRC_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_QRC_LINE_TAG = re.compile(r"^\[(\d+),(\d+)\]")
_QRC_TOKEN_TIME = re.compile(r"\(\d+,\d+\)")
_ENHANCED_TIME_TAG = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_META_TAG = re.compile(r"^\[[A-Za-z][A-Za-z0-9_-]*:.*\]$")
_META_TEXT = re.compile(
    r"^(?:作词|作曲|编曲|词\s*[:：]|曲\s*[:：]|制作人|混音|母带|发行|出品|op\s*[:：]|sp\s*[:：])",
    re.IGNORECASE,
)
_SRT_TIMING = re.compile(
    r"^\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*"
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$"
)
_DECORATIVE = frozenset("♪♫♬♩★☆")


@dataclass(frozen=True)
class CanonicalLine:
    ordinal: int
    source: str
    text: str
    normalized: str


@dataclass(frozen=True)
class SubtitleCue:
    ordinal: int
    number: str
    timing: str
    text: str
    normalized: str
    raw_block_index: int


@dataclass(frozen=True)
class AlignmentOp:
    kind: str
    cue_start: int
    cue_end: int
    canonical_start: int
    canonical_end: int
    score: float = 0.0


@dataclass(frozen=True)
class MatchDecision:
    cue_ordinal: int
    canonical_ordinal: int | None
    score: float
    action: str
    reason: str
    cue_span: tuple[int, int] | None = None
    canonical_span: tuple[int, int] | None = None
    source_text: str = ""
    canonical_text: str = ""
    output_text: str = ""
    edit_operations: tuple[str, ...] = ()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_utf8(path: Path) -> tuple[str, bool]:
    payload = path.read_bytes()
    had_bom = payload.startswith(_UTF8_BOM)
    try:
        return payload.decode("utf-8-sig"), had_bom
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8") from exc


def _is_layout_char(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] in {"P", "Z", "C"} or char in _DECORATIVE


def _normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if not _is_layout_char(char))


def _clean_lyric_text(value: str) -> str:
    value = _ENHANCED_TIME_TAG.sub("", value)
    value = _QRC_TOKEN_TIME.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def _lrc_timestamp_ms(match: re.Match[str]) -> int:
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3) or "0"
    milliseconds = int((fraction + "000")[:3])
    return ((minutes * 60) + seconds) * 1000 + milliseconds


def parse_canonical_files(paths: Iterable[Path]) -> list[CanonicalLine]:
    """Parse canonical occurrences in file order and timed order within each file."""
    lines: list[CanonicalLine] = []
    for path in paths:
        text, _ = _read_utf8(path)
        entries: list[tuple[int | None, int, str, str]] = []
        sequence = 0
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or _META_TAG.match(stripped) or _META_TEXT.match(stripped):
                continue
            timestamps = list(_LRC_TIME_TAG.finditer(stripped))
            qrc_match = _QRC_LINE_TAG.match(stripped)
            if timestamps:
                body = _LRC_TIME_TAG.sub("", stripped)
                occurrence_times = [_lrc_timestamp_ms(match) for match in timestamps]
            elif qrc_match:
                body = _QRC_LINE_TAG.sub("", stripped, count=1)
                occurrence_times = [int(qrc_match.group(1))]
            elif stripped.startswith("[") and "]" in stripped:
                continue
            else:
                body = stripped
                occurrence_times = [None]
            cleaned = _clean_lyric_text(body)
            normalized = _normalize_for_match(cleaned)
            if not normalized:
                continue
            for timestamp_ms in occurrence_times:
                entries.append((timestamp_ms, sequence, cleaned, normalized))
                sequence += 1
        if entries and all(timestamp is not None for timestamp, _, _, _ in entries):
            entries.sort(key=lambda item: (int(item[0]), item[1]))
        for _, _, cleaned, normalized in entries:
            lines.append(
                CanonicalLine(
                    ordinal=len(lines),
                    source=path.name,
                    text=cleaned,
                    normalized=normalized,
                )
            )
    if not lines:
        raise ValueError("no canonical lyric lines were parsed")
    return lines


def _split_srt_blocks(text: str) -> list[str]:
    return re.split(r"((?:\r?\n){2,})", text)


def parse_srt_text(text: str) -> tuple[list[str], list[SubtitleCue]]:
    parts = _split_srt_blocks(text)
    cues: list[SubtitleCue] = []
    for index in range(0, len(parts), 2):
        block = parts[index]
        if not block.strip():
            continue
        rows = block.splitlines()
        if len(rows) < 3 or not _SRT_TIMING.match(rows[1]):
            raise ValueError(f"invalid SRT cue block near {block[:80]!r}")
        cue_text = "\n".join(rows[2:])
        normalized = _normalize_for_match(cue_text)
        if not normalized:
            raise ValueError(f"SRT cue {rows[0].strip()!r} has empty text")
        cues.append(
            SubtitleCue(
                ordinal=len(cues),
                number=rows[0],
                timing=rows[1],
                text=cue_text,
                normalized=normalized,
                raw_block_index=index,
            )
        )
    if not cues:
        raise ValueError("no SRT cues were parsed")
    return parts, cues


def read_srt(path: Path) -> tuple[str, bool, list[str], list[SubtitleCue]]:
    text, had_bom = _read_utf8(path)
    parts, cues = parse_srt_text(text)
    return text, had_bom, parts, cues


def _pair_score(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    ratio = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
    length_ratio = min(len(left), len(right)) / max(len(left), len(right))
    return ratio * (0.85 + 0.15 * length_ratio)


def _canonical_span_allowed(
    canonical: Sequence[CanonicalLine],
    start: int,
    end: int,
) -> bool:
    return end - start <= 1 or len({line.source for line in canonical[start:end]}) == 1


def _span_score_allowed(
    left: str,
    right: str,
    score: float,
    cue_count: int,
    canonical_count: int,
) -> bool:
    if cue_count == 1 and canonical_count == 1:
        return True
    if not left or not right:
        return False
    length_ratio = min(len(left), len(right)) / max(len(left), len(right))
    span_size = max(cue_count, canonical_count)
    if span_size <= 2:
        return score >= 0.82 and length_ratio >= 0.72
    return score >= 0.94 and length_ratio >= 0.90


def _unique_exact_anchors(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
) -> list[tuple[int, int]]:
    """Return a longest monotonic chain of unique exact normalized anchors."""
    cue_counts = Counter(cue.normalized for cue in cues)
    canonical_counts = Counter(line.normalized for line in canonical)
    canonical_index = {
        line.normalized: index
        for index, line in enumerate(canonical)
        if canonical_counts[line.normalized] == 1
    }
    candidates = [
        (index, canonical_index[cue.normalized])
        for index, cue in enumerate(cues)
        if len(cue.normalized) >= 4
        and cue_counts[cue.normalized] == 1
        and cue.normalized in canonical_index
    ]
    if not candidates:
        return []
    lengths = [1] * len(candidates)
    previous: list[int | None] = [None] * len(candidates)
    for i in range(len(candidates)):
        for j in range(i):
            if candidates[j][1] < candidates[i][1] and lengths[j] + 1 > lengths[i]:
                lengths[i] = lengths[j] + 1
                previous[i] = j
    current = max(range(len(candidates)), key=lambda index: lengths[index])
    result: list[tuple[int, int]] = []
    while current is not None:
        result.append(candidates[current])
        current = previous[current]
    result.reverse()
    return result


def _align_region(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    *,
    cue_offset: int,
    canonical_offset: int,
    gap_penalty: float,
    max_span: int,
    band: int | None = None,
) -> list[AlignmentOp]:
    n = len(cues)
    m = len(canonical)
    if n == 0:
        return [
            AlignmentOp(
                "canonical_gap",
                cue_offset,
                cue_offset,
                canonical_offset + j,
                canonical_offset + j + 1,
            )
            for j in range(m)
        ]
    if m == 0:
        return [
            AlignmentOp(
                "cue_gap",
                cue_offset + i,
                cue_offset + i + 1,
                canonical_offset,
                canonical_offset,
            )
            for i in range(n)
        ]

    width = band if band is not None else max(48, abs(n - m) + 24)
    width = min(max(n, m), max(1, width))
    negative = float("-inf")
    rows: list[dict[int, float]] = [{} for _ in range(n + 1)]
    back: list[dict[int, AlignmentOp]] = [{} for _ in range(n + 1)]
    rows[0][0] = 0.0

    for i in range(n + 1):
        center = int(round(i * m / n))
        j_min = 0 if i == 0 else max(0, center - width)
        j_max = m if i == n else min(m, center + width)
        for j in range(j_min, j_max + 1):
            if i == 0 and j == 0:
                continue
            best = negative
            best_op: AlignmentOp | None = None
            if i > 0 and j in rows[i - 1]:
                candidate = rows[i - 1][j] - gap_penalty
                if candidate > best:
                    best = candidate
                    best_op = AlignmentOp(
                        "cue_gap",
                        cue_offset + i - 1,
                        cue_offset + i,
                        canonical_offset + j,
                        canonical_offset + j,
                    )
            if j > 0 and j - 1 in rows[i]:
                candidate = rows[i][j - 1] - gap_penalty
                if candidate > best:
                    best = candidate
                    best_op = AlignmentOp(
                        "canonical_gap",
                        cue_offset + i,
                        cue_offset + i,
                        canonical_offset + j - 1,
                        canonical_offset + j,
                    )
            for cue_count in range(1, max_span + 1):
                prev_i = i - cue_count
                if prev_i < 0:
                    break
                left = "".join(cue.normalized for cue in cues[prev_i:i])
                for canonical_count in range(1, max_span + 1):
                    prev_j = j - canonical_count
                    if prev_j < 0:
                        break
                    if prev_j not in rows[prev_i]:
                        continue
                    if not _canonical_span_allowed(canonical, prev_j, j):
                        continue
                    right = "".join(
                        line.normalized for line in canonical[prev_j:j]
                    )
                    score = _pair_score(left, right)
                    if not _span_score_allowed(
                        left,
                        right,
                        score,
                        cue_count,
                        canonical_count,
                    ):
                        continue
                    span_penalty = 0.02 * (
                        (cue_count - 1) + (canonical_count - 1)
                    )
                    candidate = (
                        rows[prev_i][prev_j]
                        + score * max(cue_count, canonical_count)
                        - span_penalty
                    )
                    if candidate > best:
                        best = candidate
                        best_op = AlignmentOp(
                            "match",
                            cue_offset + prev_i,
                            cue_offset + i,
                            canonical_offset + prev_j,
                            canonical_offset + j,
                            score,
                        )
            if best_op is not None:
                rows[i][j] = best
                back[i][j] = best_op

    if m not in rows[n]:
        if width < max(n, m):
            return _align_region(
                cues,
                canonical,
                cue_offset=cue_offset,
                canonical_offset=canonical_offset,
                gap_penalty=gap_penalty,
                max_span=max_span,
                band=min(max(n, m), width * 2),
            )
        raise ValueError("text alignment failed")

    result: list[AlignmentOp] = []
    i = n
    j = m
    while i or j:
        op = back[i].get(j)
        if op is None:
            raise ValueError("text alignment backtrace is incomplete")
        result.append(op)
        i = op.cue_start - cue_offset
        j = op.canonical_start - canonical_offset
    result.reverse()
    return result


def align_spans(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    *,
    gap_penalty: float = 0.48,
    max_span: int = 4,
    band: int | None = None,
) -> list[AlignmentOp]:
    """Anchor exact lines, then run bounded local span alignment between anchors."""
    if not 1 <= max_span <= 4:
        raise ValueError("max_span must be between 1 and 4")
    anchors = _unique_exact_anchors(cues, canonical)
    operations: list[AlignmentOp] = []
    cue_cursor = 0
    canonical_cursor = 0
    for cue_index, canonical_index in anchors:
        operations.extend(
            _align_region(
                cues[cue_cursor:cue_index],
                canonical[canonical_cursor:canonical_index],
                cue_offset=cue_cursor,
                canonical_offset=canonical_cursor,
                gap_penalty=gap_penalty,
                max_span=max_span,
                band=band,
            )
        )
        operations.append(
            AlignmentOp(
                "match",
                cue_index,
                cue_index + 1,
                canonical_index,
                canonical_index + 1,
                1.0,
            )
        )
        cue_cursor = cue_index + 1
        canonical_cursor = canonical_index + 1
    operations.extend(
        _align_region(
            cues[cue_cursor:],
            canonical[canonical_cursor:],
            cue_offset=cue_cursor,
            canonical_offset=canonical_cursor,
            gap_penalty=gap_penalty,
            max_span=max_span,
            band=band,
        )
    )
    return operations


def align_monotonic(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    *,
    gap_penalty: float = 0.48,
    band: int | None = None,
) -> list[tuple[int, int, float]]:
    """Compatibility 1:1 monotonic alignment used by older callers/tests."""
    return [
        (op.cue_start, op.canonical_start, op.score)
        for op in align_spans(
            cues,
            canonical,
            gap_penalty=gap_penalty,
            max_span=1,
            band=band,
        )
        if op.kind == "match"
    ]


def _content_characters(value: str) -> list[str]:
    return [char for char in value if not _is_layout_char(char)]


def _edit_script(
    source: Sequence[str],
    target: Sequence[str],
) -> list[tuple[str, str | None, str | None]]:
    n = len(source)
    m = len(target)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + (source[i - 1] != target[j - 1]),
            )
    result: list[tuple[str, str | None, str | None]] = []
    i = n
    j = m
    while i or j:
        if (
            i
            and j
            and source[i - 1] == target[j - 1]
            and dp[i][j] == dp[i - 1][j - 1]
        ):
            result.append(("equal", source[i - 1], target[j - 1]))
            i -= 1
            j -= 1
        elif i and j and dp[i][j] == dp[i - 1][j - 1] + 1:
            result.append(("replace", source[i - 1], target[j - 1]))
            i -= 1
            j -= 1
        elif j and dp[i][j] == dp[i][j - 1] + 1:
            result.append(("insert", None, target[j - 1]))
            j -= 1
        elif i and dp[i][j] == dp[i - 1][j] + 1:
            result.append(("delete", source[i - 1], None))
            i -= 1
        else:
            raise AssertionError("edit backtrace failed")
    result.reverse()
    return result


def _assign_targets(
    cue_texts: Sequence[str],
    canonical_text: str,
) -> list[str]:
    source: list[str] = []
    owners: list[int] = []
    for owner, text in enumerate(cue_texts):
        for char in _content_characters(text):
            source.append(char)
            owners.append(owner)
    assigned: list[list[str]] = [[] for _ in cue_texts]
    source_index = 0
    for kind, _, target_char in _edit_script(
        source,
        _content_characters(canonical_text),
    ):
        if kind in {"equal", "replace"}:
            if target_char is not None:
                assigned[owners[source_index]].append(target_char)
            source_index += 1
        elif kind == "delete":
            source_index += 1
        elif kind == "insert":
            owner = (
                owners[source_index]
                if source_index < len(owners)
                else (owners[-1] if owners else 0)
            )
            if target_char is not None:
                assigned[owner].append(target_char)
    return ["".join(chars) for chars in assigned]


def _render_preserving_layout(
    original: str,
    target_content: str,
) -> tuple[str, tuple[str, ...]]:
    script = _edit_script(
        _content_characters(original),
        list(target_content),
    )
    insertions: dict[int, list[str]] = {}
    consumed: dict[int, str | None] = {}
    source_index = 0
    operations: list[str] = []
    for kind, _, target_char in script:
        operations.append(kind)
        if kind == "insert":
            if target_char is not None:
                insertions.setdefault(source_index, []).append(target_char)
        else:
            consumed[source_index] = (
                target_char if kind in {"equal", "replace"} else None
            )
            source_index += 1
    output: list[str] = []
    content_index = 0
    for char in original:
        if _is_layout_char(char):
            output.append(char)
            continue
        output.extend(insertions.get(content_index, []))
        replacement = consumed.get(content_index)
        if replacement is not None:
            output.append(replacement)
        content_index += 1
    output.extend(insertions.get(content_index, []))
    return "".join(output), tuple(operations)


def _safe_auto_match(
    source: str,
    target: str,
    score: float,
    threshold: float,
) -> bool:
    if score < threshold or not source or not target:
        return False
    length_ratio = min(len(source), len(target)) / max(len(source), len(target))
    if length_ratio < 0.60:
        return False
    if min(len(source), len(target)) <= 3 and score < max(threshold, 0.84):
        return False
    return True


def _has_ambiguous_alternative(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    op: AlignmentOp,
    threshold: float,
    *,
    radius: int = 3,
    max_span: int = 4,
) -> bool:
    source = "".join(
        cue.normalized for cue in cues[op.cue_start : op.cue_end]
    )
    chosen = "".join(
        line.normalized
        for line in canonical[op.canonical_start : op.canonical_end]
    )
    minimum = max(threshold, op.score - 0.06)
    start_min = max(0, op.canonical_start - radius)
    start_max = min(len(canonical), op.canonical_end + radius)
    for start in range(start_min, start_max):
        for count in range(1, max_span + 1):
            end = start + count
            if end > len(canonical):
                break
            if start == op.canonical_start and end == op.canonical_end:
                continue
            if not _canonical_span_allowed(canonical, start, end):
                continue
            candidate = "".join(
                line.normalized for line in canonical[start:end]
            )
            if candidate == chosen:
                continue
            if _pair_score(source, candidate) >= minimum:
                return True
    return False


def _gap_guarded(
    operations: Sequence[AlignmentOp],
    index: int,
    score: float,
) -> bool:
    if score >= 0.96:
        return False
    return any(
        0 <= neighbor < len(operations)
        and operations[neighbor].kind != "match"
        for neighbor in (index - 1, index + 1)
    )


def build_repair_plan_v2(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    *,
    auto_threshold: float = 0.72,
    operations: Sequence[AlignmentOp] | None = None,
) -> tuple[dict[int, str], list[MatchDecision], list[AlignmentOp]]:
    if not 0.5 <= auto_threshold <= 1.0:
        raise ValueError("auto_threshold must be between 0.5 and 1.0")
    ops = list(operations) if operations is not None else align_spans(cues, canonical)
    replacements: dict[int, str] = {}
    decisions: list[MatchDecision] = []

    for index, op in enumerate(ops):
        if op.kind == "canonical_gap":
            continue
        if op.kind == "cue_gap":
            cue = cues[op.cue_start]
            decisions.append(
                MatchDecision(
                    cue_ordinal=cue.ordinal,
                    canonical_ordinal=None,
                    score=0.0,
                    action="review",
                    reason="unmatched_subtitle_cue",
                    cue_span=(op.cue_start, op.cue_end),
                    canonical_span=(op.canonical_start, op.canonical_end),
                    source_text=cue.text,
                    output_text=cue.text,
                )
            )
            continue

        cue_group = cues[op.cue_start : op.cue_end]
        line_group = canonical[op.canonical_start : op.canonical_end]
        source_normalized = "".join(cue.normalized for cue in cue_group)
        target_normalized = "".join(line.normalized for line in line_group)
        target_text = "".join(line.text for line in line_group)

        reason = ""
        if not _safe_auto_match(
            source_normalized,
            target_normalized,
            op.score,
            auto_threshold,
        ):
            reason = "low_or_structurally_unsafe_similarity"
        elif _gap_guarded(ops, index, op.score):
            reason = "adjacent_alignment_gap_requires_review"
        elif _has_ambiguous_alternative(cues, canonical, op, auto_threshold):
            reason = "ambiguous_nearby_canonical_match"

        targets: list[str] = []
        if not reason:
            targets = _assign_targets(
                [cue.text for cue in cue_group],
                target_text,
            )
            if len(cue_group) > 1 and any(not target for target in targets):
                reason = "segmentation_would_empty_existing_cue"

        if reason:
            for cue in cue_group:
                decisions.append(
                    MatchDecision(
                        cue_ordinal=cue.ordinal,
                        canonical_ordinal=op.canonical_start,
                        score=op.score,
                        action="review",
                        reason=reason,
                        cue_span=(op.cue_start, op.cue_end),
                        canonical_span=(op.canonical_start, op.canonical_end),
                        source_text=cue.text,
                        canonical_text=target_text,
                        output_text=cue.text,
                    )
                )
            continue

        for cue, target in zip(cue_group, targets):
            output_text, edit_operations = _render_preserving_layout(
                cue.text,
                target,
            )
            if output_text == cue.text:
                action = "unchanged"
                decision_reason = "canonical_content_matches_source_segmentation"
            else:
                action = "replace"
                decision_reason = "high_confidence_span_preserving_match"
                replacements[cue.ordinal] = output_text
            decisions.append(
                MatchDecision(
                    cue_ordinal=cue.ordinal,
                    canonical_ordinal=op.canonical_start,
                    score=op.score,
                    action=action,
                    reason=decision_reason,
                    cue_span=(op.cue_start, op.cue_end),
                    canonical_span=(op.canonical_start, op.canonical_end),
                    source_text=cue.text,
                    canonical_text=target_text,
                    output_text=output_text,
                    edit_operations=edit_operations,
                )
            )

    decisions.sort(key=lambda item: item.cue_ordinal)
    return replacements, decisions, ops


def build_repair_plan(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    *,
    auto_threshold: float = 0.72,
    alignment_pairs: Sequence[tuple[int, int, float]] | None = None,
) -> tuple[dict[int, str], list[MatchDecision]]:
    operations = None
    if alignment_pairs is not None:
        operations = [
            AlignmentOp("match", cue, cue + 1, line, line + 1, score)
            for cue, line, score in alignment_pairs
        ]
    replacements, decisions, _ = build_repair_plan_v2(
        cues,
        canonical,
        auto_threshold=auto_threshold,
        operations=operations,
    )
    return replacements, decisions


def render_repaired_srt(
    parts: Sequence[str],
    cues: Sequence[SubtitleCue],
    replacements: dict[int, str],
) -> str:
    output = list(parts)
    for cue in cues:
        replacement = replacements.get(cue.ordinal)
        if replacement is None:
            continue
        original_block = parts[cue.raw_block_index]
        line_ending = "\r\n" if "\r\n" in original_block else "\n"
        trailing = line_ending if original_block.endswith(line_ending) else ""
        rows = original_block.splitlines()
        replacement_rows = replacement.split("\n")
        output[cue.raw_block_index] = (
            line_ending.join((rows[0], rows[1], *replacement_rows)) + trailing
        )
    return "".join(output)


def timeline_signature(cues: Sequence[SubtitleCue]) -> list[tuple[str, str]]:
    return [(cue.number, cue.timing) for cue in cues]


def repair_srt_text(
    source_text: str,
    canonical: Sequence[CanonicalLine],
    *,
    auto_threshold: float = 0.72,
) -> tuple[str, dict[str, object]]:
    parts, cues = parse_srt_text(source_text)
    replacements, decisions, operations = build_repair_plan_v2(
        cues,
        canonical,
        auto_threshold=auto_threshold,
    )
    matched_canonical: set[int] = set()
    for op in operations:
        if op.kind == "match":
            matched_canonical.update(range(op.canonical_start, op.canonical_end))
    unmatched_canonical = [
        line for line in canonical if line.ordinal not in matched_canonical
    ]

    rendered = render_repaired_srt(parts, cues, replacements)
    _, output_cues = parse_srt_text(rendered)
    if (
        len(cues) != len(output_cues)
        or timeline_signature(cues) != timeline_signature(output_cues)
    ):
        raise AssertionError("text-only repair changed SRT cue count, numbering or timing")

    cue_review_count = sum(item.action == "review" for item in decisions)
    review_count = cue_review_count + len(unmatched_canonical)
    edit_counts = {key: 0 for key in ("equal", "replace", "insert", "delete")}
    for item in decisions:
        for operation in item.edit_operations:
            if operation in edit_counts:
                edit_counts[operation] += 1

    report = {
        "schema_version": "2.0",
        "mode": "text_only_preserve_timeline",
        "status": "ready" if review_count == 0 else "review_required",
        "cue_count": len(cues),
        "canonical_line_count": len(canonical),
        "replacement_count": sum(item.action == "replace" for item in decisions),
        "unchanged_count": sum(item.action == "unchanged" for item in decisions),
        "cue_review_count": cue_review_count,
        "unmatched_canonical_count": len(unmatched_canonical),
        "review_count": review_count,
        "timeline_unchanged": True,
        "cue_count_unchanged": True,
        "formatting_policy": (
            "preserve_source_timing_numbering_punctuation_spacing_and_line_breaks;"
            "allow_safe_content_insert_delete_replace_and_bounded_segmentation_spans"
        ),
        "span_match_count": sum(op.kind == "match" for op in operations),
        "segmentation_span_count": sum(
            op.kind == "match"
            and (
                op.cue_end - op.cue_start != 1
                or op.canonical_end - op.canonical_start != 1
            )
            for op in operations
        ),
        "edit_counts": edit_counts,
        "decisions": [
            {
                "cue_ordinal": item.cue_ordinal,
                "canonical_ordinal": item.canonical_ordinal,
                "cue_span": list(item.cue_span) if item.cue_span else None,
                "canonical_span": (
                    list(item.canonical_span) if item.canonical_span else None
                ),
                "score": round(item.score, 6),
                "action": item.action,
                "reason": item.reason,
                "source_text": item.source_text,
                "canonical_text": item.canonical_text,
                "output_text": item.output_text,
                "edit_operations": list(item.edit_operations),
            }
            for item in decisions
        ],
        "unmatched_canonical": [
            {
                "canonical_ordinal": line.ordinal,
                "source": line.source,
                "text": line.text,
                "reason": "canonical_line_missing_from_subtitle_alignment",
            }
            for line in unmatched_canonical
        ],
    }
    return rendered, report


def write_repair_outputs(
    source_srt: Path,
    canonical_paths: Sequence[Path],
    output_srt: Path,
    *,
    report_path: Path | None = None,
    auto_threshold: float = 0.72,
) -> dict[str, object]:
    source_text, had_bom = _read_utf8(source_srt)
    canonical = parse_canonical_files(canonical_paths)
    rendered, report = repair_srt_text(
        source_text,
        canonical,
        auto_threshold=auto_threshold,
    )
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    payload = rendered.encode("utf-8")
    if had_bom:
        payload = _UTF8_BOM + payload
    output_srt.write_bytes(payload)
    report["inputs"] = {
        "source_srt_sha256": _sha256_file(source_srt),
        "canonical_lyrics": [
            {"name": path.name, "sha256": _sha256_file(path)}
            for path in canonical_paths
        ],
    }
    report["output_srt_sha256"] = _sha256_file(output_srt)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report
