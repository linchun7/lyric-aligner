"""Immutable artifact lineage for Lyric Aligner v4."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

ARTIFACT_SCHEMA_VERSION = "1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def output_record(path: Path, *, role: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"artifact output does not exist: {path}")
    return {
        "role": role,
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_artifact_manifest(
    *,
    task_fingerprint_sha256: str,
    stage: str,
    algorithm_version: str,
    outputs: Iterable[tuple[str, Path]],
    normalized_config: dict[str, Any] | None = None,
    producer: dict[str, Any] | None = None,
    dependencies: dict[str, str] | None = None,
    models: dict[str, str] | None = None,
    upstream_artifact_ids: Iterable[str] = (),
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = [output_record(path, role=role) for role, path in outputs]
    core = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "task_fingerprint_sha256": task_fingerprint_sha256,
        "stage": stage,
        "algorithm_version": algorithm_version,
        "normalized_config": normalized_config or {},
        "producer": producer or {},
        "dependencies": dependencies or {},
        "models": models or {},
        "upstream_artifact_ids": sorted(str(value) for value in upstream_artifact_ids),
        "outputs": records,
        "evidence": evidence or {},
    }
    return {**core, "artifact_id": canonical_json_sha256(core)}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def validate_upstream_artifact(
    payload: dict[str, Any],
    *,
    expected_task_fingerprint: str,
    expected_algorithm_version: str | None = None,
    expected_stage: str | None = None,
) -> list[str]:
    issues: list[str] = []
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        issues.append("artifact schema_version mismatch")
    if payload.get("task_fingerprint_sha256") != expected_task_fingerprint:
        issues.append("artifact task fingerprint mismatch")
    if expected_algorithm_version is not None and payload.get("algorithm_version") != expected_algorithm_version:
        issues.append(
            "artifact algorithm version mismatch: "
            f"expected {expected_algorithm_version}, got {payload.get('algorithm_version')}"
        )
    if expected_stage is not None and payload.get("stage") != expected_stage:
        issues.append(f"artifact stage mismatch: expected {expected_stage}, got {payload.get('stage')}")

    artifact_id = payload.get("artifact_id")
    unsigned = {key: value for key, value in payload.items() if key != "artifact_id"}
    if not artifact_id or artifact_id != canonical_json_sha256(unsigned):
        issues.append("artifact_id does not match manifest contents")
    return issues
