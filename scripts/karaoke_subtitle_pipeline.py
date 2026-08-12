#!/usr/bin/env python3
"""Optional diagnostic and draft-generation helper for edited song mixes.

The production workflow uses redo_karaoke_pipeline.py. Use this helper only for
manifest inspection, exploratory local ASR, and comparison drafts. It keeps the
original SRT and audio untouched and does not replace final QA.

Examples (PowerShell):
  python scripts/karaoke_subtitle_pipeline.py inspect `
    --audio mix.wav --srt mix.srt --song-list songs.txt --lyrics-dir lyrics

  python scripts/karaoke_subtitle_pipeline.py transcribe `
    --audio mix.wav --srt mix.srt --song-list songs.txt --lyrics-dir lyrics `
    --model small --compute-type int8 --out-dir output/job/diagnostics

  python scripts/karaoke_subtitle_pipeline.py source-timed `
    --audio mix.wav --srt mix.srt --song-list songs.txt --lyrics-dir lyrics `
    --asr-json output/job/diagnostics/asr_segments.json `
    --out-srt output/job/diagnostics/source_timed_canonical.srt
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import difflib
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,3}):(\d{2})(?:[,.](\d{1,3}))?$")
SRT_BLOCK_RE = re.compile(
    r"(?ms)^\s*(\d+)\s*\r?\n"
    r"(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3}).*?\r?\n"
    r"(.*?)(?=\r?\n\s*\r?\n|\Z)"
)
LRC_LINE_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)$")
DEFAULT_LOCAL_CONFIG = Path("private/lyric-aligner.local.json")


@dataclass
class Track:
    index: int
    start_ms: int
    artist: str
    title: str
    lrc_path: str | None
    language_hint: str | None


@dataclass
class SrtCue:
    number: int
    start_ms: int
    end_ms: int
    text: str


def parse_time(value: str) -> int:
    value = value.strip().replace(",", ".")
    m = TIME_RE.match(value)
    if not m:
        raise ValueError(f"Unsupported time value: {value!r}")
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    fraction = (m.group(4) or "0").ljust(3, "0")[:3]
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(fraction)


def format_srt_time(ms: int) -> str:
    ms = max(0, int(round(ms)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if ch.isalnum())


def parse_srt(path: Path) -> list[SrtCue]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    cues: list[SrtCue] = []
    for m in SRT_BLOCK_RE.finditer(text):
        body = " ".join(line.strip() for line in m.group(4).splitlines() if line.strip())
        cues.append(
            SrtCue(
                number=int(m.group(1)),
                start_ms=parse_time(m.group(2)),
                end_ms=parse_time(m.group(3)),
                text=body,
            )
        )
    return cues


def parse_song_list(path: Path) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\d{1,3}:\d{2}(?::\d{2})?)\s+(.+?)\s*$", line)
        if not m:
            continue
        stamp = parse_time(m.group(1))
        label = m.group(2)
        if " - " in label:
            artist, title = label.split(" - ", 1)
        else:
            artist, title = "", label
        rows.append((stamp, artist.strip(), title.strip()))
    return rows


def parse_lrc(path: Path) -> list[dict[str, Any]]:
    metadata_words = (
        "作词",
        "作曲",
        "编曲",
        "监制",
        "推广",
        "统筹",
        "混音",
        "企划",
        "出品人",
        "艺人经纪",
        "OP:",
        "[by:",
    )
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = LRC_LINE_RE.match(raw.strip())
        if not m:
            continue
        timestamp = int(m.group(1)) * 60_000 + round(float(m.group(2)) * 1000)
        text = m.group(3).strip()
        # Standard LRC metadata is stored as a timestamped tag whose payload
        # begins with ``[ar:``, ``[ti:``, ``[by:`` and similar.  Treat those as
        # metadata before applying the Chinese credit-word filter; otherwise a
        # tag such as ``[ar:redfoo]`` can become a fake lyric at time zero.
        lrc_metadata_tag = bool(re.match(r"^\[(?:ar|al|ti|by|offset|length|re|ve|au):", text, re.I))
        if not text or lrc_metadata_tag or any(word in text for word in metadata_words):
            continue
        out.append({"time_ms": timestamp, "text": text})
    return out


def lrc_candidates(lyrics_dir: Path, title: str) -> list[Path]:
    key = normalize_key(title)
    candidates = []
    for path in lyrics_dir.glob("*.lrc"):
        stem_key = normalize_key(path.stem)
        if key and (key in stem_key or stem_key in key):
            candidates.append(path)
    return sorted(candidates, key=lambda p: len(p.name))


def load_language_hints(
    path: Path | None, *, song_list: Path | None = None
) -> dict[str, str | None]:
    config_path = path
    if config_path is None and DEFAULT_LOCAL_CONFIG.exists():
        candidate = DEFAULT_LOCAL_CONFIG
        candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
        input_root = candidate_payload.get("input_root") if isinstance(candidate_payload, dict) else None
        if not input_root or song_list is None or song_list.resolve().is_relative_to(Path(input_root).resolve()):
            config_path = candidate
    if config_path is None:
        return {}
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    raw_hints = payload.get("language_hints", {}) if isinstance(payload, dict) else {}
    if not isinstance(raw_hints, dict):
        raise ValueError(f"language_hints must be an object: {config_path}")
    return {normalize_key(str(key)): (str(value) if value else None) for key, value in raw_hints.items()}


def language_hint(
    title: str,
    artist: str,
    language_hints: dict[str, str | None] | None = None,
) -> str | None:
    hints = language_hints or {}
    for key in (normalize_key(title), normalize_key(title + artist)):
        if key in hints:
            return hints[key]
    text = title + artist
    if any("\uac00" <= ch <= "\ud7a3" for ch in text):
        return "ko"
    if any("\u3040" <= ch <= "\u30ff" for ch in text):
        return "ja"
    if any("\u3400" <= ch <= "\u9fff" for ch in text):
        return "zh"
    return "en"


def make_tracks(
    song_list: Path, lyrics_dir: Path, language_hints_path: Path | None = None
) -> list[Track]:
    language_hints = load_language_hints(language_hints_path, song_list=song_list)
    tracks: list[Track] = []
    for index, (start_ms, artist, title) in enumerate(parse_song_list(song_list), start=1):
        candidates = lrc_candidates(lyrics_dir, title)
        tracks.append(
            Track(
                index=index,
                start_ms=start_ms,
                artist=artist,
                title=title,
                lrc_path=str(candidates[0]) if candidates else None,
                language_hint=language_hint(title, artist, language_hints),
            )
        )
    return tracks


def sha256(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def probe_audio(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return json.loads(result.stdout)


def infer_end_ms(audio_info: dict[str, Any], cues: Iterable[SrtCue], tail_pad_ms: int) -> int:
    format_duration = float(audio_info.get("format", {}).get("duration") or 0) * 1000
    subtitle_end = max((cue.end_ms for cue in cues), default=0) + tail_pad_ms
    return int(min(format_duration or subtitle_end, subtitle_end))


def build_manifest(
    audio: Path,
    srt: Path,
    song_list: Path,
    lyrics_dir: Path,
    language_hints_path: Path | None = None,
) -> dict[str, Any]:
    audio_info = probe_audio(audio)
    cues = parse_srt(srt)
    tracks = make_tracks(song_list, lyrics_dir, language_hints_path)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audio": {
            "path": str(audio.resolve()),
            "sha256": sha256(audio),
            "probe": audio_info,
        },
        "srt": {
            "path": str(srt.resolve()),
            "sha256": sha256(srt),
            "cue_count": len(cues),
            "last_end_ms": max((cue.end_ms for cue in cues), default=0),
            "overlap_count": sum(
                1 for left, right in zip(cues, cues[1:]) if right.start_ms < left.end_ms
            ),
        },
        "tracks": [asdict(track) for track in tracks],
    }


def cmd_inspect(args: argparse.Namespace) -> int:
    audio = Path(args.audio)
    srt = Path(args.srt)
    song_list = Path(args.song_list)
    lyrics_dir = Path(args.lyrics_dir)
    manifest = build_manifest(audio, srt, song_list, lyrics_dir, args.language_hints)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def segment_to_dict(segment: Any) -> dict[str, Any]:
    words = []
    for word in segment.words or []:
        words.append(
            {
                "start": word.start,
                "end": word.end,
                "word": word.word,
                "probability": word.probability,
            }
        )
    return {
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "avg_logprob": segment.avg_logprob,
        "compression_ratio": segment.compression_ratio,
        "no_speech_prob": segment.no_speech_prob,
        "words": words,
    }


def canonical_lrc_lines(path: Path) -> list[dict[str, Any]]:
    """Keep the first non-metadata line at each LRC timestamp.

    The supplied LRC files commonly store the sung original followed by a
    translated line at the same timestamp.  For subtitle correction the first
    line is the original-language candidate; translations are intentionally not
    mixed into the main subtitle track.
    """

    rows: list[dict[str, Any]] = []
    seen_times: set[int] = set()
    metadata = (
        "作词",
        "作曲",
        "编曲",
        "监制",
        "推广",
        "统筹",
        "混音",
        "企划",
        "出品人",
        "艺人经纪",
        "OP:",
        "[by:",
        "制作人",
        "版权",
        "SP:",
    )
    for row in parse_lrc(path):
        timestamp = int(row["time_ms"])
        text = str(row["text"]).strip()
        if timestamp in seen_times or not text or any(word in text for word in metadata):
            continue
        seen_times.add(timestamp)
        rows.append({"time_ms": timestamp, "text": text})
    return rows


def text_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if ch.isalnum())


def text_similarity(left: str, right: str) -> float:
    left_key = text_key(left)
    right_key = text_key(right)
    if not left_key or not right_key:
        return 0.0
    ratio = difflib.SequenceMatcher(None, left_key, right_key).ratio()
    if left_key in right_key or right_key in left_key:
        coverage = min(len(left_key), len(right_key)) / max(len(left_key), len(right_key))
        # A short lyric fragment inside a much longer hallucinated cue is not
        # strong enough to replace the whole cue.  Give containment only a
        # modest boost proportional to how much of the observation it covers.
        ratio = max(ratio, 0.40 + 0.55 * coverage)
    return ratio


def overlap_ms(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    return max(0, min(left_end, right_end) - max(left_start, right_start))


def is_generic_vocalization(text: str) -> bool:
    key = text_key(text)
    if not key:
        return True
    exact = {
        "na",
        "nana",
        "nanana",
        "oh",
        "ooh",
        "yeah",
        "uh",
        "uhhuh",
        "hey",
        "ba",
        "baba",
        "dum",
        "dumdum",
        "whipwhiplash",
    }
    if key in exact:
        return True
    # Jianying often writes repeated ad-libs as ``NA NA NA OH`` or
    # ``dum-dum-da``.  Recognize them as ambiguous non-lexical vocalizations;
    # callers must not preserve them unless canonical LRC contains them.
    tokens = [text_key(token) for token in re.findall(r"[\w']+", text, flags=re.UNICODE)]
    generic_tokens = exact | {"da", "huh"}
    return bool(tokens) and all(token in generic_tokens for token in tokens)


def best_lrc_sequences(observation: str, lines: list[dict[str, Any]], max_len: int = 4) -> list[dict[str, Any]]:
    """Return high-scoring consecutive LRC line sequences for an observation."""

    results: list[dict[str, Any]] = []
    obs_key = text_key(observation)
    if len(obs_key) < 4:
        return results
    for start in range(len(lines)):
        for length in range(1, max_len + 1):
            end = start + length
            if end > len(lines):
                break
            selected = lines[start:end]
            text = " ".join(str(row["text"]) for row in selected)
            score = text_similarity(observation, text)
            key = text_key(text)
            coverage = min(len(obs_key), len(key)) / max(1, max(len(obs_key), len(key)))
            if score < 0.42 or coverage < 0.45:
                continue
            if length == 1 and is_generic_vocalization(text) and score < 0.82:
                continue
            results.append(
                {
                    "text": text,
                    "score": score,
                    "coverage": coverage,
                    "start_index": start,
                    "end_index": end - 1,
                    "start_time_ms": selected[0]["time_ms"],
                    "end_time_ms": selected[-1]["time_ms"],
                    "length": length,
                }
            )
    results.sort(key=lambda row: (row["score"], row["coverage"], -row["length"]), reverse=True)
    # Keep only a few non-overlapping alternatives; this is enough for cue
    # correction and prevents repeated chorus lines from flooding the report.
    out: list[dict[str, Any]] = []
    for row in results:
        if any(
            row["start_index"] == other["start_index"]
            and row["end_index"] == other["end_index"]
            for other in out
        ):
            continue
        out.append(row)
        if len(out) >= 5:
            break
    return out


def track_windows(tracks: list[Track], end_ms: int) -> list[tuple[Track, int, int]]:
    windows: list[tuple[Track, int, int]] = []
    for index, track in enumerate(tracks):
        next_start = tracks[index + 1].start_ms if index + 1 < len(tracks) else end_ms
        windows.append((track, track.start_ms, next_start))
    return windows


def choose_correction(
    cue: SrtCue,
    tracks: list[tuple[Track, int, int]],
    asr_by_track: dict[int, list[dict[str, Any]]],
    lrc_by_track: dict[int, list[dict[str, Any]]],
    min_score: float,
) -> dict[str, Any] | None:
    """Return the best canonical replacement for one SRT cue, if auditable."""

    candidates: list[dict[str, Any]] = []
    for track, win_start, win_end in tracks:
        cue_overlap = overlap_ms(cue.start_ms, cue.end_ms, win_start, win_end)
        near_boundary = abs(cue.start_ms - win_start) <= 4000 or abs(cue.end_ms - win_end) <= 4000
        if cue_overlap <= 0 and not near_boundary:
            continue
        lines = lrc_by_track.get(track.index, [])
        if not lines:
            continue

        # Direct evidence from the existing Jianying text.  Matching short
        # consecutive LRC sequences lets one SRT cue retain all lyrics it
        # actually contains instead of replacing it with an arbitrary fragment.
        for sequence in best_lrc_sequences(cue.text, lines):
            score = sequence["score"]
            if score < min_score or sequence["coverage"] < 0.55:
                continue
            candidates.append(
                {
                    "track": track,
                    "line": {
                        "text": sequence["text"],
                        "time_ms": sequence["start_time_ms"],
                    },
                    "score": score,
                    "source": "srt",
                    "evidence_start": cue.start_ms,
                    "evidence_end": cue.end_ms,
                    "overlap": cue_overlap,
                    "sequence_length": sequence["length"],
                }
            )

        # Evidence from local multilingual ASR.  A single ASR segment may cover
        # several sung lines; keep the top few candidates so a later cue can
        # select the line whose evidence overlaps it most closely.
        for segment in asr_by_track.get(track.index, []):
            seg_start = round(float(segment["start"]) * 1000)
            seg_end = round(float(segment["end"]) * 1000)
            seg_overlap = overlap_ms(cue.start_ms, cue.end_ms, seg_start, seg_end)
            if seg_overlap <= 0:
                continue
            best = best_lrc_sequences(str(segment.get("text", "")), lines)
            for sequence in best:
                score = sequence["score"]
                fraction = seg_overlap / max(1, seg_end - seg_start)
                if score < max(0.42, min_score - 0.05):
                    continue
                if score < 0.65 and fraction < 0.50:
                    continue
                candidates.append(
                    {
                        "track": track,
                        "line": {
                            "text": sequence["text"],
                            "time_ms": sequence["start_time_ms"],
                        },
                        "score": score,
                        "source": "asr",
                        "evidence_start": seg_start,
                        "evidence_end": seg_end,
                        "overlap": seg_overlap,
                        "fraction": fraction,
                        "sequence_length": sequence["length"],
                    }
                )

    if not candidates:
        return None

    # Prefer evidence from the track whose time window contains the cue, then
    # prefer stronger text evidence and larger temporal overlap.
    def rank(candidate: dict[str, Any]) -> tuple[float, float, float, float]:
        track: Track = candidate["track"]
        inside = 1.0 if track.start_ms <= cue.start_ms < next((b for t, a, b in tracks if t.index == track.index), 10**12) else 0.0
        source_bonus = 0.06 if candidate["source"] == "srt" else 0.0
        fraction = float(candidate.get("fraction", 0.0))
        return (
            inside + source_bonus,
            candidate["score"],
            fraction,
            candidate["overlap"] / max(1, cue.end_ms - cue.start_ms),
        )

    best = max(candidates, key=rank)
    # Avoid using a weak match from an adjacent song at a cut.
    if best["score"] < min_score:
        return None
    confidence = "high" if best["score"] >= 0.78 else "medium"
    if best["source"] == "asr" and float(best.get("fraction", 0.0)) >= 0.55:
        confidence = "high" if best["score"] >= 0.65 else "medium"
    return {
        "text": best["line"]["text"],
        "track_index": best["track"].index,
        "track_title": best["track"].title,
        "lrc_time_ms": best["line"]["time_ms"],
        "score": best["score"],
        "source": best["source"],
        "confidence": confidence,
    }


def write_srt(cues: list[tuple[SrtCue, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for cue, text in cues:
        blocks.append(
            f"{cue.number}\n{format_srt_time(cue.start_ms)} --> {format_srt_time(cue.end_ms)}\n{text.strip()}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def cmd_correct(args: argparse.Namespace) -> int:
    audio = Path(args.audio)
    srt = Path(args.srt)
    song_list = Path(args.song_list)
    lyrics_dir = Path(args.lyrics_dir)
    asr_path = Path(args.asr_json)
    cues = parse_srt(srt)
    tracks = make_tracks(song_list, lyrics_dir, args.language_hints)
    asr_payload = json.loads(asr_path.read_text(encoding="utf-8"))
    asr_by_track: dict[int, list[dict[str, Any]]] = {}
    for row in asr_payload.get("tracks", []):
        index = int(row["track"]["index"])
        asr_by_track[index] = list(row.get("segments", []))
    lrc_by_track: dict[int, list[dict[str, Any]]] = {}
    for track in tracks:
        if track.lrc_path:
            lrc_by_track[track.index] = canonical_lrc_lines(Path(track.lrc_path))

    probe = probe_audio(audio)
    duration_ms = int(float(probe.get("format", {}).get("duration") or 0) * 1000)
    windows = track_windows(tracks, duration_ms)
    out_cues: list[tuple[SrtCue, str]] = []
    report_rows: list[dict[str, Any]] = []
    for cue in cues:
        correction = choose_correction(cue, windows, asr_by_track, lrc_by_track, args.min_score)
        new_text = correction["text"] if correction else cue.text
        out_cues.append((cue, new_text))
        report_rows.append(
            {
                "cue": cue.number,
                "start": format_srt_time(cue.start_ms),
                "end": format_srt_time(cue.end_ms),
                "original": cue.text,
                "replacement": new_text if correction else "",
                "status": "replaced" if correction else "unresolved",
                "confidence": correction["confidence"] if correction else "",
                "source": correction["source"] if correction else "",
                "track": correction["track_title"] if correction else "",
                "lrc_time": round(correction["lrc_time_ms"] / 1000, 3) if correction else "",
                "score": round(correction["score"], 3) if correction else "",
            }
        )

    write_srt(out_cues, Path(args.out_srt))
    report_path = args.out_report or Path(args.out_srt).with_name(Path(args.out_srt).stem + "_review.csv")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report_rows[0].keys()) if report_rows else ["cue"])
        writer.writeheader()
        writer.writerows(report_rows)
    replaced = sum(row["status"] == "replaced" for row in report_rows)
    print(f"wrote {args.out_srt} ({replaced}/{len(report_rows)} cues replaced)")
    print(f"review report: {report_path}")
    return 0


def cmd_source_timed(args: argparse.Namespace) -> int:
    """Correct text while preserving every original Jianying interval exactly.

    The line-aligned output is useful for rebuilding a clean lyric track, but
    it must not replace a trusted edit timeline.  This command therefore uses
    LRC/ASR alignment only to select the canonical wording and writes the
    original SRT cue start/end unchanged.
    """

    audio = Path(args.audio)
    srt = Path(args.srt)
    song_list = Path(args.song_list)
    lyrics_dir = Path(args.lyrics_dir)
    asr_payload = json.loads(Path(args.asr_json).read_text(encoding="utf-8"))
    cues = parse_srt(srt)
    tracks = make_tracks(song_list, lyrics_dir, args.language_hints)
    manual_events = load_manual_events(args.manual_events)
    probe = probe_audio(audio)
    duration_ms = int(float(probe.get("format", {}).get("duration") or 0) * 1000)
    windows = track_windows(tracks, duration_ms)

    # Approximate track ownership for cues at ordinary song positions.  A
    # later event-to-cue pass can override this near a real cut (e.g. Fever
    # starts slightly before the rounded song-list boundary).
    cue_window_track: dict[int, int] = {}
    for cue in cues:
        inside = next(
            (track.index for track, start, end in windows if start <= cue.start_ms < end),
            None,
        )
        if inside is not None:
            cue_window_track[cue.number] = inside
            continue
        overlaps = [
            (overlap_ms(cue.start_ms, cue.end_ms, start, end), track.index)
            for track, start, end in windows
        ]
        cue_window_track[cue.number] = max(overlaps, default=(0, 0))[1]

    lines_by_track: dict[int, list[dict[str, Any]]] = {}
    alignment_events: list[dict[str, Any]] = []
    for position, track in enumerate(tracks):
        track_end = tracks[position + 1].start_ms if position + 1 < len(tracks) else duration_ms
        lines = canonical_lrc_lines(Path(track.lrc_path)) if track.lrc_path else []
        lines_by_track[track.index] = lines
        row = next(
            (item for item in asr_payload.get("tracks", []) if int(item["track"]["index"]) == track.index),
            None,
        )
        track_events, _ = build_track_alignment_events(
            track,
            track_end,
            cues,
            row.get("segments", []) if row else [],
            lines,
            preroll_ms=args.preroll_ms,
        )
        alignment_events.extend(
            event for event in track_events if float(event.get("score", 0.0)) >= args.projection_score
        )
    alignment_events.extend(manual_events)

    # Assign each projected line to the most plausible original cue.  A clear
    # text match beats a slightly nearer cue; this prevents the opening
    # narration cue from stealing the overlapping ``One look`` lyric.
    assigned: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in alignment_events:
        candidates: list[tuple[float, int, SrtCue]] = []
        event_track = int(event.get("track_index", 0))
        for cue in cues:
            distance = abs(cue.start_ms - int(event["start_ms"]))
            if distance > args.max_projection_gap_ms:
                continue
            same_window = cue_window_track.get(cue.number) == event_track
            near_boundary = distance <= args.boundary_tolerance_ms
            if not same_window and not near_boundary:
                continue
            candidates.append((text_similarity(cue.text, str(event["text"])), distance, cue))
        if not candidates:
            continue
        best_similarity = max(item[0] for item in candidates)
        if best_similarity >= args.text_match_score:
            candidates = [item for item in candidates if item[0] >= best_similarity - 0.02]
        selected = min(candidates, key=lambda item: item[1])[2]
        assigned[selected.number].append(event)

    out_cues: list[tuple[SrtCue, str]] = []
    report_rows: list[dict[str, Any]] = []
    for cue in cues:
        assigned_events: list[dict[str, Any]] = []
        for event in sorted(
            assigned.get(cue.number, []), key=lambda row: (row["start_ms"], row["end_ms"])
        ):
            if not any(
                event.get("lrc_time_ms") == previous.get("lrc_time_ms")
                and text_key(event["text"]) == text_key(previous["text"])
                for previous in assigned_events
            ):
                assigned_events.append(event)

        event_track = int(assigned_events[0].get("track_index", 0)) if assigned_events else 0
        track_index = event_track or cue_window_track.get(cue.number, 0)
        direct_candidates = (
            []
            if is_generic_vocalization(cue.text)
            else best_lrc_sequences(cue.text, lines_by_track.get(track_index, []))
        )
        direct_candidates = [
            row
            for row in direct_candidates
            if row["score"] >= args.direct_score and row["coverage"] >= args.direct_coverage
        ]
        if direct_candidates:
            replacement = direct_candidates[0]["text"]
            source = "srt_direct"
            score = float(direct_candidates[0]["score"])
            confidence = "high" if score >= 0.85 else "medium"
            lrc_time = str(direct_candidates[0]["start_time_ms"])
        elif assigned_events:
            # For a cue whose own text is too damaged for a direct LRC match,
            # use only the nearest projected line.  Concatenating every line
            # that happened to fall within a long SRT interval can move the
            # next lyric backward and create a false multi-line replacement.
            nearest = min(
                assigned_events,
                key=lambda event: abs(int(event["start_ms"]) - cue.start_ms),
            )
            distance = abs(int(nearest["start_ms"]) - cue.start_ms)
            generic_source = is_generic_vocalization(cue.text)
            generic_candidate = is_generic_vocalization(str(nearest["text"]))
            if distance > args.single_projection_max_gap_ms or (
                generic_source and not generic_candidate
            ):
                replacement = cue.text
                source = "unresolved"
                score = ""
                confidence = ""
                lrc_time = ""
            else:
                replacement = str(nearest["text"])
                score = float(nearest.get("score", 0.0))
                source = (
                    "manual_projection"
                    if nearest.get("source") == "manual_lrc"
                    else "timeline_projection"
                )
                confidence = "high" if score >= 0.82 else "medium"
                lrc_time = str(nearest.get("lrc_time_ms", ""))
        else:
            replacement = cue.text
            source = "unresolved"
            score = ""
            confidence = ""
            lrc_time = ""

        changed = replacement != cue.text
        out_cues.append((cue, replacement))
        report_rows.append(
            {
                "cue": cue.number,
                "start": format_srt_time(cue.start_ms),
                "end": format_srt_time(cue.end_ms),
                "original": cue.text,
                "replacement": replacement if changed else "",
                "status": "replaced" if changed else ("unresolved" if source == "unresolved" else "unchanged"),
                "timing_source": "jianying_srt",
                "confidence": confidence,
                "source": source,
                "track": tracks[track_index - 1].title if 0 < track_index <= len(tracks) else "",
                "lrc_time": lrc_time,
                "score": round(float(score), 3) if score != "" else "",
            }
        )

    out_path = Path(args.out_srt)
    write_srt(out_cues, out_path)
    report_path = args.out_report or out_path.with_name(out_path.stem + "_review.csv")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = list(report_rows[0].keys()) if report_rows else ["cue"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report_rows)
    print(f"wrote {out_path} ({sum(row['status'] == 'replaced' for row in report_rows)}/{len(report_rows)} cues replaced)")
    print(f"timing source: original Jianying SRT ({len(cues)} cues preserved)")
    print(f"review report: {report_path}")
    return 0


def _line_is_generic(text: str) -> bool:
    return is_generic_vocalization(text)


def _simple_similarity(left: str, right: str) -> float:
    """Similarity used while selecting monotonic timing anchors."""

    score = text_similarity(left, right)
    if _line_is_generic(right) and score < 0.85:
        return 0.0
    return score


def _fit_affine(run: list[dict[str, Any]], track_start_ms: int) -> tuple[float, float, list[dict[str, Any]]]:
    """Fit global_rel_seconds = intercept + slope * lrc_seconds robustly."""

    usable = list(run)
    if len(usable) < 2:
        if usable:
            return usable[0]["off"] / 1000, 1.0, usable
        return 0.0, 1.0, usable
    for _ in range(2):
        xs = [row["lt"] / 1000 for row in usable]
        ys = [(row["start_ms"] - track_start_ms) / 1000 for row in usable]
        xm = sum(xs) / len(xs)
        ym = sum(ys) / len(ys)
        denominator = sum((x - xm) ** 2 for x in xs)
        slope = (
            sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denominator
            if denominator
            else 1.0
        )
        slope = max(0.45, min(1.8, slope))
        intercept = ym - slope * xm
        residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
        if len(usable) < 6:
            break
        kept = [row for row, residual in zip(usable, residuals) if abs(residual) <= 2.0]
        if len(kept) < max(3, len(usable) // 2):
            break
        usable = kept
    return intercept, slope, usable


def build_track_alignment_events(
    track: Track,
    track_end_ms: int,
    cues: list[SrtCue],
    asr_segments: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    preroll_ms: int = 3000,
    max_event_duration_ms: int = 10_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Align LRC line times to the edited mix using SRT/ASR anchors.

    The fit is piecewise affine and is driven by observed lyric anchors.  It
    does not infer timing from BPM.  When the source audio is cut, a new run is
    started once source order, offset or the observed gap indicates a splice.
    """

    observations: list[tuple[int, int, str, str]] = []
    for cue in cues:
        if cue.start_ms < track.start_ms - preroll_ms or cue.start_ms >= track_end_ms:
            continue
        observations.append((cue.start_ms, cue.end_ms, cue.text, "srt"))
    for segment in asr_segments:
        observations.append(
            (
                round(float(segment["start"]) * 1000),
                round(float(segment["end"]) * 1000),
                str(segment.get("text", "")),
                "asr",
            )
        )
    observations.sort(key=lambda row: (row[0], 0 if row[3] == "asr" else 1))

    anchors: list[dict[str, Any]] = []
    current_offset: float | None = None
    current_index: int | None = None
    for start_ms, end_ms, text, source in observations:
        text_key_value = text_key(text)
        if len(text_key_value) < 5:
            continue
        candidates: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            score = _simple_similarity(text, str(line["text"]))
            if score < 0.62:
                continue
            offset = (start_ms - track.start_ms - int(line["time_ms"])) / 1000
            candidates.append(
                {
                    "score": score,
                    "index": index,
                    "offset": offset,
                    "lt": int(line["time_ms"]),
                    "text": str(line["text"]),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "source": source,
                }
            )
        if not candidates:
            continue
        if current_offset is None:
            selected = max(candidates, key=lambda row: row["score"])
        else:
            selected = max(
                candidates,
                key=lambda row: (
                    row["score"]
                    - 0.025 * min(abs(row["offset"] - current_offset), 30.0)
                    + (0.08 if current_index is None or row["index"] >= current_index - 5 else 0.0),
                    row["score"],
                ),
            )
            if selected["score"] < 0.72 and abs(selected["offset"] - current_offset) > 8.0:
                continue
        anchors.append(selected)
        current_offset = selected["offset"]
        current_index = selected["index"]

    runs: list[list[dict[str, Any]]] = []
    for anchor in anchors:
        if not runs:
            runs.append([anchor])
            continue
        previous_run = runs[-1]
        previous = previous_run[-1]
        median_offset = statistics.median(row["offset"] for row in previous_run)
        source_jump = anchor["index"] - previous["index"]
        global_gap = anchor["start_ms"] - previous["start_ms"]
        should_split = (
            anchor["index"] < previous["index"] - 3
            or abs(anchor["offset"] - median_offset) > 5.0
            or global_gap > 30_000
            or (source_jump > 25 and global_gap < 5_000)
        )
        if should_split:
            runs.append([anchor])
        else:
            previous_run.append(anchor)

    events: list[dict[str, Any]] = []
    run_summaries: list[dict[str, Any]] = []
    for run_index, run in enumerate(runs, start=1):
        if len(run) < 3:
            continue
        intercept, slope, fit_rows = _fit_affine(run, track.start_ms)
        if len(fit_rows) < 3:
            continue
        first_index = min(row["index"] for row in fit_rows)
        last_index = max(row["index"] for row in fit_rows)
        line_start = max(0, first_index - 2)
        line_end = min(len(lines) - 1, last_index + 2)
        run_start_global = track.start_ms + round((intercept + slope * lines[line_start]["time_ms"] / 1000) * 1000)
        run_end_global = track.start_ms + round((intercept + slope * lines[line_end]["time_ms"] / 1000) * 1000)
        run_summaries.append(
            {
                "run": run_index,
                "anchor_count": len(fit_rows),
                "first_lrc_ms": int(lines[line_start]["time_ms"]),
                "last_lrc_ms": int(lines[line_end]["time_ms"]),
                "global_start_ms": run_start_global,
                "global_end_ms": run_end_global,
                "slope": round(slope, 6),
                "intercept": round(intercept, 6),
            }
        )
        for index in range(line_start, line_end + 1):
            line = lines[index]
            start_global = track.start_ms + round((intercept + slope * line["time_ms"] / 1000) * 1000)
            if start_global < track.start_ms - preroll_ms or start_global >= track_end_ms + 1000:
                continue
            next_global: int | None = None
            # Use the next source-LRC line even when it falls just outside
            # this fitted run.  Otherwise the last emitted line of a run would
            # inherit the whole track boundary (tens of seconds of silence or
            # instrumental audio) and obscure the next splice.
            for next_index in range(index + 1, len(lines)):
                candidate = lines[next_index]
                projected = track.start_ms + round(
                    (intercept + slope * candidate["time_ms"] / 1000) * 1000
                )
                if projected > start_global + 50:
                    next_global = projected
                    break
            if next_global is None:
                next_global = min(track_end_ms, start_global + max_event_duration_ms)
            else:
                next_global = min(next_global, start_global + max_event_duration_ms, track_end_ms)
            events.append(
                {
                    "start_ms": start_global,
                    "end_ms": next_global or track_end_ms,
                    "text": str(line["text"]),
                    "track": track.title,
                    "track_index": track.index,
                    "source": "projected_lrc",
                    "confidence": "alignment",
                    "score": round(statistics.mean(row["score"] for row in fit_rows), 3),
                    "run": run_index,
                    "lrc_time_ms": int(line["time_ms"]),
                }
            )

    return events, run_summaries


