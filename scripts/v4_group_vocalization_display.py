"""Materialize lossless display groups from a replayed human-gap result.

This is a display derivative, not a replacement acoustic report or release seal.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.vocalization_display import POLICY, group_display_rows
from scripts.v4_materialize_calibrated_alignment import load_json, sha256_json, _write_srt
from scripts.verified_boundary_receipt import replay_gap_receipt, verified_qa_edges


def prepare(receipt, report, srt):
    artifact = load_json(receipt,label='gap receipt')
    if artifact.get('schema_version') != 'human-gap-review-materialization-1.0':
        raise ValueError('display grouping requires replayable gap receipt')
    direct, rows, _ = replay_gap_receipt(receipt,artifact)
    verified_qa_edges(artifact_path=receipt,report_path=report,srt_path=srt,
        task_fingerprint=artifact['task_fingerprint_sha256'],source_srt_sha256=artifact['source_srt_sha256'],
        final_audio_sha256=artifact['final_audio_sha256'])
    review = load_json(direct['review'],label='review')
    after, groups = group_display_rows(rows,review['records'])
    for group in groups:
        group['member_row_sha256'] = [sha256_json(rows[k-1]) for k in group['member_positions']]
    return artifact,direct,after,groups


def verify(output_dir):
    output_dir = Path(output_dir).resolve()
    receipt = load_json(output_dir/'display.artifact.json',label='display receipt')
    if receipt.get('schema_version') != 'vocalization-display-materialization-1.0' or receipt.get('policy_id') != POLICY:
        raise ValueError('unsupported display receipt')
    if sha256_json({k:v for k,v in receipt.items() if k!='artifact_sha256'}) != receipt['artifact_sha256']:
        raise ValueError('display receipt hash mismatch')
    paths = {k:(output_dir/v['path']).resolve() for k,v in receipt['inputs'].items()}
    if set(paths) != {'receipt','report','srt'} or any(sha256_file(p)!=receipt['inputs'][k]['sha256'] for k,p in paths.items()):
        raise ValueError('display input mismatch')
    upstream,_,after,groups = prepare(**paths)
    if (receipt.get('display_only') is not True or receipt.get('publish_ready') is not False or
        receipt.get('acoustic_boundary_improvement_claimed') is not False or receipt.get('output_cue_count') != len(after)):
        raise ValueError('display receipt scope is overstated')
    if groups != receipt['groups'] or any(receipt.get(k)!=upstream[k] for k in ('task_fingerprint_sha256','source_srt_sha256','final_audio_sha256')):
        raise ValueError('display replay mismatch')
    actual = parse_srt_strict(output_dir/'display.srt')
    if len(actual)!=len(after) or any((c.start_ms,c.end_ms,c.text)!=(r['start_ms'],r['end_ms'],r['text']) for c,r in zip(actual,after)):
        raise ValueError('display SRT mismatch')
    if sha256_file(output_dir/'display.srt') != receipt['output_srt_sha256']:
        raise ValueError('display output hash mismatch')
    return receipt


def materialize(*, receipt, report, srt, output_dir):
    paths = {k:Path(v).resolve() for k,v in dict(receipt=receipt,report=report,srt=srt).items()}
    upstream,direct,after,groups = prepare(**paths)
    manifest = load_json(direct['manifest'],label='manifest')
    output_dir = Path(output_dir).resolve()
    staging = output_dir.with_name(output_dir.name+'.staging')
    for destination in (output_dir,staging):
        validate_materializer_preflight(manifest_path=direct['manifest'],manifest=manifest,
            direct_inputs={**direct,**{'display_'+k:v for k,v in paths.items()}},lineage_payloads={},output_dir=destination,outputs={})
        if destination.exists():
            raise FileExistsError('display output must be new')
    staging.mkdir(parents=True)
    _write_srt(staging/'display.srt',after)
    result = dict(schema_version='vocalization-display-materialization-1.0',policy_id=POLICY,
        **{k:upstream[k] for k in ('task_fingerprint_sha256','source_srt_sha256','final_audio_sha256')},
        inputs={k:dict(path=os.path.relpath(v,output_dir),sha256=sha256_file(v)) for k,v in paths.items()},
        groups=groups,output_srt_sha256=sha256_file(staging/'display.srt'),output_cue_count=len(after),
        producer_code_sha256=sha256_file(Path(__file__)),display_only=True,publish_ready=False,
        acoustic_boundary_improvement_claimed=False)
    result['artifact_sha256']=sha256_json(result)
    (staging/'display.artifact.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    verify(staging)
    staging.rename(output_dir)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('receipt','report','srt','output-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    result=materialize(**vars(parser.parse_args()))
    print(json.dumps({'groups':len(result['groups']),'cues':result['output_cue_count'],'publish_ready':False}))
