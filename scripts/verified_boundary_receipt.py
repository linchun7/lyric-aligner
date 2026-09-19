"""Replay exact human reuse before QA consumes per-edge confirmation.

Only exact final values within the audited human interval receive edge scope.
A confirmation never covers the opposite edge or an unrelated inserted cue.
"""
from pathlib import Path

from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.srt import parse_srt_strict
from scripts.v4_materialize_calibrated_alignment import load_json, load_report, sha256_json
from scripts.v4_reuse_human_boundaries import prepare_reuse


def replay_gap_receipt(artifact_path, artifact):
    from scripts.v4_apply_gap_review import prepare
    artifact_path = Path(artifact_path).resolve()
    if sha256_json({k:v for k,v in artifact.items() if k != 'artifact_sha256'}) != artifact.get('artifact_sha256'):
        raise ValueError('gap receipt hash mismatch')
    paths = {}
    for role, record in artifact['input_files'].items():
        p = (artifact_path.parent/record['path']).resolve()
        if sha256_file(p) != record['sha256']:
            raise ValueError('gap receipt input changed: '+role)
        paths[role+'_path'] = p
    manifest, direct, _, after, decisions, edges = prepare(**{k:paths[k] for k in (
        'manifest_path','lock_path','review_path','report_path','srt_path','prior_receipt_path')})
    if set(direct) != set(artifact['input_files']) or any(paths[k+'_path'] != p.resolve() for k,p in direct.items()):
        raise ValueError('gap receipt replay dependency inventory mismatch')
    for key, expected in [('task_fingerprint_sha256',manifest['task_fingerprint_sha256']),
                          ('source_srt_sha256',manifest['inputs']['source_srt']['sha256']),
                          ('final_audio_sha256',manifest['inputs']['audio']['sha256'])]:
        if artifact.get(key) != expected:
            raise ValueError('gap receipt replay identity mismatch: '+key)
    if decisions != artifact['decisions']:
        raise ValueError('gap receipt decisions differ from replay')
    return direct, after, edges


def verified_qa_edges(*, artifact_path, report_path, srt_path, task_fingerprint, source_srt_sha256, final_audio_sha256):
    artifact_path = Path(artifact_path).resolve()
    artifact = load_json(artifact_path, label='boundary reuse receipt')
    unsigned = {k:v for k,v in artifact.items() if k != 'artifact_sha256'}
    if sha256_json(unsigned) != artifact.get('artifact_sha256'):
        raise ValueError('boundary reuse receipt hash mismatch')
    if artifact.get('schema_version') not in ('human-boundary-reuse-materialization-1.1', 'human-gap-review-materialization-1.0'):
        raise ValueError('boundary receipt lacks replayable input roles')
    for key, expected in [('task_fingerprint_sha256', task_fingerprint),
                          ('source_srt_sha256', source_srt_sha256), ('final_audio_sha256', final_audio_sha256),
                          ('output_report_sha256', sha256_file(report_path)),
                          ('output_srt_sha256', sha256_file(srt_path))]:
        if artifact.get(key) != expected:
            raise ValueError('boundary reuse receipt scope mismatch: '+key)
    if artifact['schema_version'] == 'human-gap-review-materialization-1.0':
        _, after, edges = replay_gap_receipt(artifact_path,artifact)
        _, rows = load_report(report_path)
        normalized = lambda r: {k:str(v) for k,v in r.items() if v is not None and str(v) != ''}
        if len(rows) != len(after) or any(normalized(a)!=normalized(b) for a,b in zip(rows,after)):
            raise ValueError('gap final report differs from replay')
        cues = parse_srt_strict(srt_path)
        if len(cues) != len(after) or any((c.start_ms,c.end_ms,c.text.strip()) !=
                (int(r['start_ms']),int(r['end_ms']),r['text'].strip()) for c,r in zip(cues,after)):
            raise ValueError('gap final SRT differs from replay')
        return edges, artifact['artifact_sha256']
    inputs = {}
    for role in ('manifest', 'lock', 'gold', 'report', 'srt'):
        record = artifact['input_files'][role]
        p = (artifact_path.parent/record['path']).resolve()
        if sha256_file(p) != record['sha256']:
            raise ValueError('boundary reuse input changed: '+role)
        inputs[role+'_path'] = p
    manifest, lock, gold, _, _, before, after, decisions = prepare_reuse(**inputs)
    for key, expected in [('task_fingerprint_sha256', manifest['task_fingerprint_sha256']),
                          ('final_audio_sha256', manifest['inputs']['audio']['sha256']),
                          ('selection_lock_sha256', lock['lock_sha256']),
                          ('human_gold_artifact_sha256', gold['artifact_sha256'])]:
        if artifact.get(key) != expected:
            raise ValueError('boundary reuse replay identity mismatch: '+key)
    if decisions != artifact.get('decisions') or sha256_json(decisions) != artifact.get('decisions_sha256'):
        raise ValueError('boundary reuse decisions differ from fresh replay')
    _, current = load_report(report_path)
    def normalized(row):
        return {k:str(v) for k,v in row.items() if v is not None and str(v) != ''}
    if len(current) != len(after) or any(normalized(a) != normalized(b) for a,b in zip(current, after)):
        raise ValueError('boundary reuse output rows differ from fresh replay')
    cues = parse_srt_strict(srt_path)
    if len(cues) != len(after) or any(
        (c.start_ms,c.end_ms,c.text.strip()) != (int(r['start_ms']),int(r['end_ms']),r['text'].strip())
        for c,r in zip(cues,after)
    ):
        raise ValueError('boundary reuse final SRT differs from fresh replay')
    edges = {}
    for decision in decisions:
        position, kind = decision['output_position'], decision['boundary_kind']
        if position is None or decision['reason'] not in ('exact_audio_and_lexical_target', 'already_within_confirmed_tolerance'):
            continue
        value = int(current[position-1][kind+'_ms'])
        if abs(value-decision['confirmed_ms']) <= decision['uncertainty_ms']:
            edges[position, kind] = value
    return edges, artifact['artifact_sha256']
