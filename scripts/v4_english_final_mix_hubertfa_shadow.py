#!/usr/bin/env python3
"""Run the uncalibrated English HuBERTFA direct-final-mix shadow observer."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lyric_aligner.alignment.english_final_mix_hubertfa_shadow import (
    build_adapter_request,
    bind_adapter_response,
    build_shadow_plan,
    evaluate_shadow,
    sha256_file,
)
from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.io.path_safety import validate_artifact_output_tree

DEFAULT_SIDECAR_PYTHON = (
    REPO_ROOT / "private" / "_models" / "hubertfa_sidecar" / "runtime" / ".venv" / "Scripts" / "python.exe"
)
DEFAULT_ADAPTER = REPO_ROOT / "scripts" / "hubertfa_english_final_mix_shadow_adapter.py"


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _resolve_manifest_audio(task_manifest: dict) -> tuple[Path, str]:
    inputs = task_manifest.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("task manifest has no inputs object")
    record = inputs.get("audio")
    if not isinstance(record, dict) or record.get("kind") != "file":
        raise ValueError("task manifest audio binding is invalid")
    raw = str(record.get("path") or "")
    expected = str(record.get("sha256") or "")
    if not raw or len(expected) != 64:
        raise ValueError("task manifest audio binding is incomplete")
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if not path.is_file() or sha256_file(path) != expected:
        raise ValueError("task manifest final audio SHA mismatch")
    return path, expected


def _fresh_paths(out_dir: Path) -> dict[str, Path]:
    resolved = out_dir.resolve()
    names = {
        "plan": resolved / "plan.json",
        "request": resolved / "request.json",
        "response": resolved / "response.json",
        "evaluation": resolved / "evaluation.json",
    }
    if len({str(path).casefold() for path in names.values()}) != len(names):
        raise ValueError("shadow output paths collide")
    for path in names.values():
        if path.exists():
            raise FileExistsError(f"shadow output must be fresh: {path}")
    resolved.mkdir(parents=True, exist_ok=True)
    return names


def _hidden_startup() -> tuple[object | None, int]:
    if os.name != "nt":
        return None, 0
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return startupinfo, creationflags


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-lock", type=Path, required=True)
    parser.add_argument("--track-assets", type=Path, required=True)
    parser.add_argument("--final-audit", type=Path, required=True)
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--sidecar-python", type=Path, default=DEFAULT_SIDECAR_PYTHON)
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    args = parser.parse_args()

    selection_path = args.selection_lock.resolve()
    assets_path = args.track_assets.resolve()
    audit_path = args.final_audit.resolve()
    manifest_path = args.task_manifest.resolve()
    sidecar_python = args.sidecar_python.resolve()
    adapter = args.adapter.resolve()
    for path in (selection_path, assets_path, audit_path, manifest_path, sidecar_python, adapter):
        if not path.is_file():
            raise FileNotFoundError(path)

    selection = _load_json(selection_path)
    assets = _load_json(assets_path)
    manifest = _load_json(manifest_path)
    final_audio, final_audio_sha = _resolve_manifest_audio(manifest)
    if str(selection.get("task_fingerprint_sha256") or "") != str(manifest.get("task_fingerprint_sha256") or ""):
        raise ValueError("selection/task-manifest fingerprint mismatch")
    with audit_path.open("r", encoding="utf-8-sig", newline="") as handle:
        audit_rows = list(csv.DictReader(handle))
    if not audit_rows:
        raise ValueError("final audit is empty")
    info = sf.info(str(final_audio))
    mix_duration_ms = int(round(info.frames * 1000.0 / info.samplerate))
    validate_artifact_output_tree(
        inputs={
            "selection_lock": selection_path,
            "track_assets": assets_path,
            "final_audit": audit_path,
            "task_manifest": manifest_path,
            "final_audio": final_audio,
            "sidecar_python": sidecar_python,
            "adapter": adapter,
        },
        output_dir=args.out_dir.resolve(),
    )
    output_paths = _fresh_paths(args.out_dir)

    plan = build_shadow_plan(
        selection_lock=selection,
        selection_lock_sha256=sha256_file(selection_path),
        track_assets=assets,
        track_assets_sha256=sha256_file(assets_path),
        final_audit_rows=audit_rows,
        final_audit_sha256=sha256_file(audit_path),
        final_audio_path=final_audio,
        final_audio_sha256=final_audio_sha,
        mix_duration_ms=mix_duration_ms,
    )
    atomic_write_json(output_paths["plan"], plan)
    request = build_adapter_request(plan)
    atomic_write_json(output_paths["request"], request)

    startupinfo, creationflags = _hidden_startup()
    completed = subprocess.run(
        [
            str(sidecar_python),
            str(adapter),
            "--request",
            str(output_paths["request"]),
            "--response",
            str(output_paths["response"]),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=startupinfo,
        creationflags=creationflags,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "English HuBERTFA shadow adapter failed "
            f"({completed.returncode}): {completed.stderr[-4000:]}"
        )
    response = _load_json(output_paths["response"])
    bound_response = bind_adapter_response(plan, request, response)
    evaluation = evaluate_shadow(plan, bound_response)
    evaluation["bindings"].update(
        {
            "selection_lock_sha256": sha256_file(selection_path),
            "track_assets_sha256": sha256_file(assets_path),
            "final_audit_sha256": sha256_file(audit_path),
            "task_manifest_sha256": sha256_file(manifest_path),
            "final_audio_sha256": final_audio_sha,
            "response_sha256": sha256_file(output_paths["response"]),
            "adapter_sha256": sha256_file(adapter),
        }
    )
    atomic_write_json(output_paths["evaluation"], evaluation)
    print(
        json.dumps(
            {
                "authority": evaluation["authority"],
                "selected": evaluation["selected_target_count"],
                "prepared": evaluation["prepared_record_count"],
                "aligned": evaluation["aligned_record_count"],
                "out_dir": str(args.out_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
