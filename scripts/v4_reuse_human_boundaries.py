#!/usr/bin/env python3
"""Apply already-confirmed outer boundaries to an exact, hash-bound final mix.

Writes a new SRT/report/decision bundle; never overwrites an existing artifact.
This applies known human answers, not a model's predictions or calibration.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.human_boundary_reuse import POLICY_ID, apply_confirmed_boundaries
from scripts.v4_materialize_calibrated_alignment import (
    _validated_rows, _write_csv, _write_srt, load_json, load_report, sha256_json,
)
from scripts.v4_plan_boundary_refinement import resolve_manifest_file
from scripts.task_contract import validate_task_manifest_schema


def prepare_reuse(*, manifest_path, lock_path, gold_path, report_path, srt_path):
    manifest = load_json(manifest_path, label="manifest")
    issues = validate_task_manifest_schema(manifest)
    if issues:
        raise ValueError("invalid task manifest: " + "; ".join(issues))
    lock = load_json(lock_path, label="selection lock")
    gold = load_json(gold_path, label="human gold")
    source = resolve_manifest_file(manifest["inputs"]["source_srt"], label="source_srt")
    resolve_manifest_file(manifest["inputs"]["audio"], label="audio")
    fingerprint = manifest["task_fingerprint_sha256"]
    original_report = Path(lock["inputs"]["report_path"])
    if sha256_file(original_report) != lock["inputs"]["report_sha256"]:
        raise ValueError("locked source report hash mismatch")
    _, original_rows = load_report(original_report)
    source_by_number = {c.number:c for c in parse_srt_strict(source)}
    old_by_number = {int(r["original_cue"]):r for r in original_rows if str(r.get("original_cue","")).isdigit() and r["kind"]=="existing"}
    for target in lock["selection"]["populations"]["outer"]:
        old = old_by_number.get(target["cue_number"])
        cue = source_by_number.get(target["cue_number"])
        if old is None or cue is None or old.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("locked target source/task identity mismatch")
        if (old["track"],int(old["start_ms"]),int(old["end_ms"]),old["text"]) != (target["track"],target["start_ms"],target["end_ms"],target["canonical_text"]):
            raise ValueError("selection no longer matches locked source report")
        if "".join(old["original"].split()) != "".join(cue.text.split()):
            raise ValueError("source cue identity no longer matches confirmed target")
    fields, rows = load_report(report_path)
    cues = parse_srt_strict(srt_path)
    if len(rows) != len(cues) or any((int(r['start_ms']),int(r['end_ms']),r['text'].strip()) != (c.start_ms,c.end_ms,c.text.strip()) for r,c in zip(rows,cues)):
        raise ValueError("baseline report/SRT mismatch")
    after, decisions = apply_confirmed_boundaries(
        report_rows=rows, selection_lock=lock, selection_lock_file_sha256=sha256_file(lock_path),
        human_gold=gold, final_audio_sha256=manifest["inputs"]["audio"]["sha256"], task_fingerprint=fingerprint,
    )
    after = _validated_rows(after)
    return manifest, lock, gold, original_report, fields, rows, after, decisions


def materialize(*, manifest_path, lock_path, gold_path, report_path, srt_path, output_dir):
    manifest = load_json(manifest_path, label="manifest")
    manifest_issues = validate_task_manifest_schema(manifest)
    if manifest_issues:
        raise ValueError("invalid task manifest: " + "; ".join(manifest_issues))
    lock = load_json(lock_path, label="selection lock")
    gold = load_json(gold_path, label="human gold")
    output_dir = output_dir.resolve()
    staging = output_dir.with_name(output_dir.name+".staging")
    direct = {"manifest":manifest_path,"lock":lock_path,"gold":gold_path,"report":report_path,"srt":srt_path}
    for destination in (output_dir, staging):
        validate_materializer_preflight(
            manifest_path=manifest_path, manifest=manifest, direct_inputs=direct,
            lineage_payloads={"lock":lock,"gold":gold}, output_dir=destination, outputs={},
        )
        if destination.exists():
            raise FileExistsError("output/staging directory must be new")
    manifest, lock, gold, original_report, fields, rows, after, decisions = prepare_reuse(
        manifest_path=manifest_path, lock_path=lock_path, gold_path=gold_path,
        report_path=report_path, srt_path=srt_path,
    )
    fingerprint = manifest['task_fingerprint_sha256']
    inputs = {str(p.resolve()):sha256_file(p) for p in [manifest_path,lock_path,gold_path,report_path,srt_path,original_report]}
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    # Partial staging is retained if an IO error occurs; it is never promoted
    # without the complete SRT readback and final artifact.
    _write_csv(staging/'corrected.csv', fields, after)
    _write_srt(staging/'corrected.srt', after)
    readback = parse_srt_strict(staging/'corrected.srt')
    if len(readback) != len(after):
        raise ValueError("output SRT count mismatch")
    actual_changes = []
    for position,(old,new,written) in enumerate(zip(rows,after,readback),1):
        if written.text.strip() != old['text'].strip() or (written.start_ms,written.end_ms)!=(new['start_ms'],new['end_ms']):
            raise ValueError("output SRT differs from authorized rows")
        for kind in ('start','end'):
            if int(old[kind+'_ms']) != new[kind+'_ms']:
                actual_changes.append((position,kind,int(old[kind+'_ms']),new[kind+'_ms']))
    expected_changes = [(d['output_position'],d['boundary_kind'],d['before_ms'],d['selected_ms']) for d in decisions if d['action']=='reuse_human_confirmation']
    if sorted(actual_changes) != sorted(expected_changes):
        raise ValueError("actual SRT diff differs from confirmed boundaries")
    artifact = {
        "schema_version":"human-boundary-reuse-materialization-1.1", "policy_id":POLICY_ID,
        "authority_scope":"exact_human_confirmed_records_only_not_model_generalization",
        "task_fingerprint_sha256":fingerprint,"final_audio_sha256":gold['final_audio_sha256'],
        "source_srt_sha256":manifest['inputs']['source_srt']['sha256'],
        "human_gold_artifact_sha256":gold['artifact_sha256'],"selection_lock_sha256":lock['lock_sha256'],
        "inputs":inputs,
        "input_files":{role:{"path":os.path.relpath(p.resolve(),output_dir), "sha256":sha256_file(p)}
                       for role,p in direct.items()},
        "decisions":decisions,"decisions_sha256":sha256_json(decisions),
        "input_cues":len(rows),"output_cues":len(after),
        "start_changed_count":sum(c[1]=='start' for c in actual_changes),
        "end_changed_count":sum(c[1]=='end' for c in actual_changes),
        "text_changed_count":0,"new_human_annotations":0,
        "publish_ready":False,"release_status":"full_release_not_evaluated_by_annotation_reuse",
        "output_srt_sha256":sha256_file(staging/'corrected.srt'),
        "output_report_sha256":sha256_file(staging/'corrected.csv'),
        "producer_code_sha256":{
            "cli":sha256_file(Path(__file__)),
            "policy":sha256_file(Path(__file__).resolve().parents[1]/'lyric_aligner/timeline/human_boundary_reuse.py'),
            "geometry":sha256_file(Path(__file__).resolve().parents[1]/'lyric_aligner/timeline/joint_boundary_geometry.py'),
        },
    }
    artifact['artifact_sha256']=sha256_json(artifact)
    (staging/'reuse.artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    staging.rename(output_dir)
    return artifact


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('task-manifest','selection-lock','gold','report','srt','output-dir'):
        parser.add_argument('--'+arg,type=Path,required=True)
    a=parser.parse_args()
    artifact=materialize(manifest_path=a.task_manifest,lock_path=a.selection_lock,gold_path=a.gold,report_path=a.report,srt_path=a.srt,output_dir=a.output_dir)
    print(json.dumps({k:artifact[k] for k in ('start_changed_count','end_changed_count','new_human_annotations','release_status')}))


if __name__=='__main__':
    main()
