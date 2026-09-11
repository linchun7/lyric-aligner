#!/usr/bin/env python3
"""Materialize one canonical-text occurrence on its immutable editor topology.

This restores an editor baseline; it does not promote inferred lyric clocks.
The new output has its own audit/artifact and must undergo fresh product QA.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,re,sys
from dataclasses import replace
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from lyric_aligner import __version__
from lyric_aligner.assets.bindings import bindings_from_payload
from lyric_aligner.contracts.artifacts import build_artifact_manifest,atomic_write_json,sha256_file,canonical_json_sha256
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight,declared_input_paths
from lyric_aligner.qa.final_integrity import validate_srt_report_binding,read_audit_rows
from lyric_aligner.srt import Cue,parse_srt_strict,parse_time,cue_id,text_sha256
from lyric_aligner.text.canonical_lyrics import parse_canonical_lyrics
from lyric_aligner.text_repair import CanonicalLine,_normalize_for_match,parse_srt_text
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_current import smart_repair_srt_text,SMART_POLICY_ID
from lyric_aligner.timeline.editor_preservation import POLICY_ID,REGION_POLICY_ID,canonical_editor_regions,canonical_editor_cues,resolve_single_cue_suffix
from scripts.task_contract import load_task_manifest,verify_manifest_inputs,resolve_manifest_record
from scripts.v4_fuse_evidence import _validate_artifact,_load_timelines,_RUN_ROLES
from scripts.v4_materialize_calibrated_alignment import _write_csv,_write_srt
from scripts.v4_smart_repair import _json_safe


SMART_INPUT_POLICY_ID = "editor-lexical-observation-input-1.0"
_EDITOR_OBSERVATION_CACHE = {}


def _editor_observation_context(*, source_path, bindings, assets_artifact_id):
    """Build the immutable editor/Smart observation once per input identity.

    Batch preservation changes only the downstream baseline after each restored
    region. Re-running Smart over the same immutable source SRT and canonical
    bindings for every region is expensive and semantically redundant. Cache
    only the observation layer; ownership, neighbor, overlap, lineage and output
    integrity checks still run for every region.
    """
    source_sha=sha256_file(source_path)
    key=(source_sha,str(assets_artifact_id),SMART_POLICY_ID,SMART_INPUT_POLICY_ID)
    cached=_EDITOR_OBSERVATION_CACHE.get(key)
    if cached is not None:
        source,repaired,lexical_ordinals,input_preparation,smart,canonical=cached
        return list(source),list(repaired),list(lexical_ordinals),dict(input_preparation),smart,list(canonical),source_sha
    source=parse_srt_strict(source_path)
    timed=[];canonical=[]
    for ordinal,binding in enumerate(bindings):
        for line in parse_canonical_lyrics(Path(binding.canonical_lyric_path),original_index_by_timestamp=binding.original_index_by_timestamp):
            normalized=_normalize_for_match(line.text)
            if not normalized:continue
            index=len(timed)
            timed.append(TimedCanonicalOccurrence(index,Path(binding.canonical_lyric_path).name,ordinal,
                line.time_ms,line.text,normalized,line.tokens,line.timing_format))
            canonical.append(CanonicalLine(index,Path(binding.canonical_lyric_path).name,line.text,normalized,ordinal))
    smart_input,lexical_ordinals,input_preparation=_prepare_smart_input(source_path.read_text(encoding='utf-8-sig'),source)
    repaired_text,smart=smart_repair_srt_text(smart_input,timed,canonical)
    _,parsed=parse_srt_text(repaired_text)
    if len(parsed)!=len(lexical_ordinals):raise ValueError('Smart changed editor cue count')
    repaired=list(source)
    for source_index,p in zip(lexical_ordinals,parsed):
        if int(p.number)!=source[source_index].number:raise ValueError('Smart changed editor cue identity')
        repaired[source_index]=Cue(int(p.number),parse_time(p.timing.split('-->')[0]),
            parse_time(p.timing.split('-->')[1]),p.text)
    if len(_EDITOR_OBSERVATION_CACHE)>=8:_EDITOR_OBSERVATION_CACHE.clear()
    _EDITOR_OBSERVATION_CACHE[key]=(tuple(source),tuple(repaired),tuple(lexical_ordinals),dict(input_preparation),smart,tuple(canonical))
    return source,repaired,lexical_ordinals,input_preparation,smart,canonical,source_sha


def _prepare_smart_input(source_text, source):
    # Strict parsing has already validated every original block. Punctuation-only
    # observations have no lexical anchor, but keep their original cue identity.
    mapping=[i for i,c in enumerate(source) if _normalize_for_match(c.text)]
    if not mapping:
        raise ValueError('editor source has no lexical observations')
    excluded=[c.number for c in source if not _normalize_for_match(c.text)]
    prepared=source_text
    if excluded:
        blocks=re.split(r"\n\s*\n",source_text.replace('\r\n','\n').replace('\r','\n').strip())
        if len(blocks)!=len(source):raise ValueError('editor input block identity differs')
        prepared='\n\n'.join(blocks[i] for i in mapping)+'\n'
    return prepared,mapping,dict(policy_id=SMART_INPUT_POLICY_ID,
        nonlexical_source_cue_numbers=excluded,lexical_to_source_ordinals=mapping,
        input_text_sha256=hashlib.sha256(prepared.encode('utf-8')).hexdigest())


def _absolute_canonical_content_prefix(binding, selected_lines):
    """Map a contiguous timeline slice onto its bound canonical text stream."""
    if not selected_lines:
        raise ValueError('selected canonical lines are empty')
    try:
        indices=[int(row['canonical_line_index']) for row in selected_lines]
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError('selected canonical lines lack integer canonical indices') from exc
    if indices != list(range(indices[0],indices[-1]+1)):
        raise ValueError('selected canonical lines are not a continuous bound interval')
    bound=parse_canonical_lyrics(Path(binding.canonical_lyric_path),
        original_index_by_timestamp=binding.original_index_by_timestamp)
    indexed={line.index:line for line in bound}
    expected=[]
    for index,row in zip(indices,selected_lines):
        line=indexed.get(index)
        actual=_normalize_for_match(row.get('text',''))
        if line is None or not actual or actual!=_normalize_for_match(line.text):
            raise ValueError('selected canonical lines differ from bound canonical lyrics')
        expected.append(_normalize_for_match(line.text))
    if ''.join(expected)!=''.join(_normalize_for_match(row['text']) for row in selected_lines):
        raise ValueError('selected canonical stream cannot express a bound interval')
    return sum(len(_normalize_for_match(line.text)) for line in bound if line.index<indices[0])


def _canonical_line_claims(row):
    """Read old single-line and newer multi-line ownership fields.

    Older preservation CSVs only have ``canonical_line_index``.  A cue which
    crosses a canonical line boundary deliberately leaves that field empty in
    newer outputs and records its complete character span instead.  Keep the
    two representations separate here so a malformed multi-line row cannot be
    silently treated as an old single-line row.
    """
    single_raw = row.get('canonical_line_index')
    single = None
    if single_raw not in (None, ''):
        try:
            single = int(single_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError('canonical ownership has an invalid line index') from exc
        if single < 0:
            raise ValueError('canonical ownership has a negative line index')

    multiple_raw = row.get('canonical_line_indices')
    multiple = None
    if multiple_raw not in (None, ''):
        try:
            parsed = json.loads(multiple_raw) if isinstance(multiple_raw, str) else multiple_raw
        except (TypeError, ValueError, json.JSONDecodeError):
            # A few legacy audit writers used a comma-separated list.
            parsed = [part.strip() for part in str(multiple_raw).split(',') if part.strip()]
        if not isinstance(parsed, list) or not parsed:
            raise ValueError('canonical ownership has an invalid line-index list')
        try:
            multiple = [int(value) for value in parsed]
        except (TypeError, ValueError) as exc:
            raise ValueError('canonical ownership has an invalid line-index list') from exc
        if any(value < 0 for value in multiple) or len(set(multiple)) != len(multiple):
            raise ValueError('canonical ownership has an invalid line-index list')
        if single is not None and single not in multiple:
            raise ValueError('canonical ownership line-index fields disagree')
    claimed = set(multiple if multiple is not None else (() if single is None else (single,)))
    return single, multiple, claimed


def _canonical_content_span(row):
    """Return a complete canonical character span, or ``None`` for legacy rows."""
    start_raw = row.get('canonical_content_start')
    end_raw = row.get('canonical_content_end')
    present = (start_raw not in (None, ''), end_raw not in (None, ''))
    if present[0] != present[1]:
        raise ValueError('canonical ownership has incomplete content offsets')
    if not any(present):
        return None
    try:
        start, end = int(start_raw), int(end_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('canonical ownership has invalid content offsets') from exc
    if start < 0 or end <= start:
        raise ValueError('canonical ownership has invalid content offsets')
    return start, end


def _select_region_target_positions(*, baseline, baseline_rows, candidate_positions,
                                    selected_lines, content_prefix, bound_stream):
    """Select a region from old or already-preserved baseline ownership.

    The selection is whole-cue only.  A valid character span is preferred for
    split/cross-line cues; a lone canonical line index remains supported for
    legacy one-line rows.  Any span that intersects the requested region only
    partially, or cannot be verified against the cue text, fails closed.
    """
    line_ids = {int(row['canonical_line_index']) for row in selected_lines}
    region_start = content_prefix
    region_end = region_start + sum(len(_normalize_for_match(row['text'])) for row in selected_lines)
    selected = []
    for position in candidate_positions:
        row = baseline_rows[position]
        single, multiple, claimed = _canonical_line_claims(row)
        span = _canonical_content_span(row)
        span_selected = False
        if span is not None:
            start, end = span
            if end > len(bound_stream):
                raise ValueError('canonical ownership content offsets exceed bound stream')
            if bound_stream[start:end] != _normalize_for_match(baseline[position].text):
                raise ValueError('canonical ownership does not match baseline cue')
            overlaps = start < region_end and end > region_start
            if overlaps and (start < region_start or end > region_end):
                raise ValueError('canonical ownership partially overlaps requested region')
            span_selected = region_start <= start and end <= region_end
            if span_selected and claimed and not claimed.issubset(line_ids):
                raise ValueError('canonical ownership crosses requested region')
            if not span_selected and claimed.intersection(line_ids):
                raise ValueError('canonical ownership conflicts with requested region')
        line_selected = bool(claimed.intersection(line_ids))
        if line_selected and span is None:
            # A blank single-line index cannot represent a split cue without
            # its character span.  A present single index remains the legacy
            # selector, even when an older writer also emitted a multi-line
            # advisory list without offsets.
            if single is None:
                raise ValueError('canonical ownership is incomplete for region replay')
        if span_selected or line_selected:
            selected.append(position)
    if not selected or selected != list(range(selected[0], selected[-1] + 1)):
        raise ValueError('baseline region is missing or noncontiguous')
    # Validate declared spans in cue order, while retaining the legacy path
    # for a one-line row that has no character offsets.  This supports a
    # partially reprocessed region without allowing a split cue to silently
    # lose its ownership metadata.
    cursor = region_start
    for position in selected:
        length = len(_normalize_for_match(baseline[position].text))
        span = _canonical_content_span(baseline_rows[position])
        if span is not None:
            expected = (cursor, cursor + length)
            if span != expected:
                raise ValueError('canonical ownership content spans are not contiguous')
        else:
            single, _, _ = _canonical_line_claims(baseline_rows[position])
            if single is None:
                raise ValueError('canonical ownership content spans are incomplete for region replay')
        cursor += length
    if cursor != region_end:
        raise ValueError('canonical ownership content spans do not cover region')
    return selected


AUTO_REGION_POLICY_ID = 'immutable-editor-auto-region-1.0'


def _choose_editor_region(*, regions, selected, source, repaired, selected_lines,
                          binding, baseline, baseline_rows, target_positions, first, last):
    """Choose a proven, compatible region without model predictions or human gold."""
    outcomes=[];eligible=[]
    bound=parse_canonical_lyrics(Path(binding.canonical_lyric_path),
        original_index_by_timestamp=binding.original_index_by_timestamp)
    bound_stream=''.join(_normalize_for_match(line.text) for line in bound)
    for region in regions:
        span=region['canonical_line_range'];a,b=span;x,y=region['editor_cue_range']
        indices=selected[x:y];lines=selected_lines[a:b]
        item=dict(canonical_region=span,editor_cue_count=len(indices))
        try:
            if not indices or any(source[i].start_ms<first or source[i].end_ms>last for i in indices):
                raise ValueError('editor cue crosses occurrence boundary')
            prefix=_absolute_canonical_content_prefix(binding,lines)
            positions=_select_region_target_positions(baseline=baseline,baseline_rows=baseline_rows,
                candidate_positions=target_positions,selected_lines=lines,
                content_prefix=prefix,bound_stream=bound_stream)
            if ''.join(_normalize_for_match(baseline[i].text) for i in positions)!=_normalize_for_match(' '.join(r['text'] for r in lines)):
                raise ValueError('baseline region canonical content differs')
            restored,_=canonical_editor_cues([source[i] for i in indices],
                [repaired[i] for i in indices],[r['text'] for r in lines])
            retained=[c for i,c in enumerate(baseline) if i not in set(positions)]
            if any(n.start_ms<o.end_ms and o.start_ms<n.end_ms for n in restored for o in retained):
                raise ValueError('restored editor cue overlaps retained subtitle content')
            proposed=baseline[:positions[0]]+restored+baseline[positions[-1]+1:]
            if any(l.start_ms>r.start_ms for l,r in zip(proposed,proposed[1:])):
                raise ValueError('restoration reverses cue order')
            signature=lambda cues:[(c.start_ms,c.end_ms,c.text) for c in cues]
            if signature(restored)==signature([baseline[i] for i in positions]):
                item.update(status='keep',reason='already_preserved')
            else:
                item.update(status='eligible',reason='exact_canonical_stream_and_compatible_neighbors')
                eligible.append((len(indices),a,span))
        except ValueError as exc:
            item.update(status='keep',reason=str(exc))
        outcomes.append(item)
    chosen=sorted(eligible,key=lambda r:(-r[0],r[1]))[0][2] if eligible else None
    return chosen,outcomes


def materialize(*,manifest_path,srt_path,audit_path,run_path,run_artifact_path,
                assets_path,assets_artifact_path,occurrence_id,output_dir,canonical_region=None):
    paths={name:Path(value).resolve() for name,value in dict(manifest=manifest_path,srt=srt_path,
        audit=audit_path,run=run_path,run_artifact=run_artifact_path,assets=assets_path,
        assets_artifact=assets_artifact_path).items()}
    load=lambda path:json.loads(path.read_text(encoding='utf-8-sig'))
    input_hashes={str(p):sha256_file(p) for p in paths.values()}
    manifest=load_task_manifest(paths['manifest']);issues=verify_manifest_inputs(paths['manifest'],manifest)
    if issues:raise ValueError('; '.join(issues))
    fingerprint=manifest['task_fingerprint_sha256']
    run,run_artifact,assets,assets_artifact=(load(paths[k]) for k in ('run','run_artifact','assets','assets_artifact'))
    if run.get('task_fingerprint_sha256')!=fingerprint or assets.get('task_fingerprint_sha256')!=fingerprint:
        raise ValueError('run/assets payload belongs to another task')
    stage=run_artifact.get('stage')
    if stage not in _RUN_ROLES:raise ValueError('unsupported source run stage')
    _validate_artifact(run_artifact,fingerprint=fingerprint,stage=stage,role=_RUN_ROLES[stage],output=paths['run'])
    _validate_artifact(assets_artifact,fingerprint=fingerprint,stage='asset_resolution',role='track_assets',output=paths['assets'])
    timelines,timeline_ids=_load_timelines(run,run_artifact,fingerprint=fingerprint)
    matched=[t['result'] for t in timelines if t['result']['occurrence_id']==occurrence_id]
    if len(matched)!=1:raise ValueError('occurrence must have one bound timeline')
    timeline=matched[0];window=timeline['window'];first,last=window['start_ms'],window['end_ms']
    destination=Path(output_dir).resolve();staging=destination.with_name(destination.name+'.staging')
    for directory in (destination,staging):
        validate_materializer_preflight(manifest_path=paths['manifest'],manifest=manifest,
            direct_inputs=paths,lineage_payloads={'run':run,'assets':assets},output_dir=directory,outputs={})
        if directory.exists():raise FileExistsError('output/staging directory must be new')
    validate_srt_report_binding(paths['srt'],paths['audit'],expected_task_fingerprint=fingerprint)
    baseline=parse_srt_strict(paths['srt']);baseline_rows=read_audit_rows(paths['audit'])
    target_positions=[i for i,r in enumerate(baseline_rows) if r.get('occurrence_id')==occurrence_id]
    if not target_positions:
        raise ValueError('target occurrence is missing from baseline')
    if canonical_region is None and target_positions!=list(range(target_positions[0],target_positions[-1]+1)):
        raise ValueError('whole-occurrence restoration requires contiguous baseline ownership')
    if any(c.start_ms<first or c.end_ms>last for i,c in enumerate(baseline) if i in target_positions):
        raise ValueError('baseline occurrence extends beyond selected window')
    source_path=resolve_manifest_record(paths['manifest'],manifest['inputs']['source_srt'])
    source=parse_srt_strict(source_path)
    selected=[i for i,c in enumerate(source) if c.start_ms<last and c.end_ms>first]
    if not selected:
        raise ValueError('editor cue crosses occurrence boundary or occurrence is empty')
    # A whole-occurrence request owns every intersecting editor cue, so its
    # historical cross-boundary refusal remains unchanged. A region request
    # narrows ``selected`` only after its exact whole-cue proof below; checking
    # it here would let an unselected edge cue veto a safe internal region.
    if canonical_region is None and any(source[i].start_ms<first or source[i].end_ms>last for i in selected):
        raise ValueError('editor cue crosses occurrence boundary or occurrence is empty')
    bindings=bindings_from_payload(assets,verify_files=True)
    for dependency in declared_input_paths({'run':run,'assets':assets}).values():
        if dependency.is_file():input_hashes[str(dependency.resolve())]=sha256_file(dependency)
    input_hashes[str(source_path)]=sha256_file(source_path)
    binding=next(b for b in bindings if b.occurrence_id==occurrence_id)
    if binding.canonical_selection_sha256!=timeline['canonical_selection_sha256'] or binding.track_id!=timeline['track_id']:
        raise ValueError('canonical selection differs between assets and timeline')
    source,repaired,lexical_ordinals,input_preparation,smart,canonical,_=_editor_observation_context(
        source_path=source_path,bindings=bindings,assets_artifact_id=assets_artifact['artifact_id'])
    # Timing must be compared to the actual Smart output, not reconstructed away.
    for i in selected:
        if (repaired[i].number,repaired[i].start_ms,repaired[i].end_ms)!=(source[i].number,source[i].start_ms,source[i].end_ms):
            raise ValueError('Smart changed selected editor timing')
    corrections=[]
    target_binding_ordinal=bindings.index(binding)
    for decision in smart['text_decisions']:
        lexical_index=decision['cue_ordinal'];i=lexical_ordinals[lexical_index];span=decision.get('canonical_span',[])
        if i not in selected or decision.get('reason')!='layout_boundary_insertion_requires_review':continue
        if tuple(decision.get('cue_span',()))!=(lexical_index,lexical_index+1) or len(span)!=2 or span[1]!=span[0]+1:continue
        line=canonical[span[0]]
        if line.source_ordinal!=target_binding_ordinal:raise ValueError('text repair crosses occurrence identity')
        corrected=resolve_single_cue_suffix(repaired[i].text,line.text)
        if corrected is not None:
            corrections.append(dict(original_cue=source[i].number,canonical_ordinal=span[0],
                before_sha256=text_sha256(repaired[i].text),after_sha256=text_sha256(corrected),
                basis='bound_single_cue_single_word_suffix'))
            repaired[i]=replace(repaired[i],text=corrected)
    selected_lines=timeline['lines']
    if not selected_lines:
        raise ValueError('timeline has no canonical lines')
    # ``canonical_content_start/end`` are deliberately absolute coordinates in
    # the complete bound canonical stream. A production evaluation may begin
    # after canonical index 0 when an occurrence boundary clips an earlier line.
    # Persist the absolute coordinate at which this evaluated occurrence stream
    # begins so downstream hybrid validation can translate absolute ownership
    # spans without weakening the established full-LRC coordinate contract.
    evaluation_canonical_content_origin=_absolute_canonical_content_prefix(
        binding,[selected_lines[0]])
    # Region matching needs lexical content, while punctuation/music-marker cues
    # remain immutable retained subtitle content. Whole-occurrence restoration
    # keeps its historical all-cue contract and therefore does not use this filter.
    region_selected=(selected if canonical_region is None else
        [i for i in selected if _normalize_for_match(repaired[i].text)])
    auto_requested=canonical_region=='auto'
    auto_outcomes=[]
    auto_keep=False
    if auto_requested:
        regions=canonical_editor_regions([repaired[i] for i in region_selected],[r['text'] for r in selected_lines])
        canonical_region,auto_outcomes=_choose_editor_region(regions=regions,selected=region_selected,
            source=source,repaired=repaired,selected_lines=selected_lines,binding=binding,
            baseline=baseline,baseline_rows=baseline_rows,target_positions=target_positions,first=first,last=last)
        auto_keep=canonical_region is None
    effective_policy=POLICY_ID
    content_prefix=None
    if canonical_region is not None:
        if (not isinstance(canonical_region,(list,tuple)) or len(canonical_region)!=2
            or any(type(v) is not int for v in canonical_region)):
            raise ValueError('canonical region must be two integer line positions')
        regions=canonical_editor_regions([repaired[i] for i in region_selected],[r['text'] for r in selected_lines])
        region=next((r for r in regions if r['canonical_line_range']==list(canonical_region)),None)
        if region is None:raise ValueError('requested canonical region is not a unique exact whole-cue region')
        a,b=canonical_region
        selected_lines=selected_lines[a:b]
        x,y=region['editor_cue_range'];selected=region_selected[x:y]
        if not selected or any(source[i].start_ms<first or source[i].end_ms>last for i in selected):
            raise ValueError('editor cue crosses occurrence boundary or occurrence is empty')
        content_prefix=_absolute_canonical_content_prefix(binding,selected_lines)
        bound=parse_canonical_lyrics(Path(binding.canonical_lyric_path),
            original_index_by_timestamp=binding.original_index_by_timestamp)
        bound_stream=''.join(_normalize_for_match(line.text) for line in bound)
        target_positions=_select_region_target_positions(
            baseline=baseline,baseline_rows=baseline_rows,
            candidate_positions=target_positions,selected_lines=selected_lines,
            content_prefix=content_prefix,bound_stream=bound_stream)
        if ''.join(_normalize_for_match(baseline[i].text) for i in target_positions)!=_normalize_for_match(' '.join(r['text'] for r in selected_lines)):
            raise ValueError('baseline region does not contain exactly the selected canonical stream')
        corrections=[r for r in corrections if r['original_cue'] in {source[i].number for i in selected}]
        effective_policy=REGION_POLICY_ID
    if auto_keep:
        selected=[];target_positions=[];restored=[];ownership=[];content_prefix=0;corrections=[]
    else:
        if content_prefix is None:
            content_prefix=_absolute_canonical_content_prefix(binding,selected_lines)
        restored,ownership=canonical_editor_cues([source[i] for i in selected],[repaired[i] for i in selected],
            [r['text'] for r in selected_lines])
    fresh=[]
    for cue,owner in zip(restored,ownership):
        owner=dict(owner)
        owner['canonical_content_start']+=content_prefix
        owner['canonical_content_end']+=content_prefix
        index=owner['canonical_line_index']
        owner['canonical_line_index']='' if index is None else selected_lines[index]['canonical_line_index']
        owner['canonical_line_indices']=json.dumps([selected_lines[i]['canonical_line_index'] for i in owner['canonical_line_indices']])
        fresh.append(dict(start_ms=cue.start_ms,end_ms=cue.end_ms,text=cue.text,kind='existing',
            original_cue=cue.number,occurrence_id=occurrence_id,track_id=timeline['track_id'],
            ordinal=timeline['ordinal'],track=f"{timeline['artist']} - {timeline['title']}",
            timing_format='editor_preserved',end_basis='immutable_editor',**{k:v for k,v in owner.items() if k!='original_cue'},
            canonical_selection_sha256=timeline['canonical_selection_sha256'],canonical_text=cue.text,
            canonical_text_sha256=text_sha256(cue.text),text_policy_id=effective_policy))
    rows=[dict(r,upstream_position=i+1,upstream_cue_id=r.get('cue_id','')) for i,r in enumerate(baseline_rows)]
    if target_positions:
        rows[target_positions[0]:target_positions[-1]+1]=fresh
    for position,row in enumerate(rows,1):
        cue=Cue(position,int(row['start_ms']),int(row['end_ms']),row['text'])
        row.update(position=position,cue_number=position,cue_id=cue_id(position,cue),
            text_sha256=text_sha256(cue.text),task_fingerprint_sha256=fingerprint)
    for left,right in zip(rows,rows[1:]):
        if int(left['start_ms'])>int(right['start_ms']):raise ValueError('restoration reverses cue order')
    for new in fresh:
        for old in rows:
            retained_boundary = old.get('occurrence_id')!=occurrence_id or (
                canonical_region is not None and old.get('upstream_position') is not None)
            if retained_boundary and int(new['start_ms'])<int(old['end_ms']) and int(old['start_ms'])<int(new['end_ms']):
                raise ValueError('restored editor cue overlaps retained subtitle content')
    staging.mkdir(parents=True)
    _write_srt(staging/'final.srt',rows);_write_csv(staging/'final.csv',list(baseline_rows[0]),rows)
    validate_srt_report_binding(staging/'final.srt',staging/'final.csv',expected_task_fingerprint=fingerprint)
    actual=parse_srt_strict(staging/'final.srt')
    outside_before=[(c.start_ms,c.end_ms,c.text) for i,c in enumerate(baseline) if i not in target_positions]
    outside_after=[(c.start_ms,c.end_ms,c.text) for c,r in zip(actual,rows) if r.get('upstream_position') is not None]
    if outside_before!=outside_after:raise ValueError('non-target occurrence changed')
    report=dict(schema_version='editor-preservation-materialization-1.0',policy_id=effective_policy,
        canonical_region=canonical_region,
        automatic_selection=None if not auto_requested else dict(policy_id=AUTO_REGION_POLICY_ID,
            action='keep' if auto_keep else 'restore',candidates=auto_outcomes,
            selection_basis='largest compatible exact editor region; earliest canonical position breaks ties'),
        task_fingerprint_sha256=fingerprint,occurrence_id=occurrence_id,inputs=input_hashes,
        evaluation_canonical_content_origin=evaluation_canonical_content_origin,
        baseline_target_cues=len(target_positions),restored_editor_cues=len(restored),
        timing_basis='unchanged_baseline' if auto_keep else 'immutable_editor',model_timing_authority_used=False,
        canonical_stream_verified=not auto_keep,non_target_content_and_timing_unchanged=True,
        text_corrections=corrections,editor_source_cue_numbers=[c.number for c in restored],
        smart_policy_id=SMART_POLICY_ID,smart_input_preparation=input_preparation,smart_report=_json_safe(smart),
        publish_ready=False,qa_status='fresh_product_QA_required')
    atomic_write_json(staging/'preservation.json',report)
    artifact=build_artifact_manifest(task_fingerprint_sha256=fingerprint,stage='editor_preservation',
        algorithm_version=__version__,outputs=(('final_srt',staging/'final.srt'),('audit_csv',staging/'final.csv'),('preservation_report',staging/'preservation.json')),
        normalized_config=dict(policy_id=effective_policy,auto_region_policy_id=AUTO_REGION_POLICY_ID if auto_requested else None,smart_input_policy_id=SMART_INPUT_POLICY_ID,occurrence_id=occurrence_id,canonical_region=canonical_region,evaluation_canonical_content_origin=evaluation_canonical_content_origin,input_sha256=input_hashes),
        upstream_artifact_ids=tuple(sorted({run_artifact['artifact_id'],assets_artifact['artifact_id'],*timeline_ids})))
    # Paths in the artifact must refer to the transaction's final destination.
    for record in artifact['outputs']:record['path']=str(destination/Path(record['path']).name)
    artifact['artifact_id']=canonical_json_sha256({k:v for k,v in artifact.items() if k!='artifact_id'})
    atomic_write_json(staging/'preservation.artifact.json',artifact)
    if any(sha256_file(Path(p))!=digest for p,digest in input_hashes.items()):raise ValueError('input changed during materialization')
    staging.rename(destination)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('task-manifest','srt','audit','run','run-artifact','assets','assets-artifact','out-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--occurrence-id',required=True)
    parser.add_argument('--canonical-region',nargs=2,type=int,metavar=('FIRST','END'),help='Optional half-open positional range from a unique exact region')
    args=parser.parse_args()
    try:
        result=materialize(manifest_path=args.task_manifest,srt_path=args.srt,audit_path=args.audit,
            run_path=args.run,run_artifact_path=args.run_artifact,assets_path=args.assets,
            assets_artifact_path=args.assets_artifact,occurrence_id=args.occurrence_id,output_dir=args.out_dir,canonical_region=args.canonical_region)
    except (OSError,ValueError,KeyError,TypeError) as exc:parser.error(str(exc))
    print(json.dumps({k:result[k] for k in ('baseline_target_cues','restored_editor_cues','canonical_stream_verified','publish_ready')}))

if __name__=='__main__':main()
