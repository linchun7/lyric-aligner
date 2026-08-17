"""Fail-closed TrackAsset + TrackOccurrence resolver."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from lyric_aligner.assets.lyric_roles import inspect_lyric_roles
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.domain import TrackAsset, TrackOccurrence
from lyric_aligner.io.text import read_task_text

ASSET_SCHEMA_VERSION = "1.1"
AUDIO_SUFFIXES = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg"}


class AssetResolutionError(ValueError):
    """Raised when a production asset cannot be identified unambiguously."""


@dataclass(frozen=True)
class SongEntry:
    ordinal: int
    nominal_start_ms: int
    artist: str
    title: str


@dataclass(frozen=True)
class CandidateScore:
    path: Path
    score: float


def normalized_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def stable_id(*parts: str, prefix: str) -> str:
    encoded = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:20]}"


def canonical_selection(lyric_role_summary: dict) -> list[dict]:
    """Return the exact canonical original selected at every timestamp.

    The raw LRC hash is not sufficient identity: a role override can select a
    different original without changing the file bytes.  This normalized
    selection is therefore hashed into TrackAsset identity and is also exposed
    to downstream stages so they never have to re-guess the original line.
    """

    selected: list[dict] = []
    for group in lyric_role_summary.get("groups", []):
        alternatives = list(group.get("alternatives", []))
        originals = [
            (index, row)
            for index, row in enumerate(alternatives)
            if str(row.get("role")) == "original"
        ]
        if len(originals) != 1:
            raise AssetResolutionError(
                "lyric role summary must contain exactly one original per timestamp"
            )
        index, row = originals[0]
        selected.append(
            {
                "timestamp_ms": int(group["timestamp_ms"]),
                "alternative_index": int(index),
                "text": str(row.get("text", "")),
            }
        )
    if not selected:
        raise AssetResolutionError("canonical lyric selection is empty")
    return selected


def canonical_selection_sha256(selection: list[dict]) -> str:
    encoded = json.dumps(
        selection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_song_list(path: Path) -> list[SongEntry]:
    entries: list[SongEntry] = []
    for line_number, raw in enumerate(read_task_text(path).splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            stamp, label = re.split(r"\s+", line, maxsplit=1)
            minute, second = (int(part) for part in stamp.split(":"))
            artist, title = (part.strip() for part in label.split(" - ", 1))
        except (ValueError, TypeError) as exc:
            raise AssetResolutionError(
                f"song list line {line_number} must be 'MM:SS Artist - Title': {raw!r}"
            ) from exc
        if second >= 60 or minute < 0 or second < 0 or not artist or not title:
            raise AssetResolutionError(f"invalid song list line {line_number}: {raw!r}")
        entries.append(
            SongEntry(
                ordinal=len(entries) + 1,
                nominal_start_ms=(minute * 60 + second) * 1000,
                artist=artist,
                title=title,
            )
        )
    if not entries:
        raise AssetResolutionError(f"song list contains no tracks: {path}")
    if any(
        right.nominal_start_ms < left.nominal_start_ms
        for left, right in zip(entries, entries[1:])
    ):
        raise AssetResolutionError("song list nominal start times must be non-decreasing")
    return entries


def _candidate_score(entry: SongEntry, path: Path) -> float:
    """Score one filename without letting a shared title swamp artist identity."""

    title = normalized_key(entry.title)
    artist = normalized_key(entry.artist)
    artist_title = normalized_key(entry.artist + entry.title)
    stem = normalized_key(path.stem)
    if not title or not stem:
        return 0.0

    if artist_title and stem == artist_title:
        return 1.0

    title_score = difflib.SequenceMatcher(None, title, stem, autojunk=False).ratio()
    combined_score = difflib.SequenceMatcher(
        None, artist_title, stem, autojunk=False
    ).ratio()
    has_title = title in stem
    has_artist = bool(artist and artist in stem)

    if has_title and has_artist:
        return min(0.91, 0.86 + 0.05 * combined_score)
    if stem == title:
        return 0.88
    if has_title:
        return min(0.84, 0.76 + 0.08 * title_score)
    return min(0.75, max(title_score, combined_score) * 0.75)


def rank_candidates(
    entry: SongEntry, candidates: Iterable[Path]
) -> list[CandidateScore]:
    return sorted(
        (CandidateScore(path, _candidate_score(entry, path)) for path in candidates),
        key=lambda item: (item.score, item.path.name.casefold()),
        reverse=True,
    )


def choose_candidate(
    entry: SongEntry,
    candidates: Iterable[Path],
    *,
    label: str,
    min_score: float = 0.76,
    min_margin: float = 0.08,
) -> tuple[Path, dict]:
    """Choose one asset or fail closed on weak/ambiguous identity."""

    ranked = rank_candidates(entry, candidates)
    if not ranked:
        raise AssetResolutionError(
            f"no {label} candidates for {entry.artist} - {entry.title}"
        )
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    margin = top.score - (second.score if second else 0.0)
    diagnostic = {
        "top1": {"path": str(top.path), "score": round(top.score, 6)},
        "top2": (
            {"path": str(second.path), "score": round(second.score, 6)}
            if second
            else None
        ),
        "margin": round(margin, 6),
        "min_score": min_score,
        "min_margin": min_margin,
    }
    if top.score < min_score:
        raise AssetResolutionError(
            f"{label} match too weak for {entry.artist} - {entry.title}: "
            f"top1={top.path.name} score={top.score:.3f} < {min_score:.3f}"
        )
    if second is not None and margin < min_margin:
        raise AssetResolutionError(
            f"{label} match ambiguous for {entry.artist} - {entry.title}: "
            f"top1={top.path.name} {top.score:.3f}, top2={second.path.name} "
            f"{second.score:.3f}, margin={margin:.3f} < {min_margin:.3f}"
        )
    return top.path, diagnostic


def _asset_identity(entry: SongEntry) -> tuple[str, str]:
    return normalized_key(entry.artist), normalized_key(entry.title)


def resolve_assets(
    *,
    song_list: Path,
    lyrics_dir: Path,
    source_audio_dir: Path,
    language_by_track: dict[str, str] | None = None,
    middle_cut_by_occurrence: dict[int, str] | None = None,
    lyric_role_overrides_by_track: dict[str, dict[int, int]] | None = None,
    min_score: float = 0.76,
    min_margin: float = 0.08,
) -> dict:
    entries = parse_song_list(song_list)
    lrc_files = sorted(path for path in lyrics_dir.glob("*.lrc") if path.is_file())
    audio_files = sorted(
        path
        for path in source_audio_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES
    )
    if not lrc_files:
        raise AssetResolutionError(f"no LRC files in {lyrics_dir}")
    if not audio_files:
        raise AssetResolutionError(f"no source audio files in {source_audio_dir}")

    language_by_track = language_by_track or {}
    middle_cut_by_occurrence = middle_cut_by_occurrence or {}
    lyric_role_overrides_by_track = lyric_role_overrides_by_track or {}
    assets: dict[tuple[str, str], TrackAsset] = {}
    resolution: dict[tuple[str, str], dict] = {}
    occurrences: list[TrackOccurrence] = []
    claimed_lyrics: dict[Path, tuple[str, str]] = {}
    claimed_audio: dict[Path, tuple[str, str]] = {}

    for entry in entries:
        identity = _asset_identity(entry)
        asset = assets.get(identity)
        if asset is None:
            lyric_path, lyric_diag = choose_candidate(
                entry,
                lrc_files,
                label="LRC",
                min_score=min_score,
                min_margin=min_margin,
            )
            source_path, source_diag = choose_candidate(
                entry,
                audio_files,
                label="source audio",
                min_score=min_score,
                min_margin=min_margin,
            )
            for chosen, claims, label in (
                (lyric_path.resolve(), claimed_lyrics, "LRC"),
                (source_path.resolve(), claimed_audio, "source audio"),
            ):
                previous = claims.get(chosen)
                if previous is not None and previous != identity:
                    raise AssetResolutionError(
                        f"{label} file {chosen.name} would be reused by distinct tracks: "
                        f"{previous} and {identity}; provide explicit unique assets"
                    )
                claims[chosen] = identity

            language = str(
                language_by_track.get(f"{entry.artist} - {entry.title}")
                or language_by_track.get(entry.title)
                or "auto"
            )
            role_overrides = (
                lyric_role_overrides_by_track.get(f"{entry.artist} - {entry.title}")
                or lyric_role_overrides_by_track.get(entry.title)
                or {}
            )
            lyric_role_summary = inspect_lyric_roles(
                lyric_path,
                language=language,
                original_index_overrides=role_overrides,
            )
            selection = canonical_selection(lyric_role_summary)
            selection_hash = canonical_selection_sha256(selection)
            lyric_hash = sha256_file(lyric_path)
            source_hash = sha256_file(source_path)
            version_id = stable_id(
                source_hash,
                lyric_hash,
                selection_hash,
                prefix="ver",
            )
            track_id = stable_id(
                normalized_key(entry.artist),
                normalized_key(entry.title),
                source_hash,
                lyric_hash,
                selection_hash,
                prefix="track",
            )
            asset = TrackAsset(
                track_id=track_id,
                artist=entry.artist,
                title=entry.title,
                version_id=version_id,
                source_audio_path=str(source_path.resolve()),
                source_audio_sha256=source_hash,
                canonical_lyric_path=str(lyric_path.resolve()),
                canonical_lyric_sha256=lyric_hash,
                canonical_selection_sha256=selection_hash,
                language=language,
            )
            assets[identity] = asset
            resolution[identity] = {
                "lrc": lyric_diag,
                "source_audio": source_diag,
                "lyric_roles": lyric_role_summary,
                "canonical_selection": selection,
                "canonical_selection_sha256": selection_hash,
            }

        occurrence_id = stable_id(
            asset.track_id,
            str(entry.ordinal),
            str(entry.nominal_start_ms),
            prefix="occ",
        )
        occurrences.append(
            TrackOccurrence(
                occurrence_id=occurrence_id,
                track_id=asset.track_id,
                ordinal=entry.ordinal,
                nominal_start_ms=entry.nominal_start_ms,
                middle_cut=str(
                    middle_cut_by_occurrence.get(entry.ordinal, "false")
                ),
                language_profile=asset.language,
            )
        )

    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "status": "resolved",
        "song_list": str(song_list.resolve()),
        "song_list_sha256": sha256_file(song_list),
        "resolver_config": {"min_score": min_score, "min_margin": min_margin},
        "assets": [asset.to_dict() for asset in assets.values()],
        "occurrences": [occurrence.to_dict() for occurrence in occurrences],
        "resolution": [
            {
                "track_id": assets[identity].track_id,
                "artist": assets[identity].artist,
                "title": assets[identity].title,
                **diagnostic,
            }
            for identity, diagnostic in resolution.items()
        ],
    }


def write_assets_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
