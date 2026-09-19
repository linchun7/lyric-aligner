#!/usr/bin/env python3
"""Atomically restore every provable immutable-editor region in one product.

The occurrence materializer remains the authority for one exact region. This
wrapper repeats that fail-closed decision in a private transaction until every
bound occurrence is stable, then publishes one output. It never grants release
or model timing authority.
"""
from __future__ import annotations

import argparse,json,shutil,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest,atomic_write_json,sha256_file,canonical_json_sha256
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight,declared_input_paths
from lyric_aligner.qa.final_integrity import validate_srt_report_binding
from scripts.task_contract import load_task_manifest,verify_manifest_inputs,resolve_manifest_record
from scripts.v4_fuse_evidence import _validate_artifact,_load_timelines,_RUN_ROLES
from scripts.v4_preserve_editor_occurrence import AUTO_REGION_POLICY_ID,materialize as materialize_occurrence


BATCH_POLICY_ID='immutable-editor-all-occurrences-batch-1.0'
DEFAULT_MAX_PASSES_PER_OCCURRENCE=128


def _load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _compact_stage(report,iteration):
    selection=report.get('automatic_selection') or {}
    return dict(occurrence_id=report.get('occurrence_id'),iteration=iteration,
        evaluation_canonical_content_origin=report.get('evaluation_canonical_content_origin'),
        action=selection.get('action'),canonical_region=report.get('canonical_region'),
        baseline_target_cues=report.get('baseline_target_cues'),restored_editor_cues=report.get('restored_editor_cues'),
        selection_policy_id=selection.get('policy_id'),selection_basis=selection.get('selection_basis'),
        candidates=selection.get('candidates',[]),text_corrections=report.get('text_corrections',[]))


