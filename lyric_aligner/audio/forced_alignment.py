"""Backend-neutral adapters for singing/forced-alignment boundary evidence.

The sidecar aligners (for example SOFA and HuBERTFA) are deliberately isolated
from the main project environment.  They may emit Praat TextGrid or normalized
word intervals, but neither backend is allowed to mutate subtitle timing.  This
module converts their output into one conservative evidence contract.

Raw backend confidence is preserved for diagnostics only.  Automatic timing
authority is granted elsewhere only after a backend/model/language/start-or-end
scope passes human-gold calibration and independent evidence agrees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence


NONLEXICAL_TOKENS = frozenset(
    {
        "",
        "sp",
        "ap",
        "sil",
        "silence",
        "<eps>",
        "<blank>",
        "<sil>",
        "pau",
        "bnd",
        "ep",
        "br",
    }
)

PADDED_WINDOW_EDGE_GUARD_MS = 120
PADDED_WINDOW_MIN_EDITOR_DIVERGENCE_MS = 500


class ForcedAlignmentEvidenceError(ValueError):
    """Raised when an aligner output cannot be interpreted safely."""


@dataclass(frozen=True)
class AlignmentWord:
    start_s: float
    end_s: float
    text: str

    def validate(self) -> None:
        if self.start_s < 0 or self.end_s < 0 or self.end_s < self.start_s:
            raise ForcedAlignmentEvidenceError("alignment interval is invalid")
        if not isinstance(self.text, str):
            raise ForcedAlignmentEvidenceError("alignment interval text must be a string")

    @property
    def lexical(self) -> bool:
        return _normalize_token(self.text) not in NONLEXICAL_TOKENS


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().casefold()


def normalize_alignment_words(words: Iterable[Mapping[str, Any] | AlignmentWord]) -> list[AlignmentWord]:
    """Normalize and validate aligned word intervals in chronological order."""

    output: list[AlignmentWord] = []
    for raw in words:
        if isinstance(raw, AlignmentWord):
            word = raw
        elif isinstance(raw, Mapping):
            try:
                start = float(raw.get("start", raw.get("xmin")))
                end = float(raw.get("end", raw.get("xmax")))
            except (TypeError, ValueError) as exc:
                raise ForcedAlignmentEvidenceError("alignment interval has invalid timing") from exc
            word = AlignmentWord(start_s=start, end_s=end, text=str(raw.get("text") or ""))
        else:
            raise ForcedAlignmentEvidenceError("alignment interval must be an object")
        word.validate()
        output.append(word)

    if not output:
        raise ForcedAlignmentEvidenceError("alignment output contains no intervals")
    previous_start = -1.0
    for word in output:
        if word.start_s < previous_start:
            raise ForcedAlignmentEvidenceError("alignment intervals are not chronological")
        previous_start = word.start_s
    return output


_ITEM_RE = re.compile(r"^\s*item\s*\[\d+\]\s*:\s*$")
_INTERVAL_RE = re.compile(r"^\s*intervals\s*\[\d+\]\s*:\s*$")
_NAME_RE = re.compile(r'^\s*name\s*=\s*"(.*)"\s*$')
_XMIN_RE = re.compile(r"^\s*xmin\s*=\s*([-+0-9.eE]+)\s*$")
_XMAX_RE = re.compile(r"^\s*xmax\s*=\s*([-+0-9.eE]+)\s*$")
_TEXT_RE = re.compile(r'^\s*text\s*=\s*"(.*)"\s*$')


def parse_textgrid_words(text: str, *, tier_name: str = "words") -> list[AlignmentWord]:
    """Parse intervals from one named tier of a long-form Praat TextGrid.

    SOFA exports this ordinary long TextGrid representation.  The parser is
    intentionally narrow and fail-closed rather than guessing from unrelated
    tiers such as phones.
    """

    target = str(tier_name or "").strip()
    if not target:
        raise ForcedAlignmentEvidenceError("tier_name must be non-empty")

    items: list[dict[str, Any]] = []
    current_item: dict[str, Any] | None = None
    current_interval: dict[str, Any] | None = None

    def finish_interval() -> None:
        nonlocal current_interval
        if current_item is not None and current_interval is not None:
            if {"xmin", "xmax", "text"}.issubset(current_interval):
                current_item.setdefault("intervals", []).append(current_interval)
        current_interval = None

    def finish_item() -> None:
        nonlocal current_item
        finish_interval()
        if current_item is not None:
            items.append(current_item)
        current_item = None

    for line in str(text or "").splitlines():
        if _ITEM_RE.match(line):
            finish_item()
            current_item = {"intervals": []}
            continue
        if current_item is None:
            continue
        name_match = _NAME_RE.match(line)
        if name_match and current_interval is None:
            current_item["name"] = name_match.group(1)
            continue
        if _INTERVAL_RE.match(line):
            finish_interval()
            current_interval = {}
            continue
        if current_interval is None:
            continue
        xmin_match = _XMIN_RE.match(line)
        if xmin_match:
            current_interval["xmin"] = float(xmin_match.group(1))
            continue
        xmax_match = _XMAX_RE.match(line)
        if xmax_match:
            current_interval["xmax"] = float(xmax_match.group(1))
            continue
        text_match = _TEXT_RE.match(line)
        if text_match:
            current_interval["text"] = text_match.group(1).replace('""', '"')

    finish_item()
    matching = [item for item in items if str(item.get("name") or "") == target]
    if len(matching) != 1:
        raise ForcedAlignmentEvidenceError(
            f"TextGrid must contain exactly one tier named {target!r}; found {len(matching)}"
        )
    intervals = matching[0].get("intervals") or []
    return normalize_alignment_words(intervals)


def load_textgrid_words(path: str | Path, *, tier_name: str = "words") -> list[AlignmentWord]:
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_textgrid_words(text, tier_name=tier_name)


def lexical_words(words: Iterable[Mapping[str, Any] | AlignmentWord]) -> list[AlignmentWord]:
    return [word for word in normalize_alignment_words(words) if word.lexical]


def find_lexical_subsequence(
    words: Iterable[Mapping[str, Any] | AlignmentWord],
    target_tokens: Sequence[str],
) -> list[AlignmentWord]:
    """Find one unique lexical token sequence while ignoring SP/AP/silence.

    This fixes the concrete HuBERTFA failure where ``wen rou de ma SP AP hei
    ban`` did not match the intended lexical sequence.
    """

    lexical = lexical_words(words)
    target = [_normalize_token(token) for token in target_tokens if _normalize_token(token)]
    if not target:
        raise ForcedAlignmentEvidenceError("target token sequence is empty")
    sequence = [_normalize_token(word.text) for word in lexical]
    starts = [
        index
        for index in range(len(sequence) - len(target) + 1)
        if sequence[index : index + len(target)] == target
    ]
    if len(starts) != 1:
        raise ForcedAlignmentEvidenceError(
            f"target lexical sequence must match uniquely; found {len(starts)} matches"
        )
    start = starts[0]
    return lexical[start : start + len(target)]


def alignment_boundary_ms(
    words: Iterable[Mapping[str, Any] | AlignmentWord],
    *,
    window_start_ms: int,
    boundary_kind: Literal["start", "end"],
    target_tokens: Sequence[str] | None = None,
) -> int:
    """Return an absolute final-mix boundary from aligned lexical intervals."""

    if boundary_kind not in {"start", "end"}:
        raise ForcedAlignmentEvidenceError("boundary_kind must be start or end")
    try:
        offset_ms = int(window_start_ms)
    except (TypeError, ValueError) as exc:
        raise ForcedAlignmentEvidenceError("window_start_ms is invalid") from exc
    if offset_ms < 0:
        raise ForcedAlignmentEvidenceError("window_start_ms must be nonnegative")

    selected = (
        find_lexical_subsequence(words, target_tokens)
        if target_tokens is not None
        else lexical_words(words)
    )
    if not selected:
        raise ForcedAlignmentEvidenceError("alignment contains no lexical intervals")
    local_seconds = selected[0].start_s if boundary_kind == "start" else selected[-1].end_s
    return int(round(offset_ms + local_seconds * 1000.0))


def alignment_full_sequence_boundary_ms(
    words: Iterable[Mapping[str, Any] | AlignmentWord],
    *,
    window_start_ms: int,
    segment_tokens: Sequence[Sequence[str]],
    boundary_kind: Literal["start", "end", "internal"],
    boundary_index: int | None = None,
) -> int:
    """Return one boundary only after exact full lexical-sequence verification.

    This is the production-safe primitive for forced aligners.  The backend must
    align exactly the lexical units supplied by the caller after explicitly
    non-lexical SP/AP/BND-style intervals are removed.  No substring search or
    fuzzy token recovery is allowed.  ``internal`` returns the onset of the first
    lexical unit in the right-hand canonical segment.
    """

    try:
        offset_ms = int(window_start_ms)
    except (TypeError, ValueError) as exc:
        raise ForcedAlignmentEvidenceError("window_start_ms is invalid") from exc
    if offset_ms < 0:
        raise ForcedAlignmentEvidenceError("window_start_ms must be nonnegative")
    if boundary_kind not in {"start", "end", "internal"}:
        raise ForcedAlignmentEvidenceError("boundary_kind must be start, end, or internal")
    if not segment_tokens:
        raise ForcedAlignmentEvidenceError("alignment needs at least one canonical segment")

    normalized_segments: list[list[str]] = []
    for tokens in segment_tokens:
        normalized = [
            _normalize_token(token)
            for token in tokens
            if _normalize_token(token)
            and _normalize_token(token) not in NONLEXICAL_TOKENS
        ]
        if not normalized:
            raise ForcedAlignmentEvidenceError("canonical segment token sequence is empty")
        normalized_segments.append(normalized)

    lexical = lexical_words(words)
    observed = [_normalize_token(word.text) for word in lexical]
    expected = [token for segment in normalized_segments for token in segment]
    if observed != expected:
        raise ForcedAlignmentEvidenceError(
            "aligned lexical sequence does not exactly match canonical segment tokens"
        )
    if not lexical:
        raise ForcedAlignmentEvidenceError("alignment contains no lexical intervals")

    if boundary_kind == "start":
        local_seconds = lexical[0].start_s
    elif boundary_kind == "end":
        local_seconds = lexical[-1].end_s
    else:
        if len(normalized_segments) < 2:
            raise ForcedAlignmentEvidenceError("internal alignment needs at least two segments")
        try:
            index = int(boundary_index) if boundary_index is not None else 0
        except (TypeError, ValueError) as exc:
            raise ForcedAlignmentEvidenceError("internal boundary_index is invalid") from exc
        if not 1 <= index < len(normalized_segments):
            raise ForcedAlignmentEvidenceError("internal boundary_index is outside segment range")
        right_token_index = sum(len(segment) for segment in normalized_segments[:index])
        if not 0 < right_token_index < len(lexical):
            raise ForcedAlignmentEvidenceError("internal lexical boundary index is invalid")
        local_seconds = lexical[right_token_index].start_s
    return int(round(offset_ms + local_seconds * 1000.0))


def alignment_internal_boundary_ms(
    words: Iterable[Mapping[str, Any] | AlignmentWord],
    *,
    window_start_ms: int,
    segment_tokens: Sequence[Sequence[str]],
    boundary_index: int,
) -> int:
    """Return one internal canonical-segment boundary from a full alignment.

    Internal lyric segmentation must not search for a short repeated phrase.
    The complete lexical output must equal the concatenation of all canonical
    segment token sequences. The selected boundary is then the onset of the
    first lexical token belonging to the right-hand segment.

    SP/AP/BND and other explicitly non-lexical intervals are ignored. Any
    insertion, deletion, unknown token, or sequence mismatch fails closed.
    """

    return alignment_full_sequence_boundary_ms(
        words,
        window_start_ms=window_start_ms,
        segment_tokens=segment_tokens,
        boundary_kind="internal",
        boundary_index=boundary_index,
    )


def padded_window_edge_clamp_reason(
    predicted_ms: int,
    *,
    mix_window_ms: Sequence[int],
    row_interval_ms: Sequence[int],
    boundary_kind: Literal["start", "end"],
    edge_guard_ms: int = PADDED_WINDOW_EDGE_GUARD_MS,
    min_editor_divergence_ms: int = PADDED_WINDOW_MIN_EDITOR_DIVERGENCE_MS,
) -> str | None:
    """Return a stable reason when an absolute prediction collapsed to padding.

    Final-mix forced aligners can absorb instrumental padding into the first or
    last lyric token.  This guard is intentionally narrow: a prediction is
    rejected only when it is both very close to the padded alignment-window
    edge and already far away from the editor boundary.  A cue whose editor
    boundary is itself near the beginning or end of the final mix is therefore
    not rejected merely for being near an edge.
    """

    if boundary_kind not in {"start", "end"}:
        raise ForcedAlignmentEvidenceError("edge-clamp detection supports start/end only")
    if len(mix_window_ms) != 2 or len(row_interval_ms) != 2:
        raise ForcedAlignmentEvidenceError("edge-clamp detection requires two-point intervals")
    try:
        predicted = int(predicted_ms)
        window_start, window_end = [int(value) for value in mix_window_ms]
        row_start, row_end = [int(value) for value in row_interval_ms]
        edge_guard = int(edge_guard_ms)
        min_divergence = int(min_editor_divergence_ms)
    except (TypeError, ValueError) as exc:
        raise ForcedAlignmentEvidenceError("edge-clamp timing inputs are invalid") from exc
    if edge_guard < 0 or min_divergence < 0:
        raise ForcedAlignmentEvidenceError("edge-clamp thresholds must be nonnegative")
    if not 0 <= window_start <= row_start <= row_end <= window_end:
        raise ForcedAlignmentEvidenceError("edge-clamp intervals are not nested/ordered")
    if not window_start <= predicted <= window_end:
        raise ForcedAlignmentEvidenceError("edge-clamp prediction lies outside the mix window")

    if boundary_kind == "start":
        if predicted <= window_start + edge_guard and row_start - predicted >= min_divergence:
            return "padded_window_edge_clamped_start"
        return None
    if predicted >= window_end - edge_guard and predicted - row_end >= min_divergence:
        return "padded_window_edge_clamped_end"
    return None


def build_forced_alignment_evidence_point(
    *,
    words: Iterable[Mapping[str, Any] | AlignmentWord],
    window_start_ms: int,
    boundary_kind: Literal["start", "end"],
    family: str,
    correlation_group: str,
    backend_id: str,
    backend_version: str,
    model_id: str,
    model_revision: str,
    language: str,
    target_tokens: Sequence[str] | None = None,
    raw_confidence: float | None = None,
    diagnostic_uncertainty_ms: int = 0,
    evidence_sha256: str = "",
) -> dict[str, Any]:
    """Create non-authoritative final-mix forced-alignment evidence.

    Calibration is intentionally false here.  A persisted, verified calibration
    artifact must be attached later by the refinement evidence builder before the
    point can participate in automatic mutation.
    """

    required = {
        "family": family,
        "correlation_group": correlation_group,
        "backend_id": backend_id,
        "backend_version": backend_version,
        "model_id": model_id,
        "model_revision": model_revision,
        "language": language,
    }
    if any(not str(value or "").strip() for value in required.values()):
        raise ForcedAlignmentEvidenceError("forced-alignment evidence metadata is incomplete")
    if diagnostic_uncertainty_ms < 0:
        raise ForcedAlignmentEvidenceError("diagnostic_uncertainty_ms must be nonnegative")
    confidence = 0.0 if raw_confidence is None else float(raw_confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ForcedAlignmentEvidenceError("raw_confidence must be within [0,1]")

    boundary_ms = alignment_boundary_ms(
        words,
        window_start_ms=window_start_ms,
        boundary_kind=boundary_kind,
        target_tokens=target_tokens,
    )
    return {
        "family": str(family),
        "correlation_group": str(correlation_group),
        "boundary_ms": boundary_ms,
        "confidence": confidence,
        "raw_confidence": None if raw_confidence is None else confidence,
        "uncertainty_ms": int(diagnostic_uncertainty_ms),
        "backend_id": str(backend_id),
        "backend_version": str(backend_version),
        "model_id": str(model_id),
        "model_revision": str(model_revision),
        "audio_basis": "final_mix",
        "language": str(language),
        "evidence_sha256": str(evidence_sha256 or ""),
        "calibration_passed": False,
        "calibration_sha256": "",
    }
