#!/usr/bin/env python3
"""Measure locked outer gold, available candidates and an actual final SRT.

Historical predictions are diagnostic inputs, never newly granted authority.
Report rows bind original-cue ownership across splits; the whole report must
match the supplied SRT before any final boundary enters evaluation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.evaluation.editor_risk_profile import build_editor_risk_profile_artifact
from lyric_aligner.evaluation.product_boundary_quality import evaluate_boundaries
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.materializer_path_safety import declared_input_paths


def sha_json(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def final_outer_index(report_path, srt_path, *, expected_task_fingerprint=None):
    with report_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    cues = parse_srt_strict(srt_path)
    if not rows or len(rows) != len(cues):
        raise ValueError("final report/SRT cue count mismatch")
    index = {}
    positions = {}
    for position, (row, cue) in enumerate(zip(rows, cues)):
        if expected_task_fingerprint is not None and row.get('task_fingerprint_sha256') != expected_task_fingerprint:
            raise ValueError('final report belongs to another task')
        if (int(row["start_ms"]), int(row["end_ms"]), row["text"].strip()) != (cue.start_ms, cue.end_ms, cue.text.strip()):
            raise ValueError("final report does not reproduce exact SRT")
        original = row.get("original_cue", "")
        if not original.isdigit():
            continue
        key = (row["track"], int(original))
        if key in positions and positions[key] != position - 1:
            raise ValueError("noncontiguous final cue ownership")
        positions[key] = position
        index.setdefault(key, []).append(row)
    return index


def build_report(lock_path, gold_path, predictions, final_srt=None, final_report=None):
    lock, gold = load(lock_path), load(gold_path)
    # Existing validator binds lock self-hash, gold self-hash, cue, track,
    # partition and final audio identities. Do not invent another gold validator.
    for kind in ("start", "end"):
        for partition in ("calibration", "holdout"):
            build_editor_risk_profile_artifact(selection_lock=lock, selection_lock_file_sha256=file_sha(lock_path), human_gold_artifact=gold, boundary_kind=kind, population=partition)
    selected = {r["case_id"]: r for r in lock["selection"]["populations"]["outer"]}
    records = [{
        "id": r["id"], "track": r["track"], "partition": r["partition"],
        "boundary_kind": r["boundary_kind"], "gold_ms": r["gold_ms"],
        "uncertainty_ms": r["gold_uncertainty_ms"],
        "editor_ms": selected[r["case_id"]][r["boundary_kind"] + "_ms"],
        "candidates": {}, "case_id": r["case_id"], "cue_number": r["cue_number"],
    } for r in gold["records"] if r["population"] == "outer"]
    by_id = {r["id"]: r for r in records}
    seen_scopes = set()
    for path in predictions:
        artifact = load(path)
        unsigned = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
        if artifact.get("artifact_sha256") != sha_json(unsigned) or artifact.get("predictions_sha256") != sha_json(artifact["records"]):
            raise ValueError("prediction hash mismatch")
        if artifact["selection_lock_sha256"] != lock["lock_sha256"] or artifact["scope"]["audio_basis"] != "final_mix":
            raise ValueError("prediction selection/audio basis mismatch")
        backend = artifact["backend_id"]
        kind = artifact["scope"]["boundary_kind"]
        if (backend, kind) in seen_scopes:
            raise ValueError("duplicate backend scope")
        seen_scopes.add((backend, kind))
        expected = {r["id"] for r in records if r["boundary_kind"] == kind}
        actual = [r["id"] for r in artifact["records"]]
        if len(actual) != len(set(actual)) or set(actual) != expected or not expected:
            raise ValueError("prediction population mismatch")
        for row in artifact["records"]:
            by_id[row["id"]]["candidates"][backend] = row["predicted_ms"]
    if bool(final_srt) != bool(final_report):
        raise ValueError("final SRT and report must be supplied together")
    if final_srt:
        source_report = Path(lock['inputs']['report_path'])
        if file_sha(source_report) != lock['inputs']['report_sha256']:
            raise ValueError('locked source report hash mismatch')
        with source_report.open(encoding='utf-8-sig', newline='') as handle:
            identities = {r.get('task_fingerprint_sha256', '') for r in csv.DictReader(handle)}
        if len(identities) != 1 or len(next(iter(identities), '')) != 64:
            raise ValueError('locked source report task identity is missing or ambiguous')
        final = final_outer_index(final_report, final_srt, expected_task_fingerprint=next(iter(identities)))
        for row in records:
            ownership = final.get((row["track"], row["cue_number"]))
            if not ownership:
                row["final_unavailable_reason"] = "original_ownership_absent"
                continue
            target = selected[row["case_id"]]
            # Only lossless text partitioning has the same outer lexical target.
            # Ambiguous deletion/merge/text rewrite is reported as missing.
            normalize = lambda s: "".join(s.split())
            if normalize("".join(r["text"] for r in ownership)) != normalize(target["canonical_text"]):
                row["final_unavailable_reason"] = "lexical_ownership_changed"
                continue
            row["final_ms"] = int(ownership[0]["start_ms"] if row["boundary_kind"] == "start" else ownership[-1]["end_ms"])
    report = evaluate_boundaries(records)
    report["evaluation_status"] = "historical_gold_regression_not_untouched_blind"
    report["inputs"] = {str(p): file_sha(p) for p in [lock_path, gold_path, *predictions, *([final_srt, final_report] if final_srt else [])]}
    report["final_audio_sha256"] = gold["final_audio_sha256"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-lock", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, nargs="*", default=[])
    parser.add_argument("--final-srt", type=Path)
    parser.add_argument("--final-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    direct = [args.selection_lock, args.gold, *args.predictions]
    protected = {str(path): path for path in direct}
    protected.update(declared_input_paths({str(path): load(path) for path in direct}))
    lyrics_dir = load(args.selection_lock).get("inputs", {}).get("canonical_lyrics_dir")
    if lyrics_dir:
        protected["canonical_lyrics_dir"] = Path(lyrics_dir)
    for path in (args.final_srt, args.final_report):
        if path:
            protected[str(path)] = path
    validate_separate_artifact_paths(inputs=protected, outputs={"quality_report": args.output})
    report = build_report(args.selection_lock, args.gold, args.predictions, args.final_srt, args.final_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting inputs or historical evidence.
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(f"Evaluated {report['record_count']} boundaries; {report['evaluation_status']}")


if __name__ == "__main__":
    main()
