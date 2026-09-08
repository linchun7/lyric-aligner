"""Apply explicit human gap reviews to their exact locked subtitle pair."""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight
from lyric_aligner.timeline.joint_boundary_geometry import select_compatible_edits
from scripts.v4_materialize_calibrated_alignment import load_json, load_report, sha256_json, _write_csv, _write_srt
from scripts.v4_plan_boundary_refinement import resolve_manifest_file
from scripts.task_contract import validate_task_manifest_schema

SCHEMA = 'human-gap-review-materialization-1.0'
POLICY = 'exact-confirmed-gap-edges-1.0'


def apply_reviews(rows, lock, review):
    if lock.get('schema_version') != 'gap-boundary-review-pack-1.0' or review.get('schema_version') != 'human-gap-boundary-review-1.0':
        raise ValueError('unsupported gap review schema')
    if sha256_json({k:v for k,v in lock.items() if k != 'lock_sha256'}) != lock.get('lock_sha256'):
        raise ValueError('gap lock hash mismatch')
    for key, expected in [('selection_lock_sha256', lock['lock_sha256']),
                          ('final_audio_sha256', lock['final_audio_sha256']),
                          ('task_fingerprint_sha256', lock['task_fingerprint_sha256'])]:
        if review.get(key) != expected:
            raise ValueError('gap review scope mismatch: '+key)
    cases, records = lock['cases'], review['records']
    case_ids = [c['id'] for c in cases]
    record_ids = [r['id'] for r in records]
    if not cases or len(set(case_ids)) != len(cases) or len(set(record_ids)) != len(records) or set(case_ids) != set(record_ids):
        raise ValueError('gap review must cover exact unique locked identities')
    by_id = {r['id']:r for r in records}
    after, decisions, edits = copy.deepcopy(rows), [], []
    for case in cases:
        record = by_id[case['id']]
        for key in ('track', 'lrc_indices', 'text', 'clip_sha256'):
            if record.get(key) != case[key]:
                raise ValueError('gap review target mismatch: '+key)
        if type(record.get('human_confirmed')) is not bool or record.get('presence') not in ('present', 'absent', 'uncertain'):
            raise ValueError('invalid explicit human review state')
        matches = [i for i,r in enumerate(rows) if all(r.get(k) == case[k] for k in ('track','lrc_indices','text'))]
        if len(matches) != 1:
            raise ValueError('gap target is missing or ambiguous')
        i = matches[0]
        if any(int(rows[i][k+'_ms']) != case['reference_'+k+'_ms'] for k in ('start','end')):
            raise ValueError('gap baseline boundary changed')
        active = record['human_confirmed'] and record['presence'] == 'present'
        if active and (type(record.get('start_ms')) is not int or type(record.get('end_ms')) is not int or
                       not case['clip_start_ms'] <= record['start_ms'] < record['end_ms'] <= case['clip_end_ms']):
            raise ValueError('confirmed interval must be inside reviewed audio')
        for kind in ('start','end'):
            decision = dict(id=case['id']+':'+kind, case_id=case['id'], row_index=i, boundary_kind=kind,
                            before_ms=int(rows[i][kind+'_ms']), selected_ms=int(rows[i][kind+'_ms']),
                            confirmed_ms=record.get(kind+'_ms'), reason='pending_human_confirmation')
            if active:
                decision['reason'] = 'geometry_conflict'
                edits.append(dict(id=decision['id'], row_index=i, boundary_kind=kind, selected_ms=record[kind+'_ms']))
            elif record['human_confirmed']:
                decision['reason'] = 'presence_requires_structural_resolution'
            decisions.append(decision)
    accepted = select_compatible_edits(rows, edits)
    edit_ids = {e['id'] for e in edits}
    for d in decisions:
        if d['id'] in edit_ids and (d['id'] in accepted or d['confirmed_ms'] == d['before_ms']):
            d.update(selected_ms=d['confirmed_ms'], reason='exact_human_confirmed_edge')
            after[d['row_index']][d['boundary_kind']+'_ms'] = d['selected_ms']
    return after, decisions


