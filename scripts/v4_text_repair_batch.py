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

from lyric_aligner.text_repair import write_repair_outputs


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--auto-threshold", type=float, default=0.72)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        parser.error("manifest must contain a non-empty jobs array")

    base = manifest_path.parent
    results: list[dict[str, object]] = []
    ready_count = review_count = error_count = 0
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
            if source.resolve() == output.resolve():
                raise ValueError("output must not overwrite source_srt")
            if not source.is_file():
                raise ValueError("source_srt does not exist")
            if any(not path.is_file() for path in canonical):
                raise ValueError("one or more canonical lyric files do not exist")
            report = write_repair_outputs(
                source,
                canonical,
                output,
                report_path=report_path,
                auto_threshold=float(job.get("auto_threshold", args.auto_threshold)),
            )
            status = str(report["status"])
            if status == "ready":
                ready_count += 1
            else:
                review_count += 1
            results.append({
                "id": job_id,
                "status": status,
                "replacement_count": report["replacement_count"],
                "review_count": report["review_count"],
                "segmentation_span_count": report["segmentation_span_count"],
                "timeline_unchanged": report["timeline_unchanged"],
                "cue_count_unchanged": report["cue_count_unchanged"],
                "output_srt_sha256": report["output_srt_sha256"],
            })
        except (KeyError, OSError, ValueError, AssertionError, json.JSONDecodeError) as exc:
            error_count += 1
            results.append({"id": job_id, "status": "error", "error": str(exc)})

    summary = {
        "schema_version": "1.0",
        "mode": "text_repair_v2_batch",
        "job_count": len(jobs),
        "ready_count": ready_count,
        "review_required_count": review_count,
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
