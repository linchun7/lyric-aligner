"""Local cache for expensive harmonic source features.

The cache is an execution optimization only. Formal artifacts continue to bind
source file SHA-256 and algorithm/config identity; cache files are disposable
and never become timing authority or lineage inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lyric_aligner.audio.features import FeatureBundle


CACHE_SCHEMA_VERSION = "1.0"
FEATURE_IMPLEMENTATION_ID = "harmonic-hpss-chroma-cens-mfcc-v1"


@dataclass(frozen=True)
class FeatureCacheSpec:
    audio_sha256: str
    sr: int
    hop_length: int
    n_mfcc: int = 13
    implementation_id: str = FEATURE_IMPLEMENTATION_ID

    def __post_init__(self) -> None:
        digest = self.audio_sha256.lower().strip()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("audio_sha256 must be a 64-character hexadecimal digest")
        if self.sr <= 0 or self.hop_length <= 0 or self.n_mfcc < 2:
            raise ValueError("invalid feature cache sampling parameters")
        if not self.implementation_id.strip():
            raise ValueError("feature cache implementation_id must not be empty")

    def normalized(self) -> dict[str, object]:
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "implementation_id": self.implementation_id,
            "audio_sha256": self.audio_sha256.lower().strip(),
            "sr": int(self.sr),
            "hop_length": int(self.hop_length),
            "n_mfcc": int(self.n_mfcc),
        }

    @property
    def key(self) -> str:
        encoded = json.dumps(
            self.normalized(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def cache_path(cache_dir: Path, spec: FeatureCacheSpec) -> Path:
    return Path(cache_dir) / f"source-{spec.key}.npz"


def _validate_bundle(bundle: FeatureBundle, spec: FeatureCacheSpec) -> None:
    if bundle.sr != spec.sr or bundle.hop_length != spec.hop_length:
        raise ValueError("feature bundle sampling parameters do not match cache spec")
    if bundle.duration_seconds <= 0:
        raise ValueError("feature bundle duration must be positive")
    if bundle.chroma.ndim != 2 or bundle.mfcc.ndim != 2:
        raise ValueError("feature matrices must be two-dimensional")
    if bundle.chroma.shape[1] != bundle.mfcc.shape[1] or bundle.chroma.shape[1] < 2:
        raise ValueError("feature matrices must share a non-trivial frame count")
    if not np.all(np.isfinite(bundle.chroma)) or not np.all(np.isfinite(bundle.mfcc)):
        raise ValueError("feature matrices contain non-finite values")


def load_feature_bundle(cache_dir: Path, spec: FeatureCacheSpec) -> FeatureBundle | None:
    path = cache_path(cache_dir, spec)
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as payload:
            metadata_json = str(payload["metadata"].item())
            metadata = json.loads(metadata_json)
            if metadata != spec.normalized():
                return None
            bundle = FeatureBundle(
                sr=int(payload["sr"].item()),
                hop_length=int(payload["hop_length"].item()),
                duration_seconds=float(payload["duration_seconds"].item()),
                chroma=np.asarray(payload["chroma"], dtype=np.float32),
                mfcc=np.asarray(payload["mfcc"], dtype=np.float32),
            )
        _validate_bundle(bundle, spec)
        return bundle
    except (
        OSError,
        EOFError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ):
        # Cache corruption must never block or weaken a production run. Treat it
        # as a miss and rebuild from the SHA-bound source audio.
        return None


def save_feature_bundle(cache_dir: Path, spec: FeatureCacheSpec, bundle: FeatureBundle) -> Path:
    _validate_bundle(bundle, spec)
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = cache_path(directory, spec)
    metadata_json = json.dumps(
        spec.normalized(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=".npz",
        dir=str(directory),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(
            temporary,
            metadata=np.asarray(metadata_json),
            sr=np.asarray(bundle.sr, dtype=np.int64),
            hop_length=np.asarray(bundle.hop_length, dtype=np.int64),
            duration_seconds=np.asarray(bundle.duration_seconds, dtype=np.float64),
            chroma=np.asarray(bundle.chroma, dtype=np.float32),
            mfcc=np.asarray(bundle.mfcc, dtype=np.float32),
        )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target
