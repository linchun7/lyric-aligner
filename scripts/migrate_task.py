#!/usr/bin/env python3
"""Create a schema-2 task manifest and migrate existing QA JSON safely."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from task_contract import (
    build_task_manifest,
    migrate_qa_payload,
    write_json_atomic,
)


def backup_once(path: Path) -> Path:
    backup = path.with_name(path.name + ".schema1.bak")
    if backup.exists():
        raise FileExistsError(f"migration backup already exists: {backup}")
    shutil.copy2(path, backup)
    return backup


def migrate_file(
    path: Path,
    manifest: dict,
    artifact_type: str,
) -> str:
    if not path.exists():
        return "missing"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    migrated = migrate_qa_payload(payload, manifest, artifact_type)
    if migrated == payload:
        return "already-current"
    backup_once(path)
    write_json_atomic(path, migrated)
    return "migrated"


def prepare_migration(
    path: Path,
    manifest: dict,
    artifact_type: str,
) -> tuple[str, dict | None]:
    if not path.exists():
        return "missing", None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    migrated = migrate_qa_payload(payload, manifest, artifact_type)
    return ("already-current", None) if migrated == payload else ("migrated", migrated)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--song-list", required=True, type=Path)
    parser.add_argument("--lyrics-dir", required=True, type=Path)
    parser.add_argument("--bpm-changes", type=Path)
    parser.add_argument("--source-audio-dir", type=Path)
    parser.add_argument("--manual-overrides", required=True, type=Path)
    parser.add_argument(
        "--regression-cases", required=True, action="append", type=Path
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", default=Path("."), type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        manifest = build_task_manifest(
            root,
            args.task,
            source_srt=args.source_srt.resolve(),
            audio=args.audio.resolve(),
            song_list=args.song_list.resolve(),
            lyrics_dir=args.lyrics_dir.resolve(),
            bpm_changes=args.bpm_changes.resolve() if args.bpm_changes else None,
            source_audio_dir=(
                args.source_audio_dir.resolve() if args.source_audio_dir else None
            ),
        )
        if args.manifest.exists():
            raise FileExistsError(f"task manifest already exists: {args.manifest}")
        planned = [
            (
                args.manual_overrides,
                "manual_overrides",
                *prepare_migration(
                    args.manual_overrides, manifest, "manual_overrides"
                ),
            ),
            *[
                (
                    path,
                    "regression_cases",
                    *prepare_migration(path, manifest, "regression_cases"),
                )
                for path in args.regression_cases
            ],
        ]
        for path, _, status, _ in planned:
            if status == "migrated" and path.with_name(
                path.name + ".schema1.bak"
            ).exists():
                raise FileExistsError(
                    f"migration backup already exists: {path.name}.schema1.bak"
                )

        originals = {
            path: path.read_bytes()
            for path, _, status, _ in planned
            if status == "migrated"
        }
        created_backups: list[Path] = []
        try:
            for path, _, status, migrated in planned:
                if status != "migrated" or migrated is None:
                    continue
                created_backups.append(backup_once(path))
                write_json_atomic(path, migrated)
            write_json_atomic(args.manifest, manifest)
        except OSError:
            for path, content in originals.items():
                path.write_bytes(content)
            for backup in created_backups:
                backup.unlink(missing_ok=True)
            args.manifest.unlink(missing_ok=True)
            raise
        result = {
            "manifest": str(args.manifest),
            "task_fingerprint_sha256": manifest["task_fingerprint_sha256"],
            "manual_overrides": planned[0][2],
            "regression_cases": {
                str(path): status for path, _, status, _ in planned[1:]
            },
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
