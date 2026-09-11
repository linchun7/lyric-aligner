#!/usr/bin/env python3
"""Build task-bound, shadow-only positional evidence for adjacent transitions."""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import librosa
from lyric_aligner import __version__
from lyric_aligner.audio.adjacent_transition_positional_v2 import adjudicate_transition, Policy, PositionalEvidenceError
from lyric_aligner.audio.bounded_mix import load_bounded_mix
from lyric_aligner.audio.feature_cache import FeatureCacheSpec, load_feature_bundle, save_feature_bundle
from lyric_aligner.audio.features import extract_harmonic_features
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest, sha256_file, validate_artifact_output, validate_upstream_artifact
from lyric_aligner.pipeline.context import build_pipeline_context
from task_contract import load_task_manifest, resolve_manifest_record, verify_manifest_inputs

def load(p):
    value = json.loads(Path(p).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict): raise ValueError(f"expected JSON object: {p}")
    return value
def sha(p): return sha256_file(Path(p))
def _stage(path, artifact_path, fingerprint, stage, role, algorithm):
    payload, artifact = load(path), load(artifact_path)
    if payload.get("task_fingerprint_sha256") != fingerprint or payload.get("algorithm_version") != algorithm: raise PositionalEvidenceError(f"invalid {role} lineage: {path}")
    issues = validate_upstream_artifact(artifact, expected_task_fingerprint=fingerprint, expected_algorithm_version=algorithm, expected_stage=stage) + validate_artifact_output(artifact, role=role, path=Path(path))
    if issues: raise PositionalEvidenceError(f"invalid {role} artifact: {'; '.join(issues)}")
    return payload, artifact
def fine_payload(path, artifact_path, *, expected, algorithm, asset_id, run_artifact, binding, occurrence, mix_sha):
    payload, artifact = _stage(path, artifact_path, expected, "fine_audio_alignment", "fine_alignment", algorithm)
    if artifact.get("normalized_config", {}).get("asset_artifact_id") != asset_id or artifact["artifact_id"] not in run_artifact.get("upstream_artifact_ids", []): raise PositionalEvidenceError(f"fine artifact lineage mismatch: {path}")
    for key, value in {"occurrence_id": occurrence["occurrence_id"], "track_id": binding.track_id, "source_audio_sha256": binding.source_audio_sha256, "mix_audio_sha256": mix_sha}.items():
        if payload.get(key) != value: raise PositionalEvidenceError(f"fine binding mismatch {key}: {path}")
    result, config = payload.get("result", {}), artifact.get("normalized_config", {})
    if result.get("status") != "refined" or result.get("timewarp", {}).get("blocked") or not result.get("path") or config.get("sr") != 16000 or config.get("hop_length") != 256: raise PositionalEvidenceError(f"fine payload blocked or wrong feature configuration: {path}")
    copied = dict(payload); copied["result"] = dict(result)
    # Fine path `mix_center` values are already absolute/global mix seconds.
    # Do not add feature_scope.mix_feature_start a second time.
    copied["result"]["path"] = [
        dict(row, global_mix_center=float(row.get("global_mix_center", row["mix_center"])))
        for row in result["path"]
    ]
    return copied, str(artifact["artifact_id"])
