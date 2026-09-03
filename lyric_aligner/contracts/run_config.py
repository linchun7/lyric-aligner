"""Task-bound semantic run configuration for Full V4 production runs.

The task manifest fingerprints raw task inputs. A V4 run config separately binds
optional semantic inputs that affect asset resolution but may be created after the
raw task itself, such as language or lyric-role maps. Public V4 run entrypoints
auto-discover the task-local config and expand it into the existing CLI flags
before any output-tree mutation occurs.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

RUN_CONFIG_SCHEMA_VERSION = "v4-run-config-1.0"
RUN_CONFIG_FILENAME = "v4_run_config.json"
RUN_CONFIG_FLAG = "--run-config"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SEMANTIC_INPUT_FLAGS: tuple[tuple[str, str], ...] = (
    ("profile", "--profile"),
    ("language_map", "--language-map"),
    ("middle_cut_map", "--middle-cut-map"),
    ("lyric_role_map", "--lyric-role-map"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _repository_relative(path: Path, repository_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repository_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"run config input must stay inside the repository: {resolved}") from exc


def _file_record(path: Path, repository_root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"run config input file does not exist: {resolved}")
    return {
        "path": _repository_relative(resolved, repository_root),
        "size": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _fingerprint_payload(
    task_fingerprint_sha256: str,
    semantic_inputs: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for role, _flag in SEMANTIC_INPUT_FLAGS:
        record = semantic_inputs.get(role)
        compact[role] = (
            None
            if record is None
            else {
                "size": int(record["size"]),
                "sha256": str(record["sha256"]),
            }
        )
    return {
        "schema_version": RUN_CONFIG_SCHEMA_VERSION,
        "task_fingerprint_sha256": task_fingerprint_sha256,
        "semantic_inputs": compact,
    }


def build_run_config(
    repository_root: Path,
    task_fingerprint_sha256: str,
    *,
    profile: Path | None = None,
    language_map: Path | None = None,
    middle_cut_map: Path | None = None,
    lyric_role_map: Path | None = None,
) -> dict[str, Any]:
    fingerprint = str(task_fingerprint_sha256).lower()
    if not HASH_RE.fullmatch(fingerprint):
        raise ValueError("task_fingerprint_sha256 must be a lowercase SHA-256")
    values = {
        "profile": profile,
        "language_map": language_map,
        "middle_cut_map": middle_cut_map,
        "lyric_role_map": lyric_role_map,
    }
    semantic_inputs = {
        role: (_file_record(values[role], repository_root) if values[role] is not None else None)
        for role, _flag in SEMANTIC_INPUT_FLAGS
    }
    run_fingerprint = _canonical_json_sha256(
        _fingerprint_payload(fingerprint, semantic_inputs)
    )
    return {
        "schema_version": RUN_CONFIG_SCHEMA_VERSION,
        "task_fingerprint_sha256": fingerprint,
        "run_config_fingerprint_sha256": run_fingerprint,
        "semantic_inputs": semantic_inputs,
    }


def validate_run_config_schema(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["run config must contain a JSON object"]
    issues: list[str] = []
    if payload.get("schema_version") != RUN_CONFIG_SCHEMA_VERSION:
        issues.append(f"run config schema_version must be {RUN_CONFIG_SCHEMA_VERSION}")
    task_fingerprint = str(payload.get("task_fingerprint_sha256", "")).lower()
    if not HASH_RE.fullmatch(task_fingerprint):
        issues.append("run config task_fingerprint_sha256 must be a lowercase SHA-256")
    run_fingerprint = str(payload.get("run_config_fingerprint_sha256", "")).lower()
    if not HASH_RE.fullmatch(run_fingerprint):
        issues.append("run config run_config_fingerprint_sha256 must be a lowercase SHA-256")
    semantic_inputs = payload.get("semantic_inputs")
    if not isinstance(semantic_inputs, dict):
        issues.append("run config semantic_inputs must be an object")
        return issues
    expected_roles = {role for role, _flag in SEMANTIC_INPUT_FLAGS}
    unexpected = sorted(set(semantic_inputs) - expected_roles)
    missing = sorted(expected_roles - set(semantic_inputs))
    if unexpected:
        issues.append("run config contains unsupported semantic inputs: " + ", ".join(unexpected))
    if missing:
        issues.append("run config is missing semantic inputs: " + ", ".join(missing))
    for role, _flag in SEMANTIC_INPUT_FLAGS:
        record = semantic_inputs.get(role)
        if record is None:
            continue
        if not isinstance(record, dict):
            issues.append(f"run config semantic input {role} must be an object or null")
            continue
        path = record.get("path")
        if (
            not isinstance(path, str)
            or not path.strip()
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            issues.append(f"run config semantic input {role} has invalid repository-relative path")
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            issues.append(f"run config semantic input {role} has invalid size")
        digest = str(record.get("sha256", "")).lower()
        if not HASH_RE.fullmatch(digest):
            issues.append(f"run config semantic input {role} has invalid sha256")
    if not issues:
        expected = _canonical_json_sha256(
            _fingerprint_payload(task_fingerprint, semantic_inputs)
        )
        if run_fingerprint != expected:
            issues.append("run config fingerprint does not match its semantic inputs")
    return issues


def load_run_config(
    path: Path,
    *,
    repository_root: Path,
    expected_task_fingerprint_sha256: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"run config is unreadable: {path}") from exc
    issues = validate_run_config_schema(payload)
    if not issues:
        if str(payload["task_fingerprint_sha256"]) != str(expected_task_fingerprint_sha256):
            issues.append("run config is bound to a different task fingerprint")
        for role, _flag in SEMANTIC_INPUT_FLAGS:
            record = payload["semantic_inputs"].get(role)
            if record is None:
                continue
            resolved = (repository_root / str(record["path"])).resolve()
            try:
                resolved.relative_to(repository_root.resolve())
            except ValueError:
                issues.append(f"run config semantic input {role} escapes the repository")
                continue
            if not resolved.is_file():
                issues.append(f"run config semantic input {role} does not exist")
                continue
            if resolved.stat().st_size != int(record["size"]):
                issues.append(f"run config semantic input {role} size differs from recorded value")
                continue
            if _sha256(resolved) != str(record["sha256"]):
                issues.append(f"run config semantic input {role} content differs from recorded value")
    if issues:
        raise ValueError("run config validation failed: " + "; ".join(issues))
    return payload


def default_run_config_path(task_manifest: Path) -> Path:
    resolved = task_manifest.resolve()
    if resolved.name != "task_manifest.json" or resolved.parent.name != "qa":
        raise ValueError("task manifest must be stored at private/<task>/qa/task_manifest.json")
    return resolved.parent / RUN_CONFIG_FILENAME


def _option_value(argv: Sequence[str], flag: str) -> str | None:
    found: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == flag:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise ValueError(f"{flag} requires a value")
            found.append(argv[index + 1])
            index += 2
            continue
        prefix = flag + "="
        if token.startswith(prefix):
            value = token[len(prefix) :]
            if not value:
                raise ValueError(f"{flag} requires a value")
            found.append(value)
        index += 1
    if len(found) > 1:
        raise ValueError(f"{flag} must not be provided more than once")
    return found[0] if found else None


def _load_task_fingerprint(task_manifest: Path) -> str:
    try:
        payload = json.loads(task_manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"task manifest is unreadable: {task_manifest}") from exc
    if not isinstance(payload, dict):
        raise ValueError("task manifest must contain a JSON object")
    fingerprint = str(payload.get("task_fingerprint_sha256", "")).lower()
    if not HASH_RE.fullmatch(fingerprint):
        raise ValueError("task manifest has invalid task_fingerprint_sha256")
    return fingerprint


def expand_run_config_argv(
    argv: Sequence[str],
    *,
    repository_root: Path,
) -> list[str]:
    """Auto-discover and validate a task-local semantic run config.

    If ``private/<task>/qa/v4_run_config.json`` exists, it becomes authoritative.
    Existing explicit semantic flags are accepted only when they point to the
    exact files recorded by that config. The returned argv retains ``--run-config``
    so output-tree safety can protect the config file itself before the wrapper
    strips that control argument for the unchanged production parser.
    """

    values = list(argv)
    if any(flag in values for flag in ("-h", "--help")):
        return values
    manifest_value = _option_value(values, "--task-manifest")
    if manifest_value is None:
        return values
    task_manifest = Path(manifest_value).resolve()
    expected_config = default_run_config_path(task_manifest)
    explicit_config = _option_value(values, RUN_CONFIG_FLAG)
    if explicit_config is not None:
        config_path = Path(explicit_config).resolve()
        if config_path != expected_config:
            raise ValueError(
                f"--run-config must use the task-local path {expected_config}"
            )
    elif expected_config.is_file():
        config_path = expected_config
        values.extend([RUN_CONFIG_FLAG, str(config_path)])
    else:
        return values

    task_fingerprint = _load_task_fingerprint(task_manifest)
    payload = load_run_config(
        config_path,
        repository_root=repository_root,
        expected_task_fingerprint_sha256=task_fingerprint,
    )
    semantic_inputs = payload["semantic_inputs"]
    for role, flag in SEMANTIC_INPUT_FLAGS:
        supplied = _option_value(values, flag)
        record = semantic_inputs[role]
        if record is None:
            if supplied is not None:
                raise ValueError(
                    f"{flag} was supplied but task run config records {role}=null; update v4_run_config.json intentionally"
                )
            continue
        expected_path = (repository_root / str(record["path"])).resolve()
        if supplied is None:
            values.extend([flag, str(expected_path)])
        elif Path(supplied).resolve() != expected_path:
            raise ValueError(
                f"{flag} differs from task run config; update v4_run_config.json intentionally"
            )

    return values


def strip_run_config_control_argv(argv: Sequence[str]) -> list[str]:
    """Remove wrapper-only ``--run-config`` after output-tree safety has run."""

    stripped: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == RUN_CONFIG_FLAG:
            if index + 1 >= len(argv):
                raise ValueError("--run-config requires a value")
            index += 2
            continue
        if token.startswith(RUN_CONFIG_FLAG + "="):
            index += 1
            continue
        stripped.append(token)
        index += 1
    return stripped


def write_run_config_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
