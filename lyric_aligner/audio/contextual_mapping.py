"""Bounded contextual Source-to-Mix mapping candidates.

This module is a shadow observer.  It deliberately sits beside the existing
coarse/fine production path: it consumes an already resolved occurrence and an
effective Source-to-Mix TimeWarp, retrieves a bounded 24 second contextual
window, and returns mapping candidates.  The result never grants lyric
boundary authority and never writes a subtitle.

The important coordinate rule is that feature arrays are local to their
decoded crop while every candidate emitted by this module is in absolute audio
seconds.  The crop origin is therefore part of the evidence and of the cache
identity.  A stale or differently scoped cache entry is a miss.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping

import librosa
import numpy as np

from lyric_aligner.audio.contextual_fine import (
    POLICY_VERSION as CONTEXTUAL_FINE_POLICY_VERSION,
    retrieve_contextual_onset_window,
)
from lyric_aligner.audio.independent_fine import OnsetFeatureBundle, extract_percussive_onset_features
from lyric_aligner.timeline.projector import source_time_at_mix


CONTEXTUAL_MAPPING_SCHEMA_VERSION = "contextual-mapping-evidence-1.0"
CONTEXTUAL_MAPPING_POLICY_VERSION = "contextual-mapping-shadow-1.0"
CONTEXTUAL_MAPPING_AUTHORITY = "shadow_evidence_only_never_boundary_authority"
CONTEXTUAL_MAPPING_DOMAIN = "percussive_multiband_contextual_bounded_v1"
CONTEXTUAL_MAPPING_SAMPLE_RATE = 22050
CONTEXTUAL_MAPPING_HOP_LENGTH = 256
CONTEXTUAL_MAPPING_TARGET_SECONDS = 24.0
CONTEXTUAL_MAPPING_CONTEXT_SECONDS = 2.0
CONTEXTUAL_MAPPING_SLOPE_OFFSETS = (-0.010, -0.005, 0.0, 0.005, 0.010)


class ContextualMappingError(ValueError):
    """Raised for malformed input that cannot be represented as evidence."""


@dataclass(frozen=True)
class ContextualMappingConfig:
    """Frozen observer configuration.

    The default values are the configuration used by the contextual_fine
    natural-window validation: 22050 Hz, a 24 second target, two seconds of
    context on either side, and a nominal slope grid of +/- .005/.01.
    """

    sample_rate: int = CONTEXTUAL_MAPPING_SAMPLE_RATE
    hop_length: int = CONTEXTUAL_MAPPING_HOP_LENGTH
    target_seconds: float = CONTEXTUAL_MAPPING_TARGET_SECONDS
    context_seconds: float = CONTEXTUAL_MAPPING_CONTEXT_SECONDS
    candidate_step_seconds: float = 0.05
    source_search_radius_seconds: float = 3.0
    min_score: float = 0.48
    min_margin: float = 0.025
    slope_offsets: tuple[float, ...] = CONTEXTUAL_MAPPING_SLOPE_OFFSETS

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.hop_length <= 0:
            raise ContextualMappingError("contextual feature sampling must be positive")
        if self.target_seconds <= 0 or self.context_seconds < 0:
            raise ContextualMappingError("contextual window durations are invalid")
        if self.candidate_step_seconds <= 0 or self.source_search_radius_seconds < 0:
            raise ContextualMappingError("contextual search settings are invalid")
        if not self.slope_offsets:
            raise ContextualMappingError("contextual slope grid cannot be empty")
        if any(not math.isfinite(float(value)) for value in self.slope_offsets):
            raise ContextualMappingError("contextual slope offsets must be finite")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["slope_offsets"] = [float(value) for value in self.slope_offsets]
        return payload


@dataclass(frozen=True)
class FeatureLoadRequest:
    """Request passed to an injected feature loader.

    ``start_seconds`` and ``end_seconds`` are absolute coordinates in the
    corresponding input file.  Returned feature columns must be local to that
    crop.  A loader may return :class:`FeatureWindow`, an
    :class:`OnsetFeatureBundle`, ``(bundle, origin)`` or a mapping with the
    same fields; this is intentionally easy to mock in shadow tests.
    """

    path: Path
    role: str
    input_sha256: str
    start_seconds: float
    end_seconds: float
    sample_rate: int
    hop_length: int
    domain: str = CONTEXTUAL_MAPPING_DOMAIN


@dataclass(frozen=True)
class FeatureWindow:
    """Feature bundle plus its absolute crop coordinates."""

    bundle: OnsetFeatureBundle
    absolute_start: float
    absolute_end: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "absolute_start": float(self.absolute_start),
            "absolute_end": float(self.absolute_end),
            "duration_seconds": float(self.bundle.duration_seconds),
            "sample_rate": int(self.bundle.sr),
            "hop_length": int(self.bundle.hop_length),
            "frame_count": int(self.bundle.frame_count),
        }


FeatureLoader = Callable[..., FeatureWindow | OnsetFeatureBundle | tuple[Any, ...] | Mapping[str, Any]]


def _json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_sha256(value: str, label: str) -> str:
    digest = str(value or "").lower().strip()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ContextualMappingError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _validate_input_identity(path: Path, expected: str, label: str) -> str:
    digest = _validate_sha256(expected, label)
    if path.is_file():
        actual = _file_sha256(path)
        if actual != digest:
            raise ContextualMappingError(f"{label} does not match {path}")
    return digest


def _first_float(payload: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                value = float(payload[key])
            except (TypeError, ValueError):
                raise ContextualMappingError(f"invalid numeric field: {key}") from None
            if not math.isfinite(value):
                raise ContextualMappingError(f"non-finite numeric field: {key}")
            return value
    return None


def _first_time_seconds(payload: Mapping[str, Any], keys: tuple[str, ...], millisecond_keys: tuple[str, ...]) -> float | None:
    """Read a boundary expressed in seconds, accepting the project ms aliases."""

    value = _first_float(payload, keys)
    if value is not None:
        return value
    value = _first_float(payload, millisecond_keys)
    return None if value is None else value / 1000.0


def _nested_mapping(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Mapping[str, Any] | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _occurrence_bounds(occurrence: Mapping[str, Any]) -> tuple[str, float | None, float | None, dict[str, Any]]:
    if not isinstance(occurrence, Mapping):
        raise ContextualMappingError("occurrence boundary must be an object")
    occurrence_id = str(
        occurrence.get("occurrence_id")
        or occurrence.get("id")
        or occurrence.get("track_occurrence_id")
        or ""
    ).strip()
    if not occurrence_id:
        raise ContextualMappingError("occurrence boundary requires occurrence_id")

    start = _first_time_seconds(
        occurrence,
        (
            "mix_start",
            "start",
            "start_seconds",
            "boundary_start",
            "mix_segment_start",
            "primary_start",
        ),
        ("mix_start_ms", "start_ms", "boundary_start_ms", "mix_segment_start_ms", "primary_start_ms"),
    )
    end = _first_time_seconds(
        occurrence,
        (
            "mix_end",
            "end",
            "end_seconds",
            "boundary_end",
            "mix_segment_end",
            "primary_end",
        ),
        ("mix_end_ms", "end_ms", "boundary_end_ms", "mix_segment_end_ms", "primary_end_ms"),
    )
    interval = occurrence.get("primary_interval") or occurrence.get("mix_interval")
    if start is None and end is None and isinstance(interval, (list, tuple)) and len(interval) == 2:
        try:
            start = float(interval[0])
            end = float(interval[1])
        except (TypeError, ValueError):
            raise ContextualMappingError("invalid primary occurrence interval") from None
    interval_ms = occurrence.get("primary_interval_ms") or occurrence.get("mix_interval_ms")
    if start is None and end is None and isinstance(interval_ms, (list, tuple)) and len(interval_ms) == 2:
        try:
            start = float(interval_ms[0]) / 1000.0
            end = float(interval_ms[1]) / 1000.0
        except (TypeError, ValueError):
            raise ContextualMappingError("invalid primary occurrence interval") from None
    nested = _nested_mapping(occurrence, ("boundary", "mix_boundary", "segment"))
    if nested is not None:
        if start is None:
            start = _first_time_seconds(
                nested,
                ("mix_start", "start", "start_seconds", "primary_start"),
                ("mix_start_ms", "start_ms", "primary_start_ms"),
            )
        if end is None:
            end = _first_time_seconds(
                nested,
                ("mix_end", "end", "end_seconds", "primary_end"),
                ("mix_end_ms", "end_ms", "primary_end_ms"),
            )
    if start is not None and end is not None and end <= start:
        raise ContextualMappingError("occurrence mix boundary is not monotonic")
    return occurrence_id, start, end, {
        "occurrence_id": occurrence_id,
        "mix_start": start,
        "mix_end": end,
        "segment_id": occurrence.get("segment_id") or occurrence.get("lane_id"),
    }


def _mapping_labels(mapping: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("kind", "mode", "mapping_type", "strategy", "selection", "type"):
        value = mapping.get(key)
        if value is not None:
            labels.append(str(value).upper())
    return labels


def _contains_reference_retime(mapping: Mapping[str, Any]) -> bool:
    return any("REFERENCE" in label and "RETIME" in label for label in _mapping_labels(mapping))


def _unwrap_effective_mapping(
    effective_timewarp: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], str | None]:
    if not isinstance(effective_timewarp, Mapping):
        raise ContextualMappingError("effective_timewarp must be an object")
    outer = effective_timewarp
    inner = effective_timewarp
    nested = effective_timewarp.get("mapping")
    if isinstance(nested, Mapping):
        inner = nested
    labels = _mapping_labels(outer) + _mapping_labels(inner)
    if _contains_reference_retime(outer) or _contains_reference_retime(inner):
        return inner, outer, "unsupported_reference_retime_mapping"
    if bool(outer.get("blocked")) or bool(inner.get("blocked")):
        return inner, outer, "effective_timewarp_blocked"
    selection = str(outer.get("selection") or "").upper()
    if selection and any(token in selection for token in ("DISCONTINUITY_REVIEW", "UNRESOLVED", "NOT_JUSTIFIED")):
        return inner, outer, "effective_timewarp_not_proven"
    if not any(label in {"AFFINE", "PIECEWISE", "PIECEWISE_RATE", "CUT_AWARE"} for label in labels):
        return inner, outer, "unsupported_effective_timewarp_kind"
    return inner, outer, None


def _continuous_mapping_kind(mapping: Mapping[str, Any]) -> str | None:
    labels = set(_mapping_labels(mapping))
    if "CUT_AWARE" in labels or str(mapping.get("kind") or "").upper() == "CUT_AWARE":
        return "CUT_AWARE"
    if "AFFINE" in labels:
        return "AFFINE"
    if "PIECEWISE_RATE" in labels or "PIECEWISE" in labels:
        return "PIECEWISE_RATE"
    return None


def _validate_continuous_mapping(mapping: Mapping[str, Any]) -> None:
    try:
        base = float(mapping["base_slope"])
        float(mapping["intercept"])
        breakpoints = [float(value) for value in mapping.get("breakpoints", [])]
        deltas = [float(value) for value in mapping.get("slope_deltas", [])]
    except (KeyError, TypeError, ValueError) as exc:
        raise ContextualMappingError("continuous effective_timewarp is malformed") from exc
    if not math.isfinite(base) or base <= 0:
        raise ContextualMappingError("continuous effective_timewarp slope must be positive")
    if len(breakpoints) != len(deltas):
        raise ContextualMappingError("effective_timewarp breakpoints/slope_deltas mismatch")
    if any(not math.isfinite(value) for value in breakpoints + deltas):
        raise ContextualMappingError("effective_timewarp contains non-finite values")
    if any(right <= left for left, right in zip(breakpoints, breakpoints[1:])):
        raise ContextualMappingError("effective_timewarp breakpoints are not increasing")
    slope = base
    for delta in deltas:
        slope += delta
        if slope <= 0 or not math.isfinite(slope):
            raise ContextualMappingError("effective_timewarp local slope must be positive")


def _cut_segments(mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = mapping.get("segments")
    if not isinstance(rows, list) or not rows:
        raise ContextualMappingError("CUT_AWARE effective_timewarp has no segments")
    result: list[dict[str, Any]] = []
    previous_mix_end: float | None = None
    previous_source_end: float | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ContextualMappingError("CUT_AWARE segment must be an object")
        try:
            segment_index = int(row["index"])
            mix_start = float(row["mix_start"])
            mix_end = float(row["mix_end"])
            source_start = float(row["source_start"])
            source_end = float(row["source_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextualMappingError("CUT_AWARE segment coordinates are malformed") from exc
        if segment_index != index or mix_end <= mix_start or source_end <= source_start:
            raise ContextualMappingError("CUT_AWARE segments are not monotonic")
        if previous_mix_end is not None and abs(mix_start - previous_mix_end) > 1e-3:
            raise ContextualMappingError("CUT_AWARE mix segments do not meet")
        if previous_source_end is not None and source_start <= previous_source_end:
            raise ContextualMappingError("CUT_AWARE source segments do not preserve gaps")
        child = row.get("mapping")
        if not isinstance(child, Mapping):
            raise ContextualMappingError("CUT_AWARE segment has no continuous mapping")
        _validate_continuous_mapping(child)
        result.append(
            {
                "index": index,
                "mix_start": mix_start,
                "mix_end": mix_end,
                "source_start": source_start,
                "source_end": source_end,
                "mapping": child,
            }
        )
        previous_mix_end = mix_end
        previous_source_end = source_end
    return result


def _mapping_domain(
    mapping: Mapping[str, Any],
    target_start: float,
    target_end: float,
) -> tuple[str, Mapping[str, Any], float | None, float | None, int | None, str | None]:
    """Select one continuous mapping segment without bridging a cut/breakpoint."""

    kind = _continuous_mapping_kind(mapping)
    if kind is None:
        return "", mapping, None, None, None, "unsupported_effective_timewarp_kind"
    if kind == "CUT_AWARE":
        segments = _cut_segments(mapping)
        selected = [
            row
            for row in segments
            if target_start >= row["mix_start"] - 1e-6
            and target_end <= row["mix_end"] + 1e-6
        ]
        if len(selected) != 1:
            return kind, mapping, None, None, None, "target_window_crosses_cut_or_segment"
        row = selected[0]
        child = row["mapping"]
        return (
            kind,
            child,
            float(row["mix_start"]),
            float(row["mix_end"]),
            int(row["index"]),
            None,
        )

    _validate_continuous_mapping(mapping)
    breakpoints = [float(value) for value in mapping.get("breakpoints", [])]
    crossing = [value for value in breakpoints if target_start < value < target_end]
    if crossing:
        return kind, mapping, None, None, None, "target_window_crosses_rate_breakpoint"
    # The target may fit while a context flank reaches a breakpoint.  The
    # caller clips context at the breakpoint; the 24-second target never
    # bridges a rate regime.
    segment_start = None
    segment_end = None
    for breakpoint in breakpoints:
        if breakpoint <= target_start:
            segment_start = breakpoint
        elif breakpoint >= target_end:
            segment_end = breakpoint
            break
    return kind, mapping, segment_start, segment_end, None, None


def _source_time(mapping: Mapping[str, Any], mix_seconds: float) -> float:
    try:
        value = source_time_at_mix(dict(mapping), float(mix_seconds))
    except Exception as exc:  # projector has several ValueError subclasses
        raise ContextualMappingError("effective_timewarp cannot project mix time") from exc
    if not math.isfinite(value):
        raise ContextualMappingError("effective_timewarp produced a non-finite source time")
    return float(value)


def _local_slope(mapping: Mapping[str, Any], mix_seconds: float) -> float:
    base = float(mapping["base_slope"])
    slope = base
    for breakpoint, delta in zip(mapping.get("breakpoints", []), mapping.get("slope_deltas", [])):
        if mix_seconds >= float(breakpoint):
            slope += float(delta)
    if slope <= 0 or not math.isfinite(slope):
        raise ContextualMappingError("effective_timewarp local slope must be positive")
    return float(slope)


def _slope_grid(nominal: float, offsets: tuple[float, ...]) -> tuple[float, ...]:
    values = {round(float(nominal) + float(offset), 6) for offset in offsets}
    values.add(round(float(nominal), 6))
    values = {value for value in values if value > 0 and math.isfinite(value)}
    if not values:
        raise ContextualMappingError("effective_timewarp produced no positive slope candidates")
    return tuple(sorted(values))


def _invoke_feature_loader(loader: FeatureLoader, request: FeatureLoadRequest) -> Any:
    try:
        signature = inspect.signature(loader)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        parameters = list(signature.parameters.values())
        positional = [
            parameter
            for parameter in parameters
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) == 1 and not any(
            parameter.kind == parameter.VAR_KEYWORD for parameter in parameters
        ):
            return loader(request)
    return loader(
        path=request.path,
        role=request.role,
        input_sha256=request.input_sha256,
        start_seconds=request.start_seconds,
        end_seconds=request.end_seconds,
        sample_rate=request.sample_rate,
        hop_length=request.hop_length,
        domain=request.domain,
    )


def _normalize_feature_window(value: Any, request: FeatureLoadRequest) -> FeatureWindow:
    absolute_start = request.start_seconds
    absolute_end: float | None = None
    bundle: Any = value
    if isinstance(value, FeatureWindow):
        return value
    if isinstance(value, Mapping):
        bundle = value.get("bundle") or value.get("features") or value.get("feature_bundle")
        absolute_start = float(value.get("absolute_start", value.get("origin_seconds", absolute_start)))
        if value.get("absolute_end") is not None:
            absolute_end = float(value["absolute_end"])
    elif isinstance(value, tuple) and len(value) in (2, 3):
        bundle = value[0]
        absolute_start = float(value[1])
        if len(value) == 3:
            absolute_end = float(value[2])
    if not isinstance(bundle, OnsetFeatureBundle):
        raise ContextualMappingError("feature loader must return OnsetFeatureBundle or FeatureWindow")
    if absolute_end is None:
        absolute_end = absolute_start + float(bundle.duration_seconds)
    if not math.isfinite(absolute_start) or not math.isfinite(absolute_end) or absolute_end <= absolute_start:
        raise ContextualMappingError("feature loader returned invalid crop coordinates")
    if abs(absolute_start - request.start_seconds) > 1e-3:
        raise ContextualMappingError("feature loader crop origin does not match request")
    if absolute_end < request.end_seconds - max(1.0 / request.sample_rate, 1e-6):
        # The caller can decide whether this is a terminal crop; an observer
        # window itself must still be fully covered by the returned features.
        raise ContextualMappingError("feature loader crop ended before requested window")
    return FeatureWindow(bundle=bundle, absolute_start=absolute_start, absolute_end=absolute_end)


def _default_feature_loader(request: FeatureLoadRequest) -> FeatureWindow:
    if not request.path.is_file():
        raise ContextualMappingError(f"{request.role} audio does not exist: {request.path}")
    duration = max(0.0, request.end_seconds - request.start_seconds)
    audio, _ = librosa.load(
        str(request.path),
        sr=request.sample_rate,
        mono=True,
        offset=request.start_seconds,
        duration=duration,
    )
    actual_end = request.start_seconds + len(audio) / request.sample_rate
    tolerance = max(1.0 / request.sample_rate, 0.005)
    if actual_end < request.end_seconds - tolerance:
        raise ContextualMappingError(f"{request.role} bounded decode ended before requested window")
    bundle = extract_percussive_onset_features(
        audio,
        sr=request.sample_rate,
        hop_length=request.hop_length,
    )
    return FeatureWindow(bundle=bundle, absolute_start=request.start_seconds, absolute_end=actual_end)


def _cache_key(
    *,
    role: str,
    input_sha256: str,
    request_start: float,
    request_end: float,
    config: ContextualMappingConfig,
) -> tuple[str, dict[str, Any]]:
    metadata = {
        "schema_version": CONTEXTUAL_MAPPING_SCHEMA_VERSION,
        "policy_version": CONTEXTUAL_MAPPING_POLICY_VERSION,
        "domain": CONTEXTUAL_MAPPING_DOMAIN,
        "role": role,
        "input_sha256": input_sha256,
        "start_seconds": round(float(request_start), 9),
        "end_seconds": round(float(request_end), 9),
        "config": config.to_dict(),
    }
    return _json_sha256(metadata), metadata


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"contextual-{key}.npz"


def _load_cached_feature_window(path: Path, expected_metadata: Mapping[str, Any]) -> FeatureWindow | None:
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as payload:
            metadata = json.loads(str(payload["metadata"].item()))
            if metadata != dict(expected_metadata):
                return None
            bundle = OnsetFeatureBundle(
                sr=int(payload["sr"].item()),
                hop_length=int(payload["hop_length"].item()),
                duration_seconds=float(payload["duration_seconds"].item()),
                onset=np.asarray(payload["onset"], dtype=np.float32),
                multiband_flux=np.asarray(payload["multiband_flux"], dtype=np.float32),
            )
            window = FeatureWindow(
                bundle=bundle,
                absolute_start=float(payload["absolute_start"].item()),
                absolute_end=float(payload["absolute_end"].item()),
            )
        if bundle.sr != int(expected_metadata["config"]["sample_rate"]):
            return None
        if bundle.hop_length != int(expected_metadata["config"]["hop_length"]):
            return None
        if bundle.onset.ndim != 2 or bundle.multiband_flux.ndim != 2:
            return None
        if not np.isfinite(bundle.onset).all() or not np.isfinite(bundle.multiband_flux).all():
            return None
        return window
    except (OSError, EOFError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _save_cached_feature_window(path: Path, metadata: Mapping[str, Any], window: FeatureWindow) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".npz", dir=str(path.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(
            temporary,
            metadata=np.asarray(json.dumps(dict(metadata), ensure_ascii=True, sort_keys=True, separators=(",", ":"))),
            sr=np.asarray(window.bundle.sr, dtype=np.int64),
            hop_length=np.asarray(window.bundle.hop_length, dtype=np.int64),
            duration_seconds=np.asarray(window.bundle.duration_seconds, dtype=np.float64),
            absolute_start=np.asarray(window.absolute_start, dtype=np.float64),
            absolute_end=np.asarray(window.absolute_end, dtype=np.float64),
            onset=np.asarray(window.bundle.onset, dtype=np.float32),
            multiband_flux=np.asarray(window.bundle.multiband_flux, dtype=np.float32),
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_feature_window(
    *,
    path: Path,
    role: str,
    input_sha256: str,
    start_seconds: float,
    end_seconds: float,
    config: ContextualMappingConfig,
    feature_loader: FeatureLoader | None,
    cache_dir: Path | None,
) -> tuple[FeatureWindow, dict[str, Any]]:
    if end_seconds <= start_seconds:
        raise ContextualMappingError(f"{role} feature crop is empty")
    request = FeatureLoadRequest(
        path=path,
        role=role,
        input_sha256=input_sha256,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        sample_rate=config.sample_rate,
        hop_length=config.hop_length,
    )
    key, metadata = _cache_key(
        role=role,
        input_sha256=input_sha256,
        request_start=start_seconds,
        request_end=end_seconds,
        config=config,
    )
    cached = None
    cache_hit = False
    cache_path: Path | None = None
    if cache_dir is not None and feature_loader is None:
        cache_path = _cache_path(cache_dir, key)
        cached = _load_cached_feature_window(cache_path, metadata)
        cache_hit = cached is not None
    if cached is None:
        loaded = _default_feature_loader(request) if feature_loader is None else _invoke_feature_loader(feature_loader, request)
        cached = _normalize_feature_window(loaded, request)
        if cache_path is not None:
            _save_cached_feature_window(cache_path, metadata, cached)
    bundle = cached.bundle
    if bundle.sr != config.sample_rate or bundle.hop_length != config.hop_length:
        raise ContextualMappingError(f"{role} feature loader returned incompatible sampling")
    if bundle.frame_count < 4 or bundle.duration_seconds <= 0:
        raise ContextualMappingError(f"{role} feature loader returned too few frames")
    if bundle.onset.ndim != 2 or bundle.multiband_flux.ndim != 2:
        raise ContextualMappingError(f"{role} feature matrices must be two-dimensional")
    if not np.isfinite(bundle.onset).all() or not np.isfinite(bundle.multiband_flux).all():
        raise ContextualMappingError(f"{role} feature matrices contain non-finite values")
    return cached, {
        "cache_key": key,
        "cache_path": None if cache_path is None else str(cache_path),
        "request_start": start_seconds,
        "request_end": end_seconds,
        "actual_start": cached.absolute_start,
        "actual_end": cached.absolute_end,
        "cache_hit": cache_hit,
    }


def _base_payload(
    *,
    occurrence_id: str,
    mix_center: float,
    source_sha256: str,
    mix_sha256: str,
    config: ContextualMappingConfig,
) -> dict[str, Any]:
    return {
        "schema_version": CONTEXTUAL_MAPPING_SCHEMA_VERSION,
        "policy_version": CONTEXTUAL_MAPPING_POLICY_VERSION,
        "observer_policy_version": CONTEXTUAL_FINE_POLICY_VERSION,
        "authority": CONTEXTUAL_MAPPING_AUTHORITY,
        "automatic_mutation_allowed": False,
        "boundary_authority": False,
        "evidence_role": "mapping_candidate_only",
        "occurrence_id": occurrence_id,
        "mix_center": float(mix_center),
        "source_audio_sha256": source_sha256,
        "mix_audio_sha256": mix_sha256,
        "config": config.to_dict(),
    }


def _inapplicable(
    base: dict[str, Any],
    reason: str,
    *,
    mapping_provenance: dict[str, Any] | None = None,
    target_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(base)
    payload.update(
        {
            "status": "inapplicable",
            "reason": reason,
            "ambiguous": True,
            "margin": 0.0,
            "score": None,
            "top1": None,
            "top2": None,
            "candidates": [],
            "mapping_provenance": mapping_provenance or {},
            "target_window": target_window,
        }
    )
    return payload


def generate_contextual_mapping_candidates(
    *,
    source_audio: str | Path,
    mix_audio: str | Path,
    source_audio_sha256: str,
    mix_audio_sha256: str,
    occurrence: Mapping[str, Any],
    effective_timewarp: Mapping[str, Any],
    mix_center: float,
    feature_loader: FeatureLoader | None = None,
    cache_dir: str | Path | None = None,
    config: ContextualMappingConfig | None = None,
) -> dict[str, Any]:
    """Generate one bounded contextual mapping evidence packet.

    ``effective_timewarp`` must be an already selected AFFINE,
    PIECEWISE_RATE, or CUT_AWARE mapping.  A blocked mapping, a reference-retime
    mapping, and any window that would cross a cut/rate segment are returned as
    ``status=inapplicable`` with a machine-readable reason.  Malformed input or
    hash mismatches raise :class:`ContextualMappingError` so shadow callers do
    not accidentally treat bad lineage as low confidence.
    """

    cfg = config or ContextualMappingConfig()
    source_path = Path(source_audio)
    mix_path = Path(mix_audio)
    source_sha = _validate_input_identity(source_path, source_audio_sha256, "source_audio_sha256")
    mix_sha = _validate_input_identity(mix_path, mix_audio_sha256, "mix_audio_sha256")
    occurrence_id, occurrence_start, occurrence_end, occurrence_record = _occurrence_bounds(occurrence)
    try:
        center = float(mix_center)
    except (TypeError, ValueError) as exc:
        raise ContextualMappingError("mix_center must be numeric") from exc
    if not math.isfinite(center) or center < 0:
        raise ContextualMappingError("mix_center must be a non-negative finite time")
    base = _base_payload(
        occurrence_id=occurrence_id,
        mix_center=center,
        source_sha256=source_sha,
        mix_sha256=mix_sha,
        config=cfg,
    )
    target_start = center - cfg.target_seconds / 2.0
    target_end = center + cfg.target_seconds / 2.0
    target_record = {
        "start_seconds": target_start,
        "end_seconds": target_end,
        "duration_seconds": cfg.target_seconds,
        "center_seconds": center,
    }
    if target_start < 0:
        return _inapplicable(base, "target_window_reaches_audio_start", target_window=target_record)
    if occurrence_start is not None and target_start < occurrence_start - 1e-6:
        return _inapplicable(base, "target_window_outside_occurrence_start", target_window=target_record)
    if occurrence_end is not None and target_end > occurrence_end + 1e-6:
        return _inapplicable(base, "target_window_outside_occurrence_end", target_window=target_record)
    if occurrence_start is not None and occurrence_end is not None and occurrence_end - occurrence_start < cfg.target_seconds:
        return _inapplicable(base, "occurrence_segment_shorter_than_target_window", target_window=target_record)

    inner_mapping, outer_mapping, mapping_error = _unwrap_effective_mapping(effective_timewarp)
    mapping_kind = _continuous_mapping_kind(inner_mapping)
    provenance: dict[str, Any] = {
        "mapping_kind": mapping_kind,
        "mapping_sha256": _json_sha256(dict(effective_timewarp)),
        "selection": outer_mapping.get("selection"),
        "blocked": bool(outer_mapping.get("blocked") or inner_mapping.get("blocked")),
        "effective_timewarp": dict(effective_timewarp),
        "source_of_nominal": "effective_timewarp_at_mix_center",
        "local_anchor_used_as_sole_prior": False,
    }
    if mapping_error is not None:
        return _inapplicable(base, mapping_error, mapping_provenance=provenance, target_window=target_record)
    domain_kind, continuous, domain_start, domain_end, segment_index, domain_error = _mapping_domain(
        inner_mapping, target_start, target_end
    )
    provenance.update(
        {
            "mapping_kind": domain_kind,
            "continuous_mapping_sha256": _json_sha256(dict(continuous)),
            "segment_index": segment_index,
            "segment_mix_start": domain_start,
            "segment_mix_end": domain_end,
        }
    )
    if domain_error is not None:
        return _inapplicable(base, domain_error, mapping_provenance=provenance, target_window=target_record)
    if domain_start is not None and target_start < domain_start - 1e-6:
        return _inapplicable(base, "target_window_outside_mapping_segment", mapping_provenance=provenance, target_window=target_record)
    if domain_end is not None and target_end > domain_end + 1e-6:
        return _inapplicable(base, "target_window_outside_mapping_segment", mapping_provenance=provenance, target_window=target_record)

    nominal_source_start = _source_time(continuous, target_start)
    nominal_source_end = _source_time(continuous, target_end)
    nominal_source_center = _source_time(continuous, center)
    nominal_slope = _local_slope(continuous, center)
    if nominal_source_end <= nominal_source_start:
        return _inapplicable(base, "effective_timewarp_source_interval_not_monotonic", mapping_provenance=provenance, target_window=target_record)
    provenance.update(
        {
            "nominal_source_start": nominal_source_start,
            "nominal_source_end": nominal_source_end,
            "nominal_source_center": nominal_source_center,
            "nominal_slope": nominal_slope,
            "nominal_source_span_seconds": nominal_source_end - nominal_source_start,
        }
    )
    source_segment_start = None
    source_segment_end = None
    if domain_kind == "CUT_AWARE":
        # The selected child mapping's source domain is carried by the parent
        # CUT_AWARE segment.  Re-read it so the source search cannot cross the
        # explicit source gap even when the child affine map itself is valid.
        for row in _cut_segments(inner_mapping):
            if row["index"] == segment_index:
                source_segment_start = float(row["source_start"])
                source_segment_end = float(row["source_end"])
                break

    source_search_start = nominal_source_start - cfg.source_search_radius_seconds
    source_search_end = nominal_source_end + cfg.source_search_radius_seconds
    if source_segment_start is not None:
        source_search_start = max(source_search_start, source_segment_start)
        source_search_end = min(source_search_end, source_segment_end)
    if source_search_end <= source_search_start:
        return _inapplicable(base, "source_search_domain_empty", mapping_provenance=provenance, target_window=target_record)

    max_slope = max(_slope_grid(nominal_slope, cfg.slope_offsets))
    mix_decode_start = max(0.0, target_start - cfg.context_seconds)
    mix_decode_end: float | None = target_end + cfg.context_seconds
    if domain_start is not None:
        mix_decode_start = max(mix_decode_start, domain_start)
    if domain_end is not None:
        mix_decode_end = min(mix_decode_end, domain_end)
    # A resolved occurrence is itself a structural scope.  Context may be
    # clipped at its edge, but it must never borrow audio from the neighbouring
    # occurrence merely to make a nominal 24-second window look complete.
    if occurrence_start is not None:
        mix_decode_start = max(mix_decode_start, occurrence_start)
    if occurrence_end is not None:
        mix_decode_end = min(mix_decode_end, occurrence_end)
    source_decode_start = max(0.0, source_search_start - cfg.context_seconds * max_slope)
    source_decode_end = source_search_end + cfg.context_seconds * max_slope
    if source_segment_start is not None:
        source_decode_start = max(source_decode_start, source_segment_start)
        source_decode_end = min(source_decode_end, source_segment_end)
    if mix_decode_end <= mix_decode_start or source_decode_end <= source_decode_start:
        return _inapplicable(base, "bounded_feature_scope_empty", mapping_provenance=provenance, target_window=target_record)

    # If physical durations are cheap to inspect, use them to reject a target
    # that cannot be decoded rather than silently letting a terminal crop turn
    # into a shorter query.  Injected loaders intentionally bypass this check.
    if feature_loader is None and mix_path.is_file() and source_path.is_file():
        try:
            mix_duration = float(librosa.get_duration(path=str(mix_path)))
            source_duration = float(librosa.get_duration(path=str(source_path)))
        except Exception as exc:
            raise ContextualMappingError("cannot inspect audio duration") from exc
        if mix_duration < target_end - max(1.0 / cfg.sample_rate, 0.005):
            return _inapplicable(base, "mix_audio_shorter_than_target_window", mapping_provenance=provenance, target_window=target_record)
        if nominal_source_start < -1e-6 or nominal_source_end > source_duration + 0.005:
            return _inapplicable(base, "nominal_source_window_outside_audio", mapping_provenance=provenance, target_window=target_record)
        mix_decode_end = min(float(mix_decode_end), mix_duration)
        source_decode_start = max(0.0, float(source_decode_start))
        source_decode_end = min(float(source_decode_end), source_duration)
        if mix_decode_end < target_end - max(1.0 / cfg.sample_rate, 0.005):
            return _inapplicable(base, "mix_audio_cannot_cover_target_window", mapping_provenance=provenance, target_window=target_record)
        if source_decode_end <= source_decode_start:
            return _inapplicable(base, "source_audio_cannot_cover_search_window", mapping_provenance=provenance, target_window=target_record)

    mix_context_clipped = bool(
        mix_decode_start > target_start - cfg.context_seconds + 1e-6
        or float(mix_decode_end) < target_end + cfg.context_seconds - 1e-6
    )
    source_context_clipped = bool(
        source_decode_start > source_search_start - cfg.context_seconds * max_slope + 1e-6
        or source_decode_end < source_search_end + cfg.context_seconds * max_slope - 1e-6
    )
    target_record.update(
        {
            "decode_start_seconds": mix_decode_start,
            "decode_end_seconds": float(mix_decode_end),
            "context_seconds": cfg.context_seconds,
            "context_clipped": mix_context_clipped,
        }
    )
    provenance.update(
        {
            "source_search_start": source_search_start,
            "source_search_end": source_search_end,
            "source_decode_start": source_decode_start,
            "source_decode_end": source_decode_end,
            "source_context_clipped": source_context_clipped,
            "slope_grid": list(_slope_grid(nominal_slope, cfg.slope_offsets)),
            "search_grid_policy": "effective_nominal_plus_minus_005_010",
        }
    )

    cache_root = None if cache_dir is None else Path(cache_dir)
    try:
        mix_features, mix_scope = _load_feature_window(
            path=mix_path,
            role="mix",
            input_sha256=mix_sha,
            start_seconds=mix_decode_start,
            end_seconds=float(mix_decode_end),
            config=cfg,
            feature_loader=feature_loader,
            cache_dir=cache_root,
        )
        source_features, source_scope = _load_feature_window(
            path=source_path,
            role="source",
            input_sha256=source_sha,
            start_seconds=source_decode_start,
            end_seconds=source_decode_end,
            config=cfg,
            feature_loader=feature_loader,
            cache_dir=cache_root,
        )
    except ContextualMappingError as exc:
        return _inapplicable(
            base,
            f"feature_unavailable:{exc}",
            mapping_provenance=provenance,
            target_window=target_record,
        )

    local_mix_start = target_start - mix_features.absolute_start
    local_mix_end = target_end - mix_features.absolute_start
    local_source_search_start = source_search_start - source_features.absolute_start
    local_source_search_end = source_search_end - source_features.absolute_start
    if local_mix_start < -1e-6 or local_mix_end > mix_features.bundle.duration_seconds + 1e-6:
        return _inapplicable(base, "mix_feature_crop_origin_does_not_cover_target", mapping_provenance=provenance, target_window=target_record)
    if local_source_search_start < -1e-6 or local_source_search_end > source_features.bundle.duration_seconds + 1e-6:
        return _inapplicable(base, "source_feature_crop_origin_does_not_cover_search", mapping_provenance=provenance, target_window=target_record)
    try:
        observed = retrieve_contextual_onset_window(
            mix_features.bundle,
            source_features.bundle,
            mix_start=local_mix_start,
            mix_end=local_mix_end,
            slopes=_slope_grid(nominal_slope, cfg.slope_offsets),
            source_search_start=local_source_search_start,
            source_search_end=local_source_search_end,
            candidate_step_seconds=cfg.candidate_step_seconds,
        )
    except (ValueError, FloatingPointError) as exc:
        return _inapplicable(
            base,
            f"observer_unavailable:{exc}",
            mapping_provenance=provenance,
            target_window=target_record,
        )

    available_mix_range = {
        "start_seconds": mix_decode_start,
        "end_seconds": float(mix_decode_end),
    }
    available_source_range = {
        "start_seconds": source_decode_start,
        "end_seconds": source_decode_end,
    }

    def absolute_candidate(candidate: Mapping[str, Any], *, rank: int) -> dict[str, Any]:
        row = dict(candidate)
        for key in ("source_start", "source_end", "source_center"):
            if key in row:
                row[key] = float(row[key]) + source_features.absolute_start
        row["mix_start"] = target_start
        row["mix_end"] = target_end
        row["mix_center"] = center
        row["nominal_source_center"] = nominal_source_center
        row["nominal_slope"] = nominal_slope
        row["nominal_delta_ms"] = (float(row.get("source_center", nominal_source_center)) - nominal_source_center) * 1000.0
        row["candidate_rank"] = int(rank)
        row["available_mix_range"] = dict(available_mix_range)
        row["available_source_range"] = dict(available_source_range)
        # contextual_fine reports ambiguity for the joint local decision, not
        # a calibrated per-boundary probability.  Preserve that conservative
        # meaning on the selected candidate and keep alternatives explicitly
        # unresolved instead of inventing independent confidence.
        row["ambiguous"] = bool(observed.get("ambiguous", True)) or rank != 1
        row["mapping_provenance_sha256"] = provenance["mapping_sha256"]
        return row

    candidates = [absolute_candidate(row, rank=index) for index, row in enumerate(observed.get("candidates", []), 1)]
    top1 = None if observed.get("top1") is None else absolute_candidate(observed["top1"], rank=1)
    top2 = None if observed.get("top2") is None else absolute_candidate(observed["top2"], rank=2)
    evidence = dict(base)
    evidence.update(
        {
            "status": "available",
            "reason": str(observed.get("reason") or "joint_local_context_score"),
            "ambiguous": bool(observed.get("ambiguous", True)),
            "margin": float(observed.get("margin", 0.0)),
            "score": None if top1 is None else float(top1.get("fused_score", 0.0)),
            "top1": top1,
            "top2": top2,
            "candidates": candidates,
            "mapping_provenance": provenance,
            "search_range": {
                "mix": dict(available_mix_range),
                "source": {
                    "candidate_start_seconds": source_search_start,
                    "candidate_end_seconds": source_search_end,
                    "decode_start_seconds": source_decode_start,
                    "decode_end_seconds": source_decode_end,
                },
            },
            "target_window": target_record,
            "feature_scope": {
                "domain": CONTEXTUAL_MAPPING_DOMAIN,
                "coordinates": "absolute_input_seconds_v1",
                "sample_rate": cfg.sample_rate,
                "hop_length": cfg.hop_length,
                "mix": mix_scope,
                "source": source_scope,
                "feature_loader": "injected" if feature_loader is not None else "librosa_bounded_decode",
                "full_mix_features_not_computed": True,
            },
            "observer": {
                "contextual_fine": observed,
                "local_mix_start": local_mix_start,
                "local_mix_end": local_mix_end,
                "local_source_search_start": local_source_search_start,
                "local_source_search_end": local_source_search_end,
            },
        }
    )
    return evidence


# These aliases make the packet easy to consume from a shadow CLI while
# retaining one implementation and one schema identity.
build_contextual_mapping_evidence = generate_contextual_mapping_candidates
generate_contextual_mapping_evidence = generate_contextual_mapping_candidates


__all__ = [
    "CONTEXTUAL_MAPPING_AUTHORITY",
    "CONTEXTUAL_MAPPING_CONTEXT_SECONDS",
    "CONTEXTUAL_MAPPING_DOMAIN",
    "CONTEXTUAL_MAPPING_HOP_LENGTH",
    "CONTEXTUAL_MAPPING_POLICY_VERSION",
    "CONTEXTUAL_MAPPING_SAMPLE_RATE",
    "CONTEXTUAL_MAPPING_SCHEMA_VERSION",
    "CONTEXTUAL_MAPPING_SLOPE_OFFSETS",
    "CONTEXTUAL_MAPPING_TARGET_SECONDS",
    "ContextualMappingConfig",
    "ContextualMappingError",
    "FeatureLoadRequest",
    "FeatureLoader",
    "FeatureWindow",
    "build_contextual_mapping_evidence",
    "generate_contextual_mapping_candidates",
    "generate_contextual_mapping_evidence",
]
