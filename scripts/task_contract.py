#!/usr/bin/env python3
"""Task-manifest and QA-artifact contracts for lyric-aligner."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


TASK_SCHEMA_VERSION = "2.0"
QA_SCHEMA_VERSION = "2.0"
QA_SCOPE = (
    "Bound to this exact task manifest fingerprint; never reuse for another "
    "audio, SRT, song list, BPM list, lyric set, or source-audio set."
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: Path) -> str:
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


def repository_relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"task input must stay inside the repository: {resolved}") from exc


def file_record(path: Path, root: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"task input file does not exist: {path}")
    return {
        "kind": "file",
        "path": repository_relative(path, root),
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


def directory_record(path: Path, root: Path) -> dict[str, Any]:
    if not path.is_dir():
        raise FileNotFoundError(f"task input directory does not exist: {path}")
    files = [
        {
            "path": item.relative_to(path).as_posix(),
            "size": item.stat().st_size,
            "sha256": sha256(item),
        }
        for item in sorted(path.rglob("*"), key=lambda item: item.as_posix().casefold())
        if item.is_file()
    ]
    return {
        "kind": "directory",
        "path": repository_relative(path, root),
        "file_count": len(files),
        "sha256": canonical_json_sha256(files),
        "files": files,
    }


def input_record(path: Path | None, root: Path) -> dict[str, Any] | None:
    if path is None:
        return None
    if path.is_file():
        return file_record(path, root)
    if path.is_dir():
        return directory_record(path, root)
    raise FileNotFoundError(f"task input does not exist: {path}")


def fingerprint_payload(project: str, inputs: dict[str, Any]) -> dict[str, Any]:
    compact_inputs: dict[str, Any] = {}
    for role, record in sorted(inputs.items()):
        if record is None:
            compact_inputs[role] = None
            continue
        compact: dict[str, Any] = {
            "kind": record["kind"],
            "sha256": record["sha256"],
        }
        if record["kind"] == "file":
            compact["size"] = record["size"]
        else:
            compact["file_count"] = record["file_count"]
        compact_inputs[role] = compact
    return {
        "schema_version": TASK_SCHEMA_VERSION,
        "project": project,
        "inputs": compact_inputs,
    }


def build_task_manifest(
    root: Path,
    project: str,
    *,
    source_srt: Path,
    audio: Path,
    song_list: Path,
    lyrics_dir: Path,
    bpm_changes: Path | None = None,
    source_audio_dir: Path | None = None,
) -> dict[str, Any]:
    if not project.strip():
        raise ValueError("project must be non-empty")
    inputs = {
        "source_srt": input_record(source_srt, root),
        "audio": input_record(audio, root),
        "song_list": input_record(song_list, root),
        "lyrics_dir": input_record(lyrics_dir, root),
        "bpm_changes": input_record(bpm_changes, root),
        "source_audio_dir": input_record(source_audio_dir, root),
    }
    fingerprint = canonical_json_sha256(fingerprint_payload(project, inputs))
    return {
        "schema_version": TASK_SCHEMA_VERSION,
        "project": project,
        "task_fingerprint_sha256": fingerprint,
        "inputs": inputs,
    }


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_task_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"task manifest is unreadable: {path}") from exc
    issues = validate_task_manifest_schema(payload)
    try:
        root = manifest_root(path)
        expected_project = path.resolve().parent.parent.name
        if isinstance(payload, dict) and payload.get("project") != expected_project:
            issues.append(
                "task manifest project must match private/<task>/ directory name"
            )
        if root == path.resolve():
            issues.append("task manifest repository root is invalid")
    except ValueError as exc:
        issues.append(str(exc))
    if issues:
        raise ValueError("; ".join(issues))
    return payload


def validate_task_manifest_schema(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["task manifest must contain a JSON object"]
    issues: list[str] = []
    if payload.get("schema_version") != TASK_SCHEMA_VERSION:
        issues.append(f"task manifest schema_version must be {TASK_SCHEMA_VERSION}")
    if not isinstance(payload.get("project"), str) or not payload["project"].strip():
        issues.append("task manifest project must be a non-empty string")
    fingerprint = str(payload.get("task_fingerprint_sha256", "")).lower()
    if not HASH_RE.fullmatch(fingerprint):
        issues.append("task manifest task_fingerprint_sha256 must be a lowercase SHA-256")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        issues.append("task manifest inputs must be an object")
        return issues
    required_roles = {"source_srt", "audio", "song_list", "lyrics_dir"}
    supported_roles = required_roles | {"bpm_changes", "source_audio_dir"}
    unexpected_roles = sorted(set(inputs) - supported_roles)
    if unexpected_roles:
        issues.append(
            "task manifest contains unsupported input roles: "
            + ", ".join(unexpected_roles)
        )
    for role in sorted(supported_roles):
        record = inputs.get(role)
        if record is None:
            if role in required_roles:
                issues.append(f"task manifest requires input role {role}")
            continue
        if not isinstance(record, dict):
            issues.append(f"task manifest input {role} must be an object or null")
            continue
        kind = record.get("kind")
        if kind not in {"file", "directory"}:
            issues.append(f"task manifest input {role} has invalid kind")
        path = record.get("path")
        if (
            not isinstance(path, str)
            or not path.strip()
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            issues.append(f"task manifest input {role} has invalid repository-relative path")
        digest = str(record.get("sha256", ""))
        if not HASH_RE.fullmatch(digest):
            issues.append(f"task manifest input {role} has invalid sha256")
        if kind == "file":
            size = record.get("size")
            if not isinstance(size, int) or size < 0:
                issues.append(f"task manifest file input {role} has invalid size")
        elif kind == "directory":
            count = record.get("file_count")
            files = record.get("files")
            if not isinstance(count, int) or count < 0:
                issues.append(
                    f"task manifest directory input {role} has invalid file_count"
                )
            if not isinstance(files, list) or count != len(files):
                issues.append(
                    f"task manifest directory input {role} files do not match file_count"
                )
            elif any(not isinstance(item, dict) for item in files):
                issues.append(f"task manifest directory input {role} has invalid files")
    if not issues:
        expected = canonical_json_sha256(
            fingerprint_payload(str(payload["project"]), inputs)
        )
        if fingerprint != expected:
            issues.append("task manifest fingerprint does not match its input records")
    return issues


def manifest_root(path: Path) -> Path:
    resolved = path.resolve()
    if (
        resolved.name == "task_manifest.json"
        and resolved.parent.name == "qa"
        and resolved.parent.parent.parent.name == "private"
    ):
        return resolved.parent.parent.parent.parent
    raise ValueError(
        "task manifest must be stored at private/<task>/qa/task_manifest.json"
    )


def resolve_manifest_record(path: Path, record: dict[str, Any]) -> Path:
    return manifest_root(path) / str(record["path"])


def verify_manifest_inputs(
    manifest_path: Path,
    manifest: dict[str, Any],
    roles: tuple[str, ...] | None = None,
) -> list[str]:
    issues: list[str] = []
    root = manifest_root(manifest_path)
    selected = roles or tuple(manifest["inputs"])
    for role in selected:
        record = manifest["inputs"].get(role)
        if record is None:
            continue
        actual_path = root / str(record["path"])
        try:
            actual = input_record(actual_path, root)
        except (OSError, ValueError) as exc:
            issues.append(f"{role}: {exc}")
            continue
        if actual is None or actual["sha256"] != record["sha256"]:
            issues.append(f"{role}: content differs from task manifest")
    return issues


def assert_manifest_paths(
    manifest_path: Path,
    manifest: dict[str, Any],
    provided: dict[str, Path | None],
) -> None:
    root = manifest_root(manifest_path)
    issues = verify_manifest_inputs(manifest_path, manifest, tuple(provided))
    for role, path in provided.items():
        record = manifest["inputs"].get(role)
        if record is None and path is None:
            continue
        if record is None or path is None:
            issues.append(f"{role}: command input presence differs from task manifest")
            continue
        expected_path = (root / str(record["path"])).resolve()
        if path.resolve() != expected_path:
            issues.append(f"{role}: command path differs from task manifest")
    if issues:
        raise ValueError("task manifest validation failed: " + "; ".join(issues))


def qa_metadata(manifest: dict[str, Any], artifact_type: str) -> dict[str, str]:
    source = manifest["inputs"]["source_srt"]
    return {
        "schema_version": QA_SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "project": str(manifest["project"]),
        "source_srt_sha256": str(source["sha256"]),
        "task_fingerprint_sha256": str(manifest["task_fingerprint_sha256"]),
        "scope": QA_SCOPE,
    }


def validate_qa_artifact(
    payload: Any,
    manifest: dict[str, Any],
    artifact_label: str,
    artifact_type: str,
) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{artifact_label} must contain a JSON object"]
    expected = qa_metadata(manifest, artifact_type)
    issues: list[str] = []
    for key, value in expected.items():
        actual = payload.get(key)
        if actual != value:
            issues.append(
                f"{artifact_label} has invalid {key}: expected {value!r}, got {actual!r}"
            )
    if "_source_srt_sha256" in payload:
        issues.append(
            f"{artifact_label} uses legacy _source_srt_sha256; run migrate_task.py"
        )
    return issues


def migrate_qa_payload(
    payload: Any,
    manifest: dict[str, Any],
    artifact_type: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("QA artifact must contain a JSON object")
    migrated = dict(payload)
    migrated.pop("_source_srt_sha256", None)
    for key, value in qa_metadata(manifest, artifact_type).items():
        migrated[key] = value
    return migrated


def validate_artifact_fingerprint(
    payload: Any,
    manifest: dict[str, Any],
    artifact_label: str,
) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{artifact_label} must contain a JSON object")
    expected = str(manifest["task_fingerprint_sha256"])
    actual = str(payload.get("task_fingerprint_sha256", ""))
    if actual != expected:
        raise ValueError(
            f"{artifact_label} belongs to another task fingerprint: "
            f"expected {expected}, got {actual or '<missing>'}"
        )


def report_fingerprint(rows: list[dict[str, Any]]) -> str:
    if not rows:
        raise ValueError("report must contain at least one row")
    missing = [
        index
        for index, row in enumerate(rows, start=2)
        if not str(row.get("task_fingerprint_sha256", "")).strip()
    ]
    if missing:
        raise ValueError(
            "every report row must contain task_fingerprint_sha256; "
            f"missing on CSV lines {missing[:10]}"
        )
    fingerprints = {
        str(row["task_fingerprint_sha256"]).strip() for row in rows
    }
    if len(fingerprints) != 1:
        raise ValueError("report must contain exactly one task fingerprint")
    fingerprint = next(iter(fingerprints))
    if not HASH_RE.fullmatch(fingerprint):
        raise ValueError("report task fingerprint must be a lowercase SHA-256")
    return fingerprint