def _event_overlap(left: dict[str, Any], right: dict[str, Any]) -> int:
    return overlap_ms(left["start_ms"], left["end_ms"], right["start_ms"], right["end_ms"])


def _cap_event_ends(events: list[dict[str, Any]], max_event_duration_ms: int = 10_000) -> None:
    """Prevent a projected line from swallowing the next line.

    A piecewise fit can make the final line of one run extend to the track
    boundary.  If the following run starts before that boundary, the two
    synthetic events overlap and the later conflict resolver may discard a
    valid lyric.  Cap each event at the next event in the same track; this is
    a local ordering constraint, not a BPM-derived timing estimate.
    """

    by_track: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        by_track.setdefault(int(event.get("track_index", 0)), []).append(event)
    for rows in by_track.values():
        rows.sort(key=lambda row: (int(row["start_ms"]), int(row["end_ms"])))
        for event in rows:
            event["end_ms"] = min(
                int(event["end_ms"]), int(event["start_ms"]) + max_event_duration_ms
            )
        for index, event in enumerate(rows[:-1]):
            next_start = int(rows[index + 1]["start_ms"])
            if next_start > int(event["start_ms"]) and int(event["end_ms"]) > next_start:
                event["end_ms"] = next_start


def _merge_close_projection_duplicates(events: list[dict[str, Any]], max_gap_ms: int = 5_000) -> list[dict[str, Any]]:
    """Merge the same LRC line emitted twice by adjacent splice fits.

    Piecewise runs intentionally overlap by a couple of anchor lines.  The
    same ``lrc_time_ms`` can therefore be projected twice a few milliseconds
    apart.  Keep a genuinely repeated occurrence when it is far away, but
    collapse the close duplicate so the output has one cue per source line.
    """

    merged: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda row: (int(row["start_ms"]), int(row["end_ms"]))):
        if event.get("source") not in {"projected_lrc", "manual_lrc"}:
            merged.append(event)
            continue
        duplicate = next(
            (
                previous
                for previous in reversed(merged)
                if previous.get("source") in {"projected_lrc", "manual_lrc"}
                and int(previous.get("track_index", -1)) == int(event.get("track_index", -2))
                and previous.get("lrc_time_ms") == event.get("lrc_time_ms")
                and text_key(previous.get("text", "")) == text_key(event.get("text", ""))
                and abs(int(previous["start_ms"]) - int(event["start_ms"])) <= max_gap_ms
            ),
            None,
        )
        if duplicate is None:
            merged.append(event)
            continue
        duplicate["start_ms"] = min(int(duplicate["start_ms"]), int(event["start_ms"]))
        duplicate["end_ms"] = max(int(duplicate["end_ms"]), int(event["end_ms"]))
        duplicate["score"] = max(float(duplicate.get("score", 0.0)), float(event.get("score", 0.0)))
    return merged


