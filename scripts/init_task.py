#!/usr/bin/env python3
"""Create a fingerprinted local task workspace and scoped QA skeletons."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_contract import (
    build_task_manifest,
    load_task_manifest,
    qa_metadata,
    validate_qa_artifact,
    write_json_atomic,
)
from lyric_aligner.contracts.run_config import (
    RUN_CONFIG_FILENAME,
    build_run_config,
    load_run_config,
    write_run_config_atomic,
)


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
INVALID_TASK_NAME_CHARACTERS = set('<>:"/\\|?*')


def task_name(value: str) -> str:
    if value != value.strip() or value in {"", ".", ".."}:
        raise ValueError("task name must be a non-empty single directory name")
    if Path(value).name != value or any(
        char in value for char in INVALID_TASK_NAME_CHARACTERS
    ):
        raise ValueError("task name must not contain a path or reserved character")
    if (
        value.endswith((".", " "))
        or value.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("task name is reserved on Windows")
    return value


def validate_existing_qa(
    path: Path,
    manifest: dict,
    artifact_type: str,
) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"existing QA file is unreadable: {path}") from exc
    issues = validate_qa_artifact(
        payload,
        manifest,
        str(path),
        artifact_type,
    )
    if issues:
        raise ValueError("; ".join(issues))


def init_task(
    root: Path,
    name: str,
    *,
    source_srt: Path,
    audio: Path,
    song_list: Path,
    lyrics_dir: Path,
    bpm_changes: Path | None = None,
    source_audio_dir: Path | None = None,
    mix_content_extent: Path | None = None,
    profile: Path | None = None,
    language_map: Path | None = None,
    middle_cut_map: Path | None = None,
    lyric_role_map: Path | None = None,
) -> dict[str, str]:
    name = task_name(name)
    if source_srt.suffix.lower() != ".srt":
        raise ValueError(f"source file must use the .srt extension: {source_srt}")
    private_root = root / "private" / name
    input_root = private_root / "input"
    qa_root = private_root / "qa"
    output_root = root / "output" / name
    for directory in (input_root, qa_root, output_root):
        directory.mkdir(parents=True, exist_ok=True)

    manifest_path = qa_root / "task_manifest.json"
    manifest = build_task_manifest(
        root,
        name,
        source_srt=source_srt,
        audio=audio,
        song_list=song_list,
        lyrics_dir=lyrics_dir,
        bpm_changes=bpm_changes,
        source_audio_dir=source_audio_dir,
        mix_content_extent=mix_content_extent,
    )
    if manifest_path.exists():
        existing = load_task_manifest(manifest_path)
        if existing["task_fingerprint_sha256"] != manifest["task_fingerprint_sha256"]:
            raise ValueError(
                "existing task manifest belongs to different inputs; "
                "use a new task name or migrate intentionally"
            )
    else:
        write_json_atomic(manifest_path, manifest)

    run_config_path = qa_root / RUN_CONFIG_FILENAME
    semantic_config_supplied = any(
        value is not None
        for value in (profile, language_map, middle_cut_map, lyric_role_map)
    )
    if run_config_path.exists():
        existing_run_config = load_run_config(
            run_config_path,
            repository_root=root,
            expected_task_fingerprint_sha256=str(manifest["task_fingerprint_sha256"]),
        )
        if semantic_config_supplied:
            desired_run_config = build_run_config(
                root,
                str(manifest["task_fingerprint_sha256"]),
                profile=profile,
                language_map=language_map,
                middle_cut_map=middle_cut_map,
                lyric_role_map=lyric_role_map,
            )
            if (
                existing_run_config["run_config_fingerprint_sha256"]
                != desired_run_config["run_config_fingerprint_sha256"]
            ):
                raise ValueError(
                    "existing v4_run_config.json belongs to different semantic inputs; "
                    "use scripts/init_v4_run_config.py --replace for an intentional config migration"
                )
        effective_run_config = existing_run_config
    else:
        effective_run_config = build_run_config(
            root,
            str(manifest["task_fingerprint_sha256"]),
            profile=profile,
            language_map=language_map,
            middle_cut_map=middle_cut_map,
            lyric_role_map=lyric_role_map,
        )
        write_run_config_atomic(run_config_path, effective_run_config)

    overrides_path = qa_root / f"{name}_manual_overrides.json"
    regression_path = qa_root / f"{name}_regression_cases.json"
    validate_existing_qa(overrides_path, manifest, "manual_overrides")
    validate_existing_qa(regression_path, manifest, "regression_cases")

    if not overrides_path.exists():
        overrides = {
            **qa_metadata(manifest, "manual_overrides"),
            "_insertions": [],
            "_cue_splits": [],
            "_timing_overrides": {},
            "_lrc_indices_overrides": {},
            "_confirmed_omitted_lrc_events": [],
            "_confirmed_boundary_pairs": [],
            "_confirmed_overlap_intervals": [],
            "_cross_track_overlap_reviews": [],
            "_audio_edit_reviews": [],
            "_review_notes": {},
        }
        write_json_atomic(overrides_path, overrides)
    if not regression_path.exists():
        regression = {
            **qa_metadata(manifest, "regression_cases"),
            "cases": [],
        }
        write_json_atomic(regression_path, regression)
    return {
        "input": str(input_root),
        "qa": str(qa_root),
        "output": str(output_root),
        "task_manifest": str(manifest_path),
        "task_fingerprint_sha256": str(manifest["task_fingerprint_sha256"]),
        "v4_run_config": str(run_config_path),
        "run_config_fingerprint_sha256": str(
            effective_run_config["run_config_fingerprint_sha256"]
        ),
        "source_srt_sha256": str(manifest["inputs"]["source_srt"]["sha256"]),
        "manual_overrides": str(overrides_path),
        "regression_cases": str(regression_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Single local task directory name.")
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--song-list", required=True, type=Path)
    parser.add_argument("--lyrics-dir", required=True, type=Path)
    parser.add_argument("--bpm-changes", type=Path)
    parser.add_argument("--source-audio-dir", type=Path)
    parser.add_argument("--mix-content-extent", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--language-map", type=Path)
    parser.add_argument("--middle-cut-map", type=Path)
    parser.add_argument("--lyric-role-map", type=Path)
    parser.add_argument("--root", default=Path("."), type=Path)
    args = parser.parse_args()
    try:
        result = init_task(
            args.root.resolve(),
            args.task,
            source_srt=args.source_srt.resolve(),
            audio=args.audio.resolve(),
            song_list=args.song_list.resolve(),
            lyrics_dir=args.lyrics_dir.resolve(),
            bpm_changes=args.bpm_changes.resolve() if args.bpm_changes else None,
            source_audio_dir=(
                args.source_audio_dir.resolve() if args.source_audio_dir else None
            ),
            mix_content_extent=(
                args.mix_content_extent.resolve() if args.mix_content_extent else None
            ),
            profile=args.profile.resolve() if args.profile else None,
            language_map=args.language_map.resolve() if args.language_map else None,
            middle_cut_map=(
                args.middle_cut_map.resolve() if args.middle_cut_map else None
            ),
            lyric_role_map=(
                args.lyric_role_map.resolve() if args.lyric_role_map else None
            ),
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
