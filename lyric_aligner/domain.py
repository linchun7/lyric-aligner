"""Typed v4 domain objects shared by asset, alignment and QA stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TrackAsset:
    track_id: str
    artist: str
    title: str
    version_id: str
    source_audio_path: str
    source_audio_sha256: str
    canonical_lyric_path: str
    canonical_lyric_sha256: str
    language: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrackOccurrence:
    track_id: str
    occurrence_id: str
    ordinal: int
    nominal_start_ms: int
    middle_cut: str = "false"
    language_profile: str = "auto"
    active_intervals: tuple[tuple[int, int], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.middle_cut not in {"false", "true", "unknown"}:
            raise ValueError("middle_cut must be false, true, or unknown")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["active_intervals"] = [list(value) for value in self.active_intervals]
        return payload
