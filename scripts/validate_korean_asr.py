#!/usr/bin/env python3
"""Run configured ASR validation windows for low-text-evidence tracks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_LOCAL_JOBS = Path("private/lyric-aligner.local.json")


def load_jobs(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list) or not jobs:
        raise ValueError(f"ASR jobs file must contain a non-empty jobs list: {path}")
    normalized: list[dict] = []
    for index, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            raise ValueError(f"ASR job {index} must be an object")
        missing = [key for key in ("track", "start", "end", "language") if key not in job]
        if missing:
            raise ValueError(f"ASR job {index} is missing: {', '.join(missing)}")
        start = float(job["start"])
        end = float(job["end"])
        if start < 0 or end <= start:
            raise ValueError(f"ASR job {index} has invalid window: {start}-{end}")
        normalized.append(
            {
                "track": str(job["track"]),
                "start": start,
                "end": end,
                "language": str(job["language"]),
            }
        )
    return normalized


def local_profile_applies(path: Path, audio: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    input_root = payload.get("input_root") if isinstance(payload, dict) else None
    if not input_root:
        return True
    return audio.resolve().is_relative_to(Path(input_root).resolve())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    parser.add_argument(
        "--jobs",
        type=Path,
        help="JSON file containing jobs; matching local profile is used when present.",
    )
    args = parser.parse_args()

    jobs_path = args.jobs
    if jobs_path is None and DEFAULT_LOCAL_JOBS.exists():
        try:
            if local_profile_applies(DEFAULT_LOCAL_JOBS, args.audio):
                jobs_path = DEFAULT_LOCAL_JOBS
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    if jobs_path is None:
        parser.error("--jobs is required outside a local project with private/lyric-aligner.local.json")
    try:
        jobs = load_jobs(jobs_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        parser.error("faster-whisper is required to run ASR validation: " + str(exc))

    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    output = {"model": args.model, "jobs": []}
    for job in jobs:
        segments, info = model.transcribe(
            str(args.audio),
            language=job["language"],
            task="transcribe",
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            word_timestamps=True,
            vad_filter=False,
            clip_timestamps=[job["start"], job["end"]],
            log_progress=True,
        )
        rows = []
        for segment in segments:
            rows.append(
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip(),
                    "avg_logprob": segment.avg_logprob,
                    "no_speech_prob": segment.no_speech_prob,
                    "words": [
                        {
                            "start": word.start,
                            "end": word.end,
                            "word": word.word,
                            "probability": word.probability,
                        }
                        for word in (segment.words or [])
                    ],
                }
            )
            print(
                f"{job['track']} {segment.start:.2f}-{segment.end:.2f} {segment.text.strip()}",
                flush=True,
            )
        output["jobs"].append(
            {
                **job,
                "detected_language": info.language,
                "language_probability": info.language_probability,
                "segments": rows,
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
