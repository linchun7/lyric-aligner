#!/usr/bin/env python3
"""Generate non-authoritative final-mix acoustic boundary evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lyric_aligner.audio.boundary_acoustic import (
    AcousticBoundaryConfig,
    analyze_final_mix_boundary,
    evidence_point_from_acoustic_result,
)
from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.timeline.boundary_refinement import build_boundary_evidence_artifact


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolve_manifest_file(record: dict, *, label: str) -> Path:
    if not isinstance(record, dict) or record.get("kind") != "file":
        raise ValueError(f"manifest {label} must be a file record")
    raw = str(record.get("path") or "").strip()
    expected = str(record.get("sha256") or "").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    path = path.resolve()
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"manifest {label} SHA mismatch: expected {expected}, got {actual}")
    return path


def parse_selection(values: list[str]) -> set[tuple[int, str]]:
    output: set[tuple[int, str]] = set()
    for raw in values:
        if ":" not in raw:
            raise ValueError("--boundary must be CUE:start or CUE:end")
        cue_raw, kind = raw.rsplit(":", 1)
        cue = int(cue_raw)
        if cue <= 0 or kind not in {"start", "end"}:
            raise ValueError("--boundary must be CUE:start or CUE:end")
        identity = (cue, kind)
        if identity in output:
            raise ValueError(f"duplicate --boundary selection: {raw}")
        output.add(identity)
    return output


def config_for_job(job: dict) -> AcousticBoundaryConfig:
    tier = str(job.get("difficulty_tier") or "")
    search_by_tier = {
        "A_local_refine": 700,
        "B_multi_evidence": 1000,
        "C_heavy_boundary": 1500,
        "D_structural": 2000,
    }
    return AcousticBoundaryConfig(search_ms=search_by_tier.get(tier, 900))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--boundary",
        action="append",
        default=[],
        help="restrict analysis to CUE:start or CUE:end; repeatable",
    )
    parser.add_argument("--max-jobs", type=int, default=0)
    args = parser.parse_args()

    manifest = json.loads(args.task_manifest.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("task manifest has no inputs object")
    audio_record = inputs.get("audio")
    source_record = inputs.get("source_srt")
    audio_path = resolve_manifest_file(audio_record, label="audio")
    resolve_manifest_file(source_record, label="source_srt")
    if str(plan.get("task_fingerprint_sha256") or "") != str(
        manifest.get("task_fingerprint_sha256") or ""
    ):
        raise ValueError("boundary plan/task fingerprint mismatch")
    if str(plan.get("final_audio_sha256") or "") != str(audio_record.get("sha256") or ""):
        raise ValueError("boundary plan/final audio SHA mismatch")
    if str(plan.get("source_srt_sha256") or "") != str(source_record.get("sha256") or ""):
        raise ValueError("boundary plan/source SRT SHA mismatch")

    selected = parse_selection(args.boundary)
    jobs = plan.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("boundary plan jobs must be a list")
    available_identities = {
        (int(job["cue_number"]), str(job["boundary_kind"])) for job in jobs
    }
    missing = sorted(selected - available_identities)
    if missing:
        raise ValueError(f"selected boundary not present in plan: {missing}")

    targets = [
        job
        for job in jobs
        if not selected
        or (int(job["cue_number"]), str(job["boundary_kind"])) in selected
    ]
    if args.max_jobs:
        if args.max_jobs < 1:
            raise ValueError("--max-jobs must be >= 1 when provided")
        targets = targets[: args.max_jobs]

    evidence_by_id: dict[str, list[dict]] = {}
    diagnostics: list[dict] = []
    available_count = 0
    raw_quality_candidate_count = 0
    for job in targets:
        result = analyze_final_mix_boundary(
            audio_path,
            editor_ms=int(job["editor_ms"]),
            boundary_kind=str(job["boundary_kind"]),
            config=config_for_job(job),
        )
        diagnostic = {
            "boundary_id": str(job["boundary_id"]),
            "cue_number": int(job["cue_number"]),
            "difficulty_tier": str(job.get("difficulty_tier") or ""),
            **result,
        }
        diagnostics.append(diagnostic)
        point = evidence_point_from_acoustic_result(result)
        if point is not None:
            available_count += 1
            point["evidence_sha256"] = sha256_json(diagnostic)
            evidence_by_id.setdefault(str(job["boundary_id"]), []).append(point)
            if float(point["confidence"]) >= 0.75 and int(point["uncertainty_ms"]) <= 150:
                raw_quality_candidate_count += 1

    artifact = build_boundary_evidence_artifact(
        plan=plan, evidence_by_boundary_id=evidence_by_id
    )
    artifact["backend_run"] = {
        "backend_id": "librosa_harmonic_boundary_v1",
        "audio_basis": "final_mix",
        "selected_boundary_count": len(targets),
        "available_boundary_count": available_count,
        "raw_quality_candidate_count": raw_quality_candidate_count,
        "calibration_passed": False,
        "automatic_mutation_allowed": False,
        "diagnostics": diagnostics,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "selected": len(targets),
                "available": available_count,
                "raw_quality_candidates": raw_quality_candidate_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
