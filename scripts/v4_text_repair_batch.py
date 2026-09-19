#!/usr/bin/env python3
"""Run independent Text Repair V2 jobs from one JSON manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    PRODUCTION_MIN_AUTO_THRESHOLD,
    write_repair_outputs,
)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _validate_production_threshold(value: float) -> None:
    if value < PRODUCTION_MIN_AUTO_THRESHOLD:
        raise ValueError(
            "production auto-threshold must be at least "
            f"{PRODUCTION_MIN_AUTO_THRESHOLD:.2f}"
        )


def _manifest_paths(
    jobs: list[object],
    base: Path,
    manifest_path: Path,
    summary_path: Path | None,
) -> None:
    """Fail before writing if batch outputs can collide with any batch input/output."""
    ids: set[str] = set()
    read_paths: set[Path] = {manifest_path.resolve()}
    write_paths: list[Path] = []
    for index, raw_job in enumerate(jobs):
        if not isinstance(raw_job, dict):
            continue
        job_id = str(raw_job.get("id") or f"job-{index + 1}")
        if job_id in ids:
            raise ValueError(f"duplicate batch job id: {job_id}")
        ids.add(job_id)
        if "source_srt" in raw_job:
            read_paths.add(_resolve(base, str(raw_job["source_srt"])))
        canonical_values = raw_job.get("canonical_lyrics")
        if isinstance(canonical_values, list):
            read_paths.update(_resolve(base, str(value)) for value in canonical_values)
        if "out" in raw_job:
            write_paths.append(_resolve(base, str(raw_job["out"])))
        report_value = raw_job.get("report")
        if report_value:
            write_paths.append(_resolve(base, str(report_value)))
    if summary_path is not None:
        write_paths.append(summary_path.resolve())
    if len(set(write_paths)) != len(write_paths):
        raise ValueError("batch output/report/summary paths must be unique")
    collision = next((path for path in write_paths if path in read_paths), None)
    if collision is not None:
        raise ValueError(
            "batch output/report/summary must not overwrite manifest or any batch input"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument(
        "--auto-threshold",
        type=float,
        default=DEFAULT_AUTO_THRESHOLD,
        help=(
            "Default automatic text-repair threshold; production values below "
            f"{PRODUCTION_MIN_AUTO_THRESHOLD:.2f} are rejected."
        ),
    )
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read batch manifest: {exc}")
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        parser.error("manifest must contain a non-empty jobs array")

    base = manifest_path.parent
    try:
        _validate_production_threshold(args.auto_threshold)
        _manifest_paths(jobs, base, manifest_path, args.summary)
        for raw_job in jobs:
            if isinstance(raw_job, dict) and "auto_threshold" in raw_job:
                _validate_production_threshold(float(raw_job["auto_threshold"]))
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    results: list[dict[str, object]] = []
    ready_count = review_count = error_count = coverage_warning_job_count = 0
    for index, job in enumerate(jobs):
        job_id = str(job.get("id") or f"job-{index + 1}") if isinstance(job, dict) else f"job-{index + 1}"
        try:
            if not isinstance(job, dict):
                raise ValueError("job must be an object")
            source = _resolve(base, str(job["source_srt"]))
            canonical_values = job.get("canonical_lyrics")
            if not isinstance(canonical_values, list) or not canonical_values:
                raise ValueError("canonical_lyrics must be a non-empty array")
            canonical = [_resolve(base, str(value)) for value in canonical_values]
            output = _resolve(base, str(job["out"]))
            report_value = job.get("report")
            report_path = _resolve(base, str(report_value)) if report_value else None
            if not source.is_file():
                raise ValueError("source_srt does not exist")
            if any(not path.is_file() for path in canonical):
                raise ValueError("one or more canonical lyric files do not exist")
            threshold = float(job.get("auto_threshold", args.auto_threshold))
            report = write_repair_outputs(
                source,
                canonical,
                output,
                report_path=report_path,
                auto_threshold=threshold,
            )
            status = str(report["status"])
            if status == "ready":
                ready_count += 1
            else:
                review_count += 1
            if report["coverage_warning_count"]:
                coverage_warning_job_count += 1
            results.append({
                "id": job_id,
                "status": status,
                "coverage_status": report["coverage_status"],
                "replacement_count": report["replacement_count"],
                "review_count": report["review_count"],
                "coverage_warning_count": report["coverage_warning_count"],
                "segmentation_span_count": report["segmentation_span_count"],
                "timeline_unchanged": report["timeline_unchanged"],
                "cue_count_unchanged": report["cue_count_unchanged"],
                "output_srt_sha256": report["output_srt_sha256"],
            })
        except (KeyError, OSError, TypeError, ValueError, AssertionError) as exc:
            error_count += 1
            results.append({"id": job_id, "status": "error", "error": str(exc)})

    summary = {
        "schema_version": "1.1",
        "mode": "text_repair_v2_batch",
        "job_count": len(jobs),
        "ready_count": ready_count,
        "review_required_count": review_count,
        "coverage_warning_job_count": coverage_warning_job_count,
        "error_count": error_count,
        "jobs": results,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(text, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    if error_count:
        return 3
    if review_count:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())