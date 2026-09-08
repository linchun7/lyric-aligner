"""Build an offline A/B review page with wider exact-audio context."""
from __future__ import annotations

import argparse
import base64
import copy
import json
import math
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight
from scripts.v4_apply_gap_review import prepare
from scripts.v4_materialize_calibrated_alignment import load_json, sha256_json
from scripts.v4_plan_boundary_refinement import resolve_manifest_file


def render_page(template, payload, script):
    replacements={'PAYLOAD':json.dumps(payload,ensure_ascii=False).replace('<','\\u003c'),'SCRIPT':script}
    return re.sub(r'__(PAYLOAD|SCRIPT)__',lambda match:replacements[match.group(1)],template)


def build(job_path, output_dir, expanded_ids, context_ms=30000):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    job_path, output_dir = Path(job_path).resolve(), Path(output_dir).resolve()
    job = load_json(job_path,label='gap review job')
    path = lambda value: (job_path.parent/value).resolve()
    inputs = {k+'_path':path(job['gap_review'][k]) for k in ('lock','review','prior_receipt')}
    inputs.update(manifest_path=path(job['task_manifest']),report_path=path(job['report']),srt_path=path(job['srt']))
    manifest,direct,rows,after,_,_=prepare(**inputs)
    old_lock=load_json(inputs['lock_path'],label='gap lock')
    review=load_json(inputs['review_path'],label='review')
    if not set(expanded_ids).issubset({c['id'] for c in old_lock['cases']}) or context_ms <= 0:
        raise ValueError('invalid wider-context selection')
    validate_materializer_preflight(manifest_path=inputs['manifest_path'],manifest=manifest,
        direct_inputs={**direct,'job':job_path},lineage_payloads={'lock':old_lock},output_dir=output_dir,outputs={})
    if output_dir.exists():raise FileExistsError('A/B output directory must be new')
    audio_path=resolve_manifest_file(manifest['inputs']['audio'],label='audio')
    info=sf.info(audio_path)
    output_dir.mkdir(parents=True)
    lock=copy.deepcopy(old_lock)
    lock.update(parent_lock_sha256=old_lock['lock_sha256'],prior_review_sha256=sha256_file(inputs['review_path']))
    ui_cases=[]
    for i,c in enumerate(lock['cases'],1):
        clip=output_dir/f'gap_{i}.wav'
        if c['id'] in expanded_ids:
            first=max(0,int(round((c['reference_start_ms']-context_ms)*info.samplerate/1000)))
            last=min(info.frames,int(round((c['reference_end_ms']+context_ms)*info.samplerate/1000)))
            samples,sr=sf.read(audio_path,start=first,stop=last,dtype='float32',always_2d=True)
            factor=math.gcd(sr,16000)
            samples=resample_poly(samples.mean(axis=1),16000//factor,sr//factor)
            sf.write(clip,samples,16000,subtype='PCM_16')
            c.update(clip_start_ms=first*1000/sr,clip_end_ms=last*1000/sr,source_start_frame=first,source_sample_rate=sr,clip_sha256=sha256_file(clip))
        else:shutil.copyfile(inputs['lock_path'].parent/f'gap_{i}.wav',clip)
        samples,_=sf.read(clip,dtype='float32')
        peaks=[float(np.max(np.abs(chunk))) if len(chunk) else 0 for chunk in np.array_split(samples,600)]
        index=next(i for i,r in enumerate(rows) if all(r[k]==c[k] for k in ('track','text','lrc_indices')))
        def neighbor(index):
            if not 0<=index<len(rows) or rows[index]['track']!=c['track']:return None
            result={k:(int(after[index][k]) if k.endswith('_ms') else after[index][k]) for k in ('text','start_ms','end_ms','lrc_indices')}
            result['review_id']=next((target['id'] for target in lock['cases'] if all(rows[index][k]==target[k] for k in ('track','text','lrc_indices'))),None)
            return result
        ui_cases.append({**c,'audio':'data:audio/wav;base64,'+base64.b64encode(clip.read_bytes()).decode('ascii'),
                         'peaks':peaks,'previous':neighbor(index-1),'next':neighbor(index+1)})
    lock['lock_sha256']=sha256_json({k:v for k,v in lock.items() if k!='lock_sha256'})
    (output_dir/'selection.lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    shutil.copyfile(inputs['review_path'],output_dir/'previous-review.json')
    payload=dict(lock=lock,cases=ui_cases,previous=review)
    script=Path(__file__).with_name('gap_review_ui.js').read_text(encoding='utf-8')
    template=Path(__file__).with_name('gap_review_ui.html').read_text(encoding='utf-8')
    (output_dir/'index.html').write_text(render_page(template,payload,script),encoding='utf-8')
    (output_dir/'build.json').write_text(json.dumps({'schema_version':'gap-ab-review-build-1.0',
        'job_path':str(job_path),'job_sha256':sha256_file(job_path),'lock_sha256':lock['lock_sha256'],
        'expanded_ids':expanded_ids,'script_sha256':sha256_file(Path(__file__).with_name('gap_review_ui.js'))},indent=2),encoding='utf-8')
    return lock


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--expand-case',action='append',default=[])
    parser.add_argument('--context-seconds',type=float,default=30,help='Audio context on each side; default 30 seconds')
    args=parser.parse_args()
    print(json.dumps({'lock_sha256':build(args.job,args.output_dir,args.expand_case,int(args.context_seconds*1000))['lock_sha256']}))
