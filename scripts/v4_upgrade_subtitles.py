#!/usr/bin/env python3
"""Run subtitle upgrades or fresh source-context shadow evaluation from a job.

The JSON job lists existing inputs; it cannot grant authority to a model.
Calibrated stages use the production materializer's fresh adjudication. Human
reuse validates the exact recording and canonical target. Missing evidence
automatically retains the current result without creating an annotation task.
The explicit shadow schema writes experimental shadow.srt/CSV and comparison
artifacts; it does not grant production authority or emit a release.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight
from lyric_aligner.srt import parse_srt_strict
from scripts.task_contract import validate_task_manifest_schema
from scripts.v4_evaluate_product_boundaries import build_report
from scripts.v4_materialize_calibrated_alignment import load_json, load_report, materialize_from_paths, sha256_json
from scripts.v4_plan_boundary_refinement import resolve_manifest_file
from scripts.v4_reuse_human_boundaries import materialize as reuse


_UPGRADE_JOB_FIELDS = frozenset({
    'schema_version', 'task_manifest', 'report', 'srt', 'editor_preservation',
    'calibrated_stages', 'human_confirmations', 'gap_review', 'qa',
})
_EDITOR_PRESERVATION_FIELDS = frozenset({
    'run', 'run_artifact', 'assets', 'assets_artifact', 'scope', 'occurrence_id', 'canonical_region',
    'max_passes_per_occurrence',
})
_CALIBRATED_STAGE_FIELDS = frozenset({'mode', 'plan', 'evidence', 'decisions', 'bundle'})
_HUMAN_CONFIRMATION_FIELDS = frozenset({'gold', 'selection_lock', 'predictions'})
_GAP_REVIEW_FIELDS = frozenset({'lock', 'review', 'prior_receipt'})
_QA_FIELDS = frozenset({'audio_alignment', 'manual_overrides', 'regression_cases'})


def _reject_unknown_fields(value, allowed, *, label):
    if not isinstance(value, dict):
        raise ValueError(label + ' must be an object')
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(label + ' has unknown fields: ' + ', '.join(unknown))


def validate_pair(report, srt):
    _, rows = load_report(report)
    cues = parse_srt_strict(srt)
    def displayed(row):
        if row.get('display_policy_id'):
            return (int(row['display_start_ms']), int(row['display_end_ms']), row['display_text'].strip())
        return (int(row['start_ms']), int(row['end_ms']), row['text'].strip())
    if len(rows) != len(cues) or any(
        displayed(r) !=
        (c.start_ms, c.end_ms, c.text.strip()) for r, c in zip(rows, cues)
    ):
        raise ValueError('report/SRT mismatch')
    return rows, cues


def run_job(job_path: Path, output_dir: Path):
    job_path = job_path.resolve()
    job = load_json(job_path, label='upgrade job')
    if not isinstance(job, dict):
        raise ValueError('upgrade job must be an object')
    if job.get('schema_version') == 'subtitle-shadow-upgrade-job-1.0':
        from scripts.v4_shadow_upgrade import run_shadow_job
        return run_shadow_job(job_path, output_dir)
    if job.get('schema_version') != 'subtitle-upgrade-job-1.0':
        raise ValueError('unsupported upgrade job schema')
    if 'execution_mode' in job:
        raise ValueError('execution_mode requires the explicit shadow job schema')
    unknown = sorted(set(job) - _UPGRADE_JOB_FIELDS)
    if unknown:
        raise ValueError('unknown upgrade job fields: ' + ', '.join(unknown))
    def path(value):
        p = Path(value)
        return (p if p.is_absolute() else job_path.parent/p).resolve()
    manifest_path, report_path, srt_path = (path(job[k]) for k in ('task_manifest', 'report', 'srt'))
    manifest = load_json(manifest_path, label='task manifest')
    issues = validate_task_manifest_schema(manifest)
    if issues:
        raise ValueError('invalid task manifest: '+'; '.join(issues))
    if job.get('editor_preservation') is not None:
        return run_editor_job(job, job_path, manifest_path, manifest, report_path, srt_path, output_dir, path)
    stages = job.get('calibrated_stages', [])
    if not isinstance(stages, list):
        raise ValueError('calibrated_stages must be a list')
    resolved_stages = []
    direct = {'job': job_path, 'manifest': manifest_path, 'report': report_path, 'srt': srt_path}
    lineage = {'manifest': manifest}
    for i, stage in enumerate(stages):
        _reject_unknown_fields(stage, _CALIBRATED_STAGE_FIELDS, label=f'calibrated_stages[{i}]')
        if stage.get('mode') not in ('outer', 'internal'):
            raise ValueError('invalid calibrated stage mode')
        resolved = {'mode': stage['mode']}
        for key in ('plan', 'evidence', 'decisions', 'bundle'):
            resolved[key] = path(stage[key])
            direct[f'stage_{i}_{key}'] = resolved[key]
            lineage[f'stage_{i}_{key}'] = load_json(resolved[key], label=key)
        resolved_stages.append(resolved)
    confirmations = job.get('human_confirmations')
    gold_path = lock_path = None
    predictions = []
    if confirmations is not None:
        _reject_unknown_fields(confirmations, _HUMAN_CONFIRMATION_FIELDS, label='human_confirmations')
        gold_path, lock_path = (path(confirmations[k]) for k in ('gold', 'selection_lock'))
        predictions = [path(p) for p in confirmations.get('predictions', [])]
        for key, p in [('gold', gold_path), ('lock', lock_path), *[(f'prediction_{i}', p) for i, p in enumerate(predictions)]]:
            direct[key] = p
            lineage[key] = load_json(p, label=key)
    qa_inputs = {}
    gap_inputs = {}
    if job.get('gap_review') is not None:
        _reject_unknown_fields(job['gap_review'], _GAP_REVIEW_FIELDS, label='gap_review')
        if resolved_stages or gold_path:
            raise ValueError('gap review must start from its locked baseline; use a separate upgrade job')
        for key in ('lock', 'review', 'prior_receipt'):
            gap_inputs[key+'_path'] = path(job['gap_review'][key])
            direct['gap_'+key] = gap_inputs[key+'_path']
            lineage['gap_'+key] = load_json(gap_inputs[key+'_path'], label=key)
        from scripts.v4_apply_gap_review import prepare as prepare_gap
        _, gap_direct, _, _, _, _ = prepare_gap(manifest_path=manifest_path, report_path=report_path,
            srt_path=srt_path, **gap_inputs)
        direct.update({'gap_'+k:v for k,v in gap_direct.items()})
        lineage.update({k:load_json(v,label=k) for k,v in gap_direct.items() if k in ('lock','prior_lock','prior_gold')})
    if job.get('qa') is not None:
        _reject_unknown_fields(job['qa'], _QA_FIELDS, label='qa')
        for key in ('audio_alignment', 'manual_overrides', 'regression_cases'):
            qa_inputs[key] = path(job['qa'][key])
            direct['qa_'+key] = qa_inputs[key]
            lineage['qa_'+key] = load_json(qa_inputs[key], label=key)
    destination = output_dir.resolve()
    staging = destination.with_name(destination.name+'.staging')
    for out in (destination, staging):
        validate_materializer_preflight(manifest_path=manifest_path, manifest=manifest,
            direct_inputs=direct, lineage_payloads=lineage, output_dir=out, outputs={})
        if out.exists():
            raise FileExistsError('output/staging directory must be new')
    resolve_manifest_file(manifest['inputs']['source_srt'], label='source_srt')
    resolve_manifest_file(manifest['inputs']['audio'], label='audio')
    before_rows, before = validate_pair(report_path, srt_path)
    if (resolved_stages or gold_path) and any(r.get('display_policy_id') for r in before_rows):
        raise ValueError('display audit cannot be used as acoustic materializer input; supply its pre-display canonical report and matching SRT')
    if any(r.get('task_fingerprint_sha256') != manifest['task_fingerprint_sha256'] for r in before_rows):
        raise ValueError('baseline report belongs to another task')
    # Evaluate before writing anything: corrupt gold/predictions fail preflight.
    before_quality = build_report(lock_path, gold_path, predictions, srt_path, report_path) if gold_path else None
    input_hashes = {str(p): sha256_file(p) for p in direct.values()}
    staging.mkdir(parents=True)
    stage_artifacts = []
    current_report, current_srt = report_path, srt_path
    for i, stage in enumerate(resolved_stages):
        stage_dir = staging/f'calibrated_{i:02d}'
        artifact = materialize_from_paths(mode=stage['mode'], task_manifest_path=manifest_path,
            report_path=current_report, plan_path=stage['plan'], evidence_path=stage['evidence'],
            decisions_path=stage['decisions'], bundle_path=stage['bundle'], out_dir=stage_dir)
        stage_artifacts.append(artifact)
        current_report, current_srt = stage_dir/'materialized.csv', stage_dir/'materialized.srt'
        validate_pair(current_report, current_srt)
    if gold_path:
        stage_dir = staging/'confirmed'
        stage_artifacts.append(reuse(manifest_path=manifest_path, lock_path=lock_path, gold_path=gold_path,
            report_path=current_report, srt_path=current_srt, output_dir=stage_dir))
        current_report, current_srt = stage_dir/'corrected.csv', stage_dir/'corrected.srt'
    if gap_inputs:
        from scripts.v4_apply_gap_review import materialize as apply_gap
        stage_dir = staging/'gap_review'
        stage_artifacts.append(apply_gap(manifest_path=manifest_path,report_path=current_report,
            srt_path=current_srt,output_dir=stage_dir,**gap_inputs))
        current_report, current_srt = stage_dir/'corrected.csv', stage_dir/'corrected.srt'
    shutil.copyfile(current_report, staging/'final.csv')
    shutil.copyfile(current_srt, staging/'final.srt')
    after_rows, after = validate_pair(staging/'final.csv', staging/'final.srt')
    after_quality = build_report(lock_path, gold_path, predictions, staging/'final.srt', staging/'final.csv') if gold_path else None
    qa_result = None
    if qa_inputs:
        command = [sys.executable, str(Path(__file__).with_name('redo_karaoke_pipeline.py')), 'qa',
                   '--task-manifest', str(manifest_path), '--source-srt', str(resolve_manifest_file(manifest['inputs']['source_srt'], label='source_srt')),
                   '--final-srt', str(staging/'final.srt'), '--report', str(staging/'final.csv'),
                   '--song-list', str(resolve_manifest_file(manifest['inputs']['song_list'], label='song_list')),
                   '--lyrics-dir', str(Path(__file__).resolve().parents[1]/manifest['inputs']['lyrics_dir']['path']),
                   '--out', str(staging/'qa.json'), '--out-review', str(staging/'qa_review.csv')]
        for key, value in qa_inputs.items():
            command.extend(['--'+key.replace('_', '-'), str(value)])
        if gap_inputs:
            command.extend(['--boundary-confirmations', str(staging/'gap_review/gap.artifact.json')])
        elif gold_path:
            command.extend(['--boundary-confirmations', str(staging/'confirmed/reuse.artifact.json')])
        completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        (staging/'qa.log').write_text(completed.stdout+'\n'+completed.stderr, encoding='utf-8')
        if completed.returncode not in (0, 1) or not (staging/'qa.json').is_file():
            raise ValueError('final QA execution failed; see staging/qa.log')
        qa_result = load_json(staging/'qa.json', label='QA result')
        qa_result['boundary_review_report'] = str(destination/'qa_review.csv')
        (staging/'qa.json').write_text(json.dumps(qa_result,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
        # Release manifests bind the QA bytes; regenerate after final QA paths
        # are fixed, rather than leaving a stale checksum from the subprocess.
        if qa_result.get('publish_ready'):
            from lyric_aligner.qa.final_integrity import build_release_artifact_manifest
            release = build_release_artifact_manifest(final_srt=staging/'final.srt', audit_csv=staging/'final.csv',
                qa_json=staging/'qa.json', task_fingerprint_sha256=manifest['task_fingerprint_sha256'],
                algorithm_version=qa_result['algorithm_version'], git_commit='')
            for record in release['outputs']:
                record['path'] = str(destination/Path(record['path']).name)
            release['artifact_id'] = sha256_json({k:v for k,v in release.items() if k != 'artifact_id'})
            (staging/'qa_RELEASE_ARTIFACT.json').write_text(json.dumps(release,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if after_quality:
        # These paths will survive the atomic rename; file hashes are unchanged.
        after_quality['inputs'] = {str(destination/Path(k).name) if Path(k).parent == staging else k: v
                                   for k, v in after_quality['inputs'].items()}
    display_result = None
    if gap_inputs:
        from scripts.v4_group_vocalization_display import materialize as group_display
        display_result = group_display(receipt=staging/'gap_review/gap.artifact.json',
            report=staging/'final.csv', srt=staging/'final.srt', output_dir=staging/'display')
    # No new timing mutation may escape the stage materializer: final files are
    # exact copies, including byte preservation on the no-evidence path.
    aligned = len(before) == len(after)
    summary = {
        'schema_version': 'subtitle-upgrade-result-1.0',
        'task_fingerprint_sha256': manifest['task_fingerprint_sha256'],
        'final_audio_sha256': manifest['inputs']['audio']['sha256'],
        'inputs': input_hashes, 'stage_artifacts': stage_artifacts,
        'stage_path_relocation': {'from': str(staging), 'to': str(destination)},
        'before_cues': len(before), 'after_cues': len(after),
        'position_comparison_available': aligned,
        'start_changed_count': sum(a.start_ms != b.start_ms for a, b in zip(before, after)) if aligned else None,
        'end_changed_count': sum(a.end_ms != b.end_ms for a, b in zip(before, after)) if aligned else None,
        'text_changed_count': sum(a.text != b.text for a, b in zip(before, after)) if aligned else None,
        'srt_byte_identical': sha256_file(srt_path) == sha256_file(staging/'final.srt'),
        'output_srt_sha256': sha256_file(staging/'final.srt'),
        'output_report_sha256': sha256_file(staging/'final.csv'),
        # This executor consumes existing review files; it never collects new
        # annotations. Replaying the same file must not inflate human effort.
        'new_human_annotations': 0,
        'applied_existing_gap_review_records': stage_artifacts[-1]['confirmed_record_count'] if gap_inputs else 0,
        'reporting_policy_id': 'subtitle-upgrade-reporting-1.1',
        'quality_status': 'explicit_human_gap_reviews_applied' if gap_inputs else ('historical_confirmations_reused_not_new_blind' if gold_path else 'no_human_truth_accuracy_unknown'),
        'publish_ready': bool(qa_result and qa_result.get('publish_ready') is True),
        'release_status': qa_result['release_status'] if qa_result else 'full_release_not_evaluated_by_accuracy_upgrade',
        'qa': None if qa_result is None else {k:qa_result[k] for k in ('passed', 'publish_ready', 'release_status', 'unverified_timing_mutation_count', 'verified_human_boundary_edge_count')},
        'display_derivative': None if display_result is None else {
            'srt': 'display/display.srt', 'receipt': 'display/display.artifact.json',
            'artifact_sha256': display_result['artifact_sha256'],
            'output_srt_sha256': display_result['output_srt_sha256'],
            'cue_count': display_result['output_cue_count'],
            'group_count': len(display_result['groups']),
            'publish_ready': False, 'acoustic_boundary_improvement_claimed': False,
        },
        'producer_code_sha256': sha256_file(Path(__file__)),
    }
    for name, value in [('before_quality.json', before_quality), ('after_quality.json', after_quality)]:
        if value is not None:
            (staging/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    summary['artifact_sha256'] = sha256_json(summary)
    (staging/'upgrade.artifact.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    staging.rename(destination)
    if display_result is not None:
        from scripts.v4_group_vocalization_display import verify as verify_display
        verify_display(destination/'display')
    return summary


def run_editor_job(job, job_path, manifest_path, manifest, report_path, srt_path, output_dir, path):
    """Restore a selected editor occurrence without relocating its bound artifacts."""
    if any(job.get(key) for key in ('calibrated_stages', 'human_confirmations', 'gap_review', 'qa')):
        raise ValueError('editor preservation requires its own job; run fresh QA on its output')
    config = job['editor_preservation']
    _reject_unknown_fields(config, _EDITOR_PRESERVATION_FIELDS, label='editor_preservation')
    scope=config.get('scope','single_occurrence')
    if scope not in ('single_occurrence','all_occurrences'):
        raise ValueError('editor preservation scope must be single_occurrence or all_occurrences')
    if scope=='single_occurrence':
        if not isinstance(config.get('occurrence_id'),str) or not config['occurrence_id']:
            raise ValueError('single-occurrence editor preservation requires an occurrence_id')
        if 'max_passes_per_occurrence' in config:
            raise ValueError('max_passes_per_occurrence is only valid for all_occurrences scope')
    else:
        if config.get('occurrence_id') not in (None,''):
            raise ValueError('all-occurrences editor preservation cannot specify occurrence_id')
        if 'canonical_region' in config:
            raise ValueError('all-occurrences editor preservation selects exact regions automatically')
    inputs = {key: path(config[key]) for key in ('run', 'run_artifact', 'assets', 'assets_artifact')}
    direct = dict(job=job_path, manifest=manifest_path, report=report_path, srt=srt_path, **inputs)
    validate_materializer_preflight(manifest_path=manifest_path, manifest=manifest,
        direct_inputs=direct, lineage_payloads={key:load_json(value,label=key) for key,value in inputs.items()},
        output_dir=output_dir.resolve(), outputs={})
    hashes = {str(value):sha256_file(value) for value in direct.values()}
    before = parse_srt_strict(srt_path)
    if scope=='single_occurrence':
        from scripts.v4_preserve_editor_occurrence import materialize as preserve_editor
        report = preserve_editor(manifest_path=manifest_path, srt_path=srt_path, audit_path=report_path,
        occurrence_id=config['occurrence_id'], output_dir=output_dir,
        canonical_region=config.get('canonical_region','auto'),
        **{key+'_path':value for key,value in inputs.items()})
        kept=(report.get('automatic_selection') or {}).get('action')=='keep'  # scope result
    else:
        from scripts.v4_preserve_editor_batch import materialize_batch
        report=materialize_batch(manifest_path=manifest_path,srt_path=srt_path,audit_path=report_path,
            output_dir=output_dir,max_passes_per_occurrence=config.get('max_passes_per_occurrence',128),
            **{key+'_path':value for key,value in inputs.items()})
        kept=report.get('restore_stage_count')==0
    if any(sha256_file(Path(value)) != digest for value,digest in hashes.items()):
        raise ValueError('editor upgrade input changed during execution')
    after=parse_srt_strict(output_dir/'final.srt')
    aligned=len(before)==len(after)
    result = dict(schema_version='subtitle-upgrade-result-1.0',
        task_fingerprint_sha256=manifest['task_fingerprint_sha256'], inputs=hashes,
        editor_preservation_scope=scope,
        before_cues=len(before), after_cues=len(after),
        position_comparison_available=aligned,
        start_changed_count=sum(a.start_ms!=b.start_ms for a,b in zip(before,after)) if aligned else None,
        end_changed_count=sum(a.end_ms!=b.end_ms for a,b in zip(before,after)) if aligned else None,
        text_changed_count=sum(a.text!=b.text for a,b in zip(before,after)) if aligned else None, new_human_annotations=0,
        quality_status='unchanged_no_compatible_editor_region' if kept else 'canonical_text_on_immutable_editor_timing_requires_fresh_QA',
        publish_ready=False, release_status='fresh_product_QA_required',
        preservation=report, preservation_artifact_sha256=sha256_file(output_dir/'preservation.artifact.json'),
        output_srt_sha256=sha256_file(output_dir/'final.srt'),
        output_report_sha256=sha256_file(output_dir/'final.csv'),
        producer_code_sha256=sha256_file(Path(__file__)))
    result['artifact_sha256'] = sha256_json(result)
    from lyric_aligner.contracts.artifacts import atomic_write_json
    atomic_write_json(output_dir/'upgrade.artifact.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    result = run_job(args.job, args.output_dir)
    print(json.dumps({k: result[k] for k in ('before_cues', 'after_cues', 'start_changed_count', 'end_changed_count', 'quality_status')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
