"""Text-only canonical lyric repair with immutable SRT timing."""
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
_QRC_LINE_TAG = re.compile(r"^\[(\d+),(\d+)\]")
_QRC_TOKEN_TIME = re.compile(r"\(\d+,\d+\)")
_ENHANCED_TIME_TAG = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_META_TAG = re.compile(r"^\[[A-Za-z][A-Za-z0-9_-]*:.*\]$")
_META_TEXT = re.compile(r"^(?:作词|作曲|编曲|词\s*[:：]|曲\s*[:：]|制作人|混音|母带|发行|出品|op\s*[:：]|sp\s*[:：])", re.I)
_SRT_TIMING = re.compile(r"^\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$")
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


def _ignorable(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] in {"P", "Z", "C"} or char in _DECORATIVE


def _normalize_for_match(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in value if not _ignorable(char))


def _clean_lyric_text(value: str) -> str:
    value = _ENHANCED_TIME_TAG.sub("", value)
    value = _QRC_TOKEN_TIME.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def _lrc_timestamp_ms(match: re.Match[str]) -> int:
    fraction = match.group(3) or "0"
    return ((int(match.group(1)) * 60 + int(match.group(2))) * 1000 + int((fraction + "000")[:3]))


def parse_canonical_files(paths: Iterable[Path]) -> list[CanonicalLine]:
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
                times = [_lrc_timestamp_ms(match) for match in timestamps]
            elif qrc_match:
                body = _QRC_LINE_TAG.sub("", stripped, count=1)
                times = [int(qrc_match.group(1))]
            elif stripped.startswith("[") and "]" in stripped:
                continue
            else:
                body, times = stripped, [None]
            cleaned = _clean_lyric_text(body)
            normalized = _normalize_for_match(cleaned)
            if not normalized:
                continue
            for timestamp in times:
                entries.append((timestamp, sequence, cleaned, normalized))
                sequence += 1
        if entries and all(item[0] is not None for item in entries):
            entries.sort(key=lambda item: (int(item[0]), item[1]))
        for _, _, cleaned, normalized in entries:
            lines.append(CanonicalLine(len(lines), path.name, cleaned, normalized))
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
        cues.append(SubtitleCue(len(cues), rows[0], rows[1], cue_text, normalized, index))
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


def _canonical_span_allowed(canonical: Sequence[CanonicalLine], start: int, end: int) -> bool:
    return end - start <= 1 or len({line.source for line in canonical[start:end]}) == 1


