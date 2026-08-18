#!/usr/bin/env python3
"""Run one v4 child CLI with same-invocation verified-input acceleration.

This is an internal production-run bootstrap. It never creates trust by itself:
without a valid fresh parent verification session, every patched path falls back
to the original full SHA-256 validation behavior.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"
for value in (str(REPOSITORY_ROOT), str(SCRIPTS_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

import task_contract
from lyric_aligner.assets import bindings as asset_bindings
from lyric_aligner.assets import resolver as asset_resolver
from lyric_aligner.contracts import verification_session
from lyric_aligner.contracts.verification_session import (
    file_is_attested,
    role_is_attested,
)


_ORIGINAL_VERIFY_MANIFEST_INPUTS = task_contract.verify_manifest_inputs
_ORIGINAL_BINDINGS_FROM_PAYLOAD = asset_bindings.bindings_from_payload
_ORIGINAL_RESOLVER_SHA256 = asset_resolver.sha256_file


def _verified_manifest_inputs(
    manifest_path: Path,
    manifest: dict,
    roles: tuple[str, ...] | None = None,
) -> list[str]:
    selected = roles or tuple(manifest["inputs"])
    untrusted = tuple(
        role
        for role in selected
        if manifest["inputs"].get(role) is not None
        and not role_is_attested(manifest_path, manifest, role)
    )
    if not untrusted:
        return []
    return _ORIGINAL_VERIFY_MANIFEST_INPUTS(
        manifest_path,
        manifest,
        untrusted,
    )


def _all_binding_files_attested(payload: dict) -> bool:
    for asset in payload.get("assets", []):
        try:
            source_path = Path(str(asset["source_audio_path"]))
            source_sha = str(asset["source_audio_sha256"])
            lyric_path = Path(str(asset["canonical_lyric_path"]))
            lyric_sha = str(asset["canonical_lyric_sha256"])
        except (KeyError, TypeError, ValueError):
            return False
        if not file_is_attested(source_path, source_sha):
            return False
        if not file_is_attested(lyric_path, lyric_sha):
            return False
    return True


def _verified_bindings_from_payload(
    payload: dict,
    *,
    verify_files: bool = False,
):
    if verify_files and _all_binding_files_attested(payload):
        return _ORIGINAL_BINDINGS_FROM_PAYLOAD(payload, verify_files=False)
    return _ORIGINAL_BINDINGS_FROM_PAYLOAD(payload, verify_files=verify_files)


def _verified_resolver_sha256(path: Path) -> str:
    """Return the parent-attested SHA or fall back to a real file read."""

    payload = verification_session._active_session()
    if payload is not None:
        files = payload.get("files")
        if isinstance(files, dict):
            record = files.get(str(path.resolve()))
            if (
                isinstance(record, dict)
                and verification_session._stat_matches(path.resolve(), record)
            ):
                digest = str(record.get("sha256") or "")
                if len(digest) == 64:
                    return digest
    return _ORIGINAL_RESOLVER_SHA256(path)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: v4_child_exec.py TARGET_SCRIPT [args ...]")
    target = Path(sys.argv[1]).resolve()
    try:
        target.relative_to(SCRIPTS_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit("target script must stay inside repository scripts/") from exc
    if not target.is_file() or target.suffix != ".py":
        raise SystemExit(f"invalid target script: {target}")
    if target == Path(__file__).resolve():
        raise SystemExit("v4_child_exec.py cannot execute itself")

    # assert_manifest_paths resolves verify_manifest_inputs from its defining
    # module at call time, so replacing the module global accelerates it too.
    task_contract.verify_manifest_inputs = _verified_manifest_inputs
    asset_bindings.bindings_from_payload = _verified_bindings_from_payload
    asset_resolver.sha256_file = _verified_resolver_sha256

    sys.argv = [str(target), *sys.argv[2:]]
    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
