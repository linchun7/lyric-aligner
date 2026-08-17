"""Validated single-source asset bindings for every v4 pipeline stage.

No downstream stage should rediscover a lyric file, source recording, or
same-timestamp canonical original after asset resolution.  This module turns
``track_assets.json`` into immutable per-occurrence bindings and revalidates the
semantic canonical-selection hash.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lyric_aligner.assets.resolver import canonical_selection_sha256
from lyric_aligner.contracts.artifacts import sha256_file


class AssetBindingError(ValueError):
    """Raised when a resolved asset payload is internally inconsistent."""


@dataclass(frozen=True)
class CanonicalOriginal:
    timestamp_ms: int
    alternative_index: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedAssetBinding:
    ordinal: int
    occurrence_id: str
    track_id: str
    artist: str
    title: str
    version_id: str
    nominal_start_ms: int
    middle_cut: str
    language_profile: str
    source_audio_path: str
    source_audio_sha256: str
    canonical_lyric_path: str
    canonical_lyric_sha256: str
    canonical_selection_sha256: str
    canonical_originals: tuple[CanonicalOriginal, ...]

    @property
    def original_index_by_timestamp(self) -> dict[int, int]:
        return {
            item.timestamp_ms: item.alternative_index
            for item in self.canonical_originals
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["canonical_originals"] = [
            item.to_dict() for item in self.canonical_originals
        ]
        return payload


def _required(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AssetBindingError(f"missing {label}")
    return text


def _canonical_originals(resolution: dict) -> tuple[CanonicalOriginal, ...]:
    selection = list(resolution.get("canonical_selection", []))
    originals = tuple(
        CanonicalOriginal(
            timestamp_ms=int(item["timestamp_ms"]),
            alternative_index=int(item["alternative_index"]),
            text=str(item.get("text", "")),
        )
        for item in selection
    )
    if not originals:
        raise AssetBindingError("canonical selection is empty")
    if any(
        right.timestamp_ms <= left.timestamp_ms
        for left, right in zip(originals, originals[1:])
    ):
        raise AssetBindingError(
            "canonical selection timestamps must be strictly increasing"
        )
    return originals


def bindings_from_payload(
    payload: dict,
    *,
    verify_files: bool = False,
) -> list[ResolvedAssetBinding]:
    """Validate a schema-1.1 asset payload and return per-occurrence bindings."""

    if str(payload.get("schema_version")) != "1.1":
        raise AssetBindingError(
            "resolved asset bindings require schema_version=1.1; rerun v4_resolve_assets"
        )
    if payload.get("status") != "resolved":
        raise AssetBindingError("track assets are not in resolved status")

    assets: dict[str, dict] = {}
    for asset in payload.get("assets", []):
        track_id = _required(asset.get("track_id"), "asset track_id")
        if track_id in assets:
            raise AssetBindingError(f"duplicate TrackAsset {track_id}")
        assets[track_id] = asset

    resolutions: dict[str, dict] = {}
    for resolution in payload.get("resolution", []):
        track_id = _required(resolution.get("track_id"), "resolution track_id")
        if track_id in resolutions:
            raise AssetBindingError(f"duplicate resolution for TrackAsset {track_id}")
        resolutions[track_id] = resolution

    bindings: list[ResolvedAssetBinding] = []
    seen_ordinals: set[int] = set()
    seen_occurrences: set[str] = set()
    for occurrence in payload.get("occurrences", []):
        ordinal = int(occurrence["ordinal"])
        occurrence_id = _required(
            occurrence.get("occurrence_id"), "occurrence_id"
        )
        track_id = _required(occurrence.get("track_id"), "occurrence track_id")
        if ordinal in seen_ordinals:
            raise AssetBindingError(f"duplicate occurrence ordinal {ordinal}")
        if occurrence_id in seen_occurrences:
            raise AssetBindingError(f"duplicate occurrence_id {occurrence_id}")
        seen_ordinals.add(ordinal)
        seen_occurrences.add(occurrence_id)

        asset = assets.get(track_id)
        resolution = resolutions.get(track_id)
        if asset is None or resolution is None:
            raise AssetBindingError(
                f"occurrence {occurrence_id} has no complete TrackAsset resolution"
            )

        originals = _canonical_originals(resolution)
        selection_payload = [item.to_dict() for item in originals]
        selection_hash = canonical_selection_sha256(selection_payload)
        recorded_selection_hash = _required(
            asset.get("canonical_selection_sha256"),
            "canonical_selection_sha256",
        )
        if selection_hash != recorded_selection_hash:
            raise AssetBindingError(
                f"canonical selection hash mismatch for TrackAsset {track_id}"
            )
        resolution_hash = str(
            resolution.get("canonical_selection_sha256") or ""
        ).strip()
        if resolution_hash and resolution_hash != selection_hash:
            raise AssetBindingError(
                f"resolution canonical selection hash mismatch for TrackAsset {track_id}"
            )

        source_path = Path(
            _required(asset.get("source_audio_path"), "source_audio_path")
        )
        lyric_path = Path(
            _required(asset.get("canonical_lyric_path"), "canonical_lyric_path")
        )
        source_hash = _required(
            asset.get("source_audio_sha256"), "source_audio_sha256"
        )
        lyric_hash = _required(
            asset.get("canonical_lyric_sha256"), "canonical_lyric_sha256"
        )
        if verify_files:
            if not source_path.is_file() or sha256_file(source_path) != source_hash:
                raise AssetBindingError(
                    f"source audio changed after asset resolution: {source_path}"
                )
            if not lyric_path.is_file() or sha256_file(lyric_path) != lyric_hash:
                raise AssetBindingError(
                    f"canonical lyric file changed after asset resolution: {lyric_path}"
                )

        middle_cut = str(occurrence.get("middle_cut", "false"))
        if middle_cut not in {"false", "true", "unknown"}:
            raise AssetBindingError(
                f"invalid middle_cut={middle_cut!r} for occurrence {occurrence_id}"
            )

        bindings.append(
            ResolvedAssetBinding(
                ordinal=ordinal,
                occurrence_id=occurrence_id,
                track_id=track_id,
                artist=_required(asset.get("artist"), "asset artist"),
                title=_required(asset.get("title"), "asset title"),
                version_id=_required(asset.get("version_id"), "asset version_id"),
                nominal_start_ms=int(occurrence["nominal_start_ms"]),
                middle_cut=middle_cut,
                language_profile=str(
                    occurrence.get("language_profile")
                    or asset.get("language")
                    or "auto"
                ),
                source_audio_path=str(source_path),
                source_audio_sha256=source_hash,
                canonical_lyric_path=str(lyric_path),
                canonical_lyric_sha256=lyric_hash,
                canonical_selection_sha256=selection_hash,
                canonical_originals=originals,
            )
        )

    if not bindings:
        raise AssetBindingError("track asset payload contains no occurrences")
    return sorted(bindings, key=lambda item: item.ordinal)


def binding_by_ordinal(
    payload: dict,
    *,
    verify_files: bool = False,
) -> dict[int, ResolvedAssetBinding]:
    return {
        item.ordinal: item
        for item in bindings_from_payload(payload, verify_files=verify_files)
    }
