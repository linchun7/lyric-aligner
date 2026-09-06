#!/usr/bin/env python3
"""Run Independent Fine on a locked source-to-mix benchmark without reading truth."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import librosa

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.audio.independent_fine import (
    INDEPENDENT_FINE_AUTHORITY,
    INDEPENDENT_FINE_CORRELATION_GROUP,
    INDEPENDENT_FINE_VERSION,
    extract_percussive_onset_features,
    retrieve_independent_onset_window,
)
from lyric_aligner.contracts.artifacts import atomic_write_json, sha256_file

MANIFEST_SCHEMA_VERSION = "independent-fine-known-transform-manifest-1.0"
RUN_SCHEMA_VERSION = "independent-fine-known-transform-run-1.0"
HOLDOUT_PROTOCOL_SCHEMA_VERSION = "independent-fine-holdout-protocol-1.0"
BENCHMARK_AUTHORITY = "evaluation_only_known_transform_no_timing_authority"


class IndependentFineBenchmarkRunError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_sha(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and text == text.lower() and all(c in "0123456789abcdef" for c in text)


def _load_hashed_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise IndependentFineBenchmarkRunError(f"artifact root must be an object: {path}")
    claimed = str(payload.get("artifact_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("artifact_sha256", None)
    if not _is_sha(claimed) or claimed != _sha_json(unsigned):
        raise IndependentFineBenchmarkRunError(f"artifact hash mismatch: {path}")
    return payload


def _resolve_locked_file(relative: str, expected_sha: str) -> Path:
    path = (REPOSITORY_ROOT / relative).resolve()
    root = REPOSITORY_ROOT.resolve()
    if path == root or root not in path.parents:
        raise IndependentFineBenchmarkRunError("benchmark audio path escapes repository")
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual != expected_sha:
        raise IndependentFineBenchmarkRunError(f"benchmark audio SHA mismatch: {relative}")
    return path


def load_manifest(path: Path, *, expected_partition: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise IndependentFineBenchmarkRunError("benchmark manifest must be an object")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise IndependentFineBenchmarkRunError("unsupported Independent Fine benchmark manifest schema")
    if expected_partition not in {"calibration", "holdout"}:
        raise IndependentFineBenchmarkRunError("expected partition must be calibration or holdout")
    if payload.get("partition") != expected_partition:
        raise IndependentFineBenchmarkRunError("benchmark manifest partition mismatch")
    if not _is_sha(payload.get("source_audit_sha256")):
        raise IndependentFineBenchmarkRunError("benchmark source audit SHA is invalid")
    if expected_partition == "holdout":
        for key in ("source_audit_file_sha256", "frozen_policy_sha256", "pair_selection_sha256"):
            if not _is_sha(payload.get(key)):
                raise IndependentFineBenchmarkRunError(f"holdout manifest {key} is invalid")
    claimed = str(payload.get("manifest_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256", None)
    if not _is_sha(claimed) or claimed != _sha_json(unsigned):
        raise IndependentFineBenchmarkRunError("benchmark manifest hash is invalid")
    records = payload.get("records")
    if not isinstance(records, list) or not records or int(payload.get("record_count", -1)) != len(records):
        raise IndependentFineBenchmarkRunError("benchmark manifest record count is invalid")
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping):
            raise IndependentFineBenchmarkRunError("benchmark manifest row must be an object")
        case_id = str(row.get("case_id") or "")
        if not _is_sha(case_id) or case_id in seen:
            raise IndependentFineBenchmarkRunError("benchmark case IDs must be unique SHA-256 values")
        seen.add(case_id)
        for key in ("source_sha256", "mix_sha256"):
            if not _is_sha(row.get(key)):
                raise IndependentFineBenchmarkRunError(f"benchmark row {key} is invalid")
        _resolve_locked_file(str(row.get("source_path") or ""), str(row["source_sha256"]))
        _resolve_locked_file(str(row.get("mix_path") or ""), str(row["mix_sha256"]))
        try:
            mix_start = float(row["mix_start_s"])
            mix_end = float(row["mix_end_s"])
            search_start = float(row["source_search_start_s"])
            search_end = float(row["source_search_end_s"])
            slopes = [float(value) for value in row["slopes"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise IndependentFineBenchmarkRunError("benchmark timing/search/slopes are invalid") from exc
        if mix_start < 0 or mix_end <= mix_start or search_start < 0 or search_end <= search_start:
            raise IndependentFineBenchmarkRunError("benchmark timing/search intervals are invalid")
        if not slopes or any(value <= 0 for value in slopes):
            raise IndependentFineBenchmarkRunError("benchmark slopes must be positive")
    return payload


def _load_holdout_protocol(path: Path, *, manifest: Mapping[str, Any]) -> dict[str, Any]:
    payload = _load_hashed_artifact(path)
    if payload.get("schema_version") != HOLDOUT_PROTOCOL_SCHEMA_VERSION:
        raise IndependentFineBenchmarkRunError("unexpected Independent Fine holdout protocol schema")
    if payload.get("holdout_predictions_status") != "not_run_at_protocol_freeze":
        raise IndependentFineBenchmarkRunError("holdout protocol is not in pre-prediction frozen state")
    if bool(payload.get("production_authoritative")) or bool(payload.get("automatic_mutation_allowed")):
        raise IndependentFineBenchmarkRunError("holdout protocol must remain non-authoritative")
    expected = {
        "frozen_policy_sha256": manifest.get("frozen_policy_sha256"),
        "pair_selection_sha256": manifest.get("pair_selection_sha256"),
        "holdout_affine_truth_audit_sha256": manifest.get("source_audit_sha256"),
    }
    for key, value in expected.items():
        if not _is_sha(value) or str(payload.get(key) or "") != str(value):
            raise IndependentFineBenchmarkRunError(f"holdout protocol {key} mismatch")
    return payload


def _implementation_identity() -> dict[str, Any]:
    files = [
        Path(__file__).resolve(),
        REPOSITORY_ROOT / "lyric_aligner/audio/independent_fine.py",
    ]
    rows = [
        {
            "path": path.relative_to(REPOSITORY_ROOT).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in files
    ]
    return {"files": rows, "implementation_revision": _sha_json(rows)}


def run_benchmark(
    *,
    manifest_path: Path,
    expected_partition: str,
    sample_rate: int = 22050,
    holdout_protocol_path: Path | None = None,
) -> dict[str, Any]:
    if sample_rate < 8000:
        raise IndependentFineBenchmarkRunError("benchmark sample rate is implausibly low")
    manifest = load_manifest(manifest_path, expected_partition=expected_partition)
    holdout_protocol: dict[str, Any] | None = None
    if expected_partition == "holdout":
        if holdout_protocol_path is None:
            raise IndependentFineBenchmarkRunError("holdout run requires frozen holdout protocol")
        holdout_protocol = _load_holdout_protocol(holdout_protocol_path, manifest=manifest)
    elif holdout_protocol_path is not None:
        raise IndependentFineBenchmarkRunError("calibration run must not receive holdout protocol")
    feature_cache: dict[tuple[str, str], Any] = {}

    def features(relative_path: str, digest: str):
        key = (relative_path, digest)
        cached = feature_cache.get(key)
        if cached is not None:
            return cached
        path = _resolve_locked_file(relative_path, digest)
        audio, actual_sr = librosa.load(path, sr=sample_rate, mono=True)
        bundle = extract_percussive_onset_features(audio, sr=int(actual_sr))
        feature_cache[key] = bundle
        return bundle

    output_records: list[dict[str, Any]] = []
    aligned = 0
    for row in manifest["records"]:
        base = {
            "case_id": str(row["case_id"]),
            "source_path": str(row["source_path"]),
            "source_sha256": str(row["source_sha256"]),
            "mix_path": str(row["mix_path"]),
            "mix_sha256": str(row["mix_sha256"]),
        }
        try:
            source_bundle = features(base["source_path"], base["source_sha256"])
            mix_bundle = features(base["mix_path"], base["mix_sha256"])
            result = retrieve_independent_onset_window(
                mix_bundle,
                source_bundle,
                mix_start=float(row["mix_start_s"]),
                mix_end=float(row["mix_end_s"]),
                slopes=[float(value) for value in row["slopes"]],
                source_search_start=float(row["source_search_start_s"]),
                source_search_end=float(row["source_search_end_s"]),
            )
            aligned += 1
            output_records.append(
                {
                    **base,
                    "status": "aligned",
                    "failure_reason": None,
                    "prediction": result.to_dict(),
                }
            )
        except (ValueError, OSError) as exc:
            output_records.append(
                {
                    **base,
                    "status": "unavailable",
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                    "prediction": None,
                }
            )
    implementation = _implementation_identity()
    provenance: dict[str, Any] = {}
    for key in ("source_audit_file_sha256", "frozen_policy_sha256", "pair_selection_sha256"):
        value = manifest.get(key)
        if value is not None:
            if not _is_sha(value):
                raise IndependentFineBenchmarkRunError(f"manifest provenance {key} is invalid")
            provenance[key] = str(value)
    if holdout_protocol is not None:
        provenance["holdout_protocol_sha256"] = str(holdout_protocol["artifact_sha256"])
    artifact: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "authority": BENCHMARK_AUTHORITY,
        "partition": expected_partition,
        "manifest_sha256": manifest["manifest_sha256"],
        "source_audit_sha256": manifest["source_audit_sha256"],
        **provenance,
        "observer_version": INDEPENDENT_FINE_VERSION,
        "observer_authority": INDEPENDENT_FINE_AUTHORITY,
        "correlation_group": INDEPENDENT_FINE_CORRELATION_GROUP,
        "sample_rate": sample_rate,
        "implementation": implementation,
        "record_count": len(output_records),
        "aligned_count": aligned,
        "unavailable_count": len(output_records) - aligned,
        "prediction_coverage": aligned / len(output_records),
        "records": output_records,
        "automatic_mutation_allowed": False,
        "subtitle_mutation_performed": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-partition", choices=("calibration", "holdout"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-rate", type=int, default=22050)
    parser.add_argument("--holdout-protocol", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"benchmark output already exists: {args.out}")
    artifact = run_benchmark(
        manifest_path=args.manifest,
        expected_partition=args.expected_partition,
        sample_rate=args.sample_rate,
        holdout_protocol_path=args.holdout_protocol,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(json.dumps({
        "out": str(args.out),
        "partition": artifact["partition"],
        "aligned_count": artifact["aligned_count"],
        "prediction_coverage": artifact["prediction_coverage"],
        "artifact_sha256": artifact["artifact_sha256"],
        "automatic_mutation_allowed": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
