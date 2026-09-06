#!/usr/bin/env python3
"""Deterministically select unused real source/adjusted-track pairs for Independent Fine holdout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json, sha256_file
from scripts.v4_evaluate_independent_fine_benchmark import _load_hashed
from scripts.v4_run_independent_fine_benchmark import load_manifest
from scripts.v4_freeze_independent_fine_local_support_policy import POLICY_SCHEMA_VERSION

SELECTION_SCHEMA_VERSION = "independent-fine-holdout-pair-selection-1.0"
BPM_SUFFIX_RE = re.compile(
    r"^(?P<source_bpm>\d+(?:\.\d+)?)-(?P<target_bpm>\d+(?:\.\d+)?) -  - 输出 - Stereo Out$"
)


def _parse_adjusted_stem(stem: str, source_titles: list[str]) -> tuple[str, float, float] | None:
    matches: list[tuple[str, float, float]] = []
    for title in sorted(source_titles, key=len, reverse=True):
        if not stem.startswith(title):
            continue
        suffix = stem[len(title):]
        match = BPM_SUFFIX_RE.fullmatch(suffix)
        if match is None:
            continue
        matches.append((title, float(match.group("source_bpm")), float(match.group("target_bpm"))))
    if not matches:
        return None
    longest = max(len(item[0]) for item in matches)
    narrowed = [item for item in matches if len(item[0]) == longest]
    if len(narrowed) != 1:
        raise IndependentFineHoldoutSelectionError(f"ambiguous adjusted/source title match: {stem}")
    return narrowed[0]


class IndependentFineHoldoutSelectionError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _repo_relative(path: Path) -> str:
    resolved = path.resolve()
    root = REPOSITORY_ROOT.resolve()
    if resolved == root or root not in resolved.parents:
        raise IndependentFineHoldoutSelectionError("holdout audio path escapes repository")
    return resolved.relative_to(root).as_posix()


def select_pairs(
    *,
    source_dir: Path,
    adjusted_dir: Path,
    calibration_manifest_path: Path,
    frozen_policy_path: Path,
    pair_count: int,
) -> dict[str, Any]:
    if pair_count < 4:
        raise IndependentFineHoldoutSelectionError("holdout pair_count must be at least four")
    calibration = load_manifest(calibration_manifest_path, expected_partition="calibration")
    policy = _load_hashed(frozen_policy_path)
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise IndependentFineHoldoutSelectionError("unexpected frozen policy schema")
    if policy.get("holdout_status") != "not_run_at_policy_freeze":
        raise IndependentFineHoldoutSelectionError("frozen policy is not in pre-holdout state")
    if bool(policy.get("production_authoritative")) or bool(policy.get("automatic_mutation_allowed")):
        raise IndependentFineHoldoutSelectionError("frozen policy must remain non-authoritative")
    if not source_dir.is_dir() or not adjusted_dir.is_dir():
        raise IndependentFineHoldoutSelectionError("source/adjusted directory is missing")

    used_source_shas = {str(row["source_sha256"]) for row in calibration["records"]}
    source_files = {path.stem: path for path in source_dir.glob("*.flac")}
    source_titles = list(source_files)
    candidates: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for adjusted in sorted(adjusted_dir.glob("*.wav"), key=lambda path: path.name):
        stem = adjusted.stem
        if "更" in stem or "Gee" in stem:
            continue
        parsed = _parse_adjusted_stem(stem, source_titles)
        if parsed is None:
            continue
        title, source_bpm, target_bpm = parsed
        if title in seen_titles:
            raise IndependentFineHoldoutSelectionError(f"duplicate adjusted title: {title}")
        source = source_files[title]
        source_sha = sha256_file(source)
        if source_sha in used_source_shas:
            continue
        if source_bpm <= 0 or target_bpm <= 0:
            raise IndependentFineHoldoutSelectionError("parsed BPM must be positive")
        seen_titles.add(title)
        candidates.append({
            "title": title,
            "source_path": _repo_relative(source),
            "source_sha256": source_sha,
            "adjusted_path": _repo_relative(adjusted),
            "adjusted_sha256": sha256_file(adjusted),
            "source_bpm": source_bpm,
            "target_bpm": target_bpm,
            "nominal_slope": target_bpm / source_bpm,
        })

    candidates.sort(key=lambda row: str(row["title"]))
    if len(candidates) < pair_count:
        raise IndependentFineHoldoutSelectionError("not enough eligible unused pairs for holdout")
    selected = candidates[:pair_count]
    artifact: dict[str, Any] = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "partition": "holdout",
        "selection_policy": (
            "unused_exact_flac_wav_title_pairs; exclude_Gee_and_names_containing_更; "
            "exclude_calibration_source_sha; sort_title_codepoint; take_first_n"
        ),
        "requested_pair_count": pair_count,
        "selected_pair_count": len(selected),
        "eligible_pair_count": len(candidates),
        "calibration_manifest_sha256": calibration["manifest_sha256"],
        "frozen_policy_sha256": policy["artifact_sha256"],
        "records": selected,
        "independent_fine_predictions_consulted": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--adjusted-dir", type=Path, required=True)
    parser.add_argument("--calibration-manifest", type=Path, required=True)
    parser.add_argument("--frozen-policy", type=Path, required=True)
    parser.add_argument("--pair-count", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"holdout pair selection output already exists: {args.out}")
    artifact = select_pairs(
        source_dir=args.source_dir,
        adjusted_dir=args.adjusted_dir,
        calibration_manifest_path=args.calibration_manifest,
        frozen_policy_path=args.frozen_policy,
        pair_count=args.pair_count,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(json.dumps({
        "out": str(args.out),
        "selected_pair_count": artifact["selected_pair_count"],
        "eligible_pair_count": artifact["eligible_pair_count"],
        "titles": [row["title"] for row in artifact["records"]],
        "artifact_sha256": artifact["artifact_sha256"],
        "independent_fine_predictions_consulted": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
