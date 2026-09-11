#!/usr/bin/env python3
"""Resolve exact-bracket text review regions without changing subtitle timing."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.review.text_region_adjudication import adjudicate_regions
from lyric_aligner.text_repair import parse_srt_text, render_repaired_srt, timeline_signature


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        run_dir = args.run_dir.resolve()
        out_dir = args.out_dir.resolve()
        manifest_path = run_dir / "FRESH_INPUT_MANIFEST.json"
        smart_path = run_dir / "FRESH_SMART.json"
        smart_srt_path = run_dir / "FRESH_SMART.srt"
        best_path = run_dir / "FRESH_BEST_REVIEW.srt"
        safe_dir = run_dir / "TEXT_REVIEW_ADJUDICATION"
        safe_report_path = safe_dir / "TEXT_REVIEW_ADJUDICATION.json"
        safe_srt_path = safe_dir / "TEXT_REVIEW_MATERIALIZED.srt"
        max_run_path = run_dir / "MAX_V4_RERUN2" / "v4_run.json"
        for path in (
            manifest_path,
            smart_path,
            smart_srt_path,
            best_path,
            safe_report_path,
            safe_srt_path,
            max_run_path,
        ):
            if not path.is_file():
                raise ValueError(f"required input missing: {path}")

        manifest = load_json(manifest_path)
        smart = load_json(smart_path)
        safe_report = load_json(safe_report_path)
        max_run = load_json(max_run_path)
        lineage = safe_report.get("lineage", {})
        expected = {
            "fresh_manifest_sha256": sha256_file(manifest_path),
            "smart_sha256": sha256_file(smart_path),
            "smart_srt_sha256": sha256_file(smart_srt_path),
            "best_review_sha256": sha256_file(best_path),
            "max2_run_sha256": sha256_file(max_run_path),
        }
        for key, value in expected.items():
            if str(lineage.get(key) or "") != value:
                raise ValueError(f"safe adjudication lineage mismatch: {key}")
        if sha256_file(smart_srt_path) != sha256_file(best_path):
            raise ValueError("fresh Smart and best-review SRT differ")
        if safe_report.get("timeline_immutable") is not True:
            raise ValueError("safe text adjudication did not prove timeline immutability")
        if int(safe_report.get("remaining_manual", -1)) != 174:
            raise ValueError("expected safety-audited manual population of 174")
        if max_run.get("status") != "review_required" or max_run.get("legacy_fallback_used") is not False:
            raise ValueError("corrected Max2 run identity is not fail-closed review_required")

        base_text = safe_srt_path.read_text(encoding="utf-8-sig")
        base_parts, base_cues = parse_srt_text(base_text)
        _, smart_cues = parse_srt_text(smart_srt_path.read_text(encoding="utf-8-sig"))
        if len(base_cues) != 923 or len(smart_cues) != 923:
            raise ValueError("expected 923 cues")
        if timeline_signature(base_cues) != timeline_signature(smart_cues):
            raise ValueError("safe materialized SRT changed timing")
        transition_boundaries_ms = [
            int(round(float(row["nominal_boundary"]) * 1000.0))
            for row in max_run.get("transitions", [])
            if isinstance(row, dict) and row.get("nominal_boundary") is not None
        ]
        result = adjudicate_regions(
            repo_root=ROOT,
            manifest=manifest,
            smart=smart,
            safe_report=safe_report,
            cues=base_cues,
            transition_boundaries_ms=transition_boundaries_ms,
        )
        replacements = {int(key): value for key, value in result.pop("replacements").items()}
        rendered = render_repaired_srt(base_parts, base_cues, replacements)
        _, output_cues = parse_srt_text(rendered)
        if len(output_cues) != len(base_cues):
            raise ValueError("region materialization changed cue count")
        if timeline_signature(output_cues) != timeline_signature(base_cues):
            raise ValueError("region materialization changed timing")
        unresolved_set = {
            int(row["cue_ordinal"])
            for row in safe_report.get("decisions", [])
            if row.get("resolution") == "unresolved_manual"
        }
        if not set(replacements).issubset(unresolved_set):
            raise ValueError("region materialization changed a non-manual cue")
        changed_non_targets = [
            cue.ordinal
            for cue, out in zip(base_cues, output_cues)
            if cue.ordinal not in replacements and cue.text != out.text
        ]
        if changed_non_targets:
            raise ValueError("region materialization changed non-target text")

        failure_counts = Counter(
            row.get("reason")
            for row in result.get("decisions", [])
            if row.get("resolution") == "unresolved_manual"
        )
        normalized_changed_targets = sum(
            1
            for cue, out in zip(base_cues, output_cues)
            if cue.ordinal in replacements and cue.normalized != out.normalized
        )
        presentation_equivalent_targets = sum(
            1
            for cue, out in zip(base_cues, output_cues)
            if cue.ordinal in replacements and cue.text != out.text and cue.normalized == out.normalized
        )
        report = {
            **result,
            "lineage": {
                "manifest_sha256": sha256_file(manifest_path),
                "smart_sha256": sha256_file(smart_path),
                "base_safe_report_sha256": sha256_file(safe_report_path),
                "base_safe_srt_sha256": sha256_file(safe_srt_path),
                "max2_run_sha256": sha256_file(max_run_path),
            },
            "baseline_review_count": 176,
            "prior_deterministic_resolved": 2,
            "total_auto_resolved": 2 + int(result["resolved_cue_count"]),
            "final_remaining_manual": int(result["remaining_manual"]),
            "normalized_changed_target_count": normalized_changed_targets,
            "presentation_equivalent_target_count": presentation_equivalent_targets,
            "failure_reason_counts": dict(failure_counts),
            "cue_count": len(output_cues),
            "timeline_immutable": True,
            "non_target_text_immutable": True,
            "materialized_srt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "TEXT_REGION_ADJUDICATION.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / "TEXT_REVIEW_MATERIALIZED_REGION.srt").write_text(rendered, encoding="utf-8")
        print(json.dumps({
            "resolved_region_count": report["resolved_region_count"],
            "resolved_cue_count": report["resolved_cue_count"],
            "final_remaining_manual": report["final_remaining_manual"],
            "timeline_immutable": report["timeline_immutable"],
        }, ensure_ascii=False))
        return 0
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
