#!/usr/bin/env python3
"""Run the pinned Mandarin singing pronunciation-CTC observer on one locked partition."""

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
from lyric_aligner.audio.mandarin_lyricalignment_observer import (
    ARCHITECTURE_FINGERPRINT_SHA256,
    BERT_VOCAB_SHA256,
    CORRELATION_GROUP,
    MODEL_CHECKPOINT_SHA256,
    MODEL_ID,
    OBSERVER_AUTHORITY,
    PRONUNCIATION_TABLE_SHA256,
    SOURCE_REVISION,
    LocalMandarinLyricAlignmentBackend,
    MandarinLyricAlignmentObserverError,
    build_outer_evidence,
)

MANIFEST_SCHEMA_VERSION = "wav2vec2-ctc-evaluation-manifest-1.1"
RUN_SCHEMA_VERSION = "mandarin-lyricalignment-observer-run-1.0"
BACKEND_ID = "mandarin_lyricalignment_asru2023_ctc"
BACKEND_FAMILY = "final_mix_mandarin_singing_pronunciation_ctc"
BACKEND_VERSION = "asru2023-trusted-text-pronunciation-ctc-posterior-1.0"
_ALLOWED_PARTITIONS = {"calibration", "holdout"}

EXPECTED_MODEL_FILES = {
    "args.json": (992, "ac6f70bf603dbbee793542d3d5143b6b9032a3c968d11d47c824ad2c42204dcd"),
    "model_args.json": (188, "b2d7b6ba3fd55650ccddaa585ac6beeeaa037dce3bf1ca4676113c1fb8e46d93"),
    "best_model.pt": (3144425533, MODEL_CHECKPOINT_SHA256),
}


class MandarinLyricAlignmentRunError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
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
        raise MandarinLyricAlignmentRunError("manifest clip path must be non-empty")
    candidate = (REPOSITORY_ROOT / value).resolve()
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError as exc:
        raise MandarinLyricAlignmentRunError("manifest clip path escapes repository root") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"manifest clip does not exist: {value}")
    return candidate