def load_manual_events(path: Path | None) -> list[dict[str, Any]]:
    """Load optional hand-audited canonical events without touching source files.

    The JSON may be either a bare list or ``{"events": [...]}``.  Times are
    absolute milliseconds on the edited mix.  This hook is intentionally
    separate from the automatic fit so a future job can keep a small,
    reviewable exception file for a particularly difficult vocal section.
    """

    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("events", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"manual events must be a list: {path}")
    events: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("text"):
            continue
        start = row.get("start_ms", row.get("start"))
        end = row.get("end_ms", row.get("end"))
        if isinstance(start, str):
            start = parse_time(start)
        if isinstance(end, str):
            end = parse_time(end)
        if start is None or end is None:
            continue
        events.append(
            {
                "start_ms": int(start),
                "end_ms": int(end),
                "text": str(row["text"]).strip(),
                "track": str(row.get("track", "")),
                "track_index": int(row.get("track_index", 0)),
                "source": "manual_lrc",
                "confidence": str(row.get("confidence", "manual")),
                "score": float(row.get("score", 1.0)),
                "run": row.get("run", ""),
                "lrc_time_ms": int(row.get("lrc_time_ms", 0)),
            }
        )
    return events


def cmd_align(args: argparse.Namespace) -> int:
    audio = Path(args.audio)
    srt_path = Path(args.srt)
    song_list = Path(args.song_list)
    lyrics_dir = Path(args.lyrics_dir)
    asr_payload = json.loads(Path(args.asr_json).read_text(encoding="utf-8"))
    cues = parse_srt(srt_path)
    tracks = make_tracks(song_list, lyrics_dir, args.language_hints)
    audio_info = probe_audio(audio)
    audio_duration_ms = int(float(audio_info.get("format", {}).get("duration") or 0) * 1000)
    subtitle_end_ms = max((cue.end_ms for cue in cues), default=0)
    active_end_ms = min(audio_duration_ms or subtitle_end_ms + 30_000, subtitle_end_ms + args.tail_pad_ms)

    events: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    covered_track_indices: set[int] = set()
    for position, track in enumerate(tracks):
        track_end = tracks[position + 1].start_ms if position + 1 < len(tracks) else active_end_ms
        lines = canonical_lrc_lines(Path(track.lrc_path)) if track.lrc_path else []
        row = next((item for item in asr_payload.get("tracks", []) if int(item["track"]["index"]) == track.index), None)
        asr_segments = row.get("segments", []) if row else []
        observed_end_candidates = [
            cue.end_ms
            for cue in cues
            if cue.start_ms < track_end and cue.end_ms > track.start_ms - args.preroll_ms
        ]
        observed_end_candidates.extend(
            round(float(segment["end"]) * 1000) for segment in asr_segments
        )
        observed_end = max(observed_end_candidates, default=track.start_ms)
        evidence_horizon = min(track_end, observed_end + args.evidence_tail_ms)
        track_events, run_summaries = build_track_alignment_events(
            track, track_end, cues, asr_segments, lines, preroll_ms=args.preroll_ms
        )
        track_events = [event for event in track_events if event["start_ms"] < evidence_horizon]
        if track_events:
            covered_track_indices.add(track.index)
        events.extend(track_events)
        summaries.extend({"track": track.title, **summary} for summary in run_summaries)

        # A high-confidence SRT fallback fills isolated cues that local ASR did
        # not segment, without reintroducing low-confidence hallucinations.
        for cue in cues:
            if cue.start_ms >= track_end or cue.end_ms <= track.start_ms - args.preroll_ms:
                continue
            if cue.start_ms >= evidence_horizon:
                continue
            best = best_lrc_sequences(cue.text, lines)
            if not best:
                continue
            candidate = best[0]
            if candidate["score"] < args.fallback_score or candidate["coverage"] < 0.60:
                continue
            events.append(
                {
                    "start_ms": cue.start_ms,
                    "end_ms": cue.end_ms,
                    "text": candidate["text"],
                    "track": track.title,
                    "track_index": track.index,
                    "source": "srt_fallback",
                    "confidence": "fallback",
                    "score": round(candidate["score"], 3),
                    "run": "",
                    "lrc_time_ms": int(candidate["start_time_ms"]),
                    "cue_number": cue.number,
                }
            )

    # Optional audited exceptions are additive and remain visible as
    # ``manual_lrc`` in the review CSV.  They are useful when the audio has a
    # heavily distorted/ad-libbed passage that no ASR model can anchor.
    events.extend(load_manual_events(args.manual_events))

    # Projected LRC lines are the primary canonical events.  The old Jianying
    # cue text is only an isolated fallback: if it overlaps a projected line,
    # it must not extend that line and hide the next lyric (for example,
    # ``One look`` used to swallow ``Beat drop`` at the opening cut).
    projected_events = _merge_close_projection_duplicates(
        [event for event in events if event["source"] in {"projected_lrc", "manual_lrc"}]
    )
    fallback_events = [
        event for event in events if event["source"] not in {"projected_lrc", "manual_lrc"}
    ]
    events = list(projected_events)
    for fallback in fallback_events:
        if any(_event_overlap(fallback, projected) > 0 for projected in projected_events):
            continue
        events.append(fallback)
    _cap_event_ends(events)

    # Keep only the best non-overlapping canonical event around any time point.
    # Equal text occurrences are merged.  With the fallback filtering above,
    # conflicts are normally limited to duplicate projections from adjacent
    # splice runs.
    events.sort(key=lambda row: (row["start_ms"], row["end_ms"]))
    kept: list[dict[str, Any]] = []
    for event in events:
        duplicate = next(
            (
                previous
                for previous in kept
                if text_key(previous["text"]) == text_key(event["text"])
                and _event_overlap(previous, event) > 0
            ),
            None,
        )
        if duplicate is not None:
            duplicate["start_ms"] = min(duplicate["start_ms"], event["start_ms"])
            duplicate["end_ms"] = max(duplicate["end_ms"], event["end_ms"])
            duplicate["score"] = max(duplicate["score"], event["score"])
            continue
        conflicts = [previous for previous in kept if _event_overlap(previous, event) > 0]
        if conflicts:
            source_priority = {
                "manual_lrc": 2,
                "projected_lrc": 1,
                "srt_fallback": 0,
            }
            best = max(
                conflicts + [event],
                key=lambda row: (
                    source_priority.get(row["source"], 0),
                    row["score"],
                    -abs(row["end_ms"] - row["start_ms"]),
                ),
            )
            if best is event:
                kept = [previous for previous in kept if previous not in conflicts]
                kept.append(event)
            continue
        kept.append(event)

    # Retain the opening spoken guidance at its original Jianying times.  It
    # can overlap the first song's lyrics because it is spoken over the music;
    # dropping it would lose audible content and, previously, could also drop
    # a lyric event through the conflict resolver.
    first_song_start = tracks[0].start_ms if tracks else 0
    first_song_lines = (
        canonical_lrc_lines(Path(tracks[0].lrc_path))
        if tracks and tracks[0].lrc_path
        else []
    )
    for cue in cues:
        if cue.end_ms <= first_song_start + args.narration_limit_ms and cue.start_ms < first_song_start + args.narration_limit_ms:
            # Jianying's first lyric cue is interleaved with the spoken intro.
            # Keep the spoken-only cues, but do not duplicate a lyric that is
            # already represented by the canonical LRC projection.
            if first_song_lines:
                lyric_match = best_lrc_sequences(cue.text, first_song_lines)
                if lyric_match and lyric_match[0]["score"] >= 0.75 and lyric_match[0]["coverage"] >= 0.60:
                    continue
            kept.append(
                {
                    "start_ms": cue.start_ms,
                    "end_ms": cue.end_ms,
                    "text": cue.text,
                    "track": "",
                    "track_index": 0,
                    "source": "narration",
                    "confidence": "source",
                    "score": 1.0,
                    "run": "",
                    "lrc_time_ms": "",
                }
            )

    kept.sort(key=lambda row: (row["start_ms"], row["end_ms"]))
    final_cues: list[tuple[SrtCue, str]] = []
    report_rows: list[dict[str, Any]] = []
    for number, event in enumerate(kept, start=1):
        start_ms = max(0, int(event["start_ms"]))
        end_ms = min(active_end_ms, max(start_ms + 50, int(event["end_ms"])))
        synthetic = SrtCue(number, start_ms, end_ms, event["text"])
        final_cues.append((synthetic, event["text"]))
        report_rows.append(
            {
                "status": "emitted",
                "cue": number,
                "start": format_srt_time(start_ms),
                "end": format_srt_time(end_ms),
                "track": event["track"],
                "source": event["source"],
                "confidence": event["confidence"],
                "score": event["score"],
                "lrc_time": round(event["lrc_time_ms"] / 1000, 3) if event["lrc_time_ms"] != "" else "",
                "text": event["text"],
            }
        )

    # Anything in the original song intervals not covered by an emitted event
    # is explicitly listed for review.  The clean aligned SRT does not silently
    # retain the known-bad Jianying hallucination.
    for cue in cues:
        if cue.start_ms < first_song_start + args.narration_limit_ms:
            continue
        if not any(overlap_ms(cue.start_ms, cue.end_ms, event[0].start_ms, event[0].end_ms) > 0 for event in final_cues):
            report_rows.append(
                {
                    "status": "needs_review",
                    "cue": cue.number,
                    "start": format_srt_time(cue.start_ms),
                    "end": format_srt_time(cue.end_ms),
                    "track": "",
                    "source": "jianying_unresolved",
                    "confidence": "",
                    "score": "",
                    "lrc_time": "",
                    "text": cue.text,
                }
            )

    out_path = Path(args.out_srt)
    write_srt(final_cues, out_path)
    report_path = args.out_report or out_path.with_name(out_path.stem + "_review.csv")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["status", "cue", "start", "end", "track", "source", "confidence", "score", "lrc_time", "text"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report_rows)
    summary_path = report_path.with_name(report_path.stem + "_runs.json")
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path} ({len(final_cues)} canonical line cues)")
    print(f"review report: {report_path}")
    print(f"run summary: {summary_path}")
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise SystemExit("faster-whisper is required for transcribe") from exc

    audio = Path(args.audio)
    srt = Path(args.srt)
    song_list = Path(args.song_list)
    lyrics_dir = Path(args.lyrics_dir)
    cues = parse_srt(srt)
    tracks = make_tracks(song_list, lyrics_dir, args.language_hints)
    audio_info = probe_audio(audio)
    overall_end = infer_end_ms(audio_info, cues, args.tail_pad_ms)
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)

    results: list[dict[str, Any]] = []
    for position, track in enumerate(tracks):
        start_ms = track.start_ms
        next_start = tracks[position + 1].start_ms if position + 1 < len(tracks) else overall_end
        end_ms = min(next_start, overall_end)
        if end_ms <= start_ms:
            continue
        kwargs: dict[str, Any] = {
            "clip_timestamps": f"{start_ms / 1000:.3f},{end_ms / 1000:.3f}",
            "beam_size": args.beam_size,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "vad_filter": False,
            "word_timestamps": True,
        }
        if track.language_hint:
            kwargs["language"] = track.language_hint
        segments, info = model.transcribe(str(audio), **kwargs)
        segment_rows = [segment_to_dict(segment) for segment in segments]
        results.append(
            {
                "track": asdict(track),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "detected_language": info.language,
                "language_probability": info.language_probability,
                "segments": segment_rows,
            }
        )
        print(
            f"[{position + 1}/{len(tracks)}] {track.title}: "
            f"{len(segment_rows)} segments, language={info.language} "
            f"p={info.language_probability:.3f}",
            flush=True,
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(
            build_manifest(audio, srt, song_list, lyrics_dir, args.language_hints),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "tracks": results,
    }
    (out_dir / "asr_segments.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--audio", required=True, type=Path)
    common.add_argument("--srt", required=True, type=Path)
    common.add_argument("--song-list", required=True, type=Path)
    common.add_argument("--lyrics-dir", required=True, type=Path)
    common.add_argument("--out-dir", default=Path("output/job/diagnostics"), type=Path)
    common.add_argument(
        "--language-hints",
        default=None,
        type=Path,
        help="Optional JSON config with language_hints; local profile is auto-detected when present.",
    )

    inspect = sub.add_parser("inspect", parents=[common])
    inspect.set_defaults(func=cmd_inspect)

    transcribe = sub.add_parser("transcribe", parents=[common])
    transcribe.add_argument("--model", default="small")
    transcribe.add_argument("--device", default="cpu")
    transcribe.add_argument("--compute-type", default="int8")
    transcribe.add_argument("--beam-size", default=5, type=int)
    transcribe.add_argument("--tail-pad-ms", default=30_000, type=int)
    transcribe.set_defaults(func=cmd_transcribe)

    correct = sub.add_parser(
        "correct",
        parents=[common],
        help="Create an auditable canonical-text draft using SRT, LRC and ASR evidence.",
    )
    correct.add_argument("--asr-json", required=True, type=Path)
    correct.add_argument(
        "--out-srt", default=Path("output/job/diagnostics/corrected_draft.srt"), type=Path
    )
    correct.add_argument("--out-report", default=None, type=Path)
    correct.add_argument("--min-score", default=0.48, type=float)
    correct.set_defaults(func=cmd_correct)

    source_timed = sub.add_parser(
        "source-timed",
        parents=[common],
        help="Correct lyric text while preserving every original Jianying SRT interval.",
    )
    source_timed.add_argument("--asr-json", required=True, type=Path)
    source_timed.add_argument("--manual-events", default=None, type=Path)
    source_timed.add_argument(
        "--out-srt",
        default=Path("output/job/diagnostics/source_timed_canonical.srt"),
        type=Path,
    )
    source_timed.add_argument("--out-report", default=None, type=Path)
    source_timed.add_argument("--projection-score", default=0.72, type=float)
    source_timed.add_argument("--direct-score", default=0.72, type=float)
    source_timed.add_argument("--direct-coverage", default=0.55, type=float)
    source_timed.add_argument("--text-match-score", default=0.55, type=float)
    source_timed.add_argument("--max-projection-gap-ms", default=5000, type=int)
    source_timed.add_argument("--boundary-tolerance-ms", default=1000, type=int)
    source_timed.add_argument("--single-projection-max-gap-ms", default=500, type=int)
    source_timed.add_argument("--preroll-ms", default=3000, type=int)
    source_timed.set_defaults(func=cmd_source_timed)

    align = sub.add_parser(
        "align",
        parents=[common],
        help="Build a line-level canonical SRT with piecewise timing fits from SRT/ASR anchors.",
    )
    align.add_argument("--asr-json", required=True, type=Path)
    align.add_argument(
        "--out-srt",
        default=Path("output/job/diagnostics/canonical_line_aligned.srt"),
        type=Path,
    )
    align.add_argument("--out-report", default=None, type=Path)
    align.add_argument("--fallback-score", default=0.75, type=float)
    align.add_argument("--preroll-ms", default=3000, type=int)
    align.add_argument("--tail-pad-ms", default=30_000, type=int)
    align.add_argument("--narration-limit-ms", default=10_000, type=int)
    align.add_argument(
        "--evidence-tail-ms",
        default=2_000,
        type=int,
        help="Allow this much lyric extrapolation after the last SRT/ASR evidence in a track.",
    )
    align.add_argument(
        "--manual-events",
        default=None,
        type=Path,
        help="Optional JSON list of hand-audited absolute events to merge into the aligned SRT.",
    )
    align.set_defaults(func=cmd_align)
    return parser


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parsed = build_parser().parse_args()
    raise SystemExit(parsed.func(parsed))
