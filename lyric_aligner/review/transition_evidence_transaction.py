"""Authorize transition review decisions from independent shadow evidence."""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from lyric_aligner.contracts.artifacts import sha256_file

class TransitionAuthorizationError(ValueError): pass

def _load(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value,dict): raise TransitionAuthorizationError(f"expected JSON object: {path}")
    return value
def _text(value: Any,label: str)->str:
    result=str(value or "").strip()
    if not result: raise TransitionAuthorizationError(f"missing {label}")
    return result
def _num(value: Any,label: str)->float:
    try: result=float(value)
    except (TypeError,ValueError) as exc: raise TransitionAuthorizationError(f"invalid {label}") from exc
    if result!=result or result in (float("inf"),float("-inf")): raise TransitionAuthorizationError(f"invalid {label}")
    return result
def _bind(payload: dict[str,Any], *, task:str, run_sha:str, artifact_id:str, artifact_sha:str, label:str)->None:
    for key,value in {"task_fingerprint_sha256":task,"run_sha256":run_sha,"run_artifact_id":artifact_id,"run_artifact_sha256":artifact_sha}.items():
        if payload.get(key)!=value: raise TransitionAuthorizationError(f"{label} {key} mismatch")
def _source_sha(task:dict[str,Any],root:Path)->str:
    record=task.get("inputs",{}).get("source_srt")
    if not isinstance(record,dict): raise TransitionAuthorizationError("task manifest has no source_srt")
    path=Path(_text(record.get("path"),"source_srt path")); path=path if path.is_absolute() else root/path
    actual=sha256_file(path.resolve())
    if actual!=record.get("sha256"): raise TransitionAuthorizationError("source_srt hash does not match task manifest")
    return actual
def _interval(issue:dict[str,Any])->tuple[float,float]:
    start,end=_num(issue.get("interval_start"),"interval_start"),_num(issue.get("interval_end"),"interval_end")
    if start<0 or end<=start: raise TransitionAuthorizationError("invalid issue interval")
    return start,end
def _overlap_interval(issue:dict[str,Any],row:dict[str,Any],window:float)->list[float]|None:
    start,end=_interval(issue); boundary=_num(row.get("nominal_boundary"),"nominal_boundary")
    pairs=[p for p in row.get("paired_evidence",[]) if isinstance(p,dict) and p.get("overlap_like") is True]
    centers=[_num(p.get("mix_center"),"paired mix_center") for p in pairs if abs(_num(p.get("mix_center"),"paired mix_center")-boundary)<=window/2]
    if not centers:return None
    center=min(centers,key=lambda x:abs(x-boundary)); offset=_num(row.get("mix_feature_global_start_seconds",0),"feature start"); intervals=[]
    for item in row.get("windows",[]):
        if not isinstance(item,dict) or item.get("global_mix_center")!=center or item.get("strong") is not True: continue
        raw=item.get("mix_window_local")
        if isinstance(raw,list) and len(raw)==2: intervals.append((_num(raw[0],"window start")+offset,_num(raw[1],"window end")+offset))
    if len(intervals)<2:return None
    left,right=max(x[0] for x in intervals),min(x[1] for x in intervals)
    if right<=left:left,right=center-window/2,center+window/2
    left,right=max(start,left),min(end,right)
    return [left,right] if right>left else None