def load_evaluation_manifest(path: Path, *, expected_partition: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MandarinLyricAlignmentRunError("evaluation manifest is unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise MandarinLyricAlignmentRunError("evaluation manifest must be an object")
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
    if set(payload) != required or payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise MandarinLyricAlignmentRunError("evaluation manifest fields/schema do not match")
    claimed_sha = str(payload.get("manifest_sha256") or "")
    without_sha = dict(payload)
    without_sha.pop("manifest_sha256", None)
    if len(claimed_sha) != 64 or claimed_sha != _sha256_json(without_sha):
        raise MandarinLyricAlignmentRunError("evaluation manifest hash is invalid")
    partition = str(payload.get("partition") or "")
    if partition not in _ALLOWED_PARTITIONS or partition != expected_partition:
        raise MandarinLyricAlignmentRunError(
            f"evaluation partition mismatch: {partition!r} != {expected_partition!r}"
        )
    if str(payload.get("audio_basis") or "") != "locked_final_mix_clip":
        raise MandarinLyricAlignmentRunError("evaluation audio basis must be locked_final_mix_clip")
    if str(payload.get("language") or "") != "zh":
        raise MandarinLyricAlignmentRunError("Mandarin LyricAlignment observer is scoped to zh")
    for key in ("selection_lock_sha256", "final_audio_sha256", "outer_audit_csv_sha256"):
        value = str(payload.get(key) or "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise MandarinLyricAlignmentRunError(f"manifest {key} must be lowercase SHA-256")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise MandarinLyricAlignmentRunError("evaluation records must be non-empty")
    if int(payload.get("record_count") or -1) != len(records):
        raise MandarinLyricAlignmentRunError("evaluation record_count mismatch")
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping) or set(row) != {
            "case_id", "clip_path", "clip_sha256", "window_start_ms", "canonical_text"
        }:
            raise MandarinLyricAlignmentRunError("evaluation case fields do not match exact schema")
        case_id = str(row.get("case_id") or "")
        if len(case_id) != 64 or any(char not in "0123456789abcdef" for char in case_id):
            raise MandarinLyricAlignmentRunError("case_id must be lowercase SHA-256")
        if case_id in seen:
            raise MandarinLyricAlignmentRunError("case IDs must be unique")
        seen.add(case_id)
        if not str(row.get("canonical_text") or "").strip():
            raise MandarinLyricAlignmentRunError("canonical text must be non-empty")
        try:
            window_start_ms = int(row.get("window_start_ms"))
        except (TypeError, ValueError) as exc:
            raise MandarinLyricAlignmentRunError("window_start_ms must be integer") from exc
        if window_start_ms < 0:
            raise MandarinLyricAlignmentRunError("window_start_ms must be nonnegative")
        expected_clip_sha = str(row.get("clip_sha256") or "")
        if len(expected_clip_sha) != 64 or any(
            char not in "0123456789abcdef" for char in expected_clip_sha
        ):
            raise MandarinLyricAlignmentRunError("clip_sha256 must be lowercase SHA-256")
        clip_path = _resolve_repository_file(str(row.get("clip_path") or ""))
        if _sha256_file(clip_path) != expected_clip_sha:
            raise MandarinLyricAlignmentRunError(
                f"locked evaluation clip SHA mismatch for case {case_id}"
            )
    return payload


def _implementation_identity() -> dict[str, Any]:
    relative_files = (
        "lyric_aligner/audio/mandarin_lyricalignment_observer.py",
        "lyric_aligner/audio/ctc_forced_alignment.py",
        "lyric_aligner/alignment/boundary_hypotheses.py",
        "scripts/v4_run_mandarin_lyricalignment_observer.py",
    )
    files = [
        {"path": relative, "sha256": _sha256_file(REPOSITORY_ROOT / relative)}
        for relative in relative_files
    ]
    return {"files": files, "implementation_revision": _sha256_json(files)}


RUNTIME_PACKAGES = (
    "numpy",
    "torch",
    "openai-whisper",
    "librosa",
    "soundfile",
    "pypinyin",
)


def _runtime_identity() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in RUNTIME_PACKAGES:
        try:
            packages[name] = package_version(name)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"required 3B runtime package is missing: {name}") from exc
    payload = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "packages": packages,
    }
    return {**payload, "runtime_revision": _sha256_json(payload)}


def _model_snapshot_identity(model_dir: Path) -> dict[str, Any]:
    root = model_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"3B model directory does not exist: {model_dir}")
    files: list[dict[str, Any]] = []
    for name, (expected_size, expected_sha) in EXPECTED_MODEL_FILES.items():
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"3B model snapshot is missing {name}")
        actual_size = path.stat().st_size
        actual_sha = _sha256_file(path)
        if actual_size != expected_size or actual_sha != expected_sha:
            raise MandarinLyricAlignmentRunError(
                f"3B model snapshot identity mismatch for {name}"
            )
        files.append({"path": name, "size": actual_size, "sha256": actual_sha})
    return {
        "file_count": len(files),
        "files": files,
        "snapshot_revision": _sha256_json(files),
        "architecture_fingerprint_sha256": ARCHITECTURE_FINGERPRINT_SHA256,
    }


def _lexical_identity(vocab_path: Path, pronunciation_table_path: Path) -> dict[str, Any]:
    if not vocab_path.is_file() or not pronunciation_table_path.is_file():
        raise FileNotFoundError("3B frozen lexical resources are missing")
    vocab_sha = _sha256_file(vocab_path)
    pronunciation_sha = _sha256_file(pronunciation_table_path)
    if vocab_sha != BERT_VOCAB_SHA256 or pronunciation_sha != PRONUNCIATION_TABLE_SHA256:
        raise MandarinLyricAlignmentRunError("3B frozen lexical resource identity mismatch")
    payload = {
        "bert_vocab_path": str(vocab_path),
        "bert_vocab_sha256": vocab_sha,
        "pronunciation_table_path": str(pronunciation_table_path),
        "pronunciation_table_sha256": pronunciation_sha,
        "source_revision": SOURCE_REVISION,
    }
    return {**payload, "lexical_revision": _sha256_json(payload)}