def materialize_batch(*,manifest_path,srt_path,audit_path,run_path,run_artifact_path,
                      assets_path,assets_artifact_path,output_dir,
                      max_passes_per_occurrence=DEFAULT_MAX_PASSES_PER_OCCURRENCE):
    if isinstance(max_passes_per_occurrence,bool) or not isinstance(max_passes_per_occurrence,int):
        raise ValueError('max passes per occurrence must be an integer')
    if not 1<=max_passes_per_occurrence<=512:
        raise ValueError('max passes per occurrence must be between 1 and 512')
    paths={name:Path(value).resolve() for name,value in dict(manifest=manifest_path,srt=srt_path,audit=audit_path,
        run=run_path,run_artifact=run_artifact_path,assets=assets_path,assets_artifact=assets_artifact_path).items()}
    manifest=load_task_manifest(paths['manifest']);issues=verify_manifest_inputs(paths['manifest'],manifest)
    if issues:raise ValueError('; '.join(issues))
    fingerprint=manifest['task_fingerprint_sha256']
    run,run_artifact,assets,assets_artifact=(_load(paths[k]) for k in ('run','run_artifact','assets','assets_artifact'))
    if run.get('task_fingerprint_sha256')!=fingerprint or assets.get('task_fingerprint_sha256')!=fingerprint:
        raise ValueError('run/assets payload belongs to another task')
    stage=run_artifact.get('stage')
    if stage not in _RUN_ROLES:raise ValueError('unsupported source run stage')
    _validate_artifact(run_artifact,fingerprint=fingerprint,stage=stage,role=_RUN_ROLES[stage],output=paths['run'])
    _validate_artifact(assets_artifact,fingerprint=fingerprint,stage='asset_resolution',role='track_assets',output=paths['assets'])
    _,timeline_ids=_load_timelines(run,run_artifact,fingerprint=fingerprint)
    validate_srt_report_binding(paths['srt'],paths['audit'],expected_task_fingerprint=fingerprint)
    occurrence_ids=[];seen=set()
    for occurrence in run.get('occurrences',[]):
        occurrence_id=occurrence.get('occurrence_id') if isinstance(occurrence,dict) else None
        if not isinstance(occurrence_id,str) or not occurrence_id or occurrence_id in seen:
            raise ValueError('source run occurrence identities must be nonempty and unique')
        seen.add(occurrence_id);occurrence_ids.append(occurrence_id)
    if not occurrence_ids:raise ValueError('source run has no occurrences')

    source_path=resolve_manifest_record(paths['manifest'],manifest['inputs']['source_srt'])
    input_hashes={str(p):sha256_file(p) for p in paths.values()}
    input_hashes[str(source_path.resolve())]=sha256_file(source_path)
    for dependency in declared_input_paths({'run':run,'assets':assets}).values():
        dependency=dependency.resolve()
        if dependency.is_file():input_hashes[str(dependency)]=sha256_file(dependency)

    destination=Path(output_dir).resolve();staging=destination.with_name(destination.name+'.staging')
    for directory in (destination,staging):
        validate_materializer_preflight(manifest_path=paths['manifest'],manifest=manifest,direct_inputs=paths,
            lineage_payloads={'run':run,'assets':assets},output_dir=directory,outputs={})
        if directory.exists():raise FileExistsError('batch output/staging directory must be new')

    stage_reports=[];restore_stage_count=0;restored_editor_cues_total=0;replaced_baseline_cues_total=0
    evaluation_canonical_content_origins={}
    occurrences_with_restore=set();current_srt=paths['srt'];current_audit=paths['audit'];global_stage=0
    with tempfile.TemporaryDirectory(prefix='lyric-aligner-editor-batch-') as temporary:
        scratch=Path(temporary)
        for occurrence_id in occurrence_ids:
            for iteration in range(1,max_passes_per_occurrence+1):
                global_stage+=1;stage_dir=scratch/f'{global_stage:04d}_{occurrence_id}_{iteration:03d}'
                report=materialize_occurrence(manifest_path=paths['manifest'],srt_path=current_srt,audit_path=current_audit,
                    run_path=paths['run'],run_artifact_path=paths['run_artifact'],assets_path=paths['assets'],
                    assets_artifact_path=paths['assets_artifact'],occurrence_id=occurrence_id,
                    output_dir=stage_dir,canonical_region='auto')
                compact=_compact_stage(report,iteration);stage_reports.append(compact);action=compact['action']
                origin=compact.get('evaluation_canonical_content_origin')
                if type(origin) is not int or origin < 0:
                    raise ValueError('editor preservation produced an invalid evaluation canonical content origin')
                prior_origin=evaluation_canonical_content_origins.get(occurrence_id)
                if prior_origin is not None and prior_origin != origin:
                    raise ValueError('editor preservation evaluation canonical content origin changed across passes')
                evaluation_canonical_content_origins[occurrence_id]=origin
                if action=='keep':break
                if action!='restore' or not report.get('restored_editor_cues') or not report.get('baseline_target_cues'):
                    raise ValueError('automatic preservation produced an invalid mutation action')
                restore_stage_count+=1;occurrences_with_restore.add(occurrence_id)
                restored_editor_cues_total+=int(report['restored_editor_cues'])
                replaced_baseline_cues_total+=int(report['baseline_target_cues'])
                current_srt=stage_dir/'final.srt';current_audit=stage_dir/'final.csv'
            else:raise ValueError('automatic preservation did not converge within the pass limit')

        staging.mkdir(parents=True)
        shutil.copy2(current_srt,staging/'final.srt');shutil.copy2(current_audit,staging/'final.csv')
        validate_srt_report_binding(staging/'final.srt',staging/'final.csv',expected_task_fingerprint=fingerprint)
        report=dict(schema_version='editor-preservation-batch-materialization-1.0',policy_id=BATCH_POLICY_ID,
            automatic_region_policy_id=AUTO_REGION_POLICY_ID,task_fingerprint_sha256=fingerprint,
            occurrence_count=len(occurrence_ids),occurrence_ids=occurrence_ids,stage_count=len(stage_reports),
            restore_stage_count=restore_stage_count,keep_stage_count=len(stage_reports)-restore_stage_count,
            occurrences_with_restore_count=len(occurrences_with_restore),
            occurrences_with_restore=[oid for oid in occurrence_ids if oid in occurrences_with_restore],
            replaced_baseline_cues_total=replaced_baseline_cues_total,restored_editor_cues_total=restored_editor_cues_total,
            max_passes_per_occurrence=max_passes_per_occurrence,
            selection_semantics='repeat exact unique compatible editor-region restoration until every occurrence is stable',
            timing_basis='immutable_editor_only_where_exact_unique_compatible_else_unchanged',model_timing_authority_used=False,
            evaluation_canonical_content_origins=evaluation_canonical_content_origins,
            input_srt_sha256=input_hashes[str(paths['srt'])],input_audit_sha256=input_hashes[str(paths['audit'])],
            final_srt_sha256=sha256_file(staging/'final.srt'),final_audit_sha256=sha256_file(staging/'final.csv'),
            stages=stage_reports,publish_ready=False,qa_status='fresh_product_QA_required')
        atomic_write_json(staging/'preservation.json',report)
        artifact=build_artifact_manifest(task_fingerprint_sha256=fingerprint,stage='editor_preservation_batch',
            algorithm_version=__version__,outputs=(('final_srt',staging/'final.srt'),('audit_csv',staging/'final.csv'),
                ('preservation_report',staging/'preservation.json')),
            normalized_config=dict(policy_id=BATCH_POLICY_ID,auto_region_policy_id=AUTO_REGION_POLICY_ID,
                occurrence_ids=occurrence_ids,max_passes_per_occurrence=max_passes_per_occurrence,
                evaluation_canonical_content_origins=evaluation_canonical_content_origins,input_sha256=input_hashes),
            upstream_artifact_ids=tuple(sorted({run_artifact['artifact_id'],assets_artifact['artifact_id'],*timeline_ids})))
        for record in artifact['outputs']:record['path']=str(destination/Path(record['path']).name)
        artifact['artifact_id']=canonical_json_sha256({k:v for k,v in artifact.items() if k!='artifact_id'})
        atomic_write_json(staging/'preservation.artifact.json',artifact)
        if any(sha256_file(Path(p))!=digest for p,digest in input_hashes.items()):
            raise ValueError('input changed during batch materialization')
        staging.rename(destination)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('task-manifest','srt','audit','run','run-artifact','assets','assets-artifact','out-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--max-passes-per-occurrence',type=int,default=DEFAULT_MAX_PASSES_PER_OCCURRENCE)
    args=parser.parse_args()
    try:
        result=materialize_batch(manifest_path=args.task_manifest,srt_path=args.srt,audit_path=args.audit,
            run_path=args.run,run_artifact_path=args.run_artifact,assets_path=args.assets,
            assets_artifact_path=args.assets_artifact,output_dir=args.out_dir,
            max_passes_per_occurrence=args.max_passes_per_occurrence)
    except (OSError,ValueError,KeyError,TypeError) as exc:parser.error(str(exc))
    print(json.dumps({k:result[k] for k in ('occurrence_count','restore_stage_count','restored_editor_cues_total','publish_ready')}))


if __name__=='__main__':main()