def authorize_transition_review(*,task_manifest:Path,run_path:Path,run_artifact_path:Path,template_path:Path,lexical_path:Path,positional_path:Path,decisions_out:Path,report_out:Path,artifact_out:Path|None=None,repository_root:Path|None=None)->dict[str,Any]:
    root=(repository_root or Path(__file__).resolve().parents[2]).resolve(); task,run,artifact=_load(task_manifest),_load(run_path),_load(run_artifact_path); template,lexical,positional=_load(template_path),_load(lexical_path),_load(positional_path)
    task_fp,artifact_id=_text(task.get("task_fingerprint_sha256"),"task fingerprint"),_text(artifact.get("artifact_id"),"artifact id")
    if template.get("task_fingerprint_sha256")!=task_fp: raise TransitionAuthorizationError("template task fingerprint mismatch")
    run_sha,artifact_sha=sha256_file(run_path),sha256_file(run_artifact_path); _bind(lexical,task=task_fp,run_sha=run_sha,artifact_id=artifact_id,artifact_sha=artifact_sha,label="lexical"); _bind(positional,task=task_fp,run_sha=run_sha,artifact_id=artifact_id,artifact_sha=artifact_sha,label="positional")
    source_sha=_source_sha(task,root)
    if lexical.get("source_srt_sha256")!=source_sha: raise TransitionAuthorizationError("lexical source_srt hash mismatch")
    authority=positional.get("authority")
    if not isinstance(authority,dict) or authority.get("shadow_only") is not True or authority.get("automatic_review_decision") not in (False,None) or authority.get("timing_mutation_performed") is not False or positional.get("timing_mutation_performed") is not False: raise TransitionAuthorizationError("positional evidence is not shadow-only")
    lex_rows={}
    for row in lexical.get("transitions",[]):
        key=_text(row.get("issue_candidate_id"),"lexical candidate id")
        if key in lex_rows: raise TransitionAuthorizationError("duplicate lexical transition")
        lex_rows[key]=row
    pos_rows={}
    for row in positional.get("evidence",[]):
        key=_text(row.get("transition_index"),"positional transition index")
        if key in pos_rows: raise TransitionAuthorizationError("duplicate positional transition index")
        pos_rows[key]=row
    items=template.get("review_items")
    if not isinstance(items,list) or len(items)!=len(lex_rows) or len(items)!=len(pos_rows): raise TransitionAuthorizationError("evidence transition count does not match template")
    decisions=deepcopy(template); counts={"resolved_clear":0,"confirmed_overlap":0,"null":0}; decision_map={}; used=set()
    for item in decisions["review_items"]:
        issue=item.get("issue")
        if not isinstance(issue,dict) or issue.get("kind")!="transition_ambiguity" or issue.get("code")!="ambiguous_source_occurrence": raise TransitionAuthorizationError("template contains unsupported issue")
        candidate=_text(issue.get("candidate_id"),"issue candidate id"); lex=lex_rows.get(candidate)
        if lex is None: raise TransitionAuthorizationError(f"missing lexical transition {candidate}")
        pair=(issue.get("left_occurrence_id"),issue.get("right_occurrence_id")); transitions=[row for row in run.get("transitions",[]) if (row.get("left_occurrence_id"),row.get("right_occurrence_id"))==pair]
        if len(transitions)!=1: raise TransitionAuthorizationError(f"run transition identity is not unique for {candidate}")
        boundary=_num(transitions[0].get("nominal_boundary"),"run transition boundary"); matches=[row for row in positional.get("evidence",[]) if abs(_num(row.get("nominal_boundary"),"positional boundary")-boundary)<=1e-6]
        if len(matches)!=1: raise TransitionAuthorizationError(f"positional transition identity is not unique for {candidate}")
        pos=matches[0]; index=_text(pos.get("transition_index"),"positional transition index")
        if index in used: raise TransitionAuthorizationError("positional transition reused")
        used.add(index); lexical_clear=lex.get("recommendation")=="clear_candidate"; identity,order=lex.get("identity_support") or {},lex.get("order_support") or {}; action=(pos.get("recommendation") or {}).get("action"); decision=None
        if action=="overlap_candidate_advisory":
            interval=_overlap_interval(issue,pos,_num((positional.get("policy") or {}).get("window_seconds"),"window_seconds"))
            if interval is not None: decision={"action":"confirmed_overlap","rationale":"Positional shadow evidence has sufficient fine support on both sides and same-window overlap-like paired evidence; lexical evidence is retained as text-sequential context and does not veto instrumental overlap.","confirmed_interval":interval}
        elif lexical_clear and (action=="clear_sequential_advisory" or (action=="unresolved" and identity.get("enough_identity") is True and identity.get("ambiguous_cue_count")==0 and order.get("lexical_order_clear") is True and _num(order.get("sequential_gap_ms"),"sequential gap")>=0 and _num(order.get("last_left_editor_end_ms"),"last left end")<_num(order.get("first_right_editor_start_ms"),"first right start"))): decision={"action":"resolved_clear","rationale":"Lexical occurrence authority resolves SOURCE OCCURRENCE identity from unique ordered editor witnesses; this does not prove absence of instrumental crossfade."}
        item["decision"]=decision; key=decision["action"] if decision else "null"; counts[key]+=1; decision_map[candidate]={"action":key,"transition_index":pos.get("transition_index"),"confirmed_interval":decision.get("confirmed_interval") if decision else None}
    decisions_out.parent.mkdir(parents=True,exist_ok=True); report_out.parent.mkdir(parents=True,exist_ok=True); decisions_out.write_text(json.dumps(decisions,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    report={"schema_version":"transition-review-authorization-1.0","task_fingerprint_sha256":task_fp,"run_sha256":run_sha,"run_artifact_id":artifact_id,"run_artifact_sha256":artifact_sha,"source_srt_sha256":source_sha,"evidence_hashes":{"template":sha256_file(template_path),"lexical":sha256_file(lexical_path),"positional":sha256_file(positional_path)},"authority":"review_decision_authorization_only","timing_mutation_performed":False,"text_mutation_performed":False,"counts":counts,"decision_map":decision_map}; report_out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); return report