def align_spans(cues: Sequence[SubtitleCue], canonical: Sequence[CanonicalLine], *, gap_penalty: float = 0.48, max_span: int = 2) -> list[AlignmentOp]:
    """Align 1↔1, 1↔2, 2↔1 and 2↔2 while preserving cue structure."""
    if max_span not in {1, 2}:
        raise ValueError("max_span must be 1 or 2")
    n, m = len(cues), len(canonical)
    neg = float("-inf")
    dp = [[neg] * (m + 1) for _ in range(n + 1)]
    back: list[list[AlignmentOp | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            base = dp[i][j]
            if base == neg:
                continue
            if i < n and base - gap_penalty > dp[i + 1][j]:
                dp[i + 1][j] = base - gap_penalty
                back[i + 1][j] = AlignmentOp("cue_gap", i, i + 1, j, j)
            if j < m and base - gap_penalty > dp[i][j + 1]:
                dp[i][j + 1] = base - gap_penalty
                back[i][j + 1] = AlignmentOp("canonical_gap", i, i, j, j + 1)
            for cue_count in range(1, max_span + 1):
                if i + cue_count > n:
                    break
                left = "".join(cue.normalized for cue in cues[i:i + cue_count])
                for canonical_count in range(1, max_span + 1):
                    if j + canonical_count > m:
                        break
                    if not _canonical_span_allowed(canonical, j, j + canonical_count):
                        continue
                    right = "".join(line.normalized for line in canonical[j:j + canonical_count])
                    pair_score = _pair_score(left, right)
                    if (cue_count > 1 or canonical_count > 1) and pair_score < 0.72:
                        continue
                    span_penalty = 0.02 * ((cue_count - 1) + (canonical_count - 1))
                    total = base + pair_score * max(cue_count, canonical_count) - span_penalty
                    if total > dp[i + cue_count][j + canonical_count]:
                        dp[i + cue_count][j + canonical_count] = total
                        back[i + cue_count][j + canonical_count] = AlignmentOp("match", i, i + cue_count, j, j + canonical_count, pair_score)
    if dp[n][m] == neg:
        raise ValueError("text alignment failed")
    result: list[AlignmentOp] = []
    i, j = n, m
    while i or j:
        op = back[i][j]
        if op is None:
            raise ValueError("text alignment backtrace is incomplete")
        result.append(op)
        i, j = op.cue_start, op.canonical_start
    result.reverse()
    return result


def align_monotonic(cues: Sequence[SubtitleCue], canonical: Sequence[CanonicalLine], *, gap_penalty: float = 0.48, band: int | None = None) -> list[tuple[int, int, float]]:
    del band
    return [(op.cue_start, op.canonical_start, op.score) for op in align_spans(cues, canonical, gap_penalty=gap_penalty) if op.kind == "match" and op.cue_end - op.cue_start == 1 and op.canonical_end - op.canonical_start == 1]


def _content(value: str) -> list[str]:
    return [char for char in value if not _ignorable(char)]


def _edit_script(source: Sequence[str], target: Sequence[str]) -> list[tuple[str, str | None, str | None]]:
    n, m = len(source), len(target)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (source[i - 1] != target[j - 1]))
    result: list[tuple[str, str | None, str | None]] = []
    i, j = n, m
    while i or j:
        if i and j and source[i - 1] == target[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            result.append(("equal", source[i - 1], target[j - 1])); i -= 1; j -= 1
        elif i and j and dp[i][j] == dp[i - 1][j - 1] + 1:
            result.append(("replace", source[i - 1], target[j - 1])); i -= 1; j -= 1
        elif j and dp[i][j] == dp[i][j - 1] + 1:
            result.append(("insert", None, target[j - 1])); j -= 1
        elif i and dp[i][j] == dp[i - 1][j] + 1:
            result.append(("delete", source[i - 1], None)); i -= 1
        else:
            raise AssertionError("edit backtrace failed")
    result.reverse()
    return result


def _assign_targets(cue_texts: Sequence[str], canonical_text: str) -> list[str]:
    source: list[str] = []
    owners: list[int] = []
    for owner, text in enumerate(cue_texts):
        for char in _content(text):
            source.append(char); owners.append(owner)
    assigned: list[list[str]] = [[] for _ in cue_texts]
    source_index = 0
    for kind, _, target_char in _edit_script(source, _content(canonical_text)):
        if kind in {"equal", "replace"}:
            if target_char is not None: assigned[owners[source_index]].append(target_char)
            source_index += 1
        elif kind == "delete":
            source_index += 1
        elif kind == "insert":
            owner = owners[source_index] if source_index < len(owners) else (owners[-1] if owners else 0)
            if target_char is not None: assigned[owner].append(target_char)
    return ["".join(chars) for chars in assigned]


def _render_preserving_layout(original: str, target_content: str) -> tuple[str, tuple[str, ...]]:
    script = _edit_script(_content(original), list(target_content))
    insertions: dict[int, list[str]] = {}
    consumed: dict[int, str | None] = {}
    source_index = 0
    operations: list[str] = []
    for kind, _, target_char in script:
        operations.append(kind)
        if kind == "insert":
            if target_char is not None: insertions.setdefault(source_index, []).append(target_char)
        else:
            consumed[source_index] = target_char if kind in {"equal", "replace"} else None
            source_index += 1
    out: list[str] = []
    content_index = 0
    for char in original:
        if _ignorable(char):
            out.append(char); continue
        out.extend(insertions.get(content_index, []))
        replacement = consumed.get(content_index)
        if replacement is not None: out.append(replacement)
        content_index += 1
    out.extend(insertions.get(content_index, []))
    return "".join(out), tuple(operations)


def _safe(source: str, target: str, score: float, threshold: float) -> bool:
    if score < threshold or not source or not target:
        return False
    ratio = min(len(source), len(target)) / max(len(source), len(target))
    if ratio < 0.60:
        return False
    return min(len(source), len(target)) > 3 or score >= max(threshold, 0.84)


def _ambiguous(cues: Sequence[SubtitleCue], canonical: Sequence[CanonicalLine], op: AlignmentOp, threshold: float) -> bool:
    source = "".join(c.normalized for c in cues[op.cue_start:op.cue_end])
    chosen = "".join(c.normalized for c in canonical[op.canonical_start:op.canonical_end])
    minimum = max(threshold, op.score - 0.06)
    for start in range(max(0, op.canonical_start - 3), min(len(canonical), op.canonical_end + 3)):
        for count in (1, 2):
            end = start + count
            if end > len(canonical) or (start == op.canonical_start and end == op.canonical_end) or not _canonical_span_allowed(canonical, start, end):
                continue
            candidate = "".join(c.normalized for c in canonical[start:end])
            if candidate != chosen and _pair_score(source, candidate) >= minimum:
                return True
    return False


def _gap_guarded(ops: Sequence[AlignmentOp], index: int, score: float) -> bool:
    if score >= 0.96:
        return False
    return any(0 <= n < len(ops) and ops[n].kind != "match" for n in (index - 1, index + 1))


def build_repair_plan_v2(cues: Sequence[SubtitleCue], canonical: Sequence[CanonicalLine], *, auto_threshold: float = 0.72, operations: Sequence[AlignmentOp] | None = None) -> tuple[dict[int, str], list[MatchDecision], list[AlignmentOp]]:
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
            decisions.append(MatchDecision(cue.ordinal, None, 0.0, "review", "unmatched_subtitle_cue", (op.cue_start, op.cue_end), (op.canonical_start, op.canonical_end), cue.text, "", cue.text))
            continue
        cue_group = cues[op.cue_start:op.cue_end]
        line_group = canonical[op.canonical_start:op.canonical_end]
        source_norm = "".join(c.normalized for c in cue_group)
        target_norm = "".join(c.normalized for c in line_group)
        target_text = "".join(c.text for c in line_group)
        reason = ""
        if not _safe(source_norm, target_norm, op.score, auto_threshold): reason = "low_or_structurally_unsafe_similarity"
        elif _gap_guarded(ops, index, op.score): reason = "adjacent_alignment_gap_requires_review"
        elif _ambiguous(cues, canonical, op, auto_threshold): reason = "ambiguous_nearby_canonical_match"
        if reason:
            for cue in cue_group:
                decisions.append(MatchDecision(cue.ordinal, op.canonical_start, op.score, "review", reason, (op.cue_start, op.cue_end), (op.canonical_start, op.canonical_end), cue.text, target_text, cue.text))
            continue
        targets = _assign_targets([cue.text for cue in cue_group], target_text)
        for cue, target in zip(cue_group, targets):
            output_text, edit_ops = _render_preserving_layout(cue.text, target)
            if output_text == cue.text:
                action, why = "unchanged", "canonical_content_matches_source_segmentation"
            else:
                action, why = "replace", "high_confidence_span_preserving_match"
                replacements[cue.ordinal] = output_text
            decisions.append(MatchDecision(cue.ordinal, op.canonical_start, op.score, action, why, (op.cue_start, op.cue_end), (op.canonical_start, op.canonical_end), cue.text, target_text, output_text, edit_ops))
    decisions.sort(key=lambda item: item.cue_ordinal)
    return replacements, decisions, ops


def build_repair_plan(cues: Sequence[SubtitleCue], canonical: Sequence[CanonicalLine], *, auto_threshold: float = 0.72, alignment_pairs: Sequence[tuple[int, int, float]] | None = None) -> tuple[dict[int, str], list[MatchDecision]]:
    operations = None if alignment_pairs is None else [AlignmentOp("match", c, c + 1, l, l + 1, s) for c, l, s in alignment_pairs]
    replacements, decisions, _ = build_repair_plan_v2(cues, canonical, auto_threshold=auto_threshold, operations=operations)
    return replacements, decisions


def render_repaired_srt(parts: Sequence[str], cues: Sequence[SubtitleCue], replacements: dict[int, str]) -> str:
    output = list(parts)
    for cue in cues:
        replacement = replacements.get(cue.ordinal)
        if replacement is None:
            continue
        original_block = parts[cue.raw_block_index]
        ending = "\r\n" if "\r\n" in original_block else "\n"
        trailing = ending if original_block.endswith(ending) else ""
        rows = original_block.splitlines()
        output[cue.raw_block_index] = ending.join((rows[0], rows[1], *replacement.split("\n"))) + trailing
    return "".join(output)


def timeline_signature(cues: Sequence[SubtitleCue]) -> list[tuple[str, str]]:
    return [(cue.number, cue.timing) for cue in cues]


def repair_srt_text(source_text: str, canonical: Sequence[CanonicalLine], *, auto_threshold: float = 0.72) -> tuple[str, dict[str, object]]:
    parts, cues = parse_srt_text(source_text)
    replacements, decisions, operations = build_repair_plan_v2(cues, canonical, auto_threshold=auto_threshold)
    matched: set[int] = set()
    for op in operations:
        if op.kind == "match": matched.update(range(op.canonical_start, op.canonical_end))
    unmatched = [line for line in canonical if line.ordinal not in matched]
    rendered = render_repaired_srt(parts, cues, replacements)
    _, output_cues = parse_srt_text(rendered)
    if timeline_signature(cues) != timeline_signature(output_cues) or len(cues) != len(output_cues):
        raise AssertionError("text-only repair changed SRT cue count, numbering or timing")
    cue_review = sum(item.action == "review" for item in decisions)
    review_count = cue_review + len(unmatched)
    edit_counts = {key: 0 for key in ("equal", "replace", "insert", "delete")}
    for item in decisions:
        for op in item.edit_operations:
            if op in edit_counts: edit_counts[op] += 1
    report = {
        "schema_version": "2.0", "mode": "text_only_preserve_timeline",
        "status": "ready" if review_count == 0 else "review_required",
        "cue_count": len(cues), "canonical_line_count": len(canonical),
        "replacement_count": sum(item.action == "replace" for item in decisions),
        "unchanged_count": sum(item.action == "unchanged" for item in decisions),
        "cue_review_count": cue_review, "unmatched_canonical_count": len(unmatched),
        "review_count": review_count, "timeline_unchanged": True, "cue_count_unchanged": True,
        "formatting_policy": "preserve_source_timing_numbering_punctuation_spacing_and_line_breaks;allow_safe_content_insert_delete_replace_and_1to2_2to1_2to2_segmentation",
        "span_match_count": sum(op.kind == "match" for op in operations),
        "segmentation_span_count": sum(op.kind == "match" and (op.cue_end - op.cue_start != 1 or op.canonical_end - op.canonical_start != 1) for op in operations),
        "edit_counts": edit_counts,
        "decisions": [{
            "cue_ordinal": item.cue_ordinal, "canonical_ordinal": item.canonical_ordinal,
            "cue_span": list(item.cue_span) if item.cue_span else None,
            "canonical_span": list(item.canonical_span) if item.canonical_span else None,
            "score": round(item.score, 6), "action": item.action, "reason": item.reason,
            "source_text": item.source_text, "canonical_text": item.canonical_text,
            "output_text": item.output_text, "edit_operations": list(item.edit_operations),
        } for item in decisions],
        "unmatched_canonical": [{"canonical_ordinal": line.ordinal, "source": line.source, "text": line.text, "reason": "canonical_line_missing_from_subtitle_alignment"} for line in unmatched],
    }
    return rendered, report


def write_repair_outputs(source_srt: Path, canonical_paths: Sequence[Path], output_srt: Path, *, report_path: Path | None = None, auto_threshold: float = 0.72) -> dict[str, object]:
    source_text, had_bom = _read_utf8(source_srt)
    canonical = parse_canonical_files(canonical_paths)
    rendered, report = repair_srt_text(source_text, canonical, auto_threshold=auto_threshold)
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    payload = rendered.encode("utf-8")
    if had_bom: payload = _UTF8_BOM + payload
    output_srt.write_bytes(payload)
    report["inputs"] = {"source_srt_sha256": _sha256_file(source_srt), "canonical_lyrics": [{"name": path.name, "sha256": _sha256_file(path)} for path in canonical_paths]}
    report["output_srt_sha256"] = _sha256_file(output_srt)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
