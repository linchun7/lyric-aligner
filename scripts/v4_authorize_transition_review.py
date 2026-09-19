#!/usr/bin/env python3
"""Authorize transition review decisions from lexical/positional shadow evidence."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from lyric_aligner.review.transition_evidence_transaction import authorize_transition_review

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('task_manifest','run','run_artifact','template','lexical','positional','decisions_out','report_out'):
        p.add_argument('--'+name.replace('_','-'), required=True, type=Path)
    p.add_argument('--artifact-out', type=Path)
    a=p.parse_args()
    report=authorize_transition_review(task_manifest=a.task_manifest, run_path=a.run, run_artifact_path=a.run_artifact, template_path=a.template, lexical_path=a.lexical, positional_path=a.positional, decisions_out=a.decisions_out, report_out=a.report_out, artifact_out=a.artifact_out, repository_root=ROOT)
    if a.artifact_out:
        from lyric_aligner.contracts.artifacts import build_artifact_manifest, atomic_write_json
        artifact=build_artifact_manifest(task_fingerprint_sha256=report['task_fingerprint_sha256'], stage='review_decision_authorization', algorithm_version='4.0.0a19', outputs=(('authorized_decisions',a.decisions_out),('authorization_report',a.report_out)), normalized_config={'authority':report['authority']}, upstream_artifact_ids=(report['run_artifact_id'],), evidence=report['counts'])
        atomic_write_json(a.artifact_out, artifact)
        report['artifact_id']=artifact['artifact_id']
    print(json.dumps(report, ensure_ascii=False))
    return 0
if __name__ == '__main__': raise SystemExit(main())
