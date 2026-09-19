#!/usr/bin/env python3
"""Build calibration/holdout Independent Fine packs from locked affine truth audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json, sha256_file
from scripts.v4_evaluate_independent_fine_benchmark import TRUTH_SCHEMA_VERSION
from scripts.v4_run_independent_fine_benchmark import MANIFEST_SCHEMA_VERSION

PACK_BUILDER_VERSION = "independent-fine-known-transform-pack-builder-1.1"
SELECTION_POLICY = (
    "all_truth_usable_pairs_x_3_prefrozen_rms_windows; "
    "slope_grid_nominal_plus_minus_0.005_0.01; source_start_search_plus_minus_3s"
)
TRUTH_POLICY = (
    "pair-level median affine intercept from independent broadband RMS audit; "
    "nominal BPM slope; no Independent Fine output consulted"
)
TRUTH_BASIS = "independent_rms_affine_audit_pair_median_intercept_plus_bpm_slope"


class IndependentFinePackBuildError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_sha(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and text == text.lower() and all(c in "0123456789abcdef" for c in text)


def _repository_relative_audio_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/")
    prefix = "视频歌词字幕/"
    relative = text[len(prefix):] if text.startswith(prefix) else text
    if not relative.startswith("private/") or "/../" in f"/{relative}/" or relative.startswith("private//"):
        raise IndependentFinePackBuildError("audit audio path must resolve under repository private/")
    return relative


def _finite(value: Any, *, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise IndependentFinePackBuildError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed):
        raise IndependentFinePackBuildError(f"{label} must be finite")
    return parsed


def _case_id(
    *,
    pair_index: int,
    window_index: int,
    source_sha256: str,
    mix_sha256: str,
    mix_start_s: float,
    mix_end_s: float,
    slope: float,
    intercept: float,
) -> str:
    material = (
        f"{pair_index}|{window_index}|{source_sha256}|{mix_sha256}|"
        f"{mix_start_s:.8f}|{mix_end_s:.8f}|{slope:.15f}|{intercept:.6f}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_pack(
    *,
    audit_path: Path,
    expected_audit_sha256: str,
    expected_pair_count: int,
    partition: str = "calibration",
    expected_frozen_policy_sha256: str | None = None,
    expected_pair_selection_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _is_sha(expected_audit_sha256):
        raise IndependentFinePackBuildError("expected audit SHA must be lowercase SHA-256")
    if expected_pair_count < 1:
        raise IndependentFinePackBuildError("expected pair count must be positive")
    if partition not in {"calibration", "holdout"}:
        raise IndependentFinePackBuildError("partition must be calibration or holdout")
    if sha256_file(audit_path) != expected_audit_sha256:
        raise IndependentFinePackBuildError("affine truth audit SHA mismatch")
    audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
    if not isinstance(audit, Mapping):
        raise IndependentFinePackBuildError("affine truth audit root must be an object")
    audit_partition = audit.get("partition")
    if audit_partition is not None and audit_partition != partition:
        raise IndependentFinePackBuildError("affine truth audit partition mismatch")
    source_audit_sha256 = str(audit.get("artifact_sha256") or expected_audit_sha256)
    if not _is_sha(source_audit_sha256):
        raise IndependentFinePackBuildError("affine truth audit artifact SHA is invalid")
    provenance: dict[str, str] = {}
    if partition == "holdout":
        if not _is_sha(expected_frozen_policy_sha256) or not _is_sha(expected_pair_selection_sha256):
            raise IndependentFinePackBuildError("holdout requires frozen policy and pair-selection SHA")
        if str(audit.get("frozen_policy_sha256") or "") != expected_frozen_policy_sha256:
            raise IndependentFinePackBuildError("holdout frozen policy SHA mismatch")
        if str(audit.get("pair_selection_sha256") or "") != expected_pair_selection_sha256:
            raise IndependentFinePackBuildError("holdout pair-selection SHA mismatch")
        provenance = {
            "frozen_policy_sha256": str(expected_frozen_policy_sha256),
            "pair_selection_sha256": str(expected_pair_selection_sha256),
        }
    pairs = audit.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != expected_pair_count:
        raise IndependentFinePackBuildError("affine truth audit pair count mismatch")
    if int(audit.get("truth_usable_count", -1)) != expected_pair_count:
        raise IndependentFinePackBuildError("not every affine truth pair passed the frozen gate")

    manifest_records: list[dict[str, Any]] = []
    truth_records: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for expected_pair_index, pair in enumerate(pairs, start=1):
        if not isinstance(pair, Mapping) or pair.get("truth_usable") is not True:
            raise IndependentFinePackBuildError("affine truth pair is not truth-usable")
        pair_index = int(pair.get("pair", -1))
        if pair_index != expected_pair_index:
            raise IndependentFinePackBuildError("affine truth pair ordering/identity mismatch")
        identities = pair.get("identities")
        summary = pair.get("summary")
        windows = pair.get("windows")
        if not isinstance(identities, Mapping) or not isinstance(summary, Mapping):
            raise IndependentFinePackBuildError("affine truth pair identity/summary is invalid")
        if not isinstance(windows, list) or len(windows) != 3:
            raise IndependentFinePackBuildError("each affine truth pair must have exactly three windows")
        source_sha = str(identities.get("source_sha256") or "")
        mix_sha = str(identities.get("adjusted_sha256") or "")
        if not _is_sha(source_sha) or not _is_sha(mix_sha):
            raise IndependentFinePackBuildError("affine truth audio SHA is invalid")
        source_path = _repository_relative_audio_path(identities.get("source_path"))
        mix_path = _repository_relative_audio_path(identities.get("adjusted_path"))
        slope = _finite(identities.get("nominal_slope"), label="nominal slope")
        intercept = _finite(summary.get("median_offset_seconds"), label="pair median intercept")
        if slope <= 0:
            raise IndependentFinePackBuildError("nominal slope must be positive")
        slopes = [slope - 0.01, slope - 0.005, slope, slope + 0.005, slope + 0.01]
        if any(candidate <= 0 for candidate in slopes):
            raise IndependentFinePackBuildError("slope grid contains a non-positive value")

        for expected_window_index, window in enumerate(windows, start=1):
            if not isinstance(window, Mapping) or window.get("valid") is not True:
                raise IndependentFinePackBuildError("affine truth window is invalid")
            window_index = int(window.get("window", -1))
            if window_index != expected_window_index:
                raise IndependentFinePackBuildError("affine truth window ordering/identity mismatch")
            mix_start = _finite(window.get("start_adjusted_seconds"), label="mix start")
            mix_end = _finite(window.get("end_adjusted_seconds"), label="mix end")
            if mix_start < 0 or mix_end <= mix_start:
                raise IndependentFinePackBuildError("affine truth mix window is invalid")
            expected_start = slope * mix_start + intercept
            expected_end = slope * mix_end + intercept
            if expected_start < 0 or expected_end <= expected_start:
                raise IndependentFinePackBuildError("derived source window is invalid")
            search_start = max(0.0, expected_start - 3.0)
            search_end = expected_end + 3.0
            case_id = _case_id(
                pair_index=pair_index,
                window_index=window_index,
                source_sha256=source_sha,
                mix_sha256=mix_sha,
                mix_start_s=mix_start,
                mix_end_s=mix_end,
                slope=slope,
                intercept=intercept,
            )
            if case_id in seen_case_ids:
                raise IndependentFinePackBuildError("duplicate deterministic case ID")
            seen_case_ids.add(case_id)
            manifest_records.append(
                {
                    "case_id": case_id,
                    "source_path": source_path,
                    "source_sha256": source_sha,
                    "mix_path": mix_path,
                    "mix_sha256": mix_sha,
                    "mix_start_s": mix_start,
                    "mix_end_s": mix_end,
                    "source_search_start_s": search_start,
                    "source_search_end_s": search_end,
                    "slopes": slopes,
                    "pair_index": pair_index,
                    "window_index": window_index,
                }
            )
            truth_records.append(
                {
                    "case_id": case_id,
                    "source_sha256": source_sha,
                    "mix_sha256": mix_sha,
                    "expected_source_start_s": expected_start,
                    "expected_slope": slope,
                    "truth_basis": TRUTH_BASIS,
                    "pair_index": pair_index,
                    "window_index": window_index,
                    "source_audit_sha256": source_audit_sha256,
                }
            )

    expected_record_count = expected_pair_count * 3
    if len(manifest_records) != expected_record_count or len(truth_records) != expected_record_count:
        raise IndependentFinePackBuildError("derived benchmark record count mismatch")
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "partition": partition,
        "record_count": expected_record_count,
        "source_audit_sha256": source_audit_sha256,
        "source_audit_file_sha256": expected_audit_sha256,
        "selection_policy": SELECTION_POLICY,
        "builder_version": PACK_BUILDER_VERSION,
        **provenance,
        "records": manifest_records,
    }
    manifest["manifest_sha256"] = _sha_json(manifest)
    truth: dict[str, Any] = {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "partition": partition,
        "record_count": expected_record_count,
        "source_audit_sha256": source_audit_sha256,
        "source_audit_file_sha256": expected_audit_sha256,
        "truth_policy": TRUTH_POLICY,
        "builder_version": PACK_BUILDER_VERSION,
        **provenance,
        "records": truth_records,
    }
    truth["artifact_sha256"] = _sha_json(truth)
    return manifest, truth


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expected-audit-sha256", required=True)
    parser.add_argument("--expected-pair-count", type=int, required=True)
    parser.add_argument("--partition", choices=("calibration", "holdout"), default="calibration")
    parser.add_argument("--expected-frozen-policy-sha256")
    parser.add_argument("--expected-pair-selection-sha256")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.out_dir / f"{args.partition}_manifest.json"
    truth_path = args.out_dir / f"{args.partition}_truth.json"
    if manifest_path.exists() or truth_path.exists():
        raise FileExistsError(f"Independent Fine {args.partition} pack output already exists")
    manifest, truth = build_pack(
        audit_path=args.audit,
        expected_audit_sha256=args.expected_audit_sha256,
        expected_pair_count=args.expected_pair_count,
        partition=args.partition,
        expected_frozen_policy_sha256=args.expected_frozen_policy_sha256,
        expected_pair_selection_sha256=args.expected_pair_selection_sha256,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(truth_path, truth)
    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "truth": str(truth_path),
                "record_count": manifest["record_count"],
                "manifest_sha256": manifest["manifest_sha256"],
                "truth_artifact_sha256": truth["artifact_sha256"],
                "partition": args.partition,
                "holdout_created": args.partition == "holdout",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
