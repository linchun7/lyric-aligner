#!/usr/bin/env python3
"""Execution optimizer wrapped around the unchanged v4 production core."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for value in (str(ROOT), str(SCRIPTS)):
    if value not in sys.path:
        sys.path.insert(0, value)

import librosa
import task_contract
from lyric_aligner.audio.fine_alignment import should_run_fine_alignment
from lyric_aligner.contracts.verification_session import (
    clear_verified_input_session,
    create_verified_input_session,
    file_is_attested,
    install_verified_input_session,
    role_is_attested,
)
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.pipeline.production import build_production_plan
from lyric_aligner.pipeline.verified_stage_runner import VerifiedStageRunner

SPEC = importlib.util.spec_from_file_location("_v4_run_core", SCRIPTS / "v4_run_legacy.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load v4_run_legacy.py")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-manifest", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--profile", type=Path)
    p.add_argument("--language-map", type=Path)
    p.add_argument("--middle-cut-map", type=Path)
    p.add_argument("--lyric-role-map", type=Path)
    p.add_argument("--git-commit", default="")
    p.add_argument("--workers", type=int, default=2, help="Independent stage workers, 1-4 (default 2).")
    p.add_argument("--no-resume", action="store_true", help="Force expensive stages to execute again.")
    return p


def legacy_argv(a: argparse.Namespace) -> list[str]:
    result = [str(SCRIPTS / "v4_run.py"), "--task-manifest", str(a.task_manifest), "--out-dir", str(a.out_dir)]
    for flag, value in (("--profile", a.profile), ("--language-map", a.language_map), ("--middle-cut-map", a.middle_cut_map), ("--lyric-role-map", a.lyric_role_map)):
        if value is not None:
            result += [flag, str(value)]
    if a.git_commit:
        result += ["--git-commit", a.git_commit]
    return result


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def assert_resume_git_identity(git_commit: str) -> None:
    """Make producer git identity trustworthy before it can authorize reuse."""

    if not git_commit:
        return
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0:
        raise ValueError("--git-commit requires an accessible git worktree for safe resume")
    actual = head.stdout.strip()
    if git_commit.strip() != actual:
        raise ValueError(
            "--git-commit must exactly match the currently checked-out HEAD for safe resume"
        )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        raise ValueError("cannot verify git worktree cleanliness for safe resume")
    if status.stdout.strip():
        raise ValueError(
            "--git-commit requires a clean git worktree; omit --git-commit to run without cross-run resume"
        )


def _stat_identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    if not path.is_file():
        raise ValueError(f"task input is no longer a file: {path}")
    return int(stat.st_size), int(stat.st_mtime_ns)


def manifest_stat_snapshot(manifest_path: Path, manifest: dict) -> dict[str, tuple[int, int]]:
    """Capture cheap task-input identity around the one full hash verification.

    This closes the accidental TOCTOU window between the parent's SHA-256 pass
    and creation of the same-invocation verification session without adding a
    second content read. Directory membership is included so added/removed files
    are detected as well as size/mtime changes.
    """

    root = task_contract.manifest_root(manifest_path)
    snapshot: dict[str, tuple[int, int]] = {
        str(manifest_path.resolve()): _stat_identity(manifest_path.resolve())
    }
    for record in manifest.get("inputs", {}).values():
        if record is None:
            continue
        base = (root / str(record["path"])).resolve()
        if record.get("kind") == "file":
            snapshot[str(base)] = _stat_identity(base)
            continue
        if record.get("kind") != "directory":
            raise ValueError(f"unsupported task input kind: {record.get('kind')!r}")
        if not base.is_dir():
            raise ValueError(f"task input directory no longer exists: {base}")
        for item in sorted(base.rglob("*"), key=lambda value: value.as_posix().casefold()):
            if item.is_file():
                snapshot[str(item.resolve())] = _stat_identity(item.resolve())
    return snapshot


def asset_files_attested(payload: dict) -> bool:
    assets = payload.get("assets", [])
    if not isinstance(assets, list) or not assets:
        return False
    for item in assets:
        if not isinstance(item, dict):
            return False
        try:
            if not file_is_attested(Path(str(item["source_audio_path"])), str(item["source_audio_sha256"])):
                return False
            if not file_is_attested(Path(str(item["canonical_lyric_path"])), str(item["canonical_lyric_sha256"])):
                return False
        except KeyError:
            return False
    return True


def verified_manifest_inputs(manifest_path: Path, manifest: dict, roles: tuple[str, ...] | None = None) -> list[str]:
    selected = roles or tuple(manifest["inputs"])
    remaining = tuple(role for role in selected if manifest["inputs"].get(role) is not None and not role_is_attested(manifest_path, manifest, role))
    return task_contract.verify_manifest_inputs(manifest_path, manifest, remaining) if remaining else []


def fast_context(**kwargs):
    if kwargs.get("verify_asset_files") and asset_files_attested(kwargs["track_assets_payload"]):
        kwargs["verify_asset_files"] = False
    return build_pipeline_context(**kwargs)


def fine_command(a, mix, assets, asset_artifact, coarse, coarse_artifact, out, artifact_out):
    command = [sys.executable, str(SCRIPTS / "v4_fine_align.py"), "--task-manifest", str(a.task_manifest), "--mix-audio", str(mix), "--track-assets", str(assets), "--asset-artifact", str(asset_artifact), "--coarse", str(coarse), "--coarse-artifact", str(coarse_artifact), "--out", str(out), "--artifact-out", str(artifact_out)]
    if a.git_commit:
        command += ["--git-commit", a.git_commit]
    return command


def probe_command(a, assets, asset_artifact, left, left_artifact, right, right_artifact, out, artifact_out):
    command = [sys.executable, str(SCRIPTS / "v4_probe_transition.py"), "--task-manifest", str(a.task_manifest), "--track-assets", str(assets), "--asset-artifact", str(asset_artifact), "--left-coarse", str(left), "--left-artifact", str(left_artifact), "--right-coarse", str(right), "--right-artifact", str(right_artifact), "--out", str(out), "--artifact-out", str(artifact_out)]
    if a.git_commit:
        command += ["--git-commit", a.git_commit]
    return command


def prestage(a: argparse.Namespace) -> VerifiedStageRunner:
    if a.workers < 1 or a.workers > 4:
        raise ValueError("workers must be between 1 and 4")
    clear_verified_input_session()
    assert_resume_git_identity(a.git_commit)
    manifest = task_contract.load_task_manifest(a.task_manifest)
    before_verify = manifest_stat_snapshot(a.task_manifest, manifest)
    problems = task_contract.verify_manifest_inputs(a.task_manifest, manifest)
    if problems:
        raise ValueError("task manifest validation failed: " + "; ".join(problems))
    if manifest_stat_snapshot(a.task_manifest, manifest) != before_verify:
        raise ValueError("task inputs changed while the parent verification pass was running")
    fingerprint = str(manifest["task_fingerprint_sha256"])
    out_dir = a.out_dir.resolve()
    cache = out_dir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    session = cache / "verified-inputs-session.json"
    token = create_verified_input_session(manifest_path=a.task_manifest, manifest=manifest, repository_root=task_contract.manifest_root(a.task_manifest), session_path=session)
    if manifest_stat_snapshot(a.task_manifest, manifest) != before_verify:
        raise ValueError("task inputs changed while the verified-input session was created")
    install_verified_input_session(session, token)
    runner = VerifiedStageRunner(repository_root=ROOT, task_fingerprint_sha256=fingerprint, git_commit=a.git_commit, workers=a.workers, resume=not a.no_resume)

    mix = CORE._manifest_path(a.task_manifest, manifest, "audio")
    song_list = CORE._manifest_path(a.task_manifest, manifest, "song_list")
    lyrics = CORE._manifest_path(a.task_manifest, manifest, "lyrics_dir")
    sources = CORE._manifest_path(a.task_manifest, manifest, "source_audio_dir")
    asset_dir, primary_dir, transitions_dir = out_dir / "assets", out_dir / "primary", out_dir / "transitions"
    for directory in (asset_dir, primary_dir, transitions_dir, out_dir / "timelines"):
        directory.mkdir(parents=True, exist_ok=True)
    assets, asset_artifact = asset_dir / "track_assets.json", asset_dir / "track_assets.artifact.json"
    resolve = [sys.executable, str(SCRIPTS / "v4_resolve_assets.py"), "--task-manifest", str(a.task_manifest), "--song-list", str(song_list), "--lyrics-dir", str(lyrics), "--source-dir", str(sources), "--out", str(assets), "--artifact-out", str(asset_artifact)]
    for flag, value in (("--profile", a.profile), ("--language-map", a.language_map), ("--middle-cut-map", a.middle_cut_map), ("--lyric-role-map", a.lyric_role_map)):
        CORE._append_optional(resolve, flag, value)
    if a.git_commit:
        resolve += ["--git-commit", a.git_commit]
    runner.run(resolve, allow_resume=False)

    assets_payload, artifact_payload = CORE._validate_asset_output(assets, asset_artifact)
    context = fast_context(expected_task_fingerprint=fingerprint, track_assets_payload=assets_payload, asset_artifact=artifact_payload, verify_asset_files=True)
    plan = build_production_plan(context.bindings, mix_duration=float(librosa.get_duration(path=str(mix))), transition_margin_seconds=context.profile.transition.search_margin_seconds)

    primary: dict[str, tuple[Path, Path]] = {}
    commands = []
    for item in plan.occurrences:
        safe = CORE._safe(item.occurrence_id)
        paths = (primary_dir / f"{safe}.coarse.json", primary_dir / f"{safe}.coarse.artifact.json")
        primary[item.occurrence_id] = paths
        commands.append(CORE._coarse_command(task_manifest=a.task_manifest, mix_audio=mix, track_assets=assets, asset_artifact=asset_artifact, occurrence_id=item.occurrence_id, out=paths[0], artifact_out=paths[1], git_commit=a.git_commit, mix_start=item.primary_start, mix_end=item.primary_end))
    runner.run_many(commands)

    commands = []
    for item in plan.occurrences:
        coarse, coarse_artifact = primary[item.occurrence_id]
        if not should_run_fine_alignment(load(coarse)):
            continue
        safe = CORE._safe(item.occurrence_id)
        commands.append(fine_command(a, mix, assets, asset_artifact, coarse, coarse_artifact, primary_dir / f"{safe}.fine.json", primary_dir / f"{safe}.fine.artifact.json"))
    runner.run_many(commands)

    records = []
    commands = []
    for index, transition in enumerate(plan.transitions, 1):
        stage = transitions_dir / f"{index:02d}_{CORE._safe(transition.left_occurrence_id)}__{CORE._safe(transition.right_occurrence_id)}"
        stage.mkdir(parents=True, exist_ok=True)
        left, left_artifact = stage / "left.coarse.json", stage / "left.coarse.artifact.json"
        right, right_artifact = stage / "right.coarse.json", stage / "right.coarse.artifact.json"
        records.append((stage, left, left_artifact, right, right_artifact))
        commands += [
            CORE._coarse_command(task_manifest=a.task_manifest, mix_audio=mix, track_assets=assets, asset_artifact=asset_artifact, occurrence_id=transition.left_occurrence_id, out=left, artifact_out=left_artifact, git_commit=a.git_commit, mix_start=transition.search_start, mix_end=transition.search_end),
            CORE._coarse_command(task_manifest=a.task_manifest, mix_audio=mix, track_assets=assets, asset_artifact=asset_artifact, occurrence_id=transition.right_occurrence_id, out=right, artifact_out=right_artifact, git_commit=a.git_commit, mix_start=transition.search_start, mix_end=transition.search_end),
        ]
    runner.run_many(commands)
    runner.run_many([probe_command(a, assets, asset_artifact, left, left_artifact, right, right_artifact, stage / "transition.json", stage / "transition.artifact.json") for stage, left, left_artifact, right, right_artifact in records])
    return runner


def write_summary(out_dir: Path, runner: VerifiedStageRunner) -> None:
    path = out_dir.resolve() / "cache" / "execution_summary.json"
    data = {"schema_version": "1.0", "scope": "disposable_execution_optimization_only", **runner.summary().to_dict()}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    p = parser()
    a = p.parse_args()
    try:
        try:
            runner = prestage(a)
        except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            p.error(str(exc))
        CORE._run = runner.run
        CORE.verify_manifest_inputs = verified_manifest_inputs
        CORE.build_pipeline_context = fast_context
        original = sys.argv
        try:
            sys.argv = legacy_argv(a)
            result = CORE.main()
        finally:
            sys.argv = original
        write_summary(a.out_dir, runner)
        print(json.dumps({"execution_optimization": runner.summary().to_dict()}), file=sys.stderr)
        return result
    finally:
        clear_verified_input_session()


if __name__ == "__main__":
    raise SystemExit(main())