def prepare(*, manifest_path, lock_path, review_path, report_path, srt_path, prior_receipt_path):
    from scripts.v4_upgrade_subtitles import validate_pair
    from scripts.verified_boundary_receipt import verified_qa_edges
    manifest = load_json(manifest_path, label='manifest')
    if validate_task_manifest_schema(manifest):
        raise ValueError('invalid task manifest')
    for role in ('audio','source_srt'):
        resolve_manifest_file(manifest['inputs'][role], label=role)
    lock, review = load_json(lock_path, label='gap lock'), load_json(review_path, label='human review')
    if lock['task_fingerprint_sha256'] != manifest['task_fingerprint_sha256'] or lock['final_audio_sha256'] != manifest['inputs']['audio']['sha256']:
        raise ValueError('gap lock belongs to another task/audio')
    if sha256_file(report_path) != lock['baseline_report_sha256'] or sha256_file(Path(lock['baseline_report_path'])) != lock['baseline_report_sha256']:
        raise ValueError('locked gap baseline report hash mismatch')
    rows, _ = validate_pair(report_path, srt_path)
    if any(r.get('task_fingerprint_sha256') != manifest['task_fingerprint_sha256'] or r.get('display_policy_id') for r in rows):
        raise ValueError('gap review requires exact task pre-display report')
    direct = dict(manifest=Path(manifest_path),lock=Path(lock_path),review=Path(review_path),
                  report=Path(report_path),srt=Path(srt_path),prior_receipt=Path(prior_receipt_path),
                  locked_report=Path(lock['baseline_report_path']))
    prior = load_json(prior_receipt_path, label='prior confirmation receipt')
    if prior.get('schema_version') != 'human-boundary-reuse-materialization-1.1':
        raise ValueError('gap review requires original replayable boundary receipt')
    for role, record in prior['input_files'].items():
        direct['prior_'+role] = (Path(prior_receipt_path).parent/record['path']).resolve()
    for i, case in enumerate(lock['cases'],1):
        clip = Path(lock_path).parent/f'gap_{i}.wav'
        if sha256_file(clip) != case['clip_sha256']:
            raise ValueError('reviewed clip changed')
        direct[f'clip_{i}'] = clip
    old_edges, _ = verified_qa_edges(artifact_path=prior_receipt_path,report_path=report_path,srt_path=srt_path,
        task_fingerprint=manifest['task_fingerprint_sha256'],source_srt_sha256=manifest['inputs']['source_srt']['sha256'],
        final_audio_sha256=manifest['inputs']['audio']['sha256'])
    after, decisions = apply_reviews(rows,lock,review)
    edges = {(pos,kind):value for (pos,kind),value in old_edges.items() if int(after[pos-1][kind+'_ms']) == value}
    for d in decisions:
        if d['reason'] == 'exact_human_confirmed_edge':
            edges[d['row_index']+1,d['boundary_kind']] = d['selected_ms']
    return manifest, direct, rows, after, decisions, edges


def materialize(*, output_dir, **inputs):
    from scripts.v4_upgrade_subtitles import validate_pair
    manifest, direct, before, after, decisions, _ = prepare(**inputs)
    output_dir = Path(output_dir).resolve()
    staging = output_dir.with_name(output_dir.name+'.staging')
    for out in (output_dir,staging):
        validate_materializer_preflight(manifest_path=inputs['manifest_path'],manifest=manifest,
            direct_inputs=direct,lineage_payloads={k:load_json(v,label=k) for k,v in direct.items() if k in ('lock','prior_lock','prior_gold')},output_dir=out,outputs={})
        if out.exists():
            raise FileExistsError('gap output directory must be new')
    staging.mkdir(parents=True)
    fields, _ = load_report(inputs['report_path'])
    _write_csv(staging/'corrected.csv',fields,after)
    _write_srt(staging/'corrected.srt',after)
    validate_pair(staging/'corrected.csv',staging/'corrected.srt')
    artifact = dict(schema_version=SCHEMA,policy_id=POLICY,task_fingerprint_sha256=manifest['task_fingerprint_sha256'],
        final_audio_sha256=manifest['inputs']['audio']['sha256'],source_srt_sha256=manifest['inputs']['source_srt']['sha256'],
        input_files={k:dict(path=os.path.relpath(v.resolve(),output_dir),sha256=sha256_file(v)) for k,v in direct.items()},
        decisions=decisions,output_report_sha256=sha256_file(staging/'corrected.csv'),output_srt_sha256=sha256_file(staging/'corrected.srt'),
        start_changed_count=sum(int(a['start_ms'])!=int(b['start_ms']) for a,b in zip(before,after)),
        end_changed_count=sum(int(a['end_ms'])!=int(b['end_ms']) for a,b in zip(before,after)),
        confirmed_record_count=len({d['case_id'] for d in decisions if d['reason']=='exact_human_confirmed_edge'}),
        publish_ready=False,producer_code_sha256=sha256_file(Path(__file__)))
    artifact['artifact_sha256'] = sha256_json(artifact)
    (staging/'gap.artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    staging.rename(output_dir)
    return artifact


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','lock','review','report','srt','prior-receipt','output-dir'):
        parser.add_argument('--'+name,required=True,type=Path)
    args = vars(parser.parse_args())
    print(json.dumps(materialize(output_dir=args.pop('output_dir'), **{k+'_path':v for k,v in args.items()}),ensure_ascii=False))
