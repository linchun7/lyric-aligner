#!/usr/bin/env python3
"""Generate diagnostic-only same-track splice evidence from a prepared stem."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.audio.prepared_stem import PreparedStemConfig, diagnose_same_track_splice


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _globalize(result: dict, offset_seconds: float) -> dict:
    payload = json.loads(json.dumps(result))
    for mode in payload.get("modes") or []:
        mode["first_mix_center"] = float(mode["first_mix_center"]) + offset_seconds
        mode["last_mix_center"] = float(mode["last_mix_center"]) + offset_seconds
    if payload.get("handoff_center") is not None:
        payload["handoff_center"] = float(payload["handoff_center"]) + offset_seconds
    crossover = payload.get("crossover")
    if isinstance(crossover, dict) and crossover.get("mix_time_seconds") is not None:
        crossover["mix_time_seconds"] = (
            float(crossover["mix_time_seconds"]) + offset_seconds
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mix-audio", required=True, type=Path)
    parser.add_argument("--prepared-stem", required=True, type=Path)
    parser.add_argument("--mix-start", required=True, type=float)
    parser.add_argument("--mix-end", required=True, type=float)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--sample-rate", type=int, default=8000)
    parser.add_argument("--occurrence-id", default=None)
    parser.add_argument("--track-id", default=None)
    args = parser.parse_args()

    mix_path = args.mix_audio.resolve()
    stem_path = args.prepared_stem.resolve()
    out_path = args.out.resolve()
    for label, path in (("mix audio", mix_path), ("prepared stem", stem_path)):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")
    if out_path in {mix_path, stem_path}:
        parser.error("diagnostic output may not overwrite an input audio file")
    if args.sample_rate <= 0:
        parser.error("sample-rate must be positive")
    if args.mix_start < 0 or args.mix_end <= args.mix_start:
        parser.error("mix interval must satisfy 0 <= start < end")

    try:
        mix_info = sf.info(str(mix_path))
    except (RuntimeError, OSError) as exc:
        parser.error(f"cannot inspect mix audio: {exc}")
    mix_duration = float(mix_info.frames) / float(mix_info.samplerate)
    if args.mix_end > mix_duration + 1e-6:
        parser.error("mix-end exceeds physical mix duration")

    duration = args.mix_end - args.mix_start
    mix, _ = librosa.load(
        str(mix_path),
        sr=args.sample_rate,
        mono=True,
        offset=args.mix_start,
        duration=duration,
    )
    stem, _ = librosa.load(str(stem_path), sr=args.sample_rate, mono=True)
    mix = np.asarray(mix, dtype=np.float64)
    stem = np.asarray(stem, dtype=np.float64)
    actual_duration = len(mix) / args.sample_rate
    if actual_duration + 1.0 / args.sample_rate < duration:
        parser.error("decoded mix segment is shorter than requested interval")

    config = PreparedStemConfig()
    local = diagnose_same_track_splice(
        mix,
        stem,
        sample_rate=args.sample_rate,
        occurrence_start=0.0,
        occurrence_end=actual_duration,
        config=config,
    )
    result = _globalize(local, args.mix_start)
    payload = {
        "schema_version": "prepared-stem-splice-artifact-1.0",
        "diagnostic_only": True,
        "automatic_timing_change_allowed": False,
        "production_authority_granted": False,
        "mix_audio_path": str(mix_path),
        "mix_audio_sha256": file_sha256(mix_path),
        "prepared_stem_path": str(stem_path),
        "prepared_stem_sha256": file_sha256(stem_path),
        "occurrence_id": args.occurrence_id,
        "track_id": args.track_id,
        "mix_interval": {"start": args.mix_start, "end": args.mix_end},
        "sample_rate": args.sample_rate,
        "config": asdict(config),
        "result": result,
    }
    payload["diagnostic_artifact_id"] = canonical_sha256(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "splice_supported": result["splice_supported"],
                "crossover": result.get("crossover"),
                "diagnostic_artifact_id": payload["diagnostic_artifact_id"],
                "out": str(out_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
