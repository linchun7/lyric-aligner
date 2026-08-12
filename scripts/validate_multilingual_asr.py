#!/usr/bin/env python3
"""Generate fingerprinted multilingual ASR evidence for configured windows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from language_profiles import language_code
from task_contract import assert_manifest_paths, load_task_manifest, sha256, verify_manifest_inputs


ASR_JOB_SCHEMA_VERSION = "1.0"
ALGORITHM_VERSION = "3.8"


def load_jobs(
    path: Path,
    *,
    default_language: str | None = None,
    allow_legacy: bool = False,
) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        schema_version = payload.get("schema_version")
        if schema_version != ASR_JOB_SCHEMA_VERSION and not allow_legacy:
            raise ValueError(
                f"ASR jobs schema_version must be {ASR_JOB_SCHEMA_VERSION}"
            )
        jobs = payload.get("jobs")
    else:
        jobs = payload if allow_legacy else None
    if not isinstance(jobs, list) or not jobs:
        raise ValueError(f"ASR jobs file must contain a non-empty jobs list: {path}")

    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for index, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            raise ValueError(f"ASR job {index} must be an object")
        missing = [key for key in ("track", "start", "end") if key not in job]
        if missing:
            raise ValueError(f"ASR job {index} is missing: {', '.join(missing)}")
        job_id = str(job.get("id") or f"job-{index}")
        if job_id in seen_ids:
            raise ValueError(f"duplicate ASR job id: {job_id}")
        seen_ids.add(job_id)
        start = float(job["start"])
        end = float(job["end"])
        if start < 0 or end <= start:
            raise ValueError(f"ASR job {index} has invalid window: {start}-{end}")
        language = language_code(str(job.get("language") or default_language or ""))
        mode = str(job.get("language_mode") or ("detect" if language == "mixed" else "fixed"))
        if mode not in {"fixed", "detect"}:
            raise ValueError(f"ASR job {index} language_mode must be fixed or detect")
        normalized.append(
            {
                "id": job_id,
                "track": str(job["track"]),
                "start": start,
                "end": end,
                "language": language,
                "language_mode": mode,
            }
        )
    return normalized


def run(args: argparse.Namespace, *, allow_legacy_jobs: bool = False) -> int:
    manifest = load_task_manifest(args.task_manifest)
    issues = verify_manifest_inputs(args.task_manifest, manifest)
    if issues:
        raise ValueError("task manifest validation failed: " + "; ".join(issues))
    assert_manifest_paths(args.task_manifest, manifest, {"audio": args.audio})
    jobs = load_jobs(
        args.jobs,
        default_language=getattr(args, "default_language", None),
        allow_legacy=allow_legacy_jobs,
    )

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ValueError("faster-whisper is required for ASR evidence") from exc

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    output = {
        "schema_version": "2.0",
        "algorithm_version": ALGORITHM_VERSION,
        "task_fingerprint_sha256": manifest["task_fingerprint_sha256"],
        "audio_sha256": sha256(args.audio),
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "beam_size": args.beam_size,
        "jobs": [],
    }
    for job in jobs:
        whisper_language = None if job["language_mode"] == "detect" else job["language"]
        segments, info = model.transcribe(
            str(args.audio),
            language=whisper_language,
            task="transcribe",
            beam_size=args.beam_size,
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
        output["jobs"].append(
            {
                **job,
                "detected_language": info.language,
                "language_probability": info.language_probability,
                "segments": rows,
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