def source_features(path, digest, cache_dir, memo):
    key=(digest,16000,256)
    if key in memo: return memo[key]
    spec=FeatureCacheSpec(audio_sha256=digest,sr=16000,hop_length=256); cached=load_feature_bundle(cache_dir,spec)
    if cached is not None: memo[key]=(cached,"hit"); return memo[key]
    audio,_=librosa.load(path,sr=16000,mono=True); bundle=extract_harmonic_features(audio,sr=16000,hop_length=256)
    try: save_feature_bundle(cache_dir,spec,bundle); status="miss_written"
    except OSError: status="miss_write_failed"
    memo[key]=(bundle,status); return memo[key]
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--task-manifest",required=True,type=Path); ap.add_argument("--run",required=True,type=Path); ap.add_argument("--run-artifact",required=True,type=Path); ap.add_argument("--out-dir",required=True,type=Path); ap.add_argument("--transition-index",type=int); a=ap.parse_args()
    try:
        task=load_task_manifest(a.task_manifest); issues=verify_manifest_inputs(a.task_manifest,task)
        if issues: raise ValueError("task manifest validation failed: "+"; ".join(issues))
        fingerprint=str(task["task_fingerprint_sha256"]); run=load(a.run); run_artifact=load(a.run_artifact); algorithm=run.get("algorithm_version")
        if run.get("task_fingerprint_sha256") != fingerprint: raise ValueError("run task fingerprint mismatch")
        issues=validate_upstream_artifact(run_artifact,expected_task_fingerprint=fingerprint,expected_algorithm_version=algorithm,expected_stage="production_orchestration")+validate_artifact_output(run_artifact,role="v4_production_run",path=a.run)
        if issues: raise ValueError("invalid run artifact: "+"; ".join(issues))
        base=a.run.parent; assets,asset_artifact=_stage(base/"assets"/"track_assets.json",base/"assets"/"track_assets.artifact.json",fingerprint,"asset_resolution","track_assets",algorithm)
        if run_artifact.get("normalized_config",{}).get("asset_artifact_id") != asset_artifact["artifact_id"]: raise ValueError("run asset artifact mismatch")
        context=build_pipeline_context(expected_task_fingerprint=fingerprint,track_assets_payload=assets,asset_artifact=asset_artifact,verify_asset_files=True); mix_path=resolve_manifest_record(a.task_manifest,task["inputs"]["audio"]); mix_sha=task["inputs"]["audio"]["sha256"]; source_srt_path=resolve_manifest_record(a.task_manifest,task["inputs"]["source_srt"]); source_srt_sha=task["inputs"]["source_srt"]["sha256"]; source_dir=resolve_manifest_record(a.task_manifest,task["inputs"]["source_audio_dir"]).resolve(); cache_dir=base/"cache"/"features"; cache_dir.mkdir(parents=True,exist_ok=True)
        if sha(source_srt_path) != source_srt_sha: raise ValueError("source SRT SHA mismatch")
        run_occ={str(x["occurrence_id"]):x for x in run.get("occurrences",[])}; transitions=run.get("transitions",[]); issue_map={(str(x.get("left_occurrence_id")),str(x.get("right_occurrence_id"))):x for x in run.get("issues",[]) if x.get("kind")=="transition_ambiguity"}; selected=list(enumerate(transitions,1)) if a.transition_index is None else [(a.transition_index,transitions[a.transition_index-1])]; bindings=context.binding_by_occurrence_id; fines={}; fine_artifact_ids={}; sources={}; rows=[]; duration=float(librosa.get_duration(path=str(mix_path)))
        for index,tr in selected:
            pair=(str(tr["left_occurrence_id"]),str(tr["right_occurrence_id"])); issue=issue_map[pair]
            for oid in pair:
                occurrence=run_occ[oid]; binding=bindings[oid]; source=Path(binding.source_audio_path).resolve(); source.relative_to(source_dir)
                if sha(source)!=binding.source_audio_sha256: raise ValueError("source SHA mismatch: "+str(source))
                if oid not in fines:
                    fine_value, fine_artifact_id = fine_payload(
                        Path(occurrence["fine_path"]),
                        Path(occurrence["fine_artifact_path"]),
                        expected=fingerprint,
                        algorithm=algorithm,
                        asset_id=asset_artifact["artifact_id"],
                        run_artifact=run_artifact,
                        binding=binding,
                        occurrence=occurrence,
                        mix_sha=mix_sha,
                    )
                    fines[oid] = fine_value
                    fine_artifact_ids[oid] = fine_artifact_id
                if binding.source_audio_sha256 not in sources: sources[binding.source_audio_sha256]=source_features(source,binding.source_audio_sha256,cache_dir,sources)
            boundary=float(tr["nominal_boundary"]); bounded=load_bounded_mix(mix_path,sr=16000,mix_start=max(0,boundary-8),mix_end=min(duration,boundary+8),full_mix_duration=duration,padding_seconds=2); mix_features=extract_harmonic_features(bounded.audio,sr=16000,hop_length=256)
            out=adjudicate_transition(issue=issue,transition=tr,left_fine=fines[pair[0]],right_fine=fines[pair[1]],mix_features=mix_features,left_source_features=sources[bindings[pair[0]].source_audio_sha256][0],right_source_features=sources[bindings[pair[1]].source_audio_sha256][0],mix_feature_global_start_seconds=bounded.decode_start); out.update({"transition_index":index,"transition_index_basis":"1-based","mix_decode_start":bounded.decode_start,"mix_decode_end":bounded.effective_mix_end,"fine_artifact_ids":{"left":fine_artifact_ids[pair[0]],"right":fine_artifact_ids[pair[1]]},"source_search_range_valid":all(float(x["search_range"][1])>float(x["search_range"][0]) for x in out["windows"])}) ; rows.append(out)
        # `--transition-index` is a bounded shadow-evidence selector, not an
        # authority gate.  Persist unresolved evidence as well; downstream
        # adjudication decides whether the evidence is sufficient to act.
        a.out_dir.mkdir(parents=True, exist_ok=True)
        name = "PILOT" if a.transition_index == 1 else "EVIDENCE"
        out_path = a.out_dir / f"{name}.json"
        rows.sort(key=lambda x: x["transition_index"])
        summary = {
            "transition_count": len(rows),
            "clear_sequential_advisory_count": sum(
                x["recommendation"]["action"] == "clear_sequential_advisory" for x in rows
            ),
            "overlap_candidate_advisory_count": sum(
                x["recommendation"]["action"] == "overlap_candidate_advisory" for x in rows
            ),
            "unresolved_count": sum(
                x["recommendation"]["action"] == "unresolved" for x in rows
            ),
        }
        used_fine_artifact_ids = sorted(
            {
                artifact_id
                for row in rows
                for artifact_id in row.get("fine_artifact_ids", {}).values()
            }
        )
        payload = {
            "schema_version": "adjacent-transition-positional-v2-report-1",
            "implementation": {
                "module_sha256": sha(ROOT / "lyric_aligner" / "audio" / "adjacent_transition_positional_v2.py"),
                "cli_sha256": sha(Path(__file__)),
            },
            "policy": Policy().__dict__,
            "task_fingerprint_sha256": fingerprint,
            "algorithm_version": algorithm,
            "run_sha256": sha(a.run),
            "run_artifact_id": run_artifact["artifact_id"],
            "run_artifact_sha256": sha(a.run_artifact),
            "source_srt_sha256": source_srt_sha,
            "asset_artifact_id": asset_artifact["artifact_id"],
            "fine_artifact_ids": used_fine_artifact_ids,
            "authority": {
                "shadow_only": True,
                "automatic_review_decision": False,
                "timing_mutation_performed": False,
            },
            "timing_mutation_performed": False,
            "feature_config": {
                "sr": 16000,
                "hop_length": 256,
                "cache_dir": str(cache_dir),
            },
            "evidence": rows,
            "summary": summary,
        }
        atomic_write_json(out_path, payload)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="adjacent_transition_positional_v2",
            algorithm_version=algorithm,
            outputs=(("positional_evidence", out_path),),
            normalized_config={
                "policy_id": "adjacent-transition-positional-v2-global-coordinate-constrained",
                "transition_index_basis": "1-based",
                "feature_config": payload["feature_config"],
                "implementation": payload["implementation"],
            },
            upstream_artifact_ids=tuple(
                sorted(
                    {
                        str(run_artifact["artifact_id"]),
                        str(asset_artifact["artifact_id"]),
                        *used_fine_artifact_ids,
                    }
                )
            ),
            evidence=summary,
        )
        atomic_write_json(a.out_dir / f"{name}.artifact.json", artifact)
    except (OSError,KeyError,ValueError,IndexError,json.JSONDecodeError,PositionalEvidenceError) as exc: ap.error(str(exc))
    return 0
if __name__=="__main__": raise SystemExit(main())
