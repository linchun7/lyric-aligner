"""Ephemeral parent-to-child attestation for already-verified task inputs.

A production orchestrator may fully hash/verify the task manifest once, then
create a fresh random-token session for child CLIs in the *same invocation*.
Children can use that session to avoid re-reading large immutable inputs while
still checking paths, manifest identity, file sizes and mtimes. Standalone CLIs
without the fresh environment token keep their existing full SHA-256 checks.

The session is an execution optimization only. It is never an artifact lineage
input and never weakens formal task/artifact fingerprints.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any


SESSION_SCHEMA_VERSION = "1.0"
SESSION_PATH_ENV = "LYRIC_ALIGNER_VERIFIED_INPUTS_SESSION"
SESSION_TOKEN_ENV = "LYRIC_ALIGNER_VERIFIED_INPUTS_TOKEN"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _stat_record(path: Path, expected_sha256: str, expected_size: int) -> dict[str, Any]:
    stat = path.stat()
    if not path.is_file() or stat.st_size != expected_size:
        raise ValueError(f"verified input changed while session was created: {path}")
    return {
        "sha256": expected_sha256,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def create_verified_input_session(
    *,
    manifest_path: Path,
    manifest: dict[str, Any],
    repository_root: Path,
    session_path: Path,
) -> str:
    """Create a fresh same-invocation session after full manifest verification.

    The caller is responsible for calling ``verify_manifest_inputs`` *before*
    this function. The returned token is intentionally not written in plaintext
    to disk; only its SHA-256 is persisted.
    """

    root = repository_root.resolve()
    roles: dict[str, dict[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}
    for role, record in manifest.get("inputs", {}).items():
        if record is None:
            continue
        base = root / str(record["path"])
        kind = str(record["kind"])
        role_files: list[str] = []
        if kind == "file":
            absolute = base.resolve()
            expected_size = int(record["size"])
            files[str(absolute)] = _stat_record(
                absolute,
                str(record["sha256"]),
                expected_size,
            )
            role_files.append(str(absolute))
        elif kind == "directory":
            for item in record.get("files", []):
                absolute = (base / str(item["path"])).resolve()
                files[str(absolute)] = _stat_record(
                    absolute,
                    str(item["sha256"]),
                    int(item["size"]),
                )
                role_files.append(str(absolute))
        else:
            raise ValueError(f"unsupported manifest input kind for {role}: {kind}")
        roles[str(role)] = {
            "kind": kind,
            "sha256": str(record["sha256"]),
            "files": role_files,
        }

    token = secrets.token_hex(32)
    payload = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "task_fingerprint_sha256": str(manifest["task_fingerprint_sha256"]),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": _sha256_file(manifest_path),
        "token_sha256": _token_sha256(token),
        "roles": roles,
        "files": files,
    }
    _atomic_write(session_path, payload)
    return token


def install_verified_input_session(session_path: Path, token: str) -> None:
    os.environ[SESSION_PATH_ENV] = str(session_path.resolve())
    os.environ[SESSION_TOKEN_ENV] = token


def clear_verified_input_session() -> None:
    os.environ.pop(SESSION_PATH_ENV, None)
    os.environ.pop(SESSION_TOKEN_ENV, None)


def _active_session() -> dict[str, Any] | None:
    raw_path = os.environ.get(SESSION_PATH_ENV, "").strip()
    token = os.environ.get(SESSION_TOKEN_ENV, "")
    if not raw_path or not token:
        return None
    path = Path(raw_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != SESSION_SCHEMA_VERSION:
        return None
    expected = str(payload.get("token_sha256") or "")
    if not expected or not secrets.compare_digest(expected, _token_sha256(token)):
        return None
    return payload


def _stat_matches(path: Path, record: dict[str, Any]) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return (
        path.is_file()
        and int(stat.st_size) == int(record.get("size", -1))
        and int(stat.st_mtime_ns) == int(record.get("mtime_ns", -1))
    )


def file_is_attested(path: Path, expected_sha256: str) -> bool:
    """Return True only when the active parent session attests this exact file."""

    payload = _active_session()
    if payload is None:
        return False
    files = payload.get("files")
    if not isinstance(files, dict):
        return False
    resolved = str(path.resolve())
    record = files.get(resolved)
    if not isinstance(record, dict):
        return False
    if str(record.get("sha256")) != str(expected_sha256):
        return False
    return _stat_matches(Path(resolved), record)


def role_is_attested(
    manifest_path: Path,
    manifest: dict[str, Any],
    role: str,
) -> bool:
    """Return True when a fresh session safely covers one manifest input role."""

    payload = _active_session()
    if payload is None:
        return False
    if str(payload.get("manifest_path")) != str(manifest_path.resolve()):
        return False
    if str(payload.get("task_fingerprint_sha256")) != str(
        manifest.get("task_fingerprint_sha256")
    ):
        return False
    try:
        if str(payload.get("manifest_sha256")) != _sha256_file(manifest_path):
            return False
    except OSError:
        return False

    record = manifest.get("inputs", {}).get(role)
    roles = payload.get("roles")
    if record is None or not isinstance(roles, dict):
        return False
    attested = roles.get(role)
    if not isinstance(attested, dict):
        return False
    if str(attested.get("kind")) != str(record.get("kind")):
        return False
    if str(attested.get("sha256")) != str(record.get("sha256")):
        return False

    expected_files = attested.get("files")
    files = payload.get("files")
    if not isinstance(expected_files, list) or not isinstance(files, dict):
        return False
    for value in expected_files:
        file_record = files.get(str(value))
        if not isinstance(file_record, dict):
            return False
        if not _stat_matches(Path(str(value)), file_record):
            return False

    if record.get("kind") == "directory":
        base = (manifest_path.resolve().parents[3] / str(record["path"])).resolve()
        try:
            current = {
                str(item.resolve())
                for item in base.rglob("*")
                if item.is_file()
            }
        except OSError:
            return False
        if current != {str(value) for value in expected_files}:
            return False
    return True
