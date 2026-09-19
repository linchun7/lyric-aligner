#!/usr/bin/env python3
"""Run a pinned local Wav2Vec2 CTC observer on one locked evaluation partition.

The command is intentionally evaluation-only.  It consumes trusted canonical text,
never performs transcription, never edits subtitles, and writes only observer evidence.
Calibration and holdout must be supplied as physically separate manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.audio.ctc_forced_alignment import CTCForcedAlignmentError
from lyric_aligner.audio.wav2vec2_ctc_observer import (
    WAV2VEC2_CTC_OBSERVER_AUTHORITY,
    XINGYU_XLSR53_CORRELATION_GROUP,
    XINGYU_XLSR53_MODEL_ID,
    LocalWav2Vec2CTCBackend,
    Wav2Vec2CTCObserverError,
    build_outer_evidence,
)


MANIFEST_SCHEMA_VERSION = "wav2vec2-ctc-evaluation-manifest-1.1"
RUN_SCHEMA_VERSION = "wav2vec2-ctc-observer-run-1.0"
BACKEND_ID = "wav2vec2_xlsr53_zh_ctc"
BACKEND_FAMILY = "final_mix_ssl_ctc_forced_alignment"
BACKEND_VERSION = "trusted-text-ctc-posterior-1.0"
XINGYU_REFERENCE_SOURCE_REVISION = "ef5ad02a0059ab07f4cc92f608d373447c89b007"
_ALLOWED_PARTITIONS = {"calibration", "holdout"}


class Wav2Vec2CTCRunError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"observer output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _resolve_repository_file(relative_path: str) -> Path:
    value = str(relative_path or "").strip().replace("\\", "/")
    if not value:
        raise Wav2Vec2CTCRunError("manifest clip path must be non-empty")
    candidate = (REPOSITORY_ROOT / value).resolve()
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError as exc:
        raise Wav2Vec2CTCRunError("manifest clip path escapes repository root") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"manifest clip does not exist: {value}")
    return candidate


def load_evaluation_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Wav2Vec2CTCRunError("evaluation manifest is unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise Wav2Vec2CTCRunError("evaluation manifest must be an object")
    required = {
        "schema_version",
        "partition",
        "selection_lock_sha256",
        "final_audio_sha256",
        "outer_audit_csv_sha256",
        "audio_basis",
        "language",
        "record_count",
        "records",
        "manifest_sha256",
    }
    if set(payload) != required:
        raise Wav2Vec2CTCRunError("evaluation manifest fields do not match the exact schema")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise Wav2Vec2CTCRunError("unsupported evaluation manifest schema")
    claimed_sha = str(payload.get("manifest_sha256") or "")
    without_sha = dict(payload)
    without_sha.pop("manifest_sha256", None)
    if len(claimed_sha) != 64 or claimed_sha != _sha256_json(without_sha):
        raise Wav2Vec2CTCRunError("evaluation manifest hash is invalid")
    partition = str(payload.get("partition") or "")
    if partition not in _ALLOWED_PARTITIONS:
        raise Wav2Vec2CTCRunError("evaluation partition must be calibration or holdout")
    if str(payload.get("audio_basis") or "") != "locked_final_mix_clip":
        raise Wav2Vec2CTCRunError("evaluation audio basis must be locked_final_mix_clip")
    language = str(payload.get("language") or "")
    if language != "zh":
        raise Wav2Vec2CTCRunError("current Xingyu baseline is scoped to zh")
    for key in ("selection_lock_sha256", "final_audio_sha256", "outer_audit_csv_sha256"):
        value = str(payload.get(key) or "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise Wav2Vec2CTCRunError(f"manifest {key} must be a lowercase SHA-256")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise Wav2Vec2CTCRunError("evaluation manifest records must be non-empty")
    if int(payload.get("record_count") or -1) != len(records):
        raise Wav2Vec2CTCRunError("evaluation manifest record_count mismatch")
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping) or set(row) != {
            "case_id",
            "clip_path",
            "clip_sha256",
            "window_start_ms",
            "canonical_text",
        }:
            raise Wav2Vec2CTCRunError("evaluation case fields do not match the exact schema")
        case_id = str(row.get("case_id") or "")
        if len(case_id) != 64 or any(char not in "0123456789abcdef" for char in case_id):
            raise Wav2Vec2CTCRunError("evaluation case_id must be a lowercase SHA-256")
        if case_id in seen:
            raise Wav2Vec2CTCRunError("evaluation case IDs must be unique")
        seen.add(case_id)
        if not str(row.get("canonical_text") or "").strip():
            raise Wav2Vec2CTCRunError("evaluation canonical text must be non-empty")
        try:
            window_start_ms = int(row.get("window_start_ms"))
        except (TypeError, ValueError) as exc:
            raise Wav2Vec2CTCRunError("evaluation window_start_ms must be integer") from exc
        if window_start_ms < 0:
            raise Wav2Vec2CTCRunError("evaluation window_start_ms must be nonnegative")
        expected_clip_sha = str(row.get("clip_sha256") or "")
        if len(expected_clip_sha) != 64 or any(
            char not in "0123456789abcdef" for char in expected_clip_sha
        ):
            raise Wav2Vec2CTCRunError("evaluation clip_sha256 must be a lowercase SHA-256")
        clip_path = _resolve_repository_file(str(row.get("clip_path") or ""))
        actual_clip_sha = _sha256_file(clip_path)
        if actual_clip_sha != expected_clip_sha:
            raise Wav2Vec2CTCRunError(
                f"locked evaluation clip SHA mismatch for case {case_id}"
            )
    return payload


def _implementation_identity() -> dict[str, Any]:
    relative_files = (
        "lyric_aligner/audio/wav2vec2_ctc_observer.py",
        "lyric_aligner/audio/ctc_forced_alignment.py",
        "lyric_aligner/alignment/boundary_hypotheses.py",
        "scripts/v4_run_wav2vec2_ctc_observer.py",
    )
    files = [
        {
            "path": value,
            "sha256": _sha256_file(REPOSITORY_ROOT / value),
        }
        for value in relative_files
    ]
    return {
        "files": files,
        "implementation_revision": _sha256_json(files),
    }


INFERENCE_RUNTIME_PACKAGES = (
    "numpy",
    "scipy",
    "soundfile",
    "tokenizers",
    "torch",
    "transformers",
)


def _runtime_identity() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in INFERENCE_RUNTIME_PACKAGES:
        try:
            packages[name] = package_version(name)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"required observer runtime package is missing: {name}") from exc
    payload = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "packages": packages,
    }
    return {
        **payload,
        "runtime_revision": _sha256_json(payload),
    }


MODEL_RUNTIME_FILES = (
    "config.json",
    "preprocessor_config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "vocab.json",
)


def _model_snapshot_identity(model_dir: Path) -> dict[str, Any]:
    root = model_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"model snapshot directory does not exist: {model_dir}")
    files: list[dict[str, Any]] = []
    for relative in MODEL_RUNTIME_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"model snapshot is missing required runtime file: {relative}")
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "file_count": len(files),
        "files": files,
        "snapshot_revision": _sha256_json(files),
    }


def run_observer(
    *,
    manifest_path: Path,
    model_dir: Path,
    model_revision: str,
) -> dict[str, Any]:
    manifest = load_evaluation_manifest(manifest_path)
    if len(model_revision) != 40 or any(char not in "0123456789abcdef" for char in model_revision):
        raise Wav2Vec2CTCRunError("model revision must be a pinned lowercase git SHA")
    implementation = _implementation_identity()
    runtime = _runtime_identity()
    model_snapshot = _model_snapshot_identity(model_dir)
    observer_revision = _sha256_json(
        {
            "backend_version": BACKEND_VERSION,
            "model_id": XINGYU_XLSR53_MODEL_ID,
            "model_revision": model_revision,
            "implementation_revision": implementation["implementation_revision"],
            "runtime_revision": runtime["runtime_revision"],
            "model_snapshot_revision": model_snapshot["snapshot_revision"],
            "xingyu_reference_source_revision": XINGYU_REFERENCE_SOURCE_REVISION,
        }
    )

    backend = LocalWav2Vec2CTCBackend(
        model_dir,
        model_id=XINGYU_XLSR53_MODEL_ID,
        model_revision=model_revision,
    )
    records: list[dict[str, Any]] = []
    for row in manifest["records"]:
        clip_path = _resolve_repository_file(str(row["clip_path"]))
        base_record = {
            "case_id": str(row["case_id"]),
            "clip_path": str(row["clip_path"]),
            "clip_sha256": _sha256_file(clip_path),
            "window_start_ms": int(row["window_start_ms"]),
            "canonical_text": str(row["canonical_text"]),
        }
        try:
            emission = backend.infer(
                clip_path,
                canonical_text=str(row["canonical_text"]),
            )
            evidence = build_outer_evidence(
                emission,
                window_start_ms=int(row["window_start_ms"]),
                case_id=str(row["case_id"]),
                observer_id=BACKEND_ID,
                observer_revision=observer_revision,
                correlation_group=XINGYU_XLSR53_CORRELATION_GROUP,
                audio_basis="locked_final_mix_clip",
                language="zh",
            )
        except (Wav2Vec2CTCObserverError, CTCForcedAlignmentError) as exc:
            records.append(
                {
                    **base_record,
                    "status": "unavailable",
                    "failure_type": type(exc).__name__,
                    "failure_reason": str(exc),
                    "predicted_start_ms": None,
                    "predicted_end_ms": None,
                    "evidence": None,
                }
            )
            continue
        records.append(
            {
                **base_record,
                "status": "aligned",
                "failure_type": None,
                "failure_reason": None,
                "predicted_start_ms": int(evidence.start.hypotheses[0].boundary_ms),
                "predicted_end_ms": int(evidence.end.hypotheses[0].boundary_ms),
                "evidence": evidence.to_dict(),
            }
        )

    aligned_count = sum(row["status"] == "aligned" for row in records)
    unavailable_count = len(records) - aligned_count
    artifact: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "authority": WAV2VEC2_CTC_OBSERVER_AUTHORITY,
        "partition": manifest["partition"],
        "manifest_sha256": manifest["manifest_sha256"],
        "selection_lock_sha256": manifest["selection_lock_sha256"],
        "final_audio_sha256": manifest["final_audio_sha256"],
        "outer_audit_csv_sha256": manifest["outer_audit_csv_sha256"],
        "backend_id": BACKEND_ID,
        "family": BACKEND_FAMILY,
        "correlation_group": XINGYU_XLSR53_CORRELATION_GROUP,
        "backend_version": BACKEND_VERSION,
        "model_id": XINGYU_XLSR53_MODEL_ID,
        "model_revision": model_revision,
        "xingyu_reference_source_revision": XINGYU_REFERENCE_SOURCE_REVISION,
        "implementation": implementation,
        "runtime": runtime,
        "model_snapshot": model_snapshot,
        "observer_revision": observer_revision,
        "record_count": len(records),
        "aligned_count": aligned_count,
        "unavailable_count": unavailable_count,
        "prediction_coverage": aligned_count / len(records),
        "records": records,
        "automatic_mutation_allowed": False,
        "subtitle_mutation_performed": False,
    }
    artifact["artifact_sha256"] = _sha256_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    artifact = run_observer(
        manifest_path=args.manifest,
        model_dir=args.model_dir,
        model_revision=str(args.model_revision),
    )
    _atomic_json(args.out, artifact)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "partition": artifact["partition"],
                "record_count": artifact["record_count"],
                "backend_id": artifact["backend_id"],
                "model_revision": artifact["model_revision"],
                "observer_revision": artifact["observer_revision"],
                "artifact_sha256": artifact["artifact_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
