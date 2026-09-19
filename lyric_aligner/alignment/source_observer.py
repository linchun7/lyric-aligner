"""Cached, whole-recording ASR observations in the source audio time basis.

Canonical text is deliberately absent from inference. These observed words are
lexical/timing candidates, not forced phoneme alignment or production authority.
Only an existing local model directory is accepted; execution never downloads.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from lyric_aligner.contracts.artifacts import sha256_file

SOURCE_OBSERVER_VERSION = "source-observer-1.0"
MULTILINGUAL_SOURCE_OBSERVER_VERSION = "source-observer-1.2"
LEGACY_DECODE_POLICY = "faster-whisper-s16-v1"
FLOAT_DECODE_POLICY = "pyav-float32-mono-peak-safe-2026-09-08-v1"


def json_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceObservationConfig:
    model_path: str
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    language: str | None = None
    cpu_threads: int = 4
    decode_policy: str = LEGACY_DECODE_POLICY
    multilingual: bool = False


def _runtime_identity():
    return {name: importlib.metadata.version(name) for name in ("faster-whisper", "ctranslate2", "av")}


def _factory(config):
    from faster_whisper import WhisperModel
    return WhisperModel(config.model_path, device=config.device,
        compute_type=config.compute_type, cpu_threads=config.cpu_threads,
        local_files_only=True)


def _decode(path):
    from faster_whisper.audio import decode_audio
    return decode_audio(str(path), sampling_rate=16000)


def _load_verified_cache(cache_path: Path, *, cache_key: str, identity: dict) -> dict:
    """Load one immutable cache entry, rejecting corruption and stale lineage."""

    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("source observation cache is unreadable") from exc
    if not isinstance(cached, dict):
        raise ValueError("source observation cache must be a JSON object")
    unsigned = {key: value for key, value in cached.items() if key != "artifact_sha256"}
    if (
        cached.get("cache_key_sha256") != cache_key
        or cached.get("identity") != identity
        or cached.get("artifact_sha256") != json_sha(unsigned)
    ):
        raise ValueError("source observation cache identity/hash mismatch")
    return cached


def _publish_cache_exclusively(
    cache_path: Path, result: dict, *, cache_key: str, identity: dict
) -> dict | None:
    """Publish a complete cache file exactly once without exposing partial JSON.

    ``open('x')`` makes filename creation exclusive but exposes an empty or
    partial final file while its JSON is still being written.  A fully fsynced
    temporary file followed by ``os.link`` gives the final name one atomic,
    no-replace publication step.  The loser of a concurrent publication
    verifies and accepts the existing winner; it never overwrites it.
    """

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=f".{cache_path.name}.", suffix=".tmp", dir=cache_path.parent
    )
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, cache_path)
        except FileExistsError:
            concurrent = _load_verified_cache(cache_path, cache_key=cache_key, identity=identity)
            return concurrent
        return None
    finally:
        temp_path.unlink(missing_ok=True)


def observe_source(*, audio_path: Path, audio_sha256: str, config: SourceObservationConfig,
                   cache_dir: Path, model_factory: Callable | None = None,
                   audio_loader: Callable | None = None, runtime_identity: dict | None = None) -> dict:
    """Observe the complete decoded recording, with a content-bound cache.

    Missing optional backend/model is a recoverable unavailable observation.
    Corrupt inputs/cache and failed inference are errors, never cached as success.
    """
    audio_path, cache_dir = Path(audio_path).resolve(), Path(cache_dir).resolve()
    if sha256_file(audio_path) != audio_sha256:
        raise ValueError("source audio SHA mismatch")
    if config.beam_size < 1 or config.cpu_threads < 1:
        raise ValueError("invalid source observation configuration")
    if type(config.multilingual) is not bool:
        raise ValueError("multilingual must be a bool")
    if config.multilingual and config.language is not None:
        raise ValueError("multilingual detection requires language=None")
    if config.decode_policy not in (LEGACY_DECODE_POLICY, FLOAT_DECODE_POLICY):
        raise ValueError("unsupported source decode policy")
    if config.decode_policy == FLOAT_DECODE_POLICY and audio_loader is not None:
        raise ValueError("float decode policy does not permit a custom audio loader")
    model_path = Path(config.model_path).resolve()
    if not model_path.is_dir() or not (model_path / "model.bin").is_file():
        return {"status": "unavailable", "reason": "local_model_unavailable",
                "audio_basis": "source", "source_audio_sha256": audio_sha256, "words": []}
    try:
        runtime = runtime_identity if runtime_identity is not None else _runtime_identity()
    except importlib.metadata.PackageNotFoundError:
        return {"status": "unavailable", "reason": "optional_backend_unavailable",
                "audio_basis": "source", "source_audio_sha256": audio_sha256, "words": []}
    # Bind the actual weights, vocabulary and configuration, not just a model alias.
    model_files = {p.relative_to(model_path).as_posix(): sha256_file(p)
                   for p in sorted(model_path.rglob("*")) if p.is_file()}
    if config.multilingual:
        version = MULTILINGUAL_SOURCE_OBSERVER_VERSION
    else:
        version = SOURCE_OBSERVER_VERSION if config.decode_policy == LEGACY_DECODE_POLICY else "source-observer-1.1"
    # Preserve historical default cache identity exactly, including config keys.
    identity_config = {k: v for k, v in asdict(config).items()
                       if k not in ("model_path", "decode_policy", "multilingual")}
    if config.decode_policy != LEGACY_DECODE_POLICY:
        identity_config["decode_policy"] = config.decode_policy
    if config.multilingual:
        identity_config["multilingual"] = True
    identity = {"schema_version": version, "source_audio_sha256": audio_sha256,
                "model_files": model_files, "runtime": runtime,
                "config": identity_config,
                "sample_rate": 16000, "search": "whole_decoded_recording",
                "word_timestamps": True, "vad_filter": False,
                "condition_on_previous_text": False, "temperature": 0.0}
    cache_key = json_sha(identity)
    cache_path = cache_dir / (cache_key + ".source.json")
    if cache_path.exists():
        cached = _load_verified_cache(cache_path, cache_key=cache_key, identity=identity)
        return {**cached, "cache_hit": True}
    decode_diagnostics = None
    if audio_loader is not None or config.decode_policy == LEGACY_DECODE_POLICY:
        audio = (audio_loader or _decode)(audio_path)
    else:
        from lyric_aligner.audio.float_decode import decode_float_audio
        audio, decode_diagnostics = decode_float_audio(audio_path)
    duration_ms = round(len(audio) / 16)
    if duration_ms <= 0:
        raise ValueError("empty decoded source recording")
    model = (model_factory or _factory)(config)
    transcribe_kwargs = {
        "beam_size": config.beam_size,
        "language": config.language,
        "word_timestamps": True,
        "vad_filter": False,
        "temperature": 0.0,
        "condition_on_previous_text": False,
    }
    # Keep the legacy call contract byte-for-byte at the keyword level.  The
    # opt-in flag makes faster-whisper detect language for each decode segment;
    # its TranscriptionInfo.language remains the initial detection only.
    if config.multilingual:
        transcribe_kwargs["multilingual"] = True
    segments, info = model.transcribe(audio, **transcribe_kwargs)
    words = []
    for segment in segments:
        for word in getattr(segment, "words", None) or []:
            start, end = float(word.start), float(word.end)
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                raise ValueError("backend returned invalid source word time")
            start_ms, end_ms = round(start * 1000), round(end * 1000)
            if end_ms > duration_ms + 1:
                raise ValueError("backend word extends beyond source recording")
            probability = getattr(word, "probability", None)
            if probability is not None and not math.isfinite(float(probability)):
                probability = None
            words.append({"start_ms": start_ms, "end_ms": min(end_ms, duration_ms),
                          "text": str(word.word), "probability": probability,
                          "window_id": "whole_source"})
    result = {"schema_version": version, "status": "observed",
              "authority": "observed_transcript_only", "audio_basis": "source",
              "source_audio_sha256": audio_sha256, "cache_key_sha256": cache_key,
              "identity": identity, "search_domain": {"start_ms": 0, "end_ms": duration_ms,
                  "domain_id": "whole_source"}, "words": words,
              "detected_language": getattr(info, "language", None)}
    if config.multilingual:
        # faster-whisper does not expose a language on each Segment object in
        # this API.  Do not manufacture per-segment language records from the
        # initial TranscriptionInfo value.
        result["detected_language_scope"] = "initial_detection_only"
    if decode_diagnostics is not None:
        result["decode_diagnostics"] = decode_diagnostics
    result["artifact_sha256"] = json_sha(result)
    concurrent = _publish_cache_exclusively(
        cache_path, result, cache_key=cache_key, identity=identity
    )
    if concurrent is not None:
        return {**concurrent, "cache_hit": True}
    return {**result, "cache_hit": False}
