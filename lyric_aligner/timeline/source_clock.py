"""Explicit canonical-lyric clock to bound source-audio clock transforms.

Canonical lyric timestamps may describe the original recording while the bound
``source_audio`` is a tempo-adjusted derivative. Source-to-Mix alignment operates
in the bound source-audio clock, so canonical timestamps must first be converted
into that clock. This module validates deterministic clock transforms and their
asset/occurrence identity; it does not estimate rate/offset or grant authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

SOURCE_CLOCK_POLICY_ID = "bpm-fixed-source-clock-1.0"
SOURCE_CLOCK_MAP_SCHEMA_VERSION = "source-clock-map-1.0"


class SourceClockError(ValueError):
    """Raised when an explicit source-clock transform is malformed."""


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SourceClockError(f"source clock {label} is required")
    return text


def _sha(value: Any, label: str) -> str:
    digest = str(value or "").lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise SourceClockError(f"source clock {label} must be lowercase SHA-256")
    return digest


@dataclass(frozen=True)
class SourceClockTransform:
    rate: float
    offset_ms: float
    provenance_sha256: str
    policy_id: str = SOURCE_CLOCK_POLICY_ID

    def __post_init__(self) -> None:
        if not math.isfinite(self.rate) or self.rate <= 0:
            raise SourceClockError("source clock rate must be finite and positive")
        if not math.isfinite(self.offset_ms):
            raise SourceClockError("source clock offset_ms must be finite")
        _sha(self.provenance_sha256, "provenance_sha256")
        if str(self.policy_id) != SOURCE_CLOCK_POLICY_ID:
            raise SourceClockError(
                f"source clock policy_id must be {SOURCE_CLOCK_POLICY_ID}"
            )

    def map_ms(self, value_ms: int) -> int:
        if not isinstance(value_ms, int) or isinstance(value_ms, bool) or value_ms < 0:
            raise SourceClockError("canonical clock value must be a non-negative integer ms")
        mapped = self.offset_ms + self.rate * value_ms
        if not math.isfinite(mapped) or mapped < 0:
            raise SourceClockError("source clock transform produced invalid time")
        return int(round(mapped))

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "rate": self.rate,
            "offset_ms": self.offset_ms,
            "provenance_sha256": self.provenance_sha256,
        }


def source_clock_from_mapping(value: Mapping[str, Any] | None) -> SourceClockTransform | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SourceClockError("source clock transform must be an object or null")
    try:
        rate = float(value["rate"])
        offset_ms = float(value["offset_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceClockError("source clock transform requires numeric rate/offset_ms") from exc
    return SourceClockTransform(
        rate=rate,
        offset_ms=offset_ms,
        provenance_sha256=str(value.get("provenance_sha256") or ""),
        policy_id=str(value.get("policy_id") or SOURCE_CLOCK_POLICY_ID),
    )


def source_clock_map_rows(payload: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    """Validate one explicit source-clock map and index it by occurrence ordinal."""
    if not isinstance(payload, Mapping):
        raise SourceClockError("source clock map must be an object")
    if str(payload.get("schema_version") or "") != SOURCE_CLOCK_MAP_SCHEMA_VERSION:
        raise SourceClockError(
            f"source clock map schema_version must be {SOURCE_CLOCK_MAP_SCHEMA_VERSION}"
        )
    if str(payload.get("policy_id") or "") != SOURCE_CLOCK_POLICY_ID:
        raise SourceClockError(
            f"source clock map policy_id must be {SOURCE_CLOCK_POLICY_ID}"
        )
    if payload.get("automatic_timing_change_allowed") is not True:
        raise SourceClockError("source clock map lacks explicit production timing authority")
    rows = payload.get("tracks")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise SourceClockError("source clock map tracks must be a non-empty list")
    indexed: dict[int, Mapping[str, Any]] = {}
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise SourceClockError(f"source clock track {position} must be an object")
        ordinal = row.get("ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise SourceClockError(f"source clock track {position} has invalid ordinal")
        if ordinal in indexed:
            raise SourceClockError(f"source clock map duplicates ordinal {ordinal}")
        _required_text(row.get("occurrence_id"), "occurrence_id")
        _required_text(row.get("track_id"), "track_id")
        _sha(row.get("source_audio_sha256"), "source_audio_sha256")
        _sha(row.get("canonical_lyric_sha256"), "canonical_lyric_sha256")
        _sha(row.get("canonical_selection_sha256"), "canonical_selection_sha256")
        source_clock_from_mapping(row)
        indexed[ordinal] = row
    return indexed


def source_clock_for_binding(
    binding: Any,
    rows_by_ordinal: Mapping[int, Mapping[str, Any]] | None,
) -> SourceClockTransform | None:
    """Return a transform only after exact binding identity replay."""
    if rows_by_ordinal is None:
        return None
    row = rows_by_ordinal.get(int(binding.ordinal))
    if row is None:
        return None
    checks = {
        "occurrence_id": str(binding.occurrence_id),
        "track_id": str(binding.track_id),
        "source_audio_sha256": str(binding.source_audio_sha256),
        "canonical_lyric_sha256": str(binding.canonical_lyric_sha256),
        "canonical_selection_sha256": str(binding.canonical_selection_sha256),
    }
    for key, expected in checks.items():
        if str(row.get(key) or "") != expected:
            raise SourceClockError(
                f"source clock ordinal {binding.ordinal} {key} differs from bound asset"
            )
    return source_clock_from_mapping(row)
