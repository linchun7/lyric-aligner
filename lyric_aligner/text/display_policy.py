"""Auditable display-only lyric policy for production subtitles.

Canonical lyrics remain the text/order evidence source.  This module only governs
what is shown to viewers after a production subtitle has already earned timing and
segmentation authority.  It supports three deliberately narrow operations:

* task-bound, identity-bound explicit display overrides reviewed by a model/human;
* deterministic masking of a small strong-profanity profile;
* optional shorten-only trimming of extreme line-LRC end holds whose source end is
  only the next lyric start, never an explicit vocal-end timestamp.

Text operations may not change cue timing. Timing presentation may only keep the
source start and shorten the source end; it may never extend a cue, move a start,
change cue ordering/ownership, or change canonical source identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DisplayPolicyError(ValueError):
    """Raised when a display policy is invalid or cannot be applied exactly."""


_SCHEMA_VERSION = "display-text-policy-1.0"
_MASK_PROFILE_NONE = "none"
_MASK_PROFILE_STRONG_PROFANITY_V1 = "strong_profanity_v1"
_ALLOWED_MASK_PROFILES = frozenset({_MASK_PROFILE_NONE, _MASK_PROFILE_STRONG_PROFANITY_V1})
_TIMING_MODE_TRIM_EXTREME_UNKNOWN_END_V1 = "trim_extreme_unknown_end_v1"

# Keep this list intentionally narrow.  Ambiguous/platform-sensitive words such as
# "sexy", "shot", "bullet", "kill", or "damn" require contextual review and are
# not changed automatically.
_STRONG_PROFANITY_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    r"motherfuck(?:er|ers|ing|in['’]|in|ed)?|"
    r"fuck(?:er|ers|ing|in['’]|in|ed|s)?|"
    r"bullshit(?:ting|s)?|"
    r"shit(?:ting|ty|s)?|"
    r"bitch(?:es|ing|y)?|"
    r"asshole(?:s)?|"
    r"cunt(?:s)?"
    r")(?![A-Za-z])",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class DisplayOverride:
    occurrence_id: str
    track_id: str
    canonical_line_index: int
    expected_text: str
    display_text: str
    reason: str
    reviewer: str

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.occurrence_id, self.track_id, self.canonical_line_index)


@dataclass(frozen=True)
class DisplayTimingPolicy:
    mode: str
    source_end_basis: frozenset[str]
    source_duration_at_least_ms: int
    max_display_hold_ms: int


@dataclass(frozen=True)
class DisplayPolicy:
    policy_id: str
    task_fingerprint_sha256: str
    mask_profile: str
    overrides: dict[tuple[str, str, int], DisplayOverride]
    reviewer_model: str
    timing_policy: DisplayTimingPolicy | None


@dataclass(frozen=True)
class DisplayTextResult:
    text: str
    changed: bool
    override_applied: bool
    sensitive_mask_count: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DisplayTimingResult:
    start_ms: int
    end_ms: int
    changed: bool
    reasons: tuple[str, ...]


def _required_text(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DisplayPolicyError(f"{context} requires non-empty {key}")
    return value


def _canonical_policy_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_timing_policy(payload: dict[str, Any]) -> DisplayTimingPolicy | None:
    raw = payload.get("timing_policy")
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise DisplayPolicyError("display policy timing_policy must be an object")
    mode = _required_text(raw, "mode", context="display timing policy")
    if mode != _TIMING_MODE_TRIM_EXTREME_UNKNOWN_END_V1:
        raise DisplayPolicyError(f"unsupported display timing mode: {mode!r}")
    raw_basis = raw.get("source_end_basis")
    if not isinstance(raw_basis, list) or not raw_basis:
        raise DisplayPolicyError("display timing policy requires source_end_basis list")
    basis: set[str] = set()
    for value in raw_basis:
        if not isinstance(value, str) or not value.strip():
            raise DisplayPolicyError(
                "display timing policy source_end_basis has invalid value"
            )
        basis.add(value.strip())
    if basis - {"next_line_start"}:
        raise DisplayPolicyError(
            "trim_extreme_unknown_end_v1 only permits source_end_basis=next_line_start"
        )
    threshold = raw.get("source_duration_at_least_ms")
    max_hold = raw.get("max_display_hold_ms")
    if type(threshold) is not int or threshold < 1000:
        raise DisplayPolicyError(
            "display timing policy has invalid source_duration_at_least_ms"
        )
    if type(max_hold) is not int or max_hold < 1000:
        raise DisplayPolicyError("display timing policy has invalid max_display_hold_ms")
    if max_hold >= threshold:
        raise DisplayPolicyError(
            "display timing max_display_hold_ms must be smaller than source-duration threshold"
        )
    return DisplayTimingPolicy(
        mode=mode,
        source_end_basis=frozenset(basis),
        source_duration_at_least_ms=threshold,
        max_display_hold_ms=max_hold,
    )


def load_display_policy(
    path: Path,
    *,
    expected_task_fingerprint: str,
) -> DisplayPolicy:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise DisplayPolicyError("display policy must contain a JSON object")
    if payload.get("schema_version") != _SCHEMA_VERSION:
        raise DisplayPolicyError(
            f"unsupported display policy schema: {payload.get('schema_version')!r}"
        )
    fingerprint = _required_text(
        payload,
        "task_fingerprint_sha256",
        context="display policy",
    )
    if fingerprint != expected_task_fingerprint:
        raise DisplayPolicyError("display policy belongs to another task fingerprint")

    mask_profile = str(payload.get("mask_profile") or _MASK_PROFILE_NONE).strip()
    if mask_profile not in _ALLOWED_MASK_PROFILES:
        raise DisplayPolicyError(f"unsupported display mask profile: {mask_profile!r}")

    model_review = payload.get("model_review") or {}
    if not isinstance(model_review, dict):
        raise DisplayPolicyError("display policy model_review must be an object")
    reviewer_model = str(model_review.get("reviewer_model") or "").strip()

    raw_overrides = payload.get("overrides") or []
    if not isinstance(raw_overrides, list):
        raise DisplayPolicyError("display policy overrides must be a list")
    if raw_overrides and not reviewer_model:
        raise DisplayPolicyError(
            "display policy with explicit overrides requires model_review.reviewer_model"
        )
    overrides: dict[tuple[str, str, int], DisplayOverride] = {}
    for position, row in enumerate(raw_overrides, start=1):
        context = f"display override {position}"
        if not isinstance(row, dict):
            raise DisplayPolicyError(f"{context} must be an object")
        occurrence_id = _required_text(row, "occurrence_id", context=context)
        track_id = _required_text(row, "track_id", context=context)
        index = row.get("canonical_line_index")
        if type(index) is not int or index < 0:
            raise DisplayPolicyError(f"{context} has invalid canonical_line_index")
        expected_text = _required_text(row, "expected_text", context=context)
        display_text = _required_text(row, "display_text", context=context)
        reason = _required_text(row, "reason", context=context)
        reviewer = _required_text(row, "reviewer", context=context)
        confidence = str(row.get("confidence") or "").strip().lower()
        if confidence != "high":
            raise DisplayPolicyError(
                f"{context} must be explicitly high-confidence before materialization"
            )
        override = DisplayOverride(
            occurrence_id=occurrence_id,
            track_id=track_id,
            canonical_line_index=index,
            expected_text=expected_text,
            display_text=display_text,
            reason=reason,
            reviewer=reviewer,
        )
        if override.key in overrides:
            raise DisplayPolicyError(f"duplicate display override identity: {override.key}")
        overrides[override.key] = override

    return DisplayPolicy(
        policy_id=_canonical_policy_id(payload),
        task_fingerprint_sha256=fingerprint,
        mask_profile=mask_profile,
        overrides=overrides,
        reviewer_model=reviewer_model,
        timing_policy=_load_timing_policy(payload),
    )


def mask_strong_profanity(text: str) -> tuple[str, int]:
    """Mask strong English profanity as first-letter + '*', preserving case."""

    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        word = match.group(0)
        return f"{word[0]}*"

    return _STRONG_PROFANITY_RE.sub(replace, text), count


def apply_display_policy(
    text: str,
    *,
    occurrence_id: str,
    track_id: str,
    canonical_line_index: int,
    policy: DisplayPolicy,
) -> DisplayTextResult:
    current = text
    reasons: list[str] = []
    override_applied = False
    key = (occurrence_id, track_id, canonical_line_index)
    override = policy.overrides.get(key)
    if override is not None:
        if current != override.expected_text:
            raise DisplayPolicyError(
                "display override expected_text mismatch for "
                f"{key}: expected {override.expected_text!r}, got {current!r}"
            )
        current = override.display_text
        override_applied = True
        reasons.append(f"model_override:{override.reason}")

    sensitive_mask_count = 0
    if policy.mask_profile == _MASK_PROFILE_STRONG_PROFANITY_V1:
        current, sensitive_mask_count = mask_strong_profanity(current)
        if sensitive_mask_count:
            reasons.append("strong_profanity_mask")

    return DisplayTextResult(
        text=current,
        changed=current != text,
        override_applied=override_applied,
        sensitive_mask_count=sensitive_mask_count,
        reasons=tuple(reasons),
    )


def apply_display_timing_policy(
    *,
    start_ms: int,
    end_ms: int,
    end_basis: str,
    policy: DisplayPolicy,
) -> DisplayTimingResult:
    if type(start_ms) is not int or type(end_ms) is not int or end_ms <= start_ms:
        raise DisplayPolicyError("display timing source cue has invalid start/end")
    timing = policy.timing_policy
    if timing is None:
        return DisplayTimingResult(
            start_ms=start_ms,
            end_ms=end_ms,
            changed=False,
            reasons=(),
        )
    source_duration = end_ms - start_ms
    if (
        end_basis in timing.source_end_basis
        and source_duration >= timing.source_duration_at_least_ms
    ):
        trimmed_end = start_ms + timing.max_display_hold_ms
        if trimmed_end < end_ms:
            return DisplayTimingResult(
                start_ms=start_ms,
                end_ms=trimmed_end,
                changed=True,
                reasons=("extreme_unknown_end_hold_trim",),
            )
    return DisplayTimingResult(
        start_ms=start_ms,
        end_ms=end_ms,
        changed=False,
        reasons=(),
    )
