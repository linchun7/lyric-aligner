#!/usr/bin/env python3
"""Lyric correction and alignment pipeline for edited multilingual song mixes.

Existing Jianying cues are timing anchors.  Canonical lyrics may be inserted
only where Jianying created no cue, and a cue boundary may be changed only
with stronger audio or explicit manual evidence.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$")
LRC_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)")
META_RE = re.compile(
    r"^(?:\[?by:|作词|作曲|编曲|词\s*:|曲\s*:|制作人|人声采样|未经|版权|发行|混音|母带|企划|出品人|op\s*:|sp\s*:|本作品)",
    re.IGNORECASE,
)
ALGORITHM_VERSION = "3.7"


@dataclass(frozen=True)
class Cue:
    number: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Track:
    index: int
    start_ms: int
    end_ms: int
    artist: str
    title: str
    lrc_path: str


@dataclass(frozen=True)
class LyricLine:
    index: int
    time_ms: int
    text: str


def read_text(path: Path) -> str:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_srt_scope(
    payload: dict, source_srt: Path, artifact_label: str
) -> tuple[str, str | None]:
    """Validate the source-SRT scope guard on a task QA artifact.

    ``source_srt_sha256`` is the canonical field.  The underscored spelling
    is accepted only as a migration path for existing local override files;
    new files must use the canonical spelling.
    """

    actual_hash = sha256(source_srt).lower()
    canonical = str(payload.get("source_srt_sha256", "")).strip().lower()
    legacy = str(payload.get("_source_srt_sha256", "")).strip().lower()
    if canonical and legacy and canonical != legacy:
        return actual_hash, f"{artifact_label} has conflicting source SRT hashes"
    expected_hash = canonical or legacy
    if not expected_hash:
        return actual_hash, f"{artifact_label} has no source_srt_sha256 guard"
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return actual_hash, f"{artifact_label} has an invalid source_srt_sha256 guard"
    if expected_hash != actual_hash:
        return (
            actual_hash,
            f"{artifact_label} belongs to a different source SRT: "
            f"expected {expected_hash}, got {actual_hash}",
        )
    return actual_hash, None


def parse_srt_time(value: str) -> int:
    match = TIME_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid SRT time: {value!r}")
    hour, minute, second, millis = (int(part) for part in match.groups())
    return ((hour * 60 + minute) * 60 + second) * 1000 + millis


def format_srt_time(value: int) -> str:
    hour, remain = divmod(value, 3_600_000)
    minute, remain = divmod(remain, 60_000)
    second, millis = divmod(remain, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def parse_srt(path: Path) -> list[Cue]:
    text = read_text(path).replace("\r\n", "\n").strip()
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = (part.strip() for part in lines[1].split("-->", 1))
        cues.append(
            Cue(
                number=int(lines[0].strip()),
                start_ms=parse_srt_time(start),
                end_ms=parse_srt_time(end),
                text="\n".join(lines[2:]).strip(),
            )
        )
    if not cues:
        raise ValueError(f"no SRT cues parsed from {path}")
    return cues


def write_srt(cues: Iterable[Cue], replacements: dict[int, str], path: Path) -> None:
    blocks: list[str] = []
    for cue in cues:
        blocks.append(
            f"{cue.number}\n{format_srt_time(cue.start_ms)} --> {format_srt_time(cue.end_ms)}\n"
            f"{replacements.get(cue.number, cue.text)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig", newline="\n")


def title_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def parse_bpm_changes(path: Path | None) -> list[dict]:
    """Parse labels such as ``Artist - Title103-130 - ...wav``.

    The first BPM is the source recording and the second is the edited mix.
    Their quotient is the expected source-time / mix-time slope.
    """

    if not path:
        return []
    rows: list[dict] = []
    pattern = re.compile(
        r"^(?P<label>.+?)(?P<source_bpm>\d+(?:\.\d+)?)-"
        r"(?P<mix_bpm>\d+(?:\.\d+)?)\s+-"
    )
    for raw in read_text(path).splitlines():
        match = pattern.match(raw.strip())
        if not match:
            continue
        source_bpm = float(match.group("source_bpm"))
        mix_bpm = float(match.group("mix_bpm"))
        if source_bpm <= 0 or mix_bpm <= 0:
            continue
        rows.append(
            {
                "label": match.group("label").strip(),
                "source_bpm": source_bpm,
                "mix_bpm": mix_bpm,
                "tempo_ratio": mix_bpm / source_bpm,
            }
        )
    return rows


def bpm_change_for_track(track: Track, changes: list[dict]) -> dict | None:
    if not changes:
        return None
    target = title_key(f"{track.artist} {track.title}")
    ranked = sorted(
        changes,
        key=lambda row: difflib.SequenceMatcher(
            None, target, title_key(str(row["label"]))
        ).ratio(),
        reverse=True,
    )
    if not ranked:
        return None
    score = difflib.SequenceMatcher(
        None, target, title_key(str(ranked[0]["label"]))
    ).ratio()
    return {**ranked[0], "match_score": score} if score >= 0.62 else None


def parse_song_list(path: Path, lyrics_dir: Path, final_end_ms: int) -> list[Track]:
    raw: list[tuple[int, str, str]] = []
    for line in read_text(path).splitlines():
        if not line.strip():
            continue
        stamp, label = re.split(r"\s+", line.strip(), maxsplit=1)
        minute, second = (int(part) for part in stamp.split(":"))
        artist, title = (part.strip() for part in label.split(" - ", 1))
        raw.append(((minute * 60 + second) * 1000, artist, title))

    lrc_files = list(lyrics_dir.glob("*.lrc"))
    tracks: list[Track] = []
    for idx, (start_ms, artist, title) in enumerate(raw, start=1):
        exact = [path for path in lrc_files if title_key(title) in title_key(path.stem)]
        if not exact:
            ranked = sorted(
                lrc_files,
                key=lambda candidate: difflib.SequenceMatcher(
                    None, title_key(title), title_key(candidate.stem)
                ).ratio(),
                reverse=True,
            )
            exact = ranked[:1]
        end_ms = raw[idx][0] if idx < len(raw) else final_end_ms
        tracks.append(
            Track(
                index=idx,
                start_ms=start_ms,
                end_ms=end_ms,
                artist=artist,
                title=title,
                lrc_path=str(exact[0].resolve()) if exact else "",
            )
        )
    return tracks


def parse_lrc(path: Path) -> list[LyricLine]:
    grouped: dict[int, list[str]] = {}
    for raw_line in read_text(path).splitlines():
        match = LRC_RE.match(raw_line.strip())
        if not match:
            continue
        minute, second, fraction, text = match.groups()
        fraction = fraction or "0"
        millis = int(fraction.ljust(3, "0")[:3])
        time_ms = (int(minute) * 60 + int(second)) * 1000 + millis
        text = text.strip()
        if not text or META_RE.match(text):
            continue
        grouped.setdefault(time_ms, []).append(text)

    result: list[LyricLine] = []
    for time_ms, alternatives in sorted(grouped.items()):
        # LRCs in this project place the original lyric first and translations
        # at the same timestamp.  Preserve the original wording verbatim.
        text = clean_canonical_text(alternatives[0])
        result.append(LyricLine(index=len(result), time_ms=time_ms, text=text))
    return result


def clean_canonical_text(value: str) -> str:
    """Repair unambiguous token-joining defects in supplied LRC text."""

    fixes = {
        "Causeit's": "Cause it's",
        "causeit's": "cause it's",
        "togetherlet's": "together, let's",
        " Idon't": " I don't",
        " goldI'm": " gold, I'm",
        "goldI'm": "gold, I'm",
        " likenothing's": " like nothing's",
        "likenothing's": "like nothing's",
    }
    for old, new in fixes.items():
        value = value.replace(old, new)
    return re.sub(r"\s+", " ", value).strip()


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    replacements = {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "—": "-",
        "–": "-",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return "".join(ch for ch in value if ch.isalnum())


def text_similarity(left: str, right: str) -> tuple[float, float]:
    a, b = normalized_text(left), normalized_text(right)
    if not a or not b:
        return 0.0, 0.0
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    coverage = min(len(a), len(b)) / max(len(a), len(b))
    if a in b or b in a:
        ratio = max(ratio, 0.45 + 0.5 * coverage)
    return ratio, coverage


HANGUL_INITIAL = ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h")
HANGUL_VOWEL = ("a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i")
HANGUL_FINAL = ("", "k", "k", "ks", "n", "n", "nh", "t", "l", "lk", "lm", "lp", "ls", "lt", "lp", "lh", "m", "p", "ps", "t", "t", "ng", "t", "t", "k", "t", "p", "h")


def romanize_hangul(value: str) -> str:
    output: list[str] = []
    for char in value:
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            offset = code - 0xAC00
            initial = offset // 588
            vowel = (offset % 588) // 28
            final = offset % 28
            output.append(HANGUL_INITIAL[initial] + HANGUL_VOWEL[vowel] + HANGUL_FINAL[final])
        elif char.isascii():
            output.append(char)
        else:
            output.append(" ")
    return "".join(output)


def span_similarity(observation: str, candidate: str) -> tuple[float, float, str]:
    direct_score, direct_coverage = text_similarity(observation, candidate)
    if not re.search(r"[가-힣]", candidate):
        return direct_score, direct_coverage, "text"
    pronunciation_score, pronunciation_coverage = text_similarity(
        observation, romanize_hangul(candidate)
    )
    if pronunciation_score > direct_score:
        return pronunciation_score, pronunciation_coverage, "korean_pronunciation"
    return direct_score, direct_coverage, "text"


def best_text_span(observation: str, canonical: str) -> tuple[str, float, float, str]:
    """Find the canonical word span represented by an existing SRT cue."""

    words = canonical.split()
    if len(words) <= 1:
        score, coverage, mode = span_similarity(observation, canonical)
        return canonical, score, coverage, mode
    observation_words = max(1, len(observation.split()))
    base_score, base_coverage, base_mode = span_similarity(observation, canonical)
    best = (canonical, base_score, base_coverage, base_mode)
    for length in range(max(1, observation_words - 2), min(len(words), observation_words + 3) + 1):
        for start in range(0, len(words) - length + 1):
            span = " ".join(words[start : start + length])
            score, coverage, mode = span_similarity(observation, span)
            if (score, coverage) > (best[1], best[2]):
                best = (span, score, coverage, mode)
    return best


def cue_track(cue: Cue, tracks: list[Track]) -> Track:
    for track in tracks:
        if track.start_ms <= cue.start_ms < track.end_ms:
            return track
    return tracks[-1]


def monotonic_text_candidates(cues: list[Cue], lines: list[LyricLine]) -> dict[int, dict]:
    """Align ordered cue text to ordered LRC lines using dynamic programming.

    This stage deliberately uses text evidence only.  Audio alignment is added
    later and cannot turn a weak textual match into an automatic replacement.
    """

    n, m = len(cues), len(lines)
    neg = -10**9
    score = [[neg] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    score[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            current = score[i][j]
            if current <= neg / 2:
                continue
            if i < n and current - 0.35 > score[i + 1][j]:
                score[i + 1][j] = current - 0.35
                back[i + 1][j] = (i, j, 0)  # skip cue
            if j < m and current - 0.12 > score[i][j + 1]:
                score[i][j + 1] = current - 0.12
                back[i][j + 1] = (i, j, -1)  # skip lyric
            if i < n:
                for length in (1, 2, 3):
                    if j + length > m:
                        break
                    candidate = " ".join(line.text for line in lines[j : j + length])
                    similarity, coverage = text_similarity(cues[i].text, candidate)
                    if similarity < 0.34 or coverage < 0.28:
                        continue
                    reward = 2.8 * similarity + 0.8 * coverage - 0.18 * (length - 1)
                    if current + reward > score[i + 1][j + length]:
                        score[i + 1][j + length] = current + reward
                        back[i + 1][j + length] = (i, j, length)

    # The best path may legitimately leave trailing LRC lines unused.
    end_j = max(range(m + 1), key=lambda col: score[n][col])
    i, j = n, end_j
    matches: dict[int, dict] = {}
    while i > 0 or j > 0:
        previous = back[i][j]
        if previous is None:
            break
        pi, pj, length = previous
        if length > 0:
            candidate = " ".join(line.text for line in lines[pj:j])
            similarity, coverage = text_similarity(cues[pi].text, candidate)
            matches[cues[pi].number] = {
                "candidate": candidate,
                "score": round(similarity, 6),
                "coverage": round(coverage, 6),
                "lrc_start_index": pj,
                "lrc_end_index": j - 1,
                "lrc_time_ms": lines[pj].time_ms,
                "evidence": "monotonic_text",
            }
        i, j = pi, pj
    return matches


def find_source_audio(track: Track, source_dir: Path) -> Path:
    candidates = [
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg"}
    ]
    title = title_key(track.title)
    containing = [path for path in candidates if title and title in title_key(path.stem)]
    if containing:
        return max(
            containing,
            key=lambda path: difflib.SequenceMatcher(
                None, title_key(f"{track.artist}{track.title}"), title_key(path.stem)
            ).ratio(),
        )
    ranked = sorted(
        candidates,
        key=lambda path: difflib.SequenceMatcher(None, title, title_key(path.stem)).ratio(),
        reverse=True,
    )
    if not ranked:
        raise FileNotFoundError(f"no source audio files in {source_dir}")
    return ranked[0]


def estimate_tempo_ratio(text_matches: dict[int, dict], cues: list[Cue], track_start_ms: int) -> float:
    anchors: list[tuple[float, float]] = []
    cue_by_number = {cue.number: cue for cue in cues}
    for number, match in text_matches.items():
        if match["score"] < 0.88 or match["coverage"] < 0.70:
            continue
        cue = cue_by_number[number]
        anchors.append(((cue.start_ms - track_start_ms) / 1000.0, match["lrc_time_ms"] / 1000.0))
    anchors.sort()
    slopes: list[float] = []
    for left, right in zip(anchors, anchors[1:]):
        mix_delta = right[0] - left[0]
        source_delta = right[1] - left[1]
        if 2.0 <= mix_delta <= 35.0 and source_delta > 0:
            slope = source_delta / mix_delta
            if 0.65 <= slope <= 1.55:
                slopes.append(slope)
    if not slopes:
        return 1.0
    slopes.sort()
    return slopes[len(slopes) // 2]


def waveform_candidates(query, reference, sample_rate: int, count: int = 10) -> list[dict]:
    import numpy as np
    from scipy.signal import fftconvolve, find_peaks

    query = query.astype(np.float32, copy=False)
    reference = reference.astype(np.float32, copy=False)
    query = query - float(query.mean())
    query_norm = float(np.linalg.norm(query)) + 1e-9
    query = query / query_norm
    correlation = fftconvolve(reference, query[::-1], mode="valid")
    window_energy = np.sqrt(
        np.maximum(
            fftconvolve(reference * reference, np.ones(len(query), dtype=np.float32), mode="valid"),
            1e-12,
        )
    )
    ncc = correlation / window_energy
    peaks, _ = find_peaks(ncc, distance=max(1, int(sample_rate * 1.5)))
    if not len(peaks):
        peaks = np.array([int(np.argmax(ncc))])
    ranked = peaks[np.argsort(ncc[peaks])[-count:]][::-1]
    return [
        {"source_start": float(index / sample_rate), "ncc": float(ncc[index])}
        for index in ranked
    ]


def choose_audio_path(
    windows: list[dict], tempo_ratio: float, text_anchors: list[tuple[float, float]]
) -> list[dict]:
    """Select a coherent candidate sequence while allowing real edit jumps."""

    if not windows:
        return []
    states: list[list[float]] = []
    parents: list[list[int]] = []
    first_scores: list[float] = []
    for candidate in windows[0]["candidates"]:
        score = candidate["ncc"] * 5.0
        for mix_time, source_time in text_anchors:
            if abs(mix_time - windows[0]["mix_center"]) <= 4.0:
                score += max(0.0, 1.2 - abs(source_time - candidate["source_center"]) / 2.0)
        first_scores.append(score)
    states.append(first_scores)
    parents.append([-1] * len(first_scores))

    for position in range(1, len(windows)):
        previous_window = windows[position - 1]
        window = windows[position]
        delta_mix = window["mix_center"] - previous_window["mix_center"]
        row_scores: list[float] = []
        row_parents: list[int] = []
        for candidate in window["candidates"]:
            emission = candidate["ncc"] * 5.0
            for mix_time, source_time in text_anchors:
                if abs(mix_time - window["mix_center"]) <= 4.0:
                    emission += max(0.0, 1.2 - abs(source_time - candidate["source_center"]) / 2.0)
            best_score = -10**9
            best_parent = 0
            for parent_index, parent in enumerate(previous_window["candidates"]):
                delta_source = candidate["source_center"] - parent["source_center"]
                expected = tempo_ratio * delta_mix
                error = abs(delta_source - expected)
                if error <= 1.25:
                    transition = 1.0 - 0.45 * error
                elif delta_source >= 0:
                    # A forward jump is a plausible edit cut.
                    transition = -0.65 - min(error, 30.0) * 0.01
                else:
                    # Backward jumps are possible for repeated choruses but rarer.
                    transition = -1.0 - min(error, 30.0) * 0.015
                value = states[-1][parent_index] + transition + emission
                if value > best_score:
                    best_score = value
                    best_parent = parent_index
            row_scores.append(best_score)
            row_parents.append(best_parent)
        states.append(row_scores)
        parents.append(row_parents)

    selected = [0] * len(windows)
    selected[-1] = max(range(len(states[-1])), key=lambda index: states[-1][index])
    for position in range(len(windows) - 1, 0, -1):
        selected[position - 1] = parents[position][selected[position]]
    result: list[dict] = []
    for window, candidate_index in zip(windows, selected):
        result.append({**window, "selected": window["candidates"][candidate_index]})
    return result


def audio_segments(path: list[dict], tempo_ratio: float) -> list[dict]:
    """Fit piecewise source_time = slope * mix_time + intercept mappings."""

    import numpy as np

    if not path:
        return []
    groups: list[list[dict]] = [[path[0]]]
    for row in path[1:]:
        previous = groups[-1][-1]
        delta_mix = row["mix_center"] - previous["mix_center"]
        delta_source = row["selected"]["source_center"] - previous["selected"]["source_center"]
        if abs(delta_source - tempo_ratio * delta_mix) <= 1.75:
            groups[-1].append(row)
        else:
            groups.append([row])

    segments: list[dict] = []
    for group in groups:
        if len(group) < 2:
            continue
        mix = np.array([row["mix_center"] for row in group], dtype=float)
        source = np.array([row["selected"]["source_center"] for row in group], dtype=float)
        weights = np.array([max(row["selected"]["ncc"], 0.01) for row in group], dtype=float)
        if len(group) >= 3:
            slope, intercept = np.polyfit(mix, source, 1, w=weights)
        else:
            slope = tempo_ratio
            intercept = float(np.average(source - slope * mix, weights=weights))
        residuals = source - (slope * mix + intercept)
        segments.append(
            {
                "mix_start": float(max(0.0, mix.min() - 3.0)),
                "mix_end": float(mix.max() + 3.0),
                "source_start": float(slope * max(0.0, mix.min() - 3.0) + intercept),
                "source_end": float(slope * (mix.max() + 3.0) + intercept),
                "slope": float(slope),
                "intercept": float(intercept),
                "anchor_count": len(group),
                "mean_ncc": float(weights.mean()),
                "max_residual": float(np.abs(residuals).max()),
            }
        )
    return segments


def audio_edit_candidates(
    segments: list[dict],
    tempo_ratio: float,
    text_anchors: list[tuple[float, float]] | None = None,
) -> list[dict]:
    """Find possible source-time jumps between continuous waveform segments.

    Candidates are never applied automatically.  A model/user must confirm the
    jump after checking Jianying cues and the surrounding canonical lyrics.
    """

    ordered = sorted(segments, key=lambda row: float(row["mix_start"]))
    candidates: list[dict] = []
    for left, right in zip(ordered, ordered[1:]):
        mix_time = (float(left["mix_end"]) + float(right["mix_start"])) / 2.0
        source_before = float(left["slope"]) * mix_time + float(left["intercept"])
        source_after = float(right["slope"]) * mix_time + float(right["intercept"])
        skipped = source_after - source_before
        if skipped < max(2.0, float(tempo_ratio) * 1.5):
            continue
        anchors = sorted(text_anchors or [])
        left_text = max((row for row in anchors if row[0] <= mix_time), default=None)
        right_text = min((row for row in anchors if row[0] >= mix_time), default=None)
        text_skip = None
        text_supported = False
        if left_text and right_text and right_text[0] > left_text[0]:
            text_skip = (right_text[1] - left_text[1]) - float(tempo_ratio) * (
                right_text[0] - left_text[0]
            )
            text_supported = text_skip > 1.5 and abs(text_skip - skipped) <= 2.5
        reliable_waveform = (
            int(left.get("anchor_count", 0)) >= 2
            and int(right.get("anchor_count", 0)) >= 2
            and abs(float(left["slope"]) - float(tempo_ratio)) / float(tempo_ratio) <= 0.04
            and abs(float(right["slope"]) - float(tempo_ratio)) / float(tempo_ratio) <= 0.04
            and float(left.get("max_residual", 999.0)) <= 0.8
            and float(right.get("max_residual", 999.0)) <= 0.8
        )
        status = (
            "review"
            if reliable_waveform and (text_supported or len(anchors) < 2)
            else "informational"
        )
        candidates.append(
            {
                "type": "forward_source_cut",
                "status": status,
                "mix_time": round(mix_time, 6),
                "source_start": round(source_before, 6),
                "source_end": round(source_after, 6),
                "skipped_source_seconds": round(skipped, 6),
                "left_anchor_count": int(left.get("anchor_count", 0)),
                "right_anchor_count": int(right.get("anchor_count", 0)),
                "left_residual": float(left.get("max_residual", 999.0)),
                "right_residual": float(right.get("max_residual", 999.0)),
                "text_anchor_supported": text_supported,
                "text_anchor_skipped_source_seconds": (
                    round(float(text_skip), 6) if text_skip is not None else None
                ),
                "reason": "waveform source timeline jumps forward; confirm with model/context",
            }
        )
    return candidates


def project_source_with_confirmed_cuts(
    source_seconds: float, mapping: dict, audio_track: dict
) -> tuple[float | None, str]:
    """Project one source timestamp through model-confirmed forward cuts."""

    confirmed = sorted(
        (
            row
            for row in audio_track.get("edit_candidates", [])
            if row.get("status") == "confirmed"
            and row.get("type") == "forward_source_cut"
        ),
        key=lambda row: float(row["mix_time"]),
    )
    for cut in confirmed:
        if float(cut["source_start"]) <= source_seconds < float(cut["source_end"]):
            return None, "confirmed_source_cut"
    if not confirmed:
        return (
            (source_seconds - float(mapping["intercept"])) / float(mapping["slope"]),
            str(mapping["method"]),
        )

    # Fit the constant BPM slope separately on each side of every confirmed
    # edit.  A cut changes the intercept, not the playback-rate slope.
    slope = float(audio_track.get("bpm_tempo_ratio") or mapping["slope"])
    piece = 0
    for position, cut in enumerate(confirmed):
        if source_seconds >= float(cut["source_end"]):
            piece = position + 1
    lower_mix = float(confirmed[piece - 1]["mix_time"]) if piece else float("-inf")
    upper_mix = (
        float(confirmed[piece]["mix_time"])
        if piece < len(confirmed)
        else float("inf")
    )
    intercepts = sorted(
        float(row["selected"]["source_center"]) - slope * float(row["mix_center"])
        for row in audio_track.get("path", [])
        if lower_mix <= float(row["mix_center"]) < upper_mix
    )
    if intercepts:
        intercept = intercepts[len(intercepts) // 2]
    elif piece:
        cut = confirmed[piece - 1]
        intercept = float(cut["source_end"]) - slope * float(cut["mix_time"])
    else:
        intercept = float(mapping["intercept"])
    return (source_seconds - intercept) / slope, "confirmed_cut_piecewise_audio_mapping"


def robust_linear_mapping(
    mix_times: list[float], source_times: list[float], residual_limit: float = 1.5
) -> dict:
    import numpy as np
    from scipy.stats import theilslopes

    x = np.asarray(mix_times, dtype=float)
    y = np.asarray(source_times, dtype=float)
    if len(x) < 2:
        raise ValueError("at least two mapping anchors are required")
    slope = float(theilslopes(y, x)[0]) if len(x) >= 3 else float((y[1] - y[0]) / (x[1] - x[0]))
    intercept = float(np.median(y - slope * x))
    residuals = y - (slope * x + intercept)
    inliers = np.abs(residuals) <= residual_limit
    if int(inliers.sum()) >= 3:
        slope = float(theilslopes(y[inliers], x[inliers])[0])
        intercept = float(np.median(y[inliers] - slope * x[inliers]))
        residuals = y - (slope * x + intercept)
        inliers = np.abs(residuals) <= residual_limit
    return {
        "slope": slope,
        "intercept": intercept,
        "anchor_count": int(len(x)),
        "inlier_count": int(inliers.sum()),
        "median_abs_residual": float(np.median(np.abs(residuals[inliers]))) if inliers.any() else 999.0,
        "max_inlier_residual": float(np.max(np.abs(residuals[inliers]))) if inliers.any() else 999.0,
    }


def derive_track_mapping(
    track: Track,
    track_cues: list[Cue],
    lines: list[LyricLine],
    audio_track: dict,
) -> dict:
    text_matches = monotonic_text_candidates(track_cues, lines)
    cue_by_number = {cue.number: cue for cue in track_cues}
    text_pairs: list[tuple[float, float]] = []
    for number, match in text_matches.items():
        if match["score"] < 0.90 or match["coverage"] < 0.78:
            continue
        cue = cue_by_number[number]
        text_pairs.append(
            ((cue.start_ms - track.start_ms) / 1000.0, match["lrc_time_ms"] / 1000.0)
        )

    bpm_ratio = audio_track.get("bpm_tempo_ratio")
    if bpm_ratio and len(text_pairs) < 5:
        import numpy as np

        audio_pairs = [
            (row["mix_center"], row["selected"]["source_center"])
            for row in audio_track.get("path", [])
        ]
        if not audio_pairs and not text_pairs:
            raise ValueError(f"no mapping anchors for {track.title}")
        audio_intercepts = [
            source_time - float(bpm_ratio) * mix_time
            for mix_time, source_time in audio_pairs
        ]
        audio_intercept = (
            float(np.median(audio_intercepts)) if audio_intercepts else None
        )
        # A single repeated phrase (for example ``na na``) is not a safe global
        # anchor.  Accept text anchors only when at least two agree with the
        # tempo-normalized waveform intercept.
        consistent_text_pairs = [
            pair
            for pair in text_pairs
            if audio_intercept is None
            or abs(pair[1] - float(bpm_ratio) * pair[0] - audio_intercept) <= 1.8
        ]
        use_text_pairs = (
            consistent_text_pairs if len(consistent_text_pairs) >= 2 else []
        )
        intercept_pairs = audio_pairs + use_text_pairs
        intercept_values = [
            source_time - float(bpm_ratio) * mix_time
            for mix_time, source_time in intercept_pairs
        ]
        intercept = float(np.median(intercept_values))
        residuals = [
            abs(source_time - (float(bpm_ratio) * mix_time + intercept))
            for mix_time, source_time in intercept_pairs
        ]
        mapping = {
            "slope": float(bpm_ratio),
            "intercept": intercept,
            "anchor_count": len(intercept_pairs),
            "inlier_count": sum(value <= 1.8 for value in residuals),
            "median_abs_residual": float(np.median(residuals)),
            "max_inlier_residual": max(
                [value for value in residuals if value <= 1.8], default=999.0
            ),
            "method": (
                "bpm_prior+tempo_normalized_waveform+text_anchors"
                if use_text_pairs
                else "bpm_prior+tempo_normalized_waveform"
            ),
            "rejected_text_anchor_count": len(text_pairs) - len(use_text_pairs),
        }
    elif len(text_pairs) >= 5:
        mapping = robust_linear_mapping(
            [row[0] for row in text_pairs], [row[1] for row in text_pairs], residual_limit=1.8
        )
        mapping["method"] = "high_confidence_text_anchors"
    else:
        audio_pairs = [
            (row["mix_center"], row["selected"]["source_center"])
            for row in audio_track.get("path", [])
        ]
        mapping = robust_linear_mapping(
            [row[0] for row in audio_pairs], [row[1] for row in audio_pairs], residual_limit=1.5
        )
        mapping["method"] = "original_audio_waveform"
    mapping["text_anchor_count"] = len(text_pairs)
    mapping["bpm_tempo_ratio"] = bpm_ratio
    mapping["bpm_slope_relative_delta"] = (
        abs(float(mapping["slope"]) - float(bpm_ratio)) / float(bpm_ratio)
        if bpm_ratio
        else None
    )
    return mapping


def projected_lyric_events(
    tracks: list[Track], cues: list[Cue], audio_payload: dict
) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    mappings: list[dict] = []
    audio_by_index = {
        int(row["track"]["index"]): row for row in audio_payload.get("tracks", [])
    }
    for track in tracks:
        lines = parse_lrc(Path(track.lrc_path)) if track.lrc_path else []
        track_cues = [cue for cue in cues if cue_track(cue, tracks).index == track.index]
        mapping = derive_track_mapping(track, track_cues, lines, audio_by_index[track.index])
        audio_track = audio_by_index[track.index]
        confirmed_cuts = [
            row
            for row in audio_track.get("edit_candidates", [])
            if row.get("status") == "confirmed"
        ]
        mapping_row = {
            "track": asdict(track),
            **mapping,
            "confirmed_cut_count": len(confirmed_cuts),
            "confirmed_cuts": confirmed_cuts,
            "cut_out_events": [],
        }
        mappings.append(mapping_row)
        for line in lines:
            source_seconds = line.time_ms / 1000.0
            mix_local, projection_method = project_source_with_confirmed_cuts(
                source_seconds, mapping, audio_track
            )
            if mix_local is None:
                mapping_row["cut_out_events"].append(
                    {
                        "lrc_index": line.index,
                        "lrc_time_ms": line.time_ms,
                        "text": line.text,
                        "reason": projection_method,
                    }
                )
                continue
            mix_ms = int(round(track.start_ms + mix_local * 1000.0))
            inside_window = track.start_ms <= mix_ms < track.end_ms
            nearby_text_match = False
            if not inside_window and track.start_ms - 3500 <= mix_ms <= track.end_ms + 3500:
                for cue in cues:
                    if abs(cue.start_ms - mix_ms) > 1800:
                        continue
                    similarity, coverage = text_similarity(cue.text, line.text)
                    if similarity >= 0.78 and coverage >= 0.55:
                        nearby_text_match = True
                        break
            near_boundary = abs(mix_ms - track.start_ms) <= 750 or abs(mix_ms - track.end_ms) <= 750
            if inside_window or near_boundary or nearby_text_match:
                events.append(
                    {
                        "track_index": track.index,
                        "track": track.title,
                        "lrc_index": line.index,
                        "lrc_time_ms": line.time_ms,
                        "projected_ms": mix_ms,
                        "text": line.text,
                        "mapping_method": projection_method,
                        "mapping_residual": mapping["median_abs_residual"],
                    }
                )
    events.sort(key=lambda row: (row["projected_ms"], row["track_index"], row["lrc_index"]))
    return events, mappings


def global_sequence_alignment(
    observations: list[dict],
    events: list[dict],
    *,
    max_span: int = 3,
    max_time_gap_ms: int = 5500,
) -> dict:
    """Globally align ordered observations to ordered canonical lyric events.

    Independent nearest-cue assignment is easily confused by repeated hooks or
    two LRC timestamps that are very close together.  This Viterbi-style path
    makes every cue and every projected LRC event take an explicit state:
    matched, skipped observation, or skipped lyric.  Matching is monotonic and
    may consume several consecutive LRC events when Jianying merged lines.

    The result is evidence, not an unconditional rewrite.  Callers apply it
    only when it is materially stronger than their existing assignment.
    """

    ordered_observations = sorted(
        observations, key=lambda row: (int(row["start_ms"]), int(row["end_ms"]))
    )
    ordered_events = sorted(events, key=lambda row: int(row["lrc_index"]))
    n, m = len(ordered_observations), len(ordered_events)
    neg = -10**12
    scores = [[neg] * (m + 1) for _ in range(n + 1)]
    backs: list[list[dict | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    scores[0][0] = 0.0

    for i in range(n + 1):
        for j in range(m + 1):
            current = scores[i][j]
            if current <= neg / 2:
                continue
            if i < n:
                observation_text = str(ordered_observations[i].get("text", ""))
                cue_penalty = -0.06 if (
                    len(normalized_text(observation_text)) < 2
                    or is_generic_vocalization(observation_text)
                ) else -0.34
                if current + cue_penalty > scores[i + 1][j]:
                    scores[i + 1][j] = current + cue_penalty
                    backs[i + 1][j] = {
                        "previous": (i, j),
                        "kind": "skip_observation",
                    }
            if j < m:
                lyric_penalty = (
                    -0.18
                    if is_generic_vocalization(str(ordered_events[j].get("text", "")))
                    else -0.62
                )
                if current + lyric_penalty > scores[i][j + 1]:
                    scores[i][j + 1] = current + lyric_penalty
                    backs[i][j + 1] = {
                        "previous": (i, j),
                        "kind": "skip_lyric",
                        "event_position": j,
                    }
            if i >= n or j >= m:
                continue

            observation = ordered_observations[i]
            for length in range(1, max_span + 1):
                if j + length > m:
                    break
                span_events = ordered_events[j : j + length]
                candidate_text = " ".join(str(row["text"]) for row in span_events)
                similarity, coverage, mode = span_similarity(
                    str(observation.get("text", "")), candidate_text
                )
                projected_start = int(span_events[0]["projected_ms"])
                projected_end = int(span_events[-1]["projected_ms"])
                cue_start = int(observation["start_ms"])
                cue_end = int(observation["end_ms"])
                if projected_end < cue_start:
                    distance = cue_start - projected_end
                elif projected_start > cue_end:
                    distance = projected_start - cue_end
                else:
                    distance = 0
                if distance > max_time_gap_ms:
                    continue
                inside = distance == 0
                if similarity < 0.25 and coverage < 0.24 and not inside:
                    continue
                timing = 1.05 if inside else max(-1.25, 0.20 - distance / 3500.0)
                reward = (
                    3.25 * similarity
                    + 0.80 * coverage
                    + timing
                    - 0.16 * (length - 1)
                )
                value = current + reward
                if value > scores[i + 1][j + length]:
                    scores[i + 1][j + length] = value
                    backs[i + 1][j + length] = {
                        "previous": (i, j),
                        "kind": "match",
                        "observation_position": i,
                        "event_start": j,
                        "event_end": j + length,
                        "similarity": similarity,
                        "coverage": coverage,
                        "mode": mode,
                        "distance_ms": distance,
                        "objective": reward,
                    }

            # One canonical LRC line may be sung across two adjacent Jianying
            # cells.  Let the next observation reuse the immediately previous
            # event, optionally together with new events.  Without this state,
            # a line such as "Pick it up and still make it work" must be owned
            # by only one cell and creates a false coverage hole in the other.
            if j > 0:
                for new_count in range(0, max_span):
                    if j + new_count > m:
                        break
                    event_start = j - 1
                    event_end = j + new_count
                    span_events = ordered_events[event_start:event_end]
                    candidate_text = " ".join(str(row["text"]) for row in span_events)
                    similarity, coverage, mode = span_similarity(
                        str(observation.get("text", "")), candidate_text
                    )
                    if similarity < 0.48 or coverage < 0.32:
                        continue
                    projected_start = int(span_events[0]["projected_ms"])
                    projected_end = int(span_events[-1]["projected_ms"])
                    cue_start = int(observation["start_ms"])
                    cue_end = int(observation["end_ms"])
                    if projected_end < cue_start:
                        distance = cue_start - projected_end
                    elif projected_start > cue_end:
                        distance = projected_start - cue_end
                    else:
                        distance = 0
                    if distance > max_time_gap_ms:
                        continue
                    timing = 0.85 if distance == 0 else max(-1.25, 0.10 - distance / 3500.0)
                    reward = (
                        3.25 * similarity
                        + 0.80 * coverage
                        + timing
                        - 0.28
                        - 0.16 * new_count
                    )
                    value = current + reward
                    target_j = j + new_count
                    if value > scores[i + 1][target_j]:
                        scores[i + 1][target_j] = value
                        backs[i + 1][target_j] = {
                            "previous": (i, j),
                            "kind": "match",
                            "observation_position": i,
                            "event_start": event_start,
                            "event_end": event_end,
                            "similarity": similarity,
                            "coverage": coverage,
                            "mode": mode,
                            "distance_ms": distance,
                            "objective": reward,
                            "reused_previous_event": True,
                        }

    i, j = n, m
    matches: list[dict] = []
    skipped_event_positions: list[int] = []
    skipped_observation_positions: list[int] = []
    while i > 0 or j > 0:
        back = backs[i][j]
        if back is None:
            break
        kind = str(back["kind"])
        if kind == "match":
            observation = ordered_observations[int(back["observation_position"])]
            span_events = ordered_events[int(back["event_start"]) : int(back["event_end"])]
            matches.append(
                {
                    **{key: value for key, value in back.items() if key != "previous"},
                    "observation_id": observation["id"],
                    "event_indices": [int(row["lrc_index"]) for row in span_events],
                    "events": span_events,
                }
            )
        elif kind == "skip_lyric":
            skipped_event_positions.append(int(back["event_position"]))
        elif kind == "skip_observation":
            skipped_observation_positions.append(i - 1)
        i, j = back["previous"]

    matches.reverse()
    skipped_event_positions.reverse()
    skipped_observation_positions.reverse()

    # Recover a canonical event shared across a Jianying boundary.  The DP
    # consumes each event once by default, so inspect adjacent matched cells
    # for a strong prefix/suffix split at their touching event.
    for left, right in zip(matches, matches[1:]):
        if int(left["observation_position"]) + 1 != int(right["observation_position"]):
            continue
        if int(left["event_end"]) != int(right["event_start"]):
            continue
        shared_position = int(right["event_start"])
        if shared_position >= len(ordered_events):
            continue
        shared_event = ordered_events[shared_position]
        units = boundary_units(str(shared_event["text"]))
        if len(units) < 2:
            continue
        left_observation = str(
            ordered_observations[int(left["observation_position"])].get("text", "")
        )
        right_observation = str(
            ordered_observations[int(right["observation_position"])].get("text", "")
        )
        left_observation_units = boundary_units(left_observation)
        right_observation_units = boundary_units(right_observation)
        best_split: tuple[float, float, int] | None = None
        for split in range(1, len(units)):
            prefix = units[:split]
            suffix = units[split:]
            left_edge = left_observation_units[-len(prefix) :]
            right_edge = right_observation_units[: len(suffix)]
            left_score = span_similarity(
                join_boundary_units(left_edge), join_boundary_units(prefix)
            )[0]
            right_score = span_similarity(
                join_boundary_units(right_edge), join_boundary_units(suffix)
            )[0]
            candidate = (left_score + right_score, min(left_score, right_score), split)
            if best_split is None or candidate > best_split:
                best_split = candidate
        if best_split is None or best_split[1] < 0.76 or best_split[0] < 1.62:
            continue
        left["events"] = [*left["events"], shared_event]
        left["event_indices"] = [
            *left["event_indices"],
            int(shared_event["lrc_index"]),
        ]
        left["shared_event_with_next"] = int(shared_event["lrc_index"])

    return {
        "score": scores[n][m],
        "matches": matches,
        "skipped_lyric_indices": [
            int(ordered_events[position]["lrc_index"])
            for position in skipped_event_positions
        ],
        "skipped_observation_ids": [
            ordered_observations[position]["id"]
            for position in skipped_observation_positions
        ],
    }


def apply_global_sequence_repairs(
    cues: list[Cue],
    cue_events: dict[int, list[dict]],
    events: list[dict],
    tracks: list[Track],
    preserved: set[int],
) -> tuple[int, list[dict]]:
    """Use high-confidence global paths to repair local repeated-line errors."""

    repairs = 0
    ledgers: list[dict] = []
    for track in tracks:
        track_cues = [
            cue
            for cue in cues
            if cue.number not in preserved and cue_track(cue, tracks).index == track.index
        ]
        track_events = [
            event for event in events if int(event["track_index"]) == track.index
        ]
        observations = [
            {
                "id": cue.number,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "text": cue.text,
            }
            for cue in track_cues
        ]
        alignment = global_sequence_alignment(observations, track_events)
        track_repairs = 0
        cue_by_number = {cue.number: cue for cue in track_cues}
        for match in alignment["matches"]:
            number = int(match["observation_id"])
            cue = cue_by_number[number]
            proposed = [dict(row) for row in match["events"]]
            proposed_indices = [int(row["lrc_index"]) for row in proposed]
            current_rows = cue_events[number]
            current_indices = [int(row["lrc_index"]) for row in current_rows]
            if current_indices == proposed_indices:
                continue
            # A global path may add a missing event or move a repeated event to
            # its correct occurrence, but it must not turn a previously unique
            # LRC event into an unexplained skip.  Cuts are removed before this
            # stage, so losing unique coverage here is always unsafe.
            removed_indices = set(current_indices) - set(proposed_indices)
            actually_represented_elsewhere = {
                int(row["lrc_index"])
                for other_number, other_rows in cue_events.items()
                if other_number != number
                for row in other_rows
                if int(row["track_index"]) == track.index
            }
            if any(
                index not in actually_represented_elsewhere
                for index in removed_indices
            ):
                continue
            proposed_text = " ".join(str(row["text"]) for row in proposed)
            current_text = " ".join(str(row["text"]) for row in current_rows)
            proposed_score = span_similarity(cue.text, proposed_text)[0]
            current_score = span_similarity(cue.text, current_text)[0] if current_rows else 0.0
            mode = str(match["mode"])
            similarity = float(match["similarity"])
            coverage = float(match["coverage"])
            distance = int(match["distance_ms"])
            high_direct = mode == "text" and similarity >= 0.66 and coverage >= 0.45
            high_phonetic = (
                mode == "korean_pronunciation"
                and similarity >= 0.48
                and coverage >= 0.40
            )
            timing_supported = distance == 0 and similarity >= 0.43 and coverage >= 0.34
            materially_better = proposed_score >= current_score + 0.15
            if not (high_direct or high_phonetic or timing_supported):
                continue
            if current_rows and not materially_better:
                continue
            for row in proposed:
                row["mapping_method"] = (
                    str(row["mapping_method"]) + "+global_sequence_viterbi"
                )
            cue_events[number] = proposed
            repairs += 1
            track_repairs += 1
        ledgers.append(
            {
                "track_index": track.index,
                "track": track.title,
                "matched_cue_count": len(alignment["matches"]),
                "skipped_lyric_indices": alignment["skipped_lyric_indices"],
                "skipped_observation_count": len(alignment["skipped_observation_ids"]),
                "repairs_applied": track_repairs,
            }
        )
    return repairs, ledgers


def assignment_score(cue: Cue, event: dict) -> float:
    projected_ms = int(event["projected_ms"])
    # Jianying's cue boundaries are the primary timing evidence. A projected
    # LRC timestamp anywhere inside a cue belongs to that cue unless the text
    # evidence strongly says otherwise. Measuring only from cue.start_ms made
    # late LRC timestamps prefer the following cue and could collapse two
    # successive lyrics into one subtitle cell.
    if cue.start_ms <= projected_ms <= cue.end_ms:
        edge_delta = 0.0
        timing_bonus = 1.8
    else:
        edge_delta = min(
            abs(projected_ms - cue.start_ms),
            abs(projected_ms - cue.end_ms),
        ) / 1000.0
        timing_bonus = 0.35 if cue.start_ms - 500 <= projected_ms <= cue.end_ms + 350 else 0.0
    # Jianying often writes Korean as rough English phonetics.  Direct string
    # similarity makes those events drift to an adjacent English cue even when
    # the phonetic observation is in the correct cell.
    similarity, coverage, _ = span_similarity(cue.text, str(event["text"]))
    return similarity * 4.0 + coverage * 0.5 - edge_delta + timing_bonus


def repair_collapsed_sequence_assignments(
    cues: list[Cue],
    cue_events: dict[int, list[dict]],
    tracks: list[Track],
    preserved: set[int],
) -> int:
    """Move an earlier lyric back into an adjacent misrecognized cue.

    Some LRC timestamps are late or unrealistically close together. If timing
    assigns two successive lyrics to one cue while the preceding cue is empty,
    use the two Jianying recognition texts as ordered evidence. This keeps both
    original time cells and prevents the second cell from stealing the first
    cell's canonical lyric.
    """
    repaired = 0
    positions = {cue.number: position for position, cue in enumerate(cues)}
    for current in cues:
        rows = cue_events[current.number]
        if len(rows) < 2 or current.number in preserved:
            continue
        position = positions[current.number]
        if position == 0:
            continue
        previous = cues[position - 1]
        if previous.number in preserved or cue_events[previous.number]:
            continue
        if current.start_ms - previous.end_ms > 1200:
            continue
        if cue_track(previous, tracks).index != cue_track(current, tracks).index:
            continue

        ordered = sorted(
            rows,
            key=lambda row: (int(row["track_index"]), int(row["lrc_index"])),
        )
        first, second = ordered[0], ordered[1]
        if int(first["track_index"]) != int(second["track_index"]):
            continue
        previous_first = text_similarity(previous.text, str(first["text"]))[0]
        current_first = text_similarity(current.text, str(first["text"]))[0]
        previous_second = text_similarity(previous.text, str(second["text"]))[0]
        current_second = text_similarity(current.text, str(second["text"]))[0]
        ordered_text_evidence = (
            previous_first >= 0.35
            and current_second >= 0.30
            and previous_first >= current_first + 0.15
            and current_second >= previous_second + 0.15
        )
        if not ordered_text_evidence:
            continue

        moved = dict(first)
        moved["mapping_method"] = str(moved["mapping_method"]) + "+sequence_backfill"
        cue_events[previous.number] = [moved]
        cue_events[current.number] = [row for row in rows if row is not first]
        repaired += 1
    return repaired


def enforce_monotonic_lyric_sequence(
    cues: list[Cue],
    cue_events: dict[int, list[dict]],
    events: list[dict],
    preserved: set[int],
) -> tuple[int, int]:
    """Repair backward lyric-index jumps and remove premature duplicates."""
    repaired = 0
    last_index_by_track: dict[int, int] = {}
    for cue in sorted(cues, key=lambda row: (row.start_ms, row.end_ms)):
        rows = cue_events[cue.number]
        if not rows or cue.number in preserved:
            continue
        track_index = int(rows[0]["track_index"])
        indices = [int(row["lrc_index"]) for row in rows]
        previous_index = last_index_by_track.get(track_index)
        if previous_index is not None and min(indices) < previous_index:
            candidates: list[tuple[float, float, int, dict, str, str]] = []
            for event in events:
                if int(event["track_index"]) != track_index:
                    continue
                if int(event["lrc_index"]) < previous_index:
                    continue
                delta = abs(int(event["projected_ms"]) - cue.start_ms)
                if delta > 4500:
                    continue
                span, score, coverage, mode = best_text_span(
                    cue.text, str(event["text"])
                )
                if score < 0.35 or coverage < 0.35:
                    continue
                # Text evidence leads; timing and the nearest non-decreasing
                # lyric index break ties between repeated chorus fragments.
                rank = score * 4.0 + coverage - delta / 4500.0
                candidates.append(
                    (rank, score, -int(event["lrc_index"]), event, span, mode)
                )
            if candidates:
                _, _, _, selected, span, mode = max(candidates, key=lambda row: row[:3])
                replacement = dict(selected)
                replacement["text"] = span
                replacement["mapping_method"] = (
                    str(replacement["mapping_method"])
                    + "+"
                    + mode
                    + "_span+monotonic_sequence_repair"
                )
                cue_events[cue.number] = [replacement]
                rows = cue_events[cue.number]
                indices = [int(replacement["lrc_index"])]
                repaired += 1
        last_index_by_track[track_index] = max(
            last_index_by_track.get(track_index, -1), max(indices)
        )

    positions_by_key: dict[tuple[int, int], list[int]] = {}
    ordered_cues = sorted(cues, key=lambda row: (row.start_ms, row.end_ms))
    for position, cue in enumerate(ordered_cues):
        for row in cue_events[cue.number]:
            key = (int(row["track_index"]), int(row["lrc_index"]))
            positions_by_key.setdefault(key, []).append(position)

    dropped = 0
    for position, cue in enumerate(ordered_cues):
        rows = cue_events[cue.number]
        if len(rows) < 2:
            continue
        kept: list[dict] = []
        local_dropped = 0
        for row in rows:
            key = (int(row["track_index"]), int(row["lrc_index"]))
            appears_soon_after = any(
                position < future <= position + 3
                for future in positions_by_key.get(key, [])
            )
            if appears_soon_after and len(rows) - local_dropped > 1:
                dropped += 1
                local_dropped += 1
                continue
            kept.append(row)
        if kept:
            cue_events[cue.number] = kept
    return repaired, dropped


def command_build(args: argparse.Namespace) -> int:
    cues = parse_srt(args.srt)
    tracks = parse_song_list(args.song_list, args.lyrics_dir, cues[-1].end_ms)
    audio_payload = json.loads(args.audio_alignment.read_text(encoding="utf-8"))
    events, mappings = projected_lyric_events(tracks, cues, audio_payload)

    cue_events: dict[int, list[dict]] = {cue.number: [] for cue in cues}
    preserved = set(args.preserve_cues)
    for event in events:
        candidates: list[Cue] = []
        for cue in cues:
            if cue.number in preserved:
                continue
            if cue.end_ms < event["projected_ms"] - args.max_assignment_gap_ms:
                continue
            if cue.start_ms > event["projected_ms"] + args.max_assignment_gap_ms:
                break
            candidates.append(cue)
        if not candidates:
            continue
        selected = max(candidates, key=lambda cue: assignment_score(cue, event))
        # Never put a lyric onset into a cell that has already ended merely
        # because its text is marginally more similar.  This was the concrete
        # cause of Korean events at 00:10/00:39 being attached to the preceding
        # English cue.  A nearby following cell can start a little late because
        # Jianying boundaries are frame/ASR estimates, but at least it can
        # display the lyric when it is sung.
        if selected.end_ms < int(event["projected_ms"]):
            following = [
                cue
                for cue in candidates
                if int(event["projected_ms"]) <= cue.start_ms <= int(event["projected_ms"]) + 500
            ]
            if following:
                best_following = max(following, key=lambda cue: assignment_score(cue, event))
                if assignment_score(best_following, event) >= assignment_score(selected, event) - 0.35:
                    selected = best_following
        delta = abs(selected.start_ms - event["projected_ms"])
        inside = selected.start_ms - 500 <= event["projected_ms"] <= selected.end_ms + 350
        similarity, _ = text_similarity(selected.text, str(event["text"]))
        if inside or delta <= args.max_assignment_gap_ms or similarity >= 0.82:
            cue_events[selected.number].append(event)

    # Keep only a time-contiguous sequence when several repeated LRC lines were
    # attracted to one long or generic Jianying cue.
    for number, rows in cue_events.items():
        rows.sort(key=lambda row: row["projected_ms"])
        if rows:
            cue = next(cue for cue in cues if cue.number == number)
            by_track: dict[int, list[dict]] = {}
            for row in rows:
                by_track.setdefault(int(row["track_index"]), []).append(row)
            if len(by_track) > 1:
                def track_score(track_rows: list[dict]) -> float:
                    return max(assignment_score(cue, row) for row in track_rows) + sum(
                        max(0.0, text_similarity(cue.text, str(row["text"]))[0] - 0.55)
                        for row in track_rows
                    )

                rows = max(by_track.values(), key=track_score)
        unique: list[dict] = []
        for row in rows:
            if unique and normalized_text(unique[-1]["text"]) == normalized_text(row["text"]):
                continue
            unique.append(row)
        cue_events[number] = unique

    global_sequence_repairs, global_sequence_ledgers = apply_global_sequence_repairs(
        cues, cue_events, events, tracks, preserved
    )
    ledger_by_track = {
        int(row["track_index"]): row for row in global_sequence_ledgers
    }
    for mapping in mappings:
        mapping["global_sequence_alignment"] = ledger_by_track.get(
            int(mapping["track"]["index"]), {}
        )

    sequence_repairs = repair_collapsed_sequence_assignments(
        cues, cue_events, tracks, preserved
    )

    # Existing Chinese/English recognition often represents only part of a
    # longer LRC line. Reuse the same nearby lyric event for adjacent cues and
    # extract the matching canonical word span instead of forcing the whole
    # LRC line into one cue.
    for cue in cues:
        if cue.number in preserved:
            continue
        best: tuple[float, float, dict, str, str] | None = None
        for event in events:
            if abs(int(event["projected_ms"]) - cue.start_ms) > 4500:
                continue
            span, score, coverage, mode = best_text_span(cue.text, str(event["text"]))
            candidate = (score, coverage, event, span, mode)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        direct_ok = bool(best and best[4] == "text" and best[0] >= 0.82 and best[1] >= 0.68)
        pronunciation_ok = bool(
            best
            and best[4] == "korean_pronunciation"
            and best[0] >= 0.46
            and best[1] >= 0.42
            and abs(int(best[2]["projected_ms"]) - cue.start_ms) <= 4200
        )
        if best and (direct_ok or pronunciation_ok):
            event = dict(best[2])
            event["text"] = best[3]
            event["mapping_method"] = event["mapping_method"] + "+" + best[4] + "_span"
            existing = cue_events[cue.number]
            if len(existing) <= 1:
                cue_events[cue.number] = [event]
            else:
                # Refine only the matching event.  Replacing the whole list
                # silently discarded neighbouring Korean/ad-lib events that
                # had already been assigned to the same Jianying cell.
                replaced = False
                preserved_events: list[dict] = []
                for current in existing:
                    if (
                        not replaced
                        and int(current["track_index"]) == int(event["track_index"])
                        and int(current["lrc_index"]) == int(event["lrc_index"])
                    ):
                        preserved_events.append(event)
                        replaced = True
                    else:
                        preserved_events.append(current)
                cue_events[cue.number] = preserved_events

    monotonic_repairs, premature_duplicates_removed = enforce_monotonic_lyric_sequence(
        cues, cue_events, events, preserved
    )

    assigned_events: set[tuple[int, int]] = {
        (int(row["track_index"]), int(row["lrc_index"]))
        for rows in cue_events.values()
        for row in rows
    }

    output_rows: list[dict] = []
    split_original_cues: set[int] = set()
    for cue in cues:
        rows = cue_events[cue.number]
        if len(rows) >= 2 and cue.end_ms - cue.start_ms >= args.split_long_cue_ms:
            projected = sorted(
                {
                    max(cue.start_ms, min(cue.end_ms - args.min_insert_duration_ms, int(row["projected_ms"])))
                    for row in rows
                    if cue.start_ms - 500 <= row["projected_ms"] <= cue.end_ms + 350
                }
            )
            split_rows: list[dict] = []
            for position, row in enumerate(rows):
                start_ms = max(cue.start_ms, int(row["projected_ms"]))
                next_starts = [value for value in projected if value > start_ms]
                end_ms = min(cue.end_ms, (next_starts[0] - 80) if next_starts else cue.end_ms)
                if end_ms - start_ms < args.min_insert_duration_ms:
                    continue
                split_rows.append(
                    {
                        "kind": "split",
                        "original_cue": cue.number,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "original": cue.text,
                        "text": row["text"],
                        "status": "split_unreliable_long_cue",
                        "confidence": "review",
                        "evidence": row["mapping_method"],
                        "projected_delta_ms": abs(cue.start_ms - int(row["projected_ms"])),
                        "track": row["track"],
                        "lrc_indices": str(row["lrc_index"]),
                    }
                )
            if len(split_rows) >= 2:
                output_rows.extend(split_rows)
                split_original_cues.add(cue.number)
                continue
        replacement = " ".join(str(row["text"]) for row in rows)
        if rows:
            deltas = [abs(cue.start_ms - int(row["projected_ms"])) for row in rows]
            evidence = "+".join(sorted({row["mapping_method"] for row in rows}))
            status = "replace_existing"
            confidence = "high" if min(deltas) <= 800 and len(rows) <= 2 else "review"
        else:
            replacement = cue.text
            deltas = []
            evidence = "jianying_only"
            status = "keep_existing"
            confidence = "review"
        output_rows.append(
            {
                "kind": "existing",
                "original_cue": cue.number,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "original": cue.text,
                "text": replacement,
                "status": status,
                "confidence": confidence,
                "evidence": evidence,
                "projected_delta_ms": min(deltas) if deltas else "",
                "track": rows[0]["track"] if rows else cue_track(cue, tracks).title,
                "lrc_indices": ";".join(str(row["lrc_index"]) for row in rows),
            }
        )

    auto_boundary_resegments = auto_resegment_high_confidence_boundaries(
        [row for row in output_rows if row.get("kind") == "existing"]
    )

    # Add LRC events that fall inside genuine uncovered SRT gaps. Existing cue
    # timings remain immutable; inserted intervals are clipped to the gap.
    sorted_cues = sorted(cues, key=lambda cue: (cue.start_ms, cue.end_ms))
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for cue in sorted_cues:
        if cue.start_ms - cursor >= args.min_gap_ms:
            gaps.append((cursor, cue.start_ms))
        cursor = max(cursor, cue.end_ms)
    for event_index, event in enumerate(events):
        key = (event["track_index"], event["lrc_index"])
        if key in assigned_events:
            continue
        gap = next(
            (
                (start, end)
                for start, end in gaps
                if start - 250 <= event["projected_ms"] < end - args.min_insert_duration_ms
            ),
            None,
        )
        if gap is None:
            continue
        next_time = next(
            (
                row["projected_ms"]
                for row in events[event_index + 1 :]
                if row["track_index"] == event["track_index"]
                and row["projected_ms"] > event["projected_ms"]
            ),
            event["projected_ms"] + args.default_insert_duration_ms,
        )
        start_ms = max(gap[0], int(event["projected_ms"]))
        end_ms = min(gap[1], int(next_time - 80), start_ms + args.max_insert_duration_ms)
        if end_ms - start_ms < args.min_insert_duration_ms:
            continue
        output_rows.append(
            {
                "kind": "inserted",
                "original_cue": "",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "original": "",
                "text": event["text"],
                "status": "inserted_missing_lyric",
                "confidence": "review",
                "evidence": event["mapping_method"],
                "projected_delta_ms": 0,
                "track": event["track"],
                "lrc_indices": str(event["lrc_index"]),
            }
        )

    output_rows.sort(key=lambda row: (row["start_ms"], 0 if row["kind"] == "existing" else 1))
    final_cues = [
        Cue(index, int(row["start_ms"]), int(row["end_ms"]), str(row["text"]))
        for index, row in enumerate(output_rows, start=1)
    ]
    args.out_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(final_cues, {}, args.out_srt)
    with args.out_report.open("w", encoding="utf-8-sig", newline="") as handle:
        # Rebuilt/hybrid rows are synthesized locally while existing rows may
        # also carry ASR diagnostic columns.  Using only the first row's keys
        # makes an all-track rebuild fail whenever a later row has extra
        # provenance fields.  Preserve a stable union instead.
        fields = list(dict.fromkeys(key for row in output_rows for key in row)) or ["kind"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    args.out_mapping.write_text(json.dumps(mappings, ensure_ascii=False, indent=2), encoding="utf-8")

    # Verify every original cue interval survived byte-for-byte at the draft
    # stage.  Source-only non-lexical vocalizations are removed only after
    # project-scoped LRC-index overrides have been applied during finalization.
    original_times = {
        (cue.start_ms, cue.end_ms)
        for cue in cues
        if cue.number not in split_original_cues
    }
    final_times = {(cue.start_ms, cue.end_ms) for cue in final_cues}
    missing_times = original_times - final_times
    if missing_times:
        raise AssertionError(f"{len(missing_times)} original cue intervals were lost")
    print(
        json.dumps(
            {
                "out_srt": str(args.out_srt),
                "out_report": str(args.out_report),
                "existing": len(cues),
                "inserted": sum(row["kind"] == "inserted" for row in output_rows),
                "split_original_cues": len(split_original_cues),
                "replaced_existing": sum(row["status"] == "replace_existing" for row in output_rows),
                "global_sequence_repairs": global_sequence_repairs,
                "sequence_repairs": sequence_repairs,
                "monotonic_repairs": monotonic_repairs,
                "premature_duplicates_removed": premature_duplicates_removed,
                "auto_boundary_resegments": auto_boundary_resegments,
            },
            ensure_ascii=False,
        )
    )
    return 0


def command_refine_korean(args: argparse.Namespace) -> int:
    cues = parse_srt(args.srt)
    tracks = parse_song_list(args.song_list, args.lyrics_dir, cues[-1].end_ms)
    audio_payload = json.loads(args.audio_alignment.read_text(encoding="utf-8"))
    events, _ = projected_lyric_events(tracks, cues, audio_payload)
    asr_payload = json.loads(args.asr_json.read_text(encoding="utf-8"))
    asr_jobs = {row["track"]: row for row in asr_payload.get("jobs", [])}
    with args.in_report.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    refined = 0
    for row in rows:
        track = row.get("track", "")
        job = asr_jobs.get(track)
        row["asr_text"] = ""
        row["asr_score"] = ""
        if not job:
            continue
        start = int(row["start_ms"]) / 1000.0
        end = int(row["end_ms"]) / 1000.0
        words: list[str] = []
        for segment in job.get("segments", []):
            for word in segment.get("words", []):
                midpoint = (float(word["start"]) + float(word["end"])) / 2.0
                if start - 0.12 <= midpoint <= end + 0.12 and float(word.get("probability", 0.0)) >= 0.20:
                    words.append(str(word["word"]).strip())
        observation = " ".join(word for word in words if word).strip()
        if not observation:
            continue
        row["asr_text"] = observation
        nearby = [
            event
            for event in events
            if event["track"] == track
            and int(row["start_ms"]) - 5500 <= event["projected_ms"] <= int(row["end_ms"]) + 5500
        ]
        best: tuple[float, float, float, str, dict] | None = None
        for event in nearby:
            span, score, coverage, _ = best_text_span(observation, str(event["text"]))
            distance = abs(int(event["projected_ms"]) - int(row["start_ms"])) / 1000.0
            objective = score + coverage * 0.15 - min(distance, 5.0) * 0.035
            candidate = (objective, score, coverage, span, event)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
        if not best:
            continue
        row["asr_score"] = f"{best[1]:.3f}"
        if best[1] >= args.min_asr_score and best[2] >= args.min_asr_coverage:
            row["text"] = best[3]
            row["evidence"] = row.get("evidence", "") + "+large_v3_turbo_asr"
            row["confidence"] = "high" if best[1] >= 0.78 else "review"
            row["status"] = "asr_refined_existing"
            # The selected canonical event is part of the provenance, not just
            # replacement text.  Omitting its index made valid Korean ASR rows
            # look like unresolved Jianying leftovers and also hid coverage
            # from the sequence QA.
            selected_event = best[4]
            if not str(row.get("lrc_indices", "")).strip():
                row["lrc_indices"] = str(selected_event["lrc_index"])
            refined += 1

    # Independent ASR matching can select the wrong occurrence of a repeated
    # chorus.  Reconcile the row-level decisions with one monotonic path over
    # the entire track.  This remains conservative: it changes a row only when
    # the ASR words support the canonical span and the global timing path is
    # close, or when it is materially stronger than the local selection.
    global_asr_refined = 0
    for track, job in asr_jobs.items():
        track_events = [event for event in events if event["track"] == track]
        observations = [
            {
                "id": position,
                "start_ms": int(row["start_ms"]),
                "end_ms": int(row["end_ms"]),
                "text": str(row.get("asr_text", "")),
            }
            for position, row in enumerate(rows)
            if row.get("track") == track and str(row.get("asr_text", "")).strip()
        ]
        if not observations or not track_events:
            continue
        alignment = global_sequence_alignment(observations, track_events)
        for match in alignment["matches"]:
            row = rows[int(match["observation_id"])]
            canonical = " ".join(str(event["text"]) for event in match["events"])
            span, score, coverage, _ = best_text_span(
                str(row.get("asr_text", "")), canonical
            )
            if score < args.min_asr_score or coverage < args.min_asr_coverage:
                continue
            proposed_indices = [int(value) for value in match["event_indices"]]
            current_indices = [
                int(value)
                for value in str(row.get("lrc_indices", "")).split(";")
                if value.strip().isdigit()
            ]
            current_score = float(row.get("asr_score") or 0.0)
            close_global_path = int(match["distance_ms"]) <= 1200 and score >= 0.60
            materially_better = score >= current_score + 0.08
            if current_indices == proposed_indices and normalized_text(str(row["text"])) == normalized_text(span):
                continue
            if current_indices and not (close_global_path or materially_better):
                continue
            row["text"] = span
            row["lrc_indices"] = ";".join(str(value) for value in proposed_indices)
            row["asr_score"] = f"{score:.3f}"
            row["evidence"] = row.get("evidence", "") + "+global_asr_sequence_viterbi"
            row["confidence"] = "high" if score >= 0.78 else "review"
            row["status"] = "asr_refined_existing"
            global_asr_refined += 1

    final_cues = [
        Cue(index, int(row["start_ms"]), int(row["end_ms"]), str(row["text"]))
        for index, row in enumerate(rows, start=1)
    ]
    write_srt(final_cues, {}, args.out_srt)
    with args.out_report.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = list(rows[0]) if rows else ["kind"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                "refined": refined,
                "global_asr_sequence_refined": global_asr_refined,
                "out_srt": str(args.out_srt),
            },
            ensure_ascii=False,
        )
    )
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    cues = parse_srt(args.srt)
    tracks = parse_song_list(args.song_list, args.lyrics_dir, cues[-1].end_ms)
    audio_payload = json.loads(args.audio_alignment.read_text(encoding="utf-8"))
    events, _ = projected_lyric_events(tracks, cues, audio_payload)
    overrides = json.loads(args.manual_overrides.read_text(encoding="utf-8"))
    _, scope_issue = validate_source_srt_scope(
        overrides, args.srt, "manual override file"
    )
    if scope_issue:
        raise ValueError(scope_issue)
    overrides.pop("schema_version", None)
    overrides.pop("project", None)
    overrides.pop("scope", None)
    overrides.pop("source_srt_sha256", None)
    overrides.pop("_source_srt_sha256", None)
    manual_insertions = overrides.pop("_insertions", [])
    manual_cue_splits = overrides.pop("_cue_splits", [])
    manual_timing_overrides = overrides.pop("_timing_overrides", {})
    manual_lrc_index_overrides = overrides.pop("_lrc_indices_overrides", {})
    overrides.pop("_confirmed_omitted_lrc_events", [])
    manual_review_notes = overrides.pop("_review_notes", {})
    confirmed_boundary_pairs = overrides.pop("_confirmed_boundary_pairs", [])
    with args.in_report.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    rebuild_titles = set(args.rebuild_track)
    hybrid_titles = set(args.hybrid_track)
    reconstructed_titles = rebuild_titles | hybrid_titles
    next_first_event: dict[str, int] = {}
    for position, track in enumerate(tracks):
        if position + 1 >= len(tracks):
            next_first_event[track.title] = track.end_ms
            continue
        next_track = tracks[position + 1]
        projected = [
            int(event["projected_ms"])
            for event in events
            if event["track_index"] == next_track.index
        ]
        next_first_event[track.title] = min(projected) if projected else next_track.start_ms

    rebuilt_ranges: dict[str, tuple[int, int]] = {}
    for track in tracks:
        if track.title not in reconstructed_titles:
            continue
        track_events = [event for event in events if event["track"] == track.title]
        if not track_events:
            continue
        start_candidates = [
            cue.start_ms
            for cue in cues
            if track.start_ms - 1500 <= cue.start_ms <= track.start_ms + 1500
        ]
        range_start = min(start_candidates) if start_candidates else track.start_ms
        range_end = min(track.end_ms, next_first_event[track.title] - 80)
        rebuilt_ranges[track.title] = (range_start, range_end)

    def inside_rebuild(row: dict) -> bool:
        start = int(row["start_ms"])
        return any(begin <= start < end for begin, end in rebuilt_ranges.values())

    output_rows = [row for row in rows if not inside_rebuild(row)]

    cue_by_number = {cue.number: cue for cue in cues}

    def hybrid_event_starts(track: Track, track_events: list[dict]) -> tuple[dict[int, int], dict[int, int]]:
        """Anchor projected LRC events to nearby Jianying cue onsets.

        The build report already contains a monotonic LRC assignment.  A cue
        becomes an onset anchor only when its assigned event is close to the
        audio/BPM projection; this rejects repeated-phrase misassignments such
        as a later repeated ``na na`` cue.
        """

        projected = {int(event["lrc_index"]): int(event["projected_ms"]) for event in track_events}
        lrc_time = {int(event["lrc_index"]): int(event["lrc_time_ms"]) for event in track_events}
        cue_indices: dict[int, set[int]] = {}
        for row in rows:
            if row.get("track") != track.title or not row.get("original_cue"):
                continue
            number = int(row["original_cue"])
            indices = {
                int(value)
                for value in row.get("lrc_indices", "").split(";")
                if value.strip().isdigit() and int(value) in projected
            }
            cue_indices.setdefault(number, set()).update(indices)

        anchors: dict[int, int] = {}
        anchor_cues: dict[int, int] = {}
        for number, indices in cue_indices.items():
            if not indices or number not in cue_by_number:
                continue
            first_index = min(indices)
            cue = cue_by_number[number]
            if abs(projected[first_index] - cue.start_ms) > 550:
                continue
            current = anchors.get(first_index)
            if current is None or abs(projected[first_index] - cue.start_ms) < abs(projected[first_index] - current):
                anchors[first_index] = cue.start_ms
                anchor_cues[first_index] = number

        if not anchors:
            return projected, anchor_cues
        ordered_anchors = sorted(anchors)
        output: dict[int, int] = {}
        for event in track_events:
            index = int(event["lrc_index"])
            if index in anchors:
                output[index] = anchors[index]
                continue
            left = max((value for value in ordered_anchors if value < index), default=None)
            right = min((value for value in ordered_anchors if value > index), default=None)
            if left is not None and right is not None and lrc_time[right] != lrc_time[left]:
                fraction = (lrc_time[index] - lrc_time[left]) / (lrc_time[right] - lrc_time[left])
                output[index] = int(round(anchors[left] + fraction * (anchors[right] - anchors[left])))
            elif left is not None:
                output[index] = int(round(anchors[left] + (lrc_time[index] - lrc_time[left]) / float(audio_payload["tracks"][track.index - 1].get("bpm_tempo_ratio") or 1.0)))
            elif right is not None:
                output[index] = int(round(anchors[right] - (lrc_time[right] - lrc_time[index]) / float(audio_payload["tracks"][track.index - 1].get("bpm_tempo_ratio") or 1.0)))
            else:
                output[index] = projected[index]
        return output, anchor_cues

    for track in tracks:
        if track.title not in rebuilt_ranges:
            continue
        range_start, range_end = rebuilt_ranges[track.title]
        track_events = [
            event
            for event in events
            if event["track"] == track.title and range_start <= event["projected_ms"] < range_end
        ]
        track_events.sort(key=lambda event: event["projected_ms"])
        event_starts = {int(event["lrc_index"]): int(event["projected_ms"]) for event in track_events}
        anchor_cues: dict[int, int] = {}
        if track.title in hybrid_titles:
            event_starts, anchor_cues = hybrid_event_starts(track, track_events)
            track_events.sort(key=lambda event: event_starts[int(event["lrc_index"])])
        for position, event in enumerate(track_events):
            event_index = int(event["lrc_index"])
            start_ms = event_starts[event_index]
            if position == 0 and start_ms - range_start <= 1000:
                start_ms = range_start
            next_start = (
                event_starts[int(track_events[position + 1]["lrc_index"])]
                if position + 1 < len(track_events)
                else range_end
            )
            end_ms = min(range_end, next_start - 80, start_ms + 6000)
            if end_ms - start_ms < 400:
                continue
            output_rows.append(
                {
                    "kind": "hybrid" if track.title in hybrid_titles else "rebuilt",
                    "original_cue": str(anchor_cues.get(event_index, "")),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "original": "",
                    "text": event["text"],
                    "status": (
                        "hybrid_jianying_anchor_and_original_audio"
                        if track.title in hybrid_titles
                        else "rebuilt_from_original_audio"
                    ),
                    "confidence": "high",
                    "evidence": event["mapping_method"] + (
                        "+jianying_onset_anchor+large_v3_turbo_sequence_check"
                        if event_index in anchor_cues
                        else "+interpolated_between_jianying_anchors+large_v3_turbo_sequence_check"
                    ),
                    "projected_delta_ms": start_ms - int(event["projected_ms"]),
                    "track": track.title,
                    "lrc_indices": str(event["lrc_index"]),
                }
            )

    split_applied = 0
    for split in manual_cue_splits:
        original_cue = str(split["original_cue"])
        template = next(
            (row for row in output_rows if str(row.get("original_cue", "")) == original_cue),
            None,
        )
        if not template:
            continue
        output_rows = [
            row for row in output_rows if str(row.get("original_cue", "")) != original_cue
        ]
        for part in split["parts"]:
            output_rows.append(
                {
                    **template,
                    "kind": "manual_split",
                    "start_ms": int(part["start_ms"]),
                    "end_ms": int(part["end_ms"]),
                    "text": str(part["text"]),
                    "status": "manual_verified_split",
                    "confidence": "high",
                    "evidence": str(part.get("evidence", "manual_audio_and_lrc_review")),
                    "lrc_indices": str(part.get("lrc_indices", "")),
                }
            )
        split_applied += 1

    for insertion in manual_insertions:
        output_rows.append(
            {
                "kind": "inserted",
                "original_cue": "",
                "start_ms": int(insertion["start_ms"]),
                "end_ms": int(insertion["end_ms"]),
                "original": "",
                "text": str(insertion["text"]),
                "status": "manual_verified_insertion",
                "confidence": "high",
                "evidence": str(insertion["evidence"]),
                    "projected_delta_ms": 0,
                    "track": str(insertion["track"]),
                    "lrc_indices": str(insertion.get("lrc_indices", "")),
                }
            )

    applied = 0
    for row in output_rows:
        cue_number = str(row.get("original_cue", ""))
        if cue_number and cue_number in overrides:
            row["text"] = overrides[cue_number]
            row["status"] = "manual_verified_override"
            row["confidence"] = "high"
            row["evidence"] = row.get("evidence", "") + "+manual_context_review"
            applied += 1

    timing_applied = 0
    for row in output_rows:
        cue_number = str(row.get("original_cue", ""))
        timing = manual_timing_overrides.get(cue_number)
        if not timing:
            continue
        if "start_ms" in timing:
            row["start_ms"] = int(timing["start_ms"])
        if "end_ms" in timing:
            row["end_ms"] = int(timing["end_ms"])
        row["confidence"] = "high"
        row["evidence"] = (
            row.get("evidence", "")
            + "+manual_timing_review:"
            + str(timing.get("evidence", "user_audio_review"))
        )
        timing_applied += 1

    lrc_index_applied = 0
    for row in output_rows:
        cue_number = str(row.get("original_cue", ""))
        indices = manual_lrc_index_overrides.get(cue_number)
        if indices is None:
            continue
        if isinstance(indices, list):
            row["lrc_indices"] = ";".join(str(value) for value in indices)
        else:
            row["lrc_indices"] = str(indices)
        row["evidence"] = row.get("evidence", "") + "+manual_lrc_coverage_review"
        lrc_index_applied += 1

    review_notes_applied = 0
    for row in output_rows:
        cue_number = str(row.get("original_cue", ""))
        note = manual_review_notes.get(cue_number)
        if not note:
            continue
        row["evidence"] = row.get("evidence", "") + "+manual_review_note:" + str(note)
        review_notes_applied += 1

    boundary_confirmations_applied = 0
    output_by_original = {
        str(row.get("original_cue", "")): row
        for row in output_rows
        if str(row.get("original_cue", ""))
    }
    for confirmation in confirmed_boundary_pairs:
        evidence = str(confirmation.get("evidence", "user_audio_review"))
        for key in (str(confirmation["left"]), str(confirmation["right"])):
            row = output_by_original.get(key)
            if not row:
                continue
            row["evidence"] = row.get("evidence", "") + "+manual_boundary_review:" + evidence
            row["confidence"] = "high"
            boundary_confirmations_applied += 1

    discarded_noncanonical_vocalizations = discard_noncanonical_vocalization_rows(
        output_rows
    )

    # Korean ASR refinement and manual text cleanup can expose a boundary
    # error that was not present during the earlier build pass.  Re-run the
    # conservative sequence resegmenter after every text-producing stage;
    # user-confirmed timing/boundary pairs are explicitly ignored by it.
    post_finalize_boundary_resegments = auto_resegment_high_confidence_boundaries(
        [row for row in output_rows if row.get("kind") == "existing"]
    )
    removed_noncanonical_duplicate_vocalizations = (
        deduplicate_boundary_vocalizations(output_rows, events)
    )

    output_rows.sort(key=lambda row: (int(row["start_ms"]), int(row["end_ms"])))
    # Ensure final output is non-overlapping except for overlaps already present
    # in the original Jianying file (the opening narration/lyric overlay).
    allowed_overlaps = {
        (left.start_ms, left.end_ms, right.start_ms, right.end_ms)
        for left, right in zip(cues, cues[1:])
        if left.end_ms > right.start_ms
    }
    unexpected_overlaps: list[tuple[int, int]] = []
    for left, right in zip(output_rows, output_rows[1:]):
        if int(left["end_ms"]) <= int(right["start_ms"]):
            continue
        if int(right["start_ms"]) < 10_000:
            # The source intentionally overlays opening narration with the
            # first two lyric cues.
            continue
        signature = (
            int(left["start_ms"]),
            int(left["end_ms"]),
            int(right["start_ms"]),
            int(right["end_ms"]),
        )
        if signature not in allowed_overlaps:
            unexpected_overlaps.append((int(left["start_ms"]), int(right["start_ms"])))
    if unexpected_overlaps:
        raise AssertionError(f"unexpected overlaps: {unexpected_overlaps[:5]}")

    final_cues = [
        Cue(index, int(row["start_ms"]), int(row["end_ms"]), str(row["text"]))
        for index, row in enumerate(output_rows, start=1)
    ]
    write_srt(final_cues, {}, args.out_srt)
    with args.out_report.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = list(dict.fromkeys(key for row in output_rows for key in row)) or ["kind"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    print(
        json.dumps(
            {
                "cues": len(final_cues),
                "rebuilt": sum(row["kind"] == "rebuilt" for row in output_rows),
                "manual_overrides": applied,
                "manual_cue_splits": split_applied,
                "manual_timing_overrides": timing_applied,
                "manual_lrc_index_overrides": lrc_index_applied,
                "manual_review_notes": review_notes_applied,
                "manual_boundary_confirmations": boundary_confirmations_applied,
                "discarded_noncanonical_vocalizations": discarded_noncanonical_vocalizations,
                "removed_noncanonical_duplicate_vocalizations": (
                    removed_noncanonical_duplicate_vocalizations
                ),
                "post_finalize_boundary_resegments": post_finalize_boundary_resegments,
                "out_srt": str(args.out_srt),
            },
            ensure_ascii=False,
        )
    )
    return 0


def boundary_units(value: str) -> list[str]:
    return re.findall(
        r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?|[가-힣]+|[\u4e00-\u9fff]|[^\W_]",
        value,
    )


def join_boundary_units(units: list[str]) -> str:
    output = ""
    for unit in units:
        word_like = bool(re.fullmatch(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?|[가-힣]+", unit))
        previous_word_like = bool(
            output and re.search(r"[A-Za-z0-9가-힣]$", output)
        )
        if output and word_like and previous_word_like:
            output += " "
        output += unit
    return output


def preferred_boundary_observation(row: dict) -> tuple[str, str]:
    """Prefer word-timestamp ASR over Jianying only when it fits better."""

    original = str(row.get("original", ""))
    asr_text = str(row.get("asr_text", "")).strip()
    try:
        asr_confidence = float(row.get("asr_score") or 0.0)
    except (TypeError, ValueError):
        asr_confidence = 0.0
    if not asr_text or asr_confidence < 0.60:
        return original, "jianying"
    canonical = str(row.get("text", ""))
    original_fit = span_similarity(original, canonical)[0]
    asr_fit = span_similarity(asr_text, canonical)[0]
    if asr_fit >= 0.50 and asr_fit >= original_fit + 0.04:
        return asr_text, "word_asr"
    return original, "jianying"


def boundary_review_candidates(rows: list[dict]) -> list[dict]:
    by_original = {
        int(row["original_cue"]): row
        for row in rows
        if str(row.get("original_cue", "")).isdigit()
    }
    candidates: list[dict] = []
    for number in sorted(by_original):
        if number + 1 not in by_original:
            continue
        left, right = by_original[number], by_original[number + 1]
        if left.get("track") != right.get("track"):
            continue
        if any(
            marker in str(left.get("evidence", "")) or marker in str(right.get("evidence", ""))
            for marker in (
                "manual_timing_review",
                "manual_boundary_review",
                "canonical_lrc_boundary_vocalization_dedup",
            )
        ):
            # Audio-reviewed boundaries supersede Jianying's original cut.
            continue
        if int(right["start_ms"]) - int(left["end_ms"]) > 1200:
            continue
        observed_left, left_observation_source = preferred_boundary_observation(left)
        observed_right, right_observation_source = preferred_boundary_observation(right)
        if len(normalized_text(observed_left)) < 2 or len(normalized_text(observed_right)) < 2:
            continue
        left_units = boundary_units(str(left.get("text", "")))
        right_units = boundary_units(str(right.get("text", "")))
        units = left_units + right_units
        current_split = len(left_units)
        if current_split <= 0 or current_split >= len(units):
            continue

        best_result: tuple[float, float, int, str] | None = None
        for mode in ("text", "korean_pronunciation"):
            def representation(value: str) -> str:
                if mode == "korean_pronunciation":
                    value = romanize_hangul(value)
                return normalized_text(value)

            observed_a = normalized_text(observed_left)
            observed_b = normalized_text(observed_right)
            current_a = representation(join_boundary_units(units[:current_split]))
            current_b = representation(join_boundary_units(units[current_split:]))
            current_score = (
                difflib.SequenceMatcher(None, observed_a, current_a, autojunk=False).ratio()
                + difflib.SequenceMatcher(None, observed_b, current_b, autojunk=False).ratio()
            ) / 2.0
            best_score, best_split = current_score, current_split
            for split in range(1, len(units)):
                candidate_a = representation(join_boundary_units(units[:split]))
                candidate_b = representation(join_boundary_units(units[split:]))
                score = (
                    difflib.SequenceMatcher(None, observed_a, candidate_a, autojunk=False).ratio()
                    + difflib.SequenceMatcher(None, observed_b, candidate_b, autojunk=False).ratio()
                ) / 2.0
                if score > best_score:
                    best_score, best_split = score, split
            improvement = best_score - current_score
            result = (improvement, best_score, best_split, mode)
            if best_result is None or result[:2] > best_result[:2]:
                best_result = result

        assert best_result is not None
        improvement, score, suggested_split, mode = best_result
        unit_shift = suggested_split - current_split
        def selected_representation(value: str) -> str:
            if mode == "korean_pronunciation":
                value = romanize_hangul(value)
            return normalized_text(value)

        suggested_left_text = join_boundary_units(units[:suggested_split])
        suggested_right_text = join_boundary_units(units[suggested_split:])
        edge_left_score = difflib.SequenceMatcher(
            None,
            normalized_text(observed_left),
            selected_representation(suggested_left_text),
            autojunk=False,
        ).ratio()
        edge_right_score = difflib.SequenceMatcher(
            None,
            normalized_text(observed_right),
            selected_representation(suggested_right_text),
            autojunk=False,
        ).ratio()
        # Whole-cue averages dilute a one- or two-word boundary correction in
        # a long following line.  An exact direct-text reconstruction of both
        # Jianying cells is stronger evidence even when the average gain is
        # only a few hundredths.  Keep this deliberately narrow: direct text,
        # at most three units moved, and both reconstructed cells >= 96%.
        edge_exact = (
            mode == "text"
            and improvement >= 0.02
            and 1 <= abs(unit_shift) <= 3
            and min(edge_left_score, edge_right_score) >= 0.96
        )
        if (
            not edge_exact
            and (improvement < 0.12 or abs(unit_shift) < 1 or score < 0.48)
        ):
            continue
        if edge_exact or (score >= 0.75 and improvement >= 0.15):
            risk = "high"
        elif score >= 0.60 and improvement >= 0.12:
            risk = "medium"
        else:
            risk = "low"
        candidates.append(
            {
                "category": "cross_cue_boundary",
                "risk": risk,
                "track": left.get("track", ""),
                "left_cue": number,
                "right_cue": number + 1,
                "start": format_srt_time(int(left["start_ms"])),
                "observed_left": observed_left,
                "observed_right": observed_right,
                "current_left": left.get("text", ""),
                "current_right": right.get("text", ""),
                "suggested_left": suggested_left_text,
                "suggested_right": suggested_right_text,
                "score": round(score, 6),
                "improvement": round(improvement, 6),
                "unit_shift": unit_shift,
                "mode": mode,
                "edge_exact": edge_exact,
                "edge_left_score": round(edge_left_score, 6),
                "edge_right_score": round(edge_right_score, 6),
                "observation_source": f"{left_observation_source}+{right_observation_source}",
                "reason": "observed words support a different canonical word boundary",
            }
        )
    return candidates


def auto_resegment_high_confidence_boundaries(rows: list[dict]) -> int:
    """Iteratively apply only unambiguous Jianying-supported word boundaries.

    This operates on the continuous canonical text already assigned to adjacent
    cues. It changes text distribution only, never timing. Ambiguous repeated
    refrains and pronunciation-only matches remain review items.
    """
    applied = 0
    for _ in range(6):
        candidates = []
        for row in boundary_review_candidates(rows):
            if row["category"] != "cross_cue_boundary" or row["risk"] != "high":
                continue
            score = float(row["score"])
            improvement = float(row["improvement"])
            direct = row["mode"] == "text" and score >= 0.79 and improvement >= 0.15
            # Korean pronunciation is noisier than direct text, so require a
            # larger gain.  This still safely handles repeated lyric cells
            # where Jianying rendered the missing Korean as English phonetics.
            phonetic = (
                row["mode"] == "korean_pronunciation"
                and score >= 0.78
                and improvement >= 0.18
            )
            edge_exact = bool(row.get("edge_exact"))
            if direct or phonetic or edge_exact:
                candidates.append(row)
        if not candidates:
            break
        candidates.sort(key=lambda row: float(row["improvement"]), reverse=True)
        by_original = {
            int(row["original_cue"]): row
            for row in rows
            if str(row.get("original_cue", "")).isdigit()
        }
        used: set[int] = set()
        changed = 0
        for candidate in candidates:
            left_number = int(candidate["left_cue"])
            right_number = int(candidate["right_cue"])
            if left_number in used or right_number in used:
                continue
            left = by_original.get(left_number)
            right = by_original.get(right_number)
            if not left or not right:
                continue
            if left.get("kind") != "existing" or right.get("kind") != "existing":
                continue
            left["text"] = candidate["suggested_left"]
            right["text"] = candidate["suggested_right"]
            for row in (left, right):
                row["status"] = "auto_resegmented_boundary"
                row["confidence"] = "high"
                source = str(candidate.get("observation_source", "jianying+jianying"))
                row["evidence"] = row.get("evidence", "") + "+observed_sequence_resegment:" + source
            used.update((left_number, right_number))
            applied += 1
            changed += 1
        if not changed:
            break
    return applied


def is_generic_vocalization(value: str) -> bool:
    """Return whether text is only a short non-lexical/ad-lib vocalization."""

    tokens = re.findall(r"[a-z]+", unicodedata.normalize("NFKC", value).casefold())
    compact = normalized_text(value)
    if (
        tokens
        and compact == "".join(tokens)
        and all(
            token in {"ah", "ha", "hey", "huh", "la", "na", "oh", "ooh", "uh", "yeah"}
            for token in tokens
        )
    ):
        return True
    return bool(compact) and all(char in "啊阿哈啦拉呐哪哦噢喔吧诶哎" for char in compact)


def discard_noncanonical_vocalization_rows(rows: list[dict]) -> int:
    """Drop source/ASR-only ad-libs that have no canonical LRC association."""

    kept: list[dict] = []
    discarded = 0
    for row in rows:
        has_lrc_index = any(
            value.strip().isdigit()
            for value in str(row.get("lrc_indices", "")).split(";")
        )
        if not has_lrc_index and is_generic_vocalization(str(row.get("text", ""))):
            discarded += 1
            continue
        kept.append(row)
    rows[:] = kept
    return discarded


def deduplicate_boundary_vocalizations(rows: list[dict], events: list[dict]) -> int:
    """Remove an extra adjacent ad-lib occurrence not present in canonical LRC.

    Boundary resegmentation may place the sole canonical ``Uh`` at the end of
    the left cue because Jianying heard it there.  A later manual override can
    independently restore the same ``Uh`` at the start of the right cue.  Keep
    exactly the LRC-authored occurrence and choose its side from the rows'
    canonical LRC-index association.
    """

    event_text = {
        (str(event.get("track", "")), int(event["lrc_index"])): str(event.get("text", ""))
        for event in events
        if str(event.get("lrc_index", "")).isdigit()
    }

    def canonical_text(row: dict) -> str:
        track = str(row.get("track", ""))
        indices = [
            int(value)
            for value in str(row.get("lrc_indices", "")).split(";")
            if value.strip().isdigit()
        ]
        return " ".join(event_text.get((track, index), "") for index in indices)

    ordered = sorted(rows, key=lambda row: (int(row["start_ms"]), int(row["end_ms"])))
    removed = 0
    for left, right in zip(ordered, ordered[1:]):
        if left.get("track") != right.get("track"):
            continue
        if int(right["start_ms"]) - int(left["end_ms"]) > 1200:
            continue
        left_units = boundary_units(str(left.get("text", "")))
        right_units = boundary_units(str(right.get("text", "")))
        for length in range(min(2, len(left_units), len(right_units)), 0, -1):
            left_fragment = left_units[-length:]
            right_fragment = right_units[:length]
            fragment_text = join_boundary_units(left_fragment)
            if normalized_text(fragment_text) != normalized_text(
                join_boundary_units(right_fragment)
            ):
                continue
            if not is_generic_vocalization(fragment_text):
                continue
            left_expected = canonical_text(left)
            right_expected = canonical_text(right)
            in_left = normalized_text(fragment_text) in normalized_text(left_expected)
            in_right = normalized_text(fragment_text) in normalized_text(right_expected)
            if in_right and not in_left and len(left_units) > length:
                left["text"] = join_boundary_units(left_units[:-length])
                target = left
            elif in_left and not in_right and len(right_units) > length:
                right["text"] = join_boundary_units(right_units[length:])
                target = right
            else:
                continue
            target["status"] = "auto_removed_noncanonical_duplicate_adlib"
            target["confidence"] = "high"
            target["evidence"] = (
                str(target.get("evidence", ""))
                + "+canonical_lrc_boundary_vocalization_dedup"
            )
            removed += 1
            break
    return removed


def is_repeated_lyric_fragment(value: str) -> bool:
    """Recognize intentional short refrains such as a repeated hook phrase."""
    tokens = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", value).casefold())
    if len(tokens) < 4:
        return False
    aliases = {"with": "wid"}
    normalized_tokens = [aliases.get(token, token) for token in tokens]
    unique = set(normalized_tokens)
    return len(unique) <= 3 and len(unique) <= max(2, len(normalized_tokens) // 3)


def unresolved_existing_candidates(rows: list[dict]) -> list[dict]:
    by_original = {
        int(row["original_cue"]): row
        for row in rows
        if str(row.get("original_cue", "")).isdigit()
    }
    candidates: list[dict] = []
    for number, row in sorted(by_original.items()):
        if number <= 8 or row.get("status") != "keep_existing":
            continue
        text = str(row.get("original", ""))
        if is_generic_vocalization(text) or is_repeated_lyric_fragment(text):
            continue
        neighbor_scores: list[float] = []
        for neighbor_number in (number - 1, number + 1):
            neighbor = by_original.get(neighbor_number)
            if not neighbor or neighbor.get("track") != row.get("track"):
                continue
            _, score, _, _ = best_text_span(text, str(neighbor.get("text", "")))
            neighbor_scores.append(score)
        neighbor_score = max(neighbor_scores, default=0.0)
        risk = "high" if neighbor_score >= 0.50 else "medium"
        candidates.append(
            {
                "category": "unresolved_existing_text",
                "risk": risk,
                "track": row.get("track", ""),
                "left_cue": number,
                "right_cue": "",
                "start": format_srt_time(int(row["start_ms"])),
                "observed_left": text,
                "observed_right": "",
                "current_left": row.get("text", ""),
                "current_right": "",
                "suggested_left": "",
                "suggested_right": "",
                "score": round(neighbor_score, 6),
                "improvement": "",
                "unit_shift": "",
                "mode": "text_or_pronunciation",
                "reason": "non-vocal Jianying text remains without canonical evidence",
            }
        )
    return candidates


def manual_review_note_candidates(rows: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for row in rows:
        evidence = str(row.get("evidence", ""))
        marker = "manual_review_note:"
        if marker not in evidence:
            continue
        note = evidence.split(marker, 1)[1]
        candidates.append(
            {
                "category": "manual_text_uncertainty",
                "risk": "low",
                "track": row.get("track", ""),
                "left_cue": row.get("original_cue", ""),
                "right_cue": "",
                "start": format_srt_time(int(row["start_ms"])),
                "observed_left": row.get("original", ""),
                "observed_right": "",
                "current_left": row.get("text", ""),
                "current_right": "",
                "suggested_left": "",
                "suggested_right": "",
                "score": "",
                "improvement": "",
                "unit_shift": "",
                "mode": "manual_audio_review",
                "reason": note,
            }
        )
    return candidates


def evaluate_regression_cases(
    final: list[Cue], source_srt: Path, cases_path: Path
) -> tuple[list[str], dict]:
    """Evaluate project-scoped human confirmations guarded by input hash.

    Concrete lyrics, cue numbers, and milliseconds must never leak from one
    mix into another.  A case file is therefore accepted only when its source
    Jianying SRT hash matches the current input.
    """

    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    actual_hash, scope_issue = validate_source_srt_scope(
        payload, source_srt, "regression case file"
    )
    issues: list[str] = []
    if scope_issue:
        issues.append(scope_issue)
        return issues, {"project": payload.get("project", ""), "case_count": 0, "passed": 0}

    passed = 0
    cases = list(payload.get("cases", []))
    for position, case in enumerate(cases, start=1):
        case_id = str(case.get("id") or f"case_{position}")
        kind = str(case.get("kind", "interval_text"))
        start_ms = int(case["start_ms"])
        end_ms = int(case.get("end_ms", start_ms))
        tolerance = int(case.get("tolerance_ms", 0))
        expected_text = str(case.get("text", ""))
        if kind == "interval_text":
            candidates = [
                cue
                for cue in final
                if abs(cue.start_ms - start_ms) <= tolerance
                and abs(cue.end_ms - end_ms) <= tolerance
            ]
            actual = next(
                (
                    cue
                    for cue in candidates
                    if normalized_text(cue.text) == normalized_text(expected_text)
                ),
                None,
            )
            if actual is None:
                issues.append(
                    f"project regression {case_id} failed at {format_srt_time(start_ms)}"
                )
                continue
        elif kind == "absent_text":
            offending = [
                cue
                for cue in final
                if cue.end_ms > start_ms
                and cue.start_ms < end_ms
                and normalized_text(expected_text) in normalized_text(cue.text)
            ]
            if offending:
                issues.append(
                    f"project regression {case_id} contains forbidden text at "
                    f"{format_srt_time(offending[0].start_ms)}"
                )
                continue
        else:
            issues.append(f"project regression {case_id} has unsupported kind {kind!r}")
            continue
        passed += 1
    return issues, {
        "project": payload.get("project", ""),
        "case_count": len(cases),
        "passed": passed,
        "source_srt_sha256": actual_hash,
    }


def command_qa(args: argparse.Namespace) -> int:
    from collections import Counter

    source = parse_srt(args.source_srt)
    final = parse_srt(args.final_srt)
    with args.report.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    confirmed_omissions: dict[tuple[str, int], str] = {}
    manual_override_scope_issue: str | None = None
    if args.manual_overrides:
        qa_overrides = json.loads(args.manual_overrides.read_text(encoding="utf-8"))
        _, manual_override_scope_issue = validate_source_srt_scope(
            qa_overrides, args.source_srt, "manual override file"
        )
        for item in qa_overrides.get("_confirmed_omitted_lrc_events", []):
            confirmed_omissions[(str(item["track"]), int(item["lrc_index"]))] = str(
                item.get("reason", "confirmed_audio_edit")
            )
    issues: list[str] = []
    if manual_override_scope_issue:
        issues.append(manual_override_scope_issue)
    noncanonical_vocalization_rows = [
        row
        for row in rows
        if not any(
            value.strip().isdigit()
            for value in str(row.get("lrc_indices", "")).split(";")
        )
        and is_generic_vocalization(str(row.get("text", "")))
    ]
    if noncanonical_vocalization_rows:
        issues.append(
            "non-canonical ad-lib vocalizations remain without LRC indices: "
            + str(
                [
                    str(row.get("original_cue", ""))
                    for row in noncanonical_vocalization_rows[:10]
                ]
            )
        )
    if [cue.number for cue in final] != list(range(1, len(final) + 1)):
        issues.append("final cue numbers are not sequential")
    if any(cue.end_ms <= cue.start_ms for cue in final):
        issues.append("one or more cues have non-positive duration")
    if any(not cue.text.strip() for cue in final):
        issues.append("one or more cues have blank text")
    if any("�" in cue.text for cue in final):
        issues.append("Unicode replacement characters found")
    metadata_pattern = re.compile(r"(?:企划:|出品人:|本作品经过|OP:|SP:)", re.IGNORECASE)
    if any(metadata_pattern.search(cue.text) for cue in final):
        issues.append("lyric metadata leaked into final subtitles")
    unexpected_overlaps = []
    for left, right in zip(final, final[1:]):
        if left.end_ms > right.start_ms and right.start_ms >= 10_000:
            unexpected_overlaps.append((left.number, right.number))
    if unexpected_overlaps:
        issues.append(f"unexpected overlaps: {unexpected_overlaps[:10]}")

    source_by_number = {cue.number: cue for cue in source}
    hybrid_anchor_drift: list[tuple[str, int, int]] = []
    for row in rows:
        if row.get("kind") != "hybrid" or "jianying_onset_anchor" not in row.get("evidence", ""):
            continue
        number = str(row.get("original_cue", ""))
        if not number.isdigit() or int(number) not in source_by_number:
            continue
        delta = int(row["start_ms"]) - source_by_number[int(number)].start_ms
        if delta:
            hybrid_anchor_drift.append((number, int(row["start_ms"]), delta))
    if hybrid_anchor_drift:
        issues.append(f"hybrid Jianying onset anchors drifted: {hybrid_anchor_drift[:10]}")

    bpm_mapping_issues: list[tuple[str, float, float]] = []
    bpm_mapping_warnings: list[tuple[str, float, float]] = []
    audio_edit_review_candidates: list[dict] = []
    confirmed_audio_cut_count = 0
    if args.audio_alignment:
        alignment_payload = json.loads(args.audio_alignment.read_text(encoding="utf-8"))
        for item in alignment_payload.get("tracks", []):
            for edit in item.get("edit_candidates", []):
                if edit.get("status") == "confirmed":
                    confirmed_audio_cut_count += 1
                    if float(edit["source_end"]) <= float(edit["source_start"]):
                        issues.append(
                            "invalid confirmed audio cut in "
                            + str(item["track"]["title"])
                        )
                    continue
                if edit.get("status") != "review":
                    continue
                absolute_ms = int(item["track"]["start_ms"]) + int(
                    round(float(edit["mix_time"]) * 1000)
                )
                audio_edit_review_candidates.append(
                    {
                        "category": "audio_edit_candidate",
                        "risk": "high",
                        "track": str(item["track"]["title"]),
                        "left_cue": 0,
                        "right_cue": "",
                        "start": format_srt_time(absolute_ms),
                        "observed_left": "",
                        "observed_right": "",
                        "current_left": "",
                        "current_right": "",
                        "suggested_left": (
                            f"possible source cut {float(edit['source_start']):.3f}s"
                            f" -> {float(edit['source_end']):.3f}s"
                        ),
                        "suggested_right": "",
                        "score": "",
                        "improvement": "",
                        "unit_shift": "",
                        "mode": "bpm_normalized_waveform_jump",
                        "reason": "model must confirm or reject before lyric projection",
                    }
                )
            expected = item.get("bpm_tempo_ratio")
            if not expected:
                continue
            weighted_slopes: list[float] = []
            for segment in item.get("segments", []):
                if int(segment.get("anchor_count", 0)) < 3:
                    continue
                actual = float(segment["slope"])
                relative_delta = abs(actual - float(expected)) / float(expected)
                weighted_slopes.extend([actual] * int(segment.get("anchor_count", 0)))
                if relative_delta > 0.04:
                    bpm_mapping_warnings.append(
                        (str(item["track"]["title"]), actual, relative_delta)
                    )
            if weighted_slopes:
                weighted_slopes.sort()
                dominant = weighted_slopes[len(weighted_slopes) // 2]
                relative_delta = abs(dominant - float(expected)) / float(expected)
                if relative_delta > 0.04:
                    bpm_mapping_issues.append(
                        (str(item["track"]["title"]), dominant, relative_delta)
                    )
    if bpm_mapping_issues:
        issues.append(f"audio mapping contradicts BPM prior: {bpm_mapping_issues[:10]}")

    collapsed_after_unmapped: list[tuple[str, str]] = []
    for left, right in zip(rows, rows[1:]):
        if (
            left.get("status") == "keep_existing"
            and left.get("track") == right.get("track")
            and ";" in right.get("lrc_indices", "")
            and left.get("original_cue")
            and right.get("original_cue")
            and int(right["original_cue"]) == int(left["original_cue"]) + 1
            and int(right["start_ms"]) - int(left["end_ms"]) <= 1200
            and text_similarity(
                left.get("original", ""), right.get("text", "")
            )[0] >= 0.40
        ):
            collapsed_after_unmapped.append(
                (str(left["original_cue"]), str(right["original_cue"]))
            )
    if collapsed_after_unmapped:
        issues.append(
            "unmapped cue followed by collapsed lyric sequence: "
            f"{collapsed_after_unmapped[:10]}"
        )

    lyric_index_regressions: list[tuple[str, str, int, int]] = []
    last_lrc_index: dict[str, int] = {}
    for row in sorted(rows, key=lambda item: (int(item["start_ms"]), int(item["end_ms"]))):
        indices = [
            int(value)
            for value in row.get("lrc_indices", "").split(";")
            if value.strip().isdigit()
        ]
        if not indices:
            continue
        track = str(row.get("track", ""))
        previous = last_lrc_index.get(track)
        if previous is not None and min(indices) < previous:
            lyric_index_regressions.append(
                (track, str(row.get("original_cue", "")), previous, min(indices))
            )
        last_lrc_index[track] = max(previous if previous is not None else -1, max(indices))
    if lyric_index_regressions:
        issues.append(f"lyric index moved backward: {lyric_index_regressions[:10]}")

    source_intervals = {(cue.start_ms, cue.end_ms) for cue in source}
    final_intervals = {(cue.start_ms, cue.end_ms) for cue in final}
    preserved_intervals = source_intervals & final_intervals
    regression_summary = {
        "project": "",
        "case_count": 0,
        "passed": 0,
        "source_srt_sha256": sha256(args.source_srt),
    }
    if args.regression_cases:
        regression_issues, regression_summary = evaluate_regression_cases(
            final, args.source_srt, args.regression_cases
        )
        issues.extend(regression_issues)
    long_cues = [
        {
            "number": cue.number,
            "duration_ms": cue.end_ms - cue.start_ms,
            "text": cue.text,
        }
        for cue in final
        if cue.end_ms - cue.start_ms >= 8_000
    ]
    lyric_coverage_candidates: list[dict] = []
    duplicate_lyric_event_candidates: list[dict] = []
    lyric_coverage_missing: dict[str, list[int]] = {}
    lyric_metadata_unlinked: dict[str, list[int]] = {}
    if args.song_list and args.lyrics_dir and args.audio_alignment:
        tracks = parse_song_list(args.song_list, args.lyrics_dir, source[-1].end_ms)
        alignment_payload = json.loads(args.audio_alignment.read_text(encoding="utf-8"))
        projected_events, _ = projected_lyric_events(tracks, source, alignment_payload)
        represented: dict[str, set[int]] = {}
        for row in rows:
            represented.setdefault(str(row.get("track", "")), set()).update(
                int(value)
                for value in row.get("lrc_indices", "").split(";")
                if value.strip().isdigit()
            )
        expected_indices: dict[str, set[int]] = {}
        event_by_key: dict[tuple[str, int], dict] = {}
        for event in projected_events:
            expected_indices.setdefault(str(event["track"]), set()).add(int(event["lrc_index"]))
            event_by_key[(str(event["track"]), int(event["lrc_index"]))] = event

        ordered_report_rows = sorted(rows, key=lambda row: (int(row["start_ms"]), int(row["end_ms"])))
        for left, right in zip(ordered_report_rows, ordered_report_rows[1:]):
            track = str(left.get("track", ""))
            if not track or track != str(right.get("track", "")):
                continue
            if int(right["start_ms"]) - int(left["end_ms"]) > 300:
                continue
            left_indices = {
                int(value) for value in str(left.get("lrc_indices", "")).split(";") if value.isdigit()
            }
            right_indices = {
                int(value) for value in str(right.get("lrc_indices", "")).split(";") if value.isdigit()
            }
            for index in sorted(left_indices & right_indices):
                event = event_by_key.get((track, index))
                if not event:
                    continue
                event_text = str(event["text"])
                event_norm = normalized_text(event_text)
                if len(event_norm) < 4:
                    continue
                # A shared LRC index is normal when one canonical line is split
                # across two Jianying cells.  It is a duplication only when the
                # complete event text appears in both outputs.  If both source
                # observations also contain the event, it is an audible repeat
                # rather than an accidental copy.
                if not (
                    event_norm in normalized_text(str(left.get("text", "")))
                    and event_norm in normalized_text(str(right.get("text", "")))
                ):
                    continue
                left_observed_score = span_similarity(str(left.get("original", "")), event_text)[0]
                right_observed_score = span_similarity(str(right.get("original", "")), event_text)[0]
                if min(left_observed_score, right_observed_score) >= 0.55:
                    continue
                duplicate_lyric_event_candidates.append(
                    {
                        "category": "duplicate_lyric_event",
                        "risk": "high",
                        "track": track,
                        "left_cue": int(left.get("original_cue") or 0),
                        "right_cue": int(right.get("original_cue") or 0),
                        "start": format_srt_time(int(left["start_ms"])),
                        "observed_left": str(left.get("original", "")),
                        "observed_right": str(right.get("original", "")),
                        "current_left": str(left.get("text", "")),
                        "current_right": str(right.get("text", "")),
                        "suggested_left": "",
                        "suggested_right": "",
                        "score": round(max(left_observed_score, right_observed_score), 6),
                        "improvement": "",
                        "unit_shift": "",
                        "mode": "shared_lrc_index_full_text",
                        "reason": f"LRC index {index} is substantially repeated in adjacent cues",
                    }
                )
        for event in projected_events:
            track = str(event["track"])
            index = int(event["lrc_index"])
            if (track, index) in confirmed_omissions:
                continue
            if index in represented.get(track, set()):
                continue
            lyric_metadata_unlinked.setdefault(track, []).append(index)
            nearby_final = [
                cue
                for cue in final
                if cue.start_ms - 600 <= int(event["projected_ms"]) <= cue.end_ms + 600
            ]
            final_text_score = max(
                (
                    best_text_span(cue.text, str(event["text"]))[1]
                    for cue in nearby_final
                ),
                default=0.0,
            )
            # The event can already be present as a fragment inside a cue whose
            # report carries only the neighbouring LRC index.  Treat local text
            # evidence as covered instead of raising a false omission.
            if final_text_score >= 0.55:
                continue
            lyric_coverage_missing.setdefault(track, []).append(index)
            isolated = (
                index - 1 in represented.get(track, set())
                and index + 1 in represented.get(track, set())
                and index - 1 in expected_indices.get(track, set())
                and index + 1 in expected_indices.get(track, set())
            )
            nearby = [
                cue
                for cue in source
                if cue.start_ms - 350 <= int(event["projected_ms"]) <= cue.end_ms + 350
            ]
            best_score = 0.0
            best_coverage = 0.0
            observed = ""
            for cue in nearby:
                _, score, coverage, _ = best_text_span(cue.text, str(event["text"]))
                if (score, coverage) > (best_score, best_coverage):
                    best_score, best_coverage, observed = score, coverage, cue.text
            lyric_coverage_candidates.append(
                {
                    "category": "lyric_coverage_gap",
                    "risk": "medium",
                    "track": track,
                    "left_cue": 0,
                    "right_cue": "",
                    "start": format_srt_time(int(event["projected_ms"])),
                    "observed_left": observed,
                    "observed_right": "",
                    "current_left": "",
                    "current_right": "",
                    "suggested_left": str(event["text"]),
                    "suggested_right": "",
                    "score": round(best_score, 6),
                    "improvement": "",
                    "unit_shift": "",
                    "mode": "canonical_event_coverage",
                    "reason": (
                        f"LRC index {index} is not represented; model/context confirmation required"
                        if isolated
                        else f"LRC index {index} belongs to an unresolved run; confirm an edit/cut or recover the lyric"
                    ),
                }
            )

    review_candidates = (
        boundary_review_candidates(rows)
        + unresolved_existing_candidates(rows)
        + manual_review_note_candidates(rows)
        + lyric_coverage_candidates
        + duplicate_lyric_event_candidates
        + audio_edit_review_candidates
    )
    trusted_short_intervals = {
        (int(row["start_ms"]), int(row["end_ms"]), normalized_text(str(row.get("text", ""))))
        for row in rows
        if str(row.get("status", ""))
        in {"manual_verified_insertion", "manual_verified_split"}
        or "manual_timing_review" in str(row.get("evidence", ""))
    }
    final_positions = {cue.number: position for position, cue in enumerate(final)}
    for cue in final:
        duration_ms = cue.end_ms - cue.start_ms
        position = final_positions[cue.number]
        next_cue = final[position + 1] if position + 1 < len(final) else None
        absorbed_short_prefix = bool(
            duration_ms < 300
            and next_cue
            and next_cue.start_ms - cue.end_ms <= 100
            and normalized_text(cue.text)
            and normalized_text(next_cue.text).startswith(normalized_text(cue.text))
        )
        if absorbed_short_prefix:
            continue
        if (
            cue.start_ms,
            cue.end_ms,
            normalized_text(cue.text),
        ) in trusted_short_intervals:
            # The user explicitly prioritizes correct audio timing over reading
            # speed.  A verified short sung fragment is therefore valid; only
            # unexplained sub-300 ms cues remain blocking.
            continue
        if duration_ms < 300:
            review_candidates.append(
                {
                    "category": "timing_integrity",
                    "risk": "high",
                    "track": "",
                    "left_cue": cue.number,
                    "right_cue": "",
                    "start": format_srt_time(cue.start_ms),
                    "observed_left": "",
                    "observed_right": "",
                    "current_left": cue.text,
                    "current_right": "",
                    "suggested_left": "",
                    "suggested_right": "",
                    "score": "",
                    "improvement": "",
                    "unit_shift": "",
                    "mode": "minimum_timing_integrity",
                    "reason": f"duration={duration_ms}ms and text is not absorbed by the next cue",
                }
            )
    review_candidates.sort(
        key=lambda row: (
            {"high": 0, "medium": 1, "low": 2}.get(str(row["risk"]), 3),
            int(row["left_cue"]),
        )
    )
    high_review_count = sum(row["risk"] == "high" for row in review_candidates)
    medium_review_count = sum(row["risk"] == "medium" for row in review_candidates)
    # Isolated canonical-event holes are blocking even when textual evidence is
    # only medium-confidence: that is exactly how a merged Jianying cue can
    # silently lose one lyric line in a merged-cue sequence.
    publish_ready = (
        not issues
        and high_review_count == 0
        and len(lyric_coverage_candidates) == 0
    )
    if args.out_review:
        args.out_review.parent.mkdir(parents=True, exist_ok=True)
        fields = list(review_candidates[0]) if review_candidates else ["category"]
        with args.out_review.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(review_candidates)
    summary = {
        "algorithm_version": ALGORITHM_VERSION,
        "passed": not issues,
        "publish_ready": publish_ready,
        "issues": issues,
        "source_cue_count": len(source),
        "final_cue_count": len(final),
        "source_intervals_preserved_exactly": len(preserved_intervals),
        "new_or_rebuilt_intervals": len(final_intervals - source_intervals),
        "unexpected_overlap_count": len(unexpected_overlaps),
        "collapsed_after_unmapped_count": len(collapsed_after_unmapped),
        "lyric_index_regression_count": len(lyric_index_regressions),
        "hybrid_anchor_drift_count": len(hybrid_anchor_drift),
        "bpm_mapping_issue_count": len(bpm_mapping_issues),
        "bpm_mapping_warning_count": len(bpm_mapping_warnings),
        "bpm_mapping_warnings": bpm_mapping_warnings,
        "confirmed_audio_cut_count": confirmed_audio_cut_count,
        "confirmed_omitted_lrc_events": [
            {"track": track, "lrc_index": index, "reason": reason}
            for (track, index), reason in sorted(confirmed_omissions.items())
        ],
        "unreviewed_audio_edit_candidate_count": len(audio_edit_review_candidates),
        "lyric_metadata_unlinked": lyric_metadata_unlinked,
        "lyric_coverage_missing": lyric_coverage_missing,
        "unresolved_lyric_gap_count": len(lyric_coverage_candidates),
        "unresolved_isolated_lyric_gap_count": len(lyric_coverage_candidates),
        "high_review_candidate_count": high_review_count,
        "medium_review_candidate_count": medium_review_count,
        "review_candidate_count": len(review_candidates),
        "boundary_review_report": str(args.out_review) if args.out_review else "",
        "blank_text_count": sum(not cue.text.strip() for cue in final),
        "noncanonical_vocalization_count": len(noncanonical_vocalization_rows),
        "long_cues_8s_or_more": long_cues,
        "kind_counts": dict(Counter(row["kind"] for row in rows)),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "confidence_counts": dict(Counter(row["confidence"] for row in rows)),
        "project_regression": regression_summary,
    }
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if publish_ready else 1


def command_audio_align(args: argparse.Namespace) -> int:
    import librosa
    import numpy as np

    cues = parse_srt(args.srt)
    tracks = parse_song_list(args.song_list, args.lyrics_dir, cues[-1].end_ms)
    bpm_changes = parse_bpm_changes(args.bpm_changes)
    mix_audio, _ = librosa.load(args.audio, sr=args.sample_rate, mono=True)
    mix_audio = np.diff(mix_audio, prepend=mix_audio[0]).astype(np.float32)
    payload: dict = {
        "sample_rate": args.sample_rate,
        "window_seconds": args.window_seconds,
        "step_seconds": args.step_seconds,
        "tracks": [],
    }
    for track in tracks:
        source_path = find_source_audio(track, args.source_dir)
        source_raw, _ = librosa.load(source_path, sr=args.sample_rate, mono=True)
        track_cues = [cue for cue in cues if cue_track(cue, tracks).index == track.index]
        lines = parse_lrc(Path(track.lrc_path)) if track.lrc_path else []
        text_matches = monotonic_text_candidates(track_cues, lines)
        text_tempo_ratio = estimate_tempo_ratio(text_matches, track_cues, track.start_ms)
        bpm_change = bpm_change_for_track(track, bpm_changes)
        bpm_tempo_ratio = float(bpm_change["tempo_ratio"]) if bpm_change else None
        # BPM is a prior, not a timestamp.  It normalizes the source waveform so
        # cross-correlation compares like-for-like audio; cuts are still found
        # as intercept jumps in the selected path.
        tempo_ratio = bpm_tempo_ratio or text_tempo_ratio
        if bpm_tempo_ratio:
            aligned_source = librosa.effects.time_stretch(
                source_raw.astype(np.float32, copy=False), rate=bpm_tempo_ratio
            )
        else:
            aligned_source = source_raw
        aligned_source = np.diff(
            aligned_source, prepend=aligned_source[0]
        ).astype(np.float32)
        text_anchors = [
            (
                (next(cue for cue in track_cues if cue.number == number).start_ms - track.start_ms)
                / 1000.0,
                match["lrc_time_ms"] / 1000.0,
            )
            for number, match in text_matches.items()
            if match["score"] >= 0.90 and match["coverage"] >= 0.78
        ]
        local_start = track.start_ms / 1000.0
        local_end = track.end_ms / 1000.0
        clip = mix_audio[int(local_start * args.sample_rate) : int(local_end * args.sample_rate)]
        window_size = int(args.window_seconds * args.sample_rate)
        windows: list[dict] = []
        centers = np.arange(
            args.window_seconds / 2,
            max(args.window_seconds / 2 + 0.01, len(clip) / args.sample_rate - args.window_seconds / 2),
            args.step_seconds,
        )
        for center in centers:
            begin = int((center - args.window_seconds / 2) * args.sample_rate)
            query = clip[begin : begin + window_size]
            if len(query) < window_size:
                continue
            candidates = waveform_candidates(
                query, aligned_source, args.sample_rate, args.candidate_count
            )
            for candidate in candidates:
                aligned_start = float(candidate["source_start"])
                aligned_center = aligned_start + args.window_seconds / 2
                if bpm_tempo_ratio:
                    candidate["aligned_source_start"] = aligned_start
                    candidate["source_start"] = aligned_start * bpm_tempo_ratio
                    candidate["source_center"] = aligned_center * bpm_tempo_ratio
                else:
                    candidate["source_center"] = aligned_center
            windows.append(
                {"mix_center": float(center), "candidates": candidates}
            )
        chosen = choose_audio_path(windows, tempo_ratio, text_anchors)
        segments = audio_segments(chosen, tempo_ratio)
        edit_candidates = audio_edit_candidates(segments, tempo_ratio, text_anchors)
        payload["tracks"].append(
            {
                "track": asdict(track),
                "source_audio": str(source_path.resolve()),
                "source_sha256": sha256(source_path),
                "source_duration": len(source_raw) / args.sample_rate,
                "estimated_tempo_ratio": tempo_ratio,
                "text_tempo_ratio": text_tempo_ratio,
                "bpm_tempo_ratio": bpm_tempo_ratio,
                "bpm_source": bpm_change,
                "bpm_text_ratio_delta": (
                    abs(text_tempo_ratio - bpm_tempo_ratio) / bpm_tempo_ratio
                    if bpm_tempo_ratio and text_tempo_ratio != 1.0
                    else None
                ),
                "text_anchor_count": len(text_anchors),
                "path": chosen,
                "segments": segments,
                "edit_candidates": edit_candidates,
            }
        )
        print(
            f"{track.index:02d} {track.title}: {source_path.name}, "
            f"ratio={tempo_ratio:.4f}, anchors={len(text_anchors)}, "
            f"segments={len(segments)}, edit_candidates={len(edit_candidates)}",
            flush=True,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def verify_timeline(source: list[Cue], output: list[Cue]) -> None:
    if len(source) != len(output):
        raise AssertionError(f"cue count changed: {len(source)} -> {len(output)}")
    for left, right in zip(source, output):
        if (left.number, left.start_ms, left.end_ms) != (
            right.number,
            right.start_ms,
            right.end_ms,
        ):
            raise AssertionError(f"timeline changed at cue {left.number}")


def command_prepare(args: argparse.Namespace) -> int:
    cues = parse_srt(args.srt)
    tracks = parse_song_list(args.song_list, args.lyrics_dir, cues[-1].end_ms)
    report_rows: list[dict] = []
    replacements: dict[int, str] = {}

    for track in tracks:
        track_cues = [cue for cue in cues if cue_track(cue, tracks).index == track.index]
        lines = parse_lrc(Path(track.lrc_path)) if track.lrc_path else []
        matches = monotonic_text_candidates(track_cues, lines)
        for cue in track_cues:
            match = matches.get(cue.number)
            auto = bool(
                match
                and match["score"] >= args.auto_score
                and match["coverage"] >= args.auto_coverage
                and match["lrc_start_index"] == match["lrc_end_index"]
            )
            if auto:
                replacements[cue.number] = match["candidate"]
            report_rows.append(
                {
                    "cue": cue.number,
                    "start": format_srt_time(cue.start_ms),
                    "end": format_srt_time(cue.end_ms),
                    "track": track.title,
                    "jianying_text": cue.text,
                    "candidate": match["candidate"] if match else "",
                    "score": match["score"] if match else "",
                    "coverage": match["coverage"] if match else "",
                    "lrc_time_ms": match["lrc_time_ms"] if match else "",
                    "evidence": match["evidence"] if match else "",
                    "decision": "auto_replace" if auto else "review",
                }
            )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    draft = args.out_dir / "01_text_evidence_draft.srt"
    report = args.out_dir / "01_text_evidence_review.csv"
    write_srt(cues, replacements, draft)
    with report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report_rows[0]))
        writer.writeheader()
        writer.writerows(report_rows)
    verify_timeline(cues, parse_srt(draft))

    manifest = {
        "inputs": {
            "srt": str(args.srt.resolve()),
            "srt_sha256": sha256(args.srt),
            "audio": str(args.audio.resolve()),
            "audio_sha256": sha256(args.audio),
            "song_list": str(args.song_list.resolve()),
            "lyrics_dir": str(args.lyrics_dir.resolve()),
        },
        "rules": {
            "timeline_authority": "jianying_srt",
            "bpm_alignment": False,
            "auto_score": args.auto_score,
            "auto_coverage": args.auto_coverage,
            "auto_requires_single_lrc_line": True,
        },
        "cue_count": len(cues),
        "tracks": [asdict(track) for track in tracks],
        "auto_replaced": sum(row["decision"] == "auto_replace" for row in report_rows),
        "needs_review": sum(row["decision"] == "review" for row in report_rows),
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"draft": str(draft), "report": str(report), **manifest}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Build conservative text-evidence candidates.")
    prepare.add_argument("--audio", required=True, type=Path)
    prepare.add_argument("--srt", required=True, type=Path)
    prepare.add_argument("--song-list", required=True, type=Path)
    prepare.add_argument("--lyrics-dir", required=True, type=Path)
    prepare.add_argument("--out-dir", required=True, type=Path)
    prepare.add_argument("--auto-score", type=float, default=0.90)
    prepare.add_argument("--auto-coverage", type=float, default=0.78)
    prepare.set_defaults(func=command_prepare)
    audio_align = sub.add_parser("audio-align", help="Align original recordings to the edited mix.")
    audio_align.add_argument("--audio", required=True, type=Path)
    audio_align.add_argument("--srt", required=True, type=Path)
    audio_align.add_argument("--song-list", required=True, type=Path)
    audio_align.add_argument("--lyrics-dir", required=True, type=Path)
    audio_align.add_argument("--source-dir", required=True, type=Path)
    audio_align.add_argument("--bpm-changes", type=Path)
    audio_align.add_argument("--out", required=True, type=Path)
    audio_align.add_argument("--sample-rate", type=int, default=4000)
    audio_align.add_argument("--window-seconds", type=float, default=6.0)
    audio_align.add_argument("--step-seconds", type=float, default=3.0)
    audio_align.add_argument("--candidate-count", type=int, default=10)
    audio_align.set_defaults(func=command_audio_align)
    build = sub.add_parser("build", help="Project canonical lyrics into fixed SRT cues and uncovered gaps.")
    build.add_argument("--srt", required=True, type=Path)
    build.add_argument("--song-list", required=True, type=Path)
    build.add_argument("--lyrics-dir", required=True, type=Path)
    build.add_argument("--audio-alignment", required=True, type=Path)
    build.add_argument("--out-srt", required=True, type=Path)
    build.add_argument("--out-report", required=True, type=Path)
    build.add_argument("--out-mapping", required=True, type=Path)
    build.add_argument("--preserve-cues", type=int, nargs="*", default=list(range(1, 9)))
    build.add_argument("--max-assignment-gap-ms", type=int, default=1400)
    build.add_argument("--min-gap-ms", type=int, default=700)
    build.add_argument("--min-insert-duration-ms", type=int, default=500)
    build.add_argument("--default-insert-duration-ms", type=int, default=3000)
    build.add_argument("--max-insert-duration-ms", type=int, default=6000)
    build.add_argument("--split-long-cue-ms", type=int, default=7000)
    build.set_defaults(func=command_build)
    refine = sub.add_parser("refine-korean", help="Use independent Korean ASR to split canonical LRC lines by cue.")
    refine.add_argument("--srt", required=True, type=Path)
    refine.add_argument("--song-list", required=True, type=Path)
    refine.add_argument("--lyrics-dir", required=True, type=Path)
    refine.add_argument("--audio-alignment", required=True, type=Path)
    refine.add_argument("--asr-json", required=True, type=Path)
    refine.add_argument("--in-report", required=True, type=Path)
    refine.add_argument("--out-srt", required=True, type=Path)
    refine.add_argument("--out-report", required=True, type=Path)
    refine.add_argument("--min-asr-score", type=float, default=0.56)
    refine.add_argument("--min-asr-coverage", type=float, default=0.42)
    refine.set_defaults(func=command_refine_korean)
    finalize = sub.add_parser(
        "finalize",
        help="Apply audited rebuild or hybrid choices and cue overrides.",
    )
    finalize.add_argument("--srt", required=True, type=Path)
    finalize.add_argument("--song-list", required=True, type=Path)
    finalize.add_argument("--lyrics-dir", required=True, type=Path)
    finalize.add_argument("--audio-alignment", required=True, type=Path)
    finalize.add_argument("--in-report", required=True, type=Path)
    finalize.add_argument("--manual-overrides", required=True, type=Path)
    finalize.add_argument("--rebuild-track", action="append", default=[])
    finalize.add_argument("--hybrid-track", action="append", default=[])
    finalize.add_argument("--out-srt", required=True, type=Path)
    finalize.add_argument("--out-report", required=True, type=Path)
    finalize.set_defaults(func=command_finalize)
    qa = sub.add_parser("qa", help="Run final structural and regression checks.")
    qa.add_argument("--source-srt", required=True, type=Path)
    qa.add_argument("--final-srt", required=True, type=Path)
    qa.add_argument("--report", required=True, type=Path)
    qa.add_argument("--song-list", type=Path)
    qa.add_argument("--lyrics-dir", type=Path)
    qa.add_argument("--audio-alignment", type=Path)
    qa.add_argument("--manual-overrides", type=Path)
    qa.add_argument(
        "--regression-cases",
        type=Path,
        help="Project-specific confirmed cases guarded by the source SRT hash.",
    )
    qa.add_argument("--out", required=True, type=Path)
    qa.add_argument("--out-review", type=Path)
    qa.set_defaults(func=command_qa)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
