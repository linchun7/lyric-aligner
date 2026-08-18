"""Text-only canonical lyric repair for subtitle files with immutable timing."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_UTF8_BOM = b"\xef\xbb\xbf"
_LRC_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_QRC_LINE_TAG = re.compile(r"^\[\d+,\d+\]")
_ENHANCED_TIME_TAG = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_META_TAG = re.compile(r"^\[[A-Za-z][A-Za-z0-9_-]*:.*\]$")
_META_TEXT = re.compile(
    r"^(?:作词|作曲|编曲|词\s*:|曲\s*:|制作人|混音|母带|发行|出品|op\s*:|sp\s*:)",
    re.IGNORECASE,
)
_SRT_TIMING = re.compile(
    r"^\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*"
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$"
)


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
class MatchDecision:
    cue_ordinal: int
    canonical_ordinal: int | None
    score: float
    action: str
    reason: str


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


def _normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    result: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        if category[0] in {"P", "Z"} or category == "Cc":
            continue
        result.append(char)
    return "".join(result)


def _clean_lyric_text(value: str) -> str:
    value = _ENHANCED_TIME_TAG.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_canonical_files(paths: Iterable[Path]) -> list[CanonicalLine]:
    lines: list[CanonicalLine] = []
    for path in paths:
        text, _ = _read_utf8(path)
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or _META_TAG.match(stripped) or _META_TEXT.match(stripped):
                continue
            timestamps = list(_LRC_TIME_TAG.finditer(stripped))
            repeat = len(timestamps)
            if repeat:
                body = _LRC_TIME_TAG.sub("", stripped)
            elif _QRC_LINE_TAG.match(stripped):
                repeat = 1
                body = _QRC_LINE_TAG.sub("", stripped, count=1)
            elif stripped.startswith("[") and "]" in stripped:
                # Unknown bracketed metadata must not become canonical lyric text.
                continue
            else:
                repeat = 1
                body = stripped
            cleaned = _clean_lyric_text(body)
            normalized = _normalize_for_match(cleaned)
            if not normalized:
                continue
            for _ in range(repeat):
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
    # Preserve every separator exactly so only cue text is replaced.
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


def align_monotonic(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    *,
    gap_penalty: float = 0.48,
    band: int | None = None,
) -> list[tuple[int, int, float]]:
    """Globally align cue and canonical line sequences with bounded fuzzy DP."""

    n = len(cues)
    m = len(canonical)
    if n == 0 or m == 0:
        return []
    width = band if band is not None else max(48, abs(n - m) + 24)
    neg = float("-inf")
    rows: list[dict[int, float]] = [{0: 0.0}]
    back: list[dict[int, tuple[int, int, str, float]]] = [{}]

    for i in range(0, n + 1):
        if i == 0:
            center = 0
        else:
            center = int(round(i * m / n))
        j_min = 0 if i == 0 else max(0, center - width)
        j_max = m if i == n else min(m, center + width)
        if i > 0:
            rows.append({})
            back.append({})
        row = rows[i]
        for j in range(j_min, j_max + 1):
            if i == 0 and j == 0:
                continue
            best = neg
            choice: tuple[int, int, str, float] | None = None
            if i > 0 and j in rows[i - 1]:
                candidate_score = rows[i - 1][j] - gap_penalty
                if candidate_score > best:
                    best = candidate_score
                    choice = (i - 1, j, "cue_gap", 0.0)
            if j > 0 and (j - 1) in row:
                candidate_score = row[j - 1] - gap_penalty
                if candidate_score > best:
                    best = candidate_score
                    choice = (i, j - 1, "canonical_gap", 0.0)
            if i > 0 and j > 0 and (j - 1) in rows[i - 1]:
                score = _pair_score(cues[i - 1].normalized, canonical[j - 1].normalized)
                candidate_score = rows[i - 1][j - 1] + score
                if candidate_score > best:
                    best = candidate_score
                    choice = (i - 1, j - 1, "pair", score)
            if choice is not None:
                row[j] = best
                back[i][j] = choice

    if m not in rows[n]:
        raise ValueError("text alignment band is too narrow for this subtitle/lyric pair")
    pairs: list[tuple[int, int, float]] = []
    i, j = n, m
    while i or j:
        choice = back[i].get(j)
        if choice is None:
            raise ValueError("text alignment backtrace is incomplete")
        prev_i, prev_j, operation, score = choice
        if operation == "pair":
            pairs.append((i - 1, j - 1, score))
        i, j = prev_i, prev_j
    pairs.reverse()
    return pairs


def _is_safe_auto_pair(cue: SubtitleCue, line: CanonicalLine, score: float, threshold: float) -> bool:
    if score < threshold:
        return False
    a = len(cue.normalized)
    b = len(line.normalized)
    if not a or not b:
        return False
    length_ratio = min(a, b) / max(a, b)
    if length_ratio < 0.72:
        return False
    # Very short lines need stronger evidence because one wrong character is large.
    if min(a, b) <= 3 and score < max(threshold, 0.82):
        return False
    return True


def build_repair_plan(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    *,
    auto_threshold: float = 0.72,
) -> tuple[dict[int, str], list[MatchDecision]]:
    if not 0.5 <= auto_threshold <= 1.0:
        raise ValueError("auto_threshold must be between 0.5 and 1.0")
    pairs = align_monotonic(cues, canonical)
    by_cue = {cue_index: (line_index, score) for cue_index, line_index, score in pairs}
    replacements: dict[int, str] = {}
    decisions: list[MatchDecision] = []

    for cue in cues:
        pair = by_cue.get(cue.ordinal)
        if pair is None:
            decisions.append(
                MatchDecision(cue.ordinal, None, 0.0, "review", "unmatched_subtitle_cue")
            )
            continue
        line_index, score = pair
        line = canonical[line_index]
        if not _is_safe_auto_pair(cue, line, score, auto_threshold):
            decisions.append(
                MatchDecision(cue.ordinal, line_index, score, "review", "low_or_structurally_unsafe_similarity")
            )
            continue
        if cue.text == line.text:
            decisions.append(MatchDecision(cue.ordinal, line_index, score, "unchanged", "already_canonical"))
            continue
        replacements[cue.ordinal] = line.text
        decisions.append(MatchDecision(cue.ordinal, line_index, score, "replace", "high_confidence_monotonic_match"))

    return replacements, decisions


def render_repaired_srt(parts: Sequence[str], cues: Sequence[SubtitleCue], replacements: dict[int, str]) -> str:
    output = list(parts)
    for cue in cues:
        replacement = replacements.get(cue.ordinal)
        if replacement is None:
            continue
        rows = output[cue.raw_block_index].splitlines()
        # Index and timing lines are copied byte-for-byte at the Unicode-text level.
        output[cue.raw_block_index] = "\n".join((rows[0], rows[1], replacement))
        # Restore CRLF inside this block if that was how its header was represented.
        if "\r\n" in parts[cue.raw_block_index]:
            output[cue.raw_block_index] = output[cue.raw_block_index].replace("\n", "\r\n")
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
    replacements, decisions = build_repair_plan(cues, canonical, auto_threshold=auto_threshold)
    rendered = render_repaired_srt(parts, cues, replacements)
    _, output_cues = parse_srt_text(rendered)
    if timeline_signature(cues) != timeline_signature(output_cues):
        raise AssertionError("text-only repair changed SRT numbering or timing")
    status = "ready" if all(item.action != "review" for item in decisions) else "review_required"
    report = {
        "schema_version": "1.0",
        "mode": "text_only_preserve_timeline",
        "status": status,
        "cue_count": len(cues),
        "canonical_line_count": len(canonical),
        "replacement_count": sum(item.action == "replace" for item in decisions),
        "unchanged_count": sum(item.action == "unchanged" for item in decisions),
        "review_count": sum(item.action == "review" for item in decisions),
        "timeline_unchanged": True,
        "decisions": [
            {
                "cue_ordinal": item.cue_ordinal,
                "canonical_ordinal": item.canonical_ordinal,
                "score": round(item.score, 6),
                "action": item.action,
                "reason": item.reason,
            }
            for item in decisions
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
    rendered, report = repair_srt_text(source_text, canonical, auto_threshold=auto_threshold)
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
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