def run_observer(
    *,
    manifest_path: Path,
    expected_partition: str,
    model_dir: Path,
    vocab_path: Path,
    pronunciation_table_path: Path,
) -> dict[str, Any]:
    manifest = load_evaluation_manifest(
        manifest_path,
        expected_partition=expected_partition,
    )
    implementation = _implementation_identity()
    runtime = _runtime_identity()
    model_snapshot = _model_snapshot_identity(model_dir)
    lexical = _lexical_identity(vocab_path, pronunciation_table_path)
    observer_revision = _sha256_json(
        {
            "backend_version": BACKEND_VERSION,
            "model_id": MODEL_ID,
            "model_revision": MODEL_CHECKPOINT_SHA256,
            "architecture_fingerprint_sha256": ARCHITECTURE_FINGERPRINT_SHA256,
            "source_revision": SOURCE_REVISION,
            "implementation_revision": implementation["implementation_revision"],
            "runtime_revision": runtime["runtime_revision"],
            "model_snapshot_revision": model_snapshot["snapshot_revision"],
            "lexical_revision": lexical["lexical_revision"],
        }
    )

    backend = LocalMandarinLyricAlignmentBackend(
        model_dir / "best_model.pt",
        bert_vocab_path=vocab_path,
        pronunciation_table_path=pronunciation_table_path,
        model_revision=MODEL_CHECKPOINT_SHA256,
        device="cpu",
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
                observer_revision=observer_revision,
                observer_id=BACKEND_ID,
                correlation_group=CORRELATION_GROUP,
                audio_basis="locked_final_mix_clip",
                language="zh",
            )
        except (MandarinLyricAlignmentObserverError, CTCForcedAlignmentError) as exc:
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
    artifact: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "authority": OBSERVER_AUTHORITY,
        "partition": manifest["partition"],
        "manifest_sha256": manifest["manifest_sha256"],
        "selection_lock_sha256": manifest["selection_lock_sha256"],
        "final_audio_sha256": manifest["final_audio_sha256"],
        "outer_audit_csv_sha256": manifest["outer_audit_csv_sha256"],
        "backend_id": BACKEND_ID,
        "family": BACKEND_FAMILY,
        "correlation_group": CORRELATION_GROUP,
        "backend_version": BACKEND_VERSION,
        "model_id": MODEL_ID,
        "model_revision": MODEL_CHECKPOINT_SHA256,
        "source_revision": SOURCE_REVISION,
        "architecture_fingerprint_sha256": ARCHITECTURE_FINGERPRINT_SHA256,
        "implementation": implementation,
        "runtime": runtime,
        "model_snapshot": model_snapshot,
        "lexical_identity": lexical,
        "observer_revision": observer_revision,
        "record_count": len(records),
        "aligned_count": aligned_count,
        "unavailable_count": len(records) - aligned_count,
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
    parser.add_argument("--expected-partition", choices=sorted(_ALLOWED_PARTITIONS), required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--vocab", type=Path, required=True)
    parser.add_argument("--pronunciation-table", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    artifact = run_observer(
        manifest_path=args.manifest,
        expected_partition=args.expected_partition,
        model_dir=args.model_dir,
        vocab_path=args.vocab,
        pronunciation_table_path=args.pronunciation_table,
    )
    _atomic_json(args.out, artifact)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "partition": artifact["partition"],
                "record_count": artifact["record_count"],
                "aligned_count": artifact["aligned_count"],
                "unavailable_count": artifact["unavailable_count"],
                "observer_revision": artifact["observer_revision"],
                "artifact_sha256": artifact["artifact_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
