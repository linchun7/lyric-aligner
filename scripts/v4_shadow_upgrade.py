"""Fresh source/context candidates through coupled selection to a shadow SRT.

This branch produces experimental delivery files. It never calls the calibrated
production materializer and never emits final/release artifacts. Gold is loaded
only after selection and readback; it cannot influence candidate generation.
"""
from __future__ import annotations

import csv
import json
import shutil
import time
import unicodedata
import uuid
from dataclasses import asdict, fields
from pathlib import Path

from lyric_aligner.alignment.forced_projection import _project_interval, project_source_boundary_to_mix
from lyric_aligner.alignment.source_observer import SourceObservationConfig, observe_source, json_sha
from lyric_aligner.assets.bindings import bindings_from_payload
from lyric_aligner.contracts.artifacts import sha256_file, validate_upstream_artifact, validate_artifact_output
from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight
from lyric_aligner.srt import Cue, cue_id, text_sha256
from lyric_aligner.text.canonical_lyrics import parse_canonical_lyrics
from lyric_aligner.timeline.boundary_sequence_optimizer import (
    IntervalCandidate, IntervalNode, optimize_interval_sequence,
)

SHADOW_JOB_SCHEMA = "subtitle-shadow-upgrade-job-1.0"
SHADOW_STRATEGY_ID = "source-context-interval-shadow-2026-09-08-v5-source-sequence"
SOURCE_SEQUENCE_N_BEST = 1024

_SHADOW_JOB_FIELDS = frozenset({
    "schema_version", "execution_mode", "task_manifest", "report", "srt", "assets",
    "assets_artifact", "run", "run_artifact", "sources", "source_asr", "source_cache_dir",
    "regression", "experimental_source_context_hubertfa", "experimental_joint_source_context",
    "experimental_exact_source_anchors",
})
_SHADOW_SOURCE_FIELDS = frozenset({
    "occurrence_id", "fine", "fine_artifact", "mix_centres_seconds", "cue_bindings",
})
_SOURCE_ASR_FIELDS = frozenset(field.name for field in fields(SourceObservationConfig))


def _reject_unknown_fields(value, allowed, *, label):
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(label + " has unknown fields: " + ", ".join(unknown))


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _relative_output_binding(root: Path, binding, *, label: str) -> dict:
    """Bind an internal output by final-layout relative path, never staging path."""
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ValueError(label + " requires a path/SHA binding")
    path = Path(binding["path"]).resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(label + " is outside shadow staging") from exc
    if not path.is_file() or sha256_file(path) != binding["sha256"]:
        raise ValueError(label + " changed before shadow delivery")
    return {"relative_path": relative.as_posix(), "sha256": binding["sha256"]}


def _verify_relative_output_binding(root: Path, binding, *, label: str) -> None:
    if not isinstance(binding, dict) or set(binding) != {"relative_path", "sha256"}:
        raise ValueError(label + " requires a final-layout relative path/SHA binding")
    relative = Path(binding["relative_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(label + " has unsafe relative path")
    path = root / relative
    if not path.is_file() or sha256_file(path) != binding["sha256"]:
        raise ValueError(label + " does not resolve to its bound output")


def _lexical(value):
    return "".join(unicodedata.normalize("NFKC", value).split())


def _verified_source_observation_self_hash(observation: dict) -> tuple[str, dict]:
    """Return the observer's own content hash, excluding cache-hit state.

    A cache filename and a caller-invented label are not an observation
    identity.  The source-sequence lattice has to bind the exact whole-source
    transcript it ordered, so use the observer's self-hash contract and fail
    before any packet/projection work when it is absent or stale.
    """

    if not isinstance(observation, dict):
        raise ValueError("source observer returned a non-object observation")
    payload = {key: value for key, value in observation.items() if key != "cache_hit"}
    self_hash = payload.pop("artifact_sha256", None)
    if (not isinstance(self_hash, str) or len(self_hash) != 64
            or self_hash != json_sha(payload)):
        raise ValueError("source observer requires a verified self hash")
    return self_hash, payload


def _clock(ms):
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _cue_ranges(row, cue, lines, explicit=None):
    """Resolve only indexed, lossless lexical ownership; no first-text search."""
    if explicit is not None:
        ranges = explicit
    elif all(row.get(key) not in (None, "") for key in ("canonical_content_start", "canonical_content_end")):
        # Editor-preserved cues own normalized content offsets, which may cut
        # inside an LRC line. Reconstruct their raw canonical character ranges.
        from lyric_aligner.text_repair import _normalize_for_match
        trace, normalized = [], []
        for index, text in lines.items():
            local = []
            for char_index, char in enumerate(text):
                value = _normalize_for_match(char)
                local.extend(value)
                trace.extend([(index, char_index)] * len(value))
            if "".join(local) != _normalize_for_match(text):
                return None
            normalized.extend(local)
        start, end = int(row["canonical_content_start"]), int(row["canonical_content_end"])
        if not 0 <= start < end <= len(trace):
            return None
        if ((start and trace[start - 1] == trace[start]) or (end < len(trace) and trace[end - 1] == trace[end])):
            return None
        if "".join(normalized[start:end]) != _normalize_for_match(cue.text):
            return None
        ranges = []
        for index, char_index in trace[start:end]:
            if ranges and ranges[-1]["canonical_line_index"] == index:
                ranges[-1]["end_char"] = char_index + 1
            else:
                ranges.append({"canonical_line_index": index, "start_char": char_index, "end_char": char_index + 1})
        # Punctuation is display ownership, not a missing lexical token.
        return ranges
    else:
        raw = row.get("canonical_line_index", row.get("lrc_indices", ""))
        try:
            indices = [int(v) for v in str(raw).split(",")]
            ranges = [{"canonical_line_index": index, "start_char": 0,
                       "end_char": len(lines[index])} for index in indices]
        except (ValueError, KeyError):
            return None
    parts, order = [], []
    for item in ranges:
        index, start, end = (item[k] for k in ("canonical_line_index", "start_char", "end_char"))
        if any(type(v) is not int for v in (index, start, end)):
            raise ValueError("canonical character positions must be integers")
        if index not in lines or not 0 <= start < end <= len(lines[index]):
            raise ValueError("canonical character range outside indexed line")
        if order and (index, start) < order[-1]:
            raise ValueError("canonical character ranges overlap or reverse")
        order.append((index, end))
        parts.append(lines[index][start:end])
    if not parts or _lexical("".join(parts)) != _lexical(cue.text):
        return None
    return ranges


def _materialize(rows, cues, intervals, directory, *, strategy_id=SHADOW_STRATEGY_ID):
    output_rows = []
    fields = list(rows[0])
    for field in ("shadow_baseline_start_ms", "shadow_baseline_end_ms", "shadow_strategy_id"):
        if field not in fields:
            fields.append(field)
    provenance_fields = ("cue_id", "status", "confidence", "evidence", "boundary_authority",
        "human_confirmed_start_record", "human_confirmed_end_record", "human_confirmation_artifact_sha256")
    for field in provenance_fields:
        if field in fields and "shadow_baseline_" + field not in fields:
            fields.append("shadow_baseline_" + field)
    blocks = []
    for position, (row, cue, (start, end)) in enumerate(zip(rows, cues, intervals), 1):
        updated = dict(row)
        updated.update(start_ms=str(start), end_ms=str(end), text=cue.text,
            shadow_baseline_start_ms=str(cue.start_ms), shadow_baseline_end_ms=str(cue.end_ms),
            shadow_strategy_id=strategy_id)
        if row.get("display_policy_id"):
            updated.update(display_start_ms=str(start), display_end_ms=str(end), display_text=cue.text)
        if (start, end) != (cue.start_ms, cue.end_ms):
            for field in provenance_fields:
                if field in row:
                    updated["shadow_baseline_" + field] = row[field]
                    updated[field] = ""
            if "cue_id" in row:
                updated["cue_id"] = cue_id(position, Cue(cue.number, start, end, cue.text))
            if "text_sha256" in row:
                updated["text_sha256"] = text_sha256(cue.text)
            for field, value in (("status", "experimental_shadow_timing"), ("confidence", "uncalibrated"),
                                 ("evidence", "shadow_candidate_ledger"), ("boundary_authority", "experimental_only")):
                if field in row:
                    updated[field] = value
        output_rows.append(updated)
        blocks.append(f"{cue.number}\n{_clock(start)} --> {_clock(end)}\n{cue.text}\n")
    with (directory / "shadow.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    (directory / "shadow.srt").write_text("\n".join(blocks), encoding="utf-8-sig", newline="\n")
    from scripts.v4_upgrade_subtitles import validate_pair
    _, readback = validate_pair(directory / "shadow.csv", directory / "shadow.srt")
    if [(c.start_ms, c.end_ms) for c in readback] != list(intervals):
        raise ValueError("shadow writeback differs from selected intervals")
    return output_rows, readback


def _projected_variants(candidate, mapping, baseline):
    """Compose observed edges with exact KEEP edges, as atomic intervals."""
    interval = candidate.get("source_interval_ms") or [None, None]
    if len(interval) != 2:
        raise ValueError("source candidate must have two endpoint slots")
    projections, rejected, variants = {}, [], []
    for edge, label in enumerate(("start", "end")):
        if interval[edge] is not None and candidate.get("source_interval_" + label + "_reason") == "observed_positive_duration_word":
            projection = project_source_boundary_to_mix(mapping, interval[edge])
            if projection["projection_status"] == "projected":
                projections[edge] = projection
            else:
                rejected.append({"candidate_shape": label + "_only", "reason": projection["projection_reason"]})
    if all(value is not None for value in interval):
        whole = _project_interval(mapping, *interval)
        if whole["projection_status"] != "projected":
            # A known interval crosses a removed region. Partial variants must
            # not hide that contradiction in the canonical cue ownership.
            return [], [{"candidate_shape": "all", "reason": whole["projection_reason"]}]
    for shape, used in (("both", (0, 1)), ("start_only", (0,)), ("end_only", (1,))):
        if not all(edge in projections for edge in used):
            continue
        result = list(baseline)
        for edge in used:
            result[edge] = projections[edge]["mix_ms"]
        retained = [projections[edge]["retained_mix_interval_ms"] for edge in used
            if projections[edge]["retained_mix_interval_ms"] is not None]
        if any(not low <= result[0] < result[1] <= high for low, high in retained):
            rejected.append({"candidate_shape": shape, "reason": "composed_interval_crosses_retained_cut_segment"})
            continue
        if not 0 <= result[0] < result[1]:
            rejected.append({"candidate_shape": shape, "reason": "composed_interval_nonpositive"})
            continue
        variants.append({"candidate_shape": shape, "start_ms": result[0], "end_ms": result[1],
            "selection_cost": 0.0 if len(used) == 2 else 0.5,
            "start_origin": "source_packet_word_edge" if 0 in used else "baseline_keep",
            "end_origin": "source_packet_word_edge" if 1 in used else "baseline_keep"})
    if not projections:
        rejected.append({"candidate_shape": "all", "reason": "source_endpoints_unavailable"})
    return variants, rejected


def run_shadow_job(job_path: Path, output_dir: Path, *, source_observer=None):
    from scripts.task_contract import validate_task_manifest_schema
    from scripts.v4_upgrade_subtitles import validate_pair
    from scripts.v4_plan_boundary_refinement import resolve_manifest_file
    from lyric_aligner.alignment.source_packets import BOUNDED_TARGET_POLICY, build_source_packet_candidates

    started = time.monotonic()
    job_path = Path(job_path).resolve()
    job = _load(job_path)
    if not isinstance(job, dict):
        raise ValueError("shadow job must be an object")
    if job.get("schema_version") != SHADOW_JOB_SCHEMA or job.get("execution_mode") != "shadow":
        raise ValueError("shadow job requires explicit schema and execution_mode=shadow")
    unknown = sorted(set(job) - _SHADOW_JOB_FIELDS)
    if unknown:
        raise ValueError("unknown shadow job fields: " + ", ".join(unknown))
    required = {"task_manifest", "report", "srt", "assets", "assets_artifact", "run", "run_artifact",
        "sources", "source_asr", "source_cache_dir"}
    if required - job.keys():
        raise ValueError("shadow job missing fields: " + ", ".join(sorted(required - job.keys())))
    if not isinstance(job["sources"], list) or not job["sources"]:
        raise ValueError("shadow job sources must be a nonempty list")
    if any(not isinstance(spec, dict) or {"occurrence_id", "fine", "fine_artifact"} - spec.keys() for spec in job["sources"]):
        raise ValueError("each shadow source requires occurrence_id, fine and fine_artifact")
    for spec in job["sources"]:
        _reject_unknown_fields(spec, _SHADOW_SOURCE_FIELDS, label="shadow source")
    if any(key in job for key in ("calibrated_stages", "human_confirmations", "gap_review", "editor_preservation")):
        raise ValueError("shadow job cannot include production stages")
    def path(value):
        candidate = Path(value)
        return (candidate if candidate.is_absolute() else job_path.parent / candidate).resolve()
    direct, lineage = {"job": job_path}, {"job": job}
    def bound(value, label):
        if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
            raise ValueError(f"{label} requires an exact path/SHA binding")
        p = path(value["path"])
        if sha256_file(p) != value["sha256"]:
            raise ValueError(f"{label} SHA mismatch")
        direct[label] = p
        return p
    hfa_config = None
    if "experimental_source_context_hubertfa" in job:
        # Imported only for an explicitly enabled experiment.  An ordinary
        # shadow job retains its existing producer and output identity.
        from lyric_aligner.alignment.source_context_hubertfa import SourceContextHuBERTFAConfig
        hfa_config = SourceContextHuBERTFAConfig.from_job(
            job["experimental_source_context_hubertfa"], resolve_path=path, bind_file=bound)
    joint_policy = job.get("experimental_joint_source_context")
    exact_anchor_policy = job.get("experimental_exact_source_anchors")
    if exact_anchor_policy is not None:
        if exact_anchor_policy != {"policy_id": "source-exact-word-run-legacy-bracket-v1"}:
            raise ValueError("unsupported exact source anchor policy")
        if joint_policy is None:
            raise ValueError("exact source anchors require explicit joint source context")
    if joint_policy is not None:
        if joint_policy != {"policy_id": "atomic-adjacent-pair-v1"}:
            raise ValueError("joint source context requires explicit atomic-adjacent-pair-v1")
        if (hfa_config is None or hfa_config.context_policy != "anchored-path-v1"
                or hfa_config.mode != "hfa-only-overlay"):
            raise ValueError("joint source context requires anchored-path HFA-only overlay")
    manifest_path = bound(job["task_manifest"], "manifest")
    manifest = _load(manifest_path)
    problems = validate_task_manifest_schema(manifest)
    if problems:
        raise ValueError("invalid task manifest: " + "; ".join(problems))
    report_path, srt_path = (bound(job[k], k) for k in ("report", "srt"))
    assets_path, run_path = (bound(job[k], k) for k in ("assets", "run"))
    assets, run = _load(assets_path), _load(run_path)
    lineage.update(manifest=manifest, assets=assets, run=run)
    fingerprint = manifest["task_fingerprint_sha256"]
    if any(payload.get("task_fingerprint_sha256") != fingerprint for payload in (assets, run)):
        raise ValueError("assets/run belong to another task")
    def artifact(binding_value, label, payload_path, payload, stage, role):
        artifact_path = bound(binding_value, label)
        value = _load(artifact_path)
        lineage[label] = value
        issues = validate_upstream_artifact(value, expected_task_fingerprint=fingerprint,
            expected_algorithm_version=payload["algorithm_version"], expected_stage=stage)
        issues += validate_artifact_output(value, role=role, path=payload_path)
        if issues:
            raise ValueError(label + ": " + "; ".join(issues))
        return value
    asset_artifact = artifact(job["assets_artifact"], "assets_artifact", assets_path, assets,
        "asset_resolution", "track_assets")
    run_stage, run_role = ("review_resolution", "v4_reviewed_run") if "review_resolution" in run else ("production_orchestration", "v4_production_run")
    run_artifact = artifact(job["run_artifact"], "run_artifact", run_path, run, run_stage, run_role)
    mix_path = resolve_manifest_file(manifest["inputs"]["audio"], label="audio")
    direct["mix_audio"] = mix_path
    rows, before = validate_pair(report_path, srt_path)
    if any(row.get("task_fingerprint_sha256") != fingerprint for row in rows):
        raise ValueError("baseline belongs to another task")
    bindings = {b.occurrence_id: b for b in bindings_from_payload(assets)}
    occurrences = {o["occurrence_id"]: o for o in run["occurrences"]}
    _reject_unknown_fields(job["source_asr"], _SOURCE_ASR_FIELDS, label="source_asr")
    config = SourceObservationConfig(**job["source_asr"])
    direct["model_directory"] = path(config.model_path)
    config = SourceObservationConfig(**{**asdict(config), "model_path": str(direct["model_directory"])})
    regression_paths = None
    if job.get("regression"):
        # Bind bytes and protect paths now, without reading labels or gold values.
        regression_paths = tuple(bound(job["regression"][key], "regression:" + key)
            for key in ("selection_lock", "gold"))
    sources, seen = [], set()
    for spec in job["sources"]:
        oid = spec["occurrence_id"]
        if oid in seen:
            raise ValueError("duplicate source occurrence")
        seen.add(oid)
        binding, occurrence = bindings[oid], occurrences[oid]
        fine_path = bound(spec["fine"], "fine:" + oid)
        fine = _load(fine_path)
        lineage["fine:" + oid] = fine
        fine_artifact = artifact(spec["fine_artifact"], "fine_artifact:" + oid, fine_path, fine,
            "fine_audio_alignment", "fine_alignment")
        if (path(occurrence["fine_artifact_path"]) != path(spec["fine_artifact"]["path"])
                or fine.get("upstream_asset_artifact_id") != asset_artifact["artifact_id"]
                or asset_artifact["artifact_id"] not in fine_artifact["upstream_artifact_ids"]
                or fine_artifact["artifact_id"] not in run_artifact["upstream_artifact_ids"]):
            raise ValueError("effective mapping artifact lineage mismatch")
        if (path(occurrence["fine_path"]) != fine_path or occurrence.get("mapping_source") != "fine"
                or occurrence.get("mapping_blocked") or occurrence.get("reference_retime")):
            raise ValueError("shadow source requires a resolved effective fine mapping")
        for key, expected in {"task_fingerprint_sha256": fingerprint, "occurrence_id": oid,
                "track_id": binding.track_id, "source_audio_sha256": binding.source_audio_sha256,
                "mix_audio_sha256": manifest["inputs"]["audio"]["sha256"],
                "canonical_selection_sha256": binding.canonical_selection_sha256}.items():
            if fine.get(key) != expected:
                raise ValueError("fine mapping binding mismatch: " + key)
        warp = fine["result"]["timewarp"]
        if warp.get("blocked") or not warp.get("mapping"):
            raise ValueError("fine timewarp is blocked or absent")
        mode = warp["mapping"].get("kind", warp["mapping"].get("mode"))
        if mode not in ("AFFINE", "PIECEWISE_RATE", "CUT_AWARE"):
            raise ValueError("unsupported effective mapping mode")
        for label, p, digest in (("source", binding.source_audio_path, binding.source_audio_sha256),
                ("lyrics", binding.canonical_lyric_path, binding.canonical_lyric_sha256)):
            if sha256_file(Path(p)) != digest:
                raise ValueError(label + " changed after asset resolution")
            direct[label + ":" + oid] = Path(p)
        lines = parse_canonical_lyrics(Path(binding.canonical_lyric_path),
            original_index_by_timestamp=binding.original_index_by_timestamp)
        sources.append((spec, binding, occurrence, warp, lines))
    if not sources:
        raise ValueError("shadow job requires at least one source occurrence")
    destination = Path(output_dir).resolve()
    reserved_staging = destination.with_name(destination.name + ".staging")
    staging = destination.with_name(destination.name + ".staging." + uuid.uuid4().hex)
    for target in (destination, staging):
        validate_materializer_preflight(manifest_path=manifest_path, manifest=manifest,
            direct_inputs=direct, lineage_payloads=lineage, output_dir=target, outputs={})
        if target.exists():
            raise FileExistsError("shadow output/staging directory must be new")
    cache_dir = path(job["source_cache_dir"])
    validate_materializer_preflight(manifest_path=manifest_path, manifest=manifest,
        direct_inputs=direct, lineage_payloads=lineage, output_dir=cache_dir, outputs={})
    for target in (destination, reserved_staging, staging):
        if cache_dir == target or target in cache_dir.parents or cache_dir in target.parents:
            raise ValueError("source cache and shadow output/staging must be disjoint")
    hashes = {str(p): sha256_file(p) for p in direct.values() if p.is_file()}
    code_root = Path(__file__).resolve().parents[1]
    producers = ["scripts/v4_shadow_upgrade.py", "scripts/v4_upgrade_subtitles.py",
        "lyric_aligner/alignment/source_observer.py", "lyric_aligner/alignment/source_packets.py",
        "lyric_aligner/alignment/source_sequence.py",
        "lyric_aligner/alignment/anchored_edit.py",
        "lyric_aligner/audio/contextual_mapping.py", "lyric_aligner/audio/contextual_fine.py",
        "lyric_aligner/alignment/forced_projection.py", "lyric_aligner/timeline/projector.py",
        "lyric_aligner/timeline/boundary_sequence_optimizer.py"]
    hfa_direct_behavior_files = ()
    if hfa_config is not None:
        # This is a deliberately bounded direct-behavior manifest for the HFA
        # experiment. It is not a claim to hash Python's entire transitive
        # environment; model/runtime/vendor inputs have their own bindings.
        hfa_direct_behavior_files = (
            "lyric_aligner/alignment/source_context_hubertfa.py",
            "lyric_aligner/alignment/hubertfa_time_bands.py",
            "scripts/source_context_hubertfa_adapter.py",
            "lyric_aligner/alignment/source_packets.py",
            "lyric_aligner/text/alignment_lexical.py",
            "lyric_aligner/audio/forced_alignment.py",
            "lyric_aligner/alignment/asr_executor.py",
            "lyric_aligner/text/bijective_han.py",
            "lyric_aligner/text/data/opencc/TSCharacters.txt",
            "lyric_aligner/text/data/opencc/STCharacters.txt",
        )
        producers.extend(hfa_direct_behavior_files)
        producers.extend(str(Path(name).relative_to(code_root)).replace("\\", "/")
            for name in hfa_config.vendor_files())
    if joint_policy is not None:
        producers.extend(("lyric_aligner/alignment/source_joint_context.py", "scripts/source_joint_overlay.py"))
    if exact_anchor_policy is not None:
        producers.append("lyric_aligner/alignment/source_exact_anchors.py")
    producer_hashes = {name: sha256_file(code_root / name) for name in producers}
    staging.mkdir(parents=True)
    _write(staging / "execution_receipt.json", {"schema_version": "shadow-execution-receipt-1.0",
        "job_sha256": hashes[str(job_path)], "intended_output": str(destination),
        "attempt_id": staging.name, "failed_attempts_preserved_for_diagnosis": True})
    ledger, observations, mapping_observations, source_sequences = {}, [], [], []
    hfa_source_inputs = []
    for spec, binding, occurrence, warp, lines in sources:
        observation = (source_observer or observe_source)(audio_path=Path(binding.source_audio_path),
            audio_sha256=binding.source_audio_sha256, config=config, cache_dir=cache_dir)
        if observation.get("source_audio_sha256") != binding.source_audio_sha256 or observation.get("audio_basis") != "source":
            raise ValueError("source observer returned another audio/time basis")
        oid = binding.occurrence_id
        mapping_checks = []
        if spec.get("mix_centres_seconds"):
            from lyric_aligner.audio.contextual_mapping import generate_contextual_mapping_candidates
            for centre in spec["mix_centres_seconds"]:
                check = generate_contextual_mapping_candidates(source_audio=Path(binding.source_audio_path),
                    mix_audio=mix_path, source_audio_sha256=binding.source_audio_sha256,
                    mix_audio_sha256=manifest["inputs"]["audio"]["sha256"],
                    occurrence={"occurrence_id": oid, "mix_start": occurrence["primary_interval"][0],
                        "mix_end": occurrence["primary_interval"][1]},
                    effective_timewarp=warp, mix_center=float(centre),
                    cache_dir=cache_dir / "contextual")
                mapping_checks.append(check)
            _write(staging / (oid + ".mapping.json"), mapping_checks)
            mapping_observations.append({"occurrence_id": oid, "windows": len(mapping_checks),
                "available": sum(check.get("status") == "available" for check in mapping_checks)})
        observations.append({"occurrence_id": oid, "status": observation["status"],
            "cache_hit": observation.get("cache_hit"), "word_count": len(observation["words"]),
            "cache_key_sha256": observation.get("cache_key_sha256"), "reason": observation.get("reason")})
        observation_self_hash, observation_payload = (None, None)
        if observation["status"] == "observed":
            observation_self_hash, observation_payload = _verified_source_observation_self_hash(observation)
        source_evidence_path = staging / (oid + ".source.json")
        source_evidence = {
            "schema_version": "source-observation-reference-1.0", "audio_basis": "source",
            "source_audio_sha256": binding.source_audio_sha256, "status": observation["status"],
            "reason": observation.get("reason"), "word_count": len(observation["words"]),
            "cache_key_sha256": observation.get("cache_key_sha256"),
            "observation_artifact_sha256": observation.get("artifact_sha256"),
            "search_domain": observation.get("search_domain"),
            "authority": "observed_transcript_only", "raw_words_in_private_cache": True}
        if observation_self_hash is not None:
            source_evidence.update({
                "source_observation_self_sha256": observation_self_hash,
                "source_observation_self_hash_scope": (
                    "source observer payload excluding artifact_sha256 and cache_hit; "
                    "not cache-file SHA256"
                ),
            })
        if hfa_config is not None and observation.get("status") == "observed":
            assert observation_payload is not None and observation_self_hash is not None
            canonical_lines = [{"canonical_line_index": line.index, "text": line.text} for line in lines]
            source_evidence.update({
                "schema_version": "source-observation-hubertfa-evidence-1.1",
                "raw_words_in_private_cache": False,
                # This is the observer object's self-hash (excluding this
                # field and runtime cache-hit state), not a cache-file SHA.
                "source_observation_self_sha256": observation_self_hash,
                "source_observation_self_hash_scope": "source observer payload excluding artifact_sha256 and cache_hit; not cache-file SHA256",
                "source_observation": {**observation_payload, "artifact_sha256": observation_self_hash},
                "canonical_lines": canonical_lines,
                "canonical_lines_sha256": json_sha(canonical_lines),
                "hfa_algorithm_identity": {
                    "protocol_version": hfa_config.identity()["protocol_version"],
                    "policy_id": hfa_config.policy_id,
                    "acoustic_request_fields": hfa_config.acoustic_request_fields(),
                    "direct_behavior_manifest": {name: producer_hashes[name]
                                                  for name in hfa_direct_behavior_files},
                    "direct_behavior_manifest_scope": "bounded project files directly controlling HFA qualification, G2P and target extraction; not a full transitive environment manifest",
                },
            })
        _write(source_evidence_path, source_evidence)
        if hfa_config is not None:
            hfa_source_inputs.append((spec, binding, occurrence, warp, lines, observation, mapping_checks,
                                      _relative_output_binding(staging, {"path": str(source_evidence_path),
                                                                         "sha256": sha256_file(source_evidence_path)},
                                                               label="HuBERTFA source evidence")))
        indexed = {line.index: line.text for line in lines}
        explicit = {int(item["position"]): item["canonical_character_ranges"] for item in spec.get("cue_bindings", [])}
        mapping = warp["mapping"]
        lower, upper = (round(float(v) * 1000) for v in occurrence["primary_interval"])
        sequence_packets, sequence_packet_entries = [], []
        owned_target_cue_count = 0
        ownership_unresolved_count = 0
        for position, (row, cue) in enumerate(zip(rows, before), 1):
            owned = (row.get("occurrence_id") == oid or (not row.get("occurrence_id")
                and row.get("track") == binding.title
                and sum(b.title == binding.title for b in bindings.values()) == 1))
            if not owned:
                continue
            owned_target_cue_count += 1
            ranges = _cue_ranges(row, cue, indexed, explicit.get(position))
            entry = {"position": position, "occurrence_id": oid,
                "occurrence_path_id": binding.source_audio_sha256 + ":" + binding.canonical_selection_sha256,
                "map_path_id": json_sha(mapping),
                "baseline_interval_ms": [cue.start_ms, cue.end_ms], "interval_candidates": [],
                "rejected_candidates": []}
            ledger[position] = entry
            if ranges is None:
                entry["reason"] = "canonical_cue_ownership_unresolved"
                ownership_unresolved_count += 1
                continue
            if observation["status"] != "observed":
                entry["reason"] = observation.get("reason", "source_backend_unavailable")
                continue
            packet = build_source_packet_candidates(canonical_lines=[{"canonical_line_index": line.index, "text": line.text} for line in lines],
                observed_words=observation["words"], target_cue={"cue_id": str(position), "canonical_character_ranges": ranges},
                source_audio_sha256=binding.source_audio_sha256,
                lyric_version=binding.canonical_selection_sha256,
                source_search_domain=observation["search_domain"],
                model_id=observation["cache_key_sha256"], context_radius=2, n_best=SOURCE_SEQUENCE_N_BEST,
                target_alignment_policy=BOUNDED_TARGET_POLICY)
            # This binding does not alter packet identity.  It makes the
            # lattice unambiguously about the verified whole-source observer
            # payload used to build every one of its packets.
            assert observation_self_hash is not None
            packet["source_observation_sha256"] = observation_self_hash
            entry["source_packet"] = packet
            entry["reason"] = packet.get("selection_reason")
            sequence_packets.append(packet)
            sequence_packet_entries.append((entry, packet, cue))

        if observation["status"] == "observed":
            from lyric_aligner.alignment.source_sequence import resolve_source_sequence
            assert observation_self_hash is not None
            source_sequence = resolve_source_sequence(
                sequence_packets, source_observation_sha256=observation_self_hash
            )
        else:
            source_sequence = {
                "schema_version": "source-sequence-1.0",
                "status": "source_observation_not_observed",
                "promotions": [],
            }
        promotions = {}
        if source_sequence.get("status") == "complete":
            for promotion in source_sequence.get("promotions", []):
                if not isinstance(promotion, dict):
                    raise ValueError("source sequence promotion must be an object")
                packet_key, candidate_id = (promotion.get("packet_cache_key_sha256"),
                                             promotion.get("candidate_id"))
                if not isinstance(packet_key, str) or not isinstance(candidate_id, str):
                    raise ValueError("source sequence promotion identity is invalid")
                promotions[packet_key] = promotion
        source_sequences.append({
            "occurrence_id": oid,
            "coverage_scope": "all current occurrence target cues with resolvable canonical ownership; not a full canonical-line lattice",
            "owned_target_cue_count": owned_target_cue_count,
            "canonical_ownership_unresolved_count": ownership_unresolved_count,
            "source_packet_count": len(sequence_packets),
            "source_packet_candidate_count": sum(len(packet.get("candidates", [])) for packet in sequence_packets),
            "source_packet_truncated_count": sum(bool(packet.get("candidates_truncated")) for packet in sequence_packets),
            "source_observation_self_sha256": observation_self_hash,
            "status": source_sequence.get("status"),
            "cache_key_sha256": source_sequence.get("cache_key_sha256"),
            "vertex_count": source_sequence.get("vertex_count"),
            "maximum_chain_length": source_sequence.get("maximum_chain_length"),
            "promotion_count": len(promotions),
        })

        for entry, packet, cue in sequence_packet_entries:
            position = int(entry["position"])
            promotion = promotions.get(packet["cache_key_sha256"])
            promotion_candidate_id = promotion.get("candidate_id") if promotion else None
            if promotion is not None:
                # Do not mutate the packet's local selection/result flags: a
                # duplicate-only promotion is separate global provenance.
                entry["source_sequence_promotion"] = {
                    **promotion,
                    "source_sequence_cache_key_sha256": source_sequence["cache_key_sha256"],
                    "source_observation_self_sha256": observation_self_hash,
                }
            for candidate in packet.get("candidates", []):
                # Experimental lexical eligibility, fixed before any gold is read.
                # This is deliberately not a calibrated expected error in ms.
                local_qualification = bool(candidate.get("full_context_disambiguated"))
                promoted_qualification = (
                    source_sequence.get("status") == "complete"
                    and packet.get("selection_reason") == "ambiguous_canonical_packet_identity"
                    and candidate.get("candidate_id") == promotion_candidate_id
                )
                if not (local_qualification or promoted_qualification):
                    entry["rejected_candidates"].append({"candidate_id": candidate["candidate_id"],
                        "reason": "context_not_disambiguated"})
                    continue
                variants, rejected = _projected_variants(candidate, mapping, (cue.start_ms, cue.end_ms))
                entry["rejected_candidates"].extend({"candidate_id": candidate["candidate_id"], **item} for item in rejected)
                for variant in variants:
                    start, end = variant["start_ms"], variant["end_ms"]
                    if not lower <= start < end <= upper:
                        entry["rejected_candidates"].append({"candidate_id": candidate["candidate_id"],
                            "candidate_shape": variant["candidate_shape"], "reason": "outside_resolved_occurrence"})
                        continue
                    checks = [check for check in mapping_checks if check.get("status") == "available"
                        and check.get("top1")
                        and check["top1"]["mix_start"] * 1000 <= start
                        and end <= check["top1"]["mix_end"] * 1000]
                    entry["contextual_mapping_checks"] = [json_sha(check) for check in checks]
                    entry["contextual_mapping_status"] = "covered" if checks else "not_checked_at_this_interval"
                    if any(check.get("ambiguous", True) for check in checks):
                        entry["reason"] = "contextual_mapping_ambiguous"
                        entry["rejected_candidates"].append({"candidate_id": candidate["candidate_id"],
                            "candidate_shape": variant["candidate_shape"], "reason": "contextual_mapping_ambiguous"})
                        continue
                    entry["interval_candidates"].append({**variant,
                        "candidate_id": str(position) + ":" + candidate["candidate_id"] + ":" + variant["candidate_shape"],
                        "source_interval_ms": candidate["source_interval_ms"], "lane_id": oid,
                        "contextual_mapping_checks": [json_sha(check) for check in checks],
                        "occurrence_path_id": entry["occurrence_path_id"],
                        "map_path_id": entry["map_path_id"], "is_baseline": False})
    hfa_state = None
    joint_contexts = {}
    joint_summary = None
    hfa_overlay_records = []
    if hfa_config is not None:
        # This deliberately reuses only the whole-recording source observation
        # and source packet identity.  Canonical text is never used to choose a
        # source-ASR time window; it only proves a complete three-line FA input.
        from lyric_aligner.alignment.source_context_hubertfa import (
            SOURCE_CONTEXT_AUTHORITY, execute_batch, finalize_records, prepare_occurrence,
        )

        hfa_context_policy = getattr(hfa_config, "context_policy", "three-line-v1")
        hfa_occurrences, all_hfa_records, hfa_record_owners = [], [], {}
        for spec, binding, occurrence, warp, lines, observation, mapping_checks, source_evidence in hfa_source_inputs:
            oid = binding.occurrence_id
            indexed = {line.index: line.text for line in lines}
            explicit = {int(item["position"]): item["canonical_character_ranges"]
                        for item in spec.get("cue_bindings", [])}
            cue_specs = []
            for position, (row, cue) in enumerate(zip(rows, before), 1):
                owned = (row.get("occurrence_id") == oid or (not row.get("occurrence_id")
                    and row.get("track") == binding.title
                    and sum(b.title == binding.title for b in bindings.values()) == 1))
                if owned:
                    cue_specs.append({"position": position, "baseline_interval_ms": [cue.start_ms, cue.end_ms],
                        "canonical_character_ranges": _cue_ranges(row, cue, indexed, explicit.get(position))})
            if observation.get("status") != "observed":
                prepared = {"schema_version": "source-context-hubertfa-prepared-occurrence-1.0",
                    "authority": SOURCE_CONTEXT_AUTHORITY, "occurrence_id": oid, "records": [],
                    "context_policy": hfa_context_policy,
                    "ledger": [{"position": spec["position"], "occurrence_id": oid,
                        "baseline_interval_ms": spec["baseline_interval_ms"], "status": "unavailable",
                        "reasons": ["source_observation_not_observed:" + str(observation.get("reason") or "unknown")]}
                        for spec in cue_specs]}
            elif not isinstance(observation.get("artifact_sha256"), str):
                prepared = {"schema_version": "source-context-hubertfa-prepared-occurrence-1.0",
                    "authority": SOURCE_CONTEXT_AUTHORITY, "occurrence_id": oid, "records": [],
                    "context_policy": hfa_context_policy,
                    "ledger": [{"position": spec["position"], "occurrence_id": oid,
                        "baseline_interval_ms": spec["baseline_interval_ms"], "status": "unavailable",
                        "reasons": ["source_observation_identity_missing"]} for spec in cue_specs]}
            else:
                prepared = prepare_occurrence(occurrence_id=oid, source_audio_path=Path(binding.source_audio_path),
                    source_audio_sha256=binding.source_audio_sha256, language="en",
                    canonical_lines=[{"canonical_line_index": line.index, "text": line.text} for line in lines],
                    cue_specs=cue_specs, observed_words=observation["words"],
                    source_observation_sha256=observation["artifact_sha256"],
                    source_search_domain=observation["search_domain"],
                    lyric_version=binding.canonical_selection_sha256,
                    model_id=observation["cache_key_sha256"], dictionary_path=hfa_config.dictionary_path,
                    policy_id=hfa_config.policy_id, context_policy=hfa_context_policy)
            anchored_block = None
            if hfa_context_policy in {"anchored-block-v1", "anchored-path-v1"} and prepared.get("packets"):
                from lyric_aligner.alignment.source_context_hubertfa import prepare_anchored_blocks
                anchored_block = prepare_anchored_blocks(prepared, dictionary_path=hfa_config.dictionary_path)
                # The compact ledger retains this bounded request once; packet
                # candidates remain compacted separately and never duplicated.
                prepared["anchored_block_records"] = anchored_block["records"]
            entry_by_position = {int(entry["position"]): entry for entry in prepared["ledger"]
                                 if isinstance(entry, dict) and isinstance(entry.get("position"), int)}

            def add_owner(record, entry, *, target_segment_index, target_position):
                record_id = str(record["record_id"])
                owner = {"occurrence_id": oid, "entry": entry, "mapping": warp["mapping"],
                    "path_id": binding.source_audio_sha256 + ":" + binding.canonical_selection_sha256,
                    "map_path_id": json_sha(warp["mapping"]), "primary_interval": occurrence["primary_interval"],
                    "mapping_checks": mapping_checks, "target_segment_index": int(target_segment_index),
                    "target_position": int(target_position)}
                hfa_record_owners.setdefault(record_id, []).append(owner)

            for record in prepared["records"]:
                all_hfa_records.append(record)
                entry = entry_by_position.get(int(record["position"]))
                if entry is None:
                    raise ValueError("HuBERTFA three-line record lacks its ledger owner")
                add_owner(record, entry, target_segment_index=1, target_position=int(record["position"]))
            for record in (anchored_block or {}).get("records", []):
                all_hfa_records.append(record)
                for output in record.get("target_outputs", []):
                    position = int(output["position"])
                    entry = entry_by_position.get(position)
                    if entry is None:
                        raise ValueError("HuBERTFA anchored block target lacks its ledger owner")
                    add_owner(record, entry, target_segment_index=int(output["target_segment_index"]), target_position=position)
            hfa_occurrences.append({"occurrence_id": oid, "prepared": prepared, "source_evidence": source_evidence,
                                    "anchored_block": anchored_block})
            if joint_policy is not None and observation.get("status") == "observed":
                joint_contexts[oid] = {"prepared": prepared, "cue_specs": cue_specs, "mapping": warp["mapping"],
                    "primary_interval": occurrence["primary_interval"], "mapping_checks": mapping_checks,
                    "path_id": binding.source_audio_sha256 + ":" + binding.canonical_selection_sha256,
                    "map_path_id": json_sha(warp["mapping"])}
                if exact_anchor_policy is not None:
                    joint_contexts[oid]["exact_observed_words"] = observation["words"]
        anchored_block_record_ids = {str(record["record_id"]) for occurrence_entry in hfa_occurrences
                                     for record in occurrence_entry["prepared"].get("anchored_block_records", [])}
        anchored_block_target_count = sum(
            len(record.get("target_outputs", [])) for occurrence_entry in hfa_occurrences
            for record in occurrence_entry["prepared"].get("anchored_block_records", []))
        owner_block_target_count = sum(len(hfa_record_owners.get(record_id, []))
                                       for record_id in anchored_block_record_ids)
        if anchored_block_target_count != owner_block_target_count:
            raise ValueError("HuBERTFA anchored block fan-out owner count mismatch")
        anchored_path_target_with_own_band_count = sum(
            int((occurrence_entry.get("anchored_block") or {}).get("anchored_path_target_with_own_band_count", 0))
            for occurrence_entry in hfa_occurrences)
        anchored_path_target_outer_only_band_count = sum(
            int((occurrence_entry.get("anchored_block") or {}).get("anchored_path_target_outer_only_band_count", 0))
            for occurrence_entry in hfa_occurrences)
        hfa_batch = None
        hfa_batch_artifacts = None
        if all_hfa_records:
            hfa_batch = execute_batch(hfa_config, all_hfa_records, work_dir=staging / "hubertfa_source_context_batch")
            hfa_batch_artifacts = {
                name: _relative_output_binding(staging, binding, label="HuBERTFA batch " + name)
                for name, binding in hfa_batch["artifacts"].items()
            }
            response_by_id = {str(record["record_id"]): record for record in hfa_batch["response"]["records"]}
            for record in all_hfa_records:
                record_id = str(record["record_id"])
                response = response_by_id[record_id]
                owners = hfa_record_owners.get(record_id, [])
                if not owners:
                    raise ValueError("HuBERTFA record lacks an owner")
                for owner in owners:
                    target_record = {**record, "target_segment_index": owner["target_segment_index"]}
                    result = finalize_records([target_record], [response], effective_mapping=owner["mapping"])[record_id]
                    entry = owner["entry"]
                    entry["result"] = result
                    entry["status"] = result["status"]
                    entry["reasons"] = [] if result["status"] == "observed_complete_interval" else [result["reason"]]
                    if result["status"] != "observed_complete_interval":
                        continue
                    lower, upper = (round(float(value) * 1000) for value in owner["primary_interval"])
                    start, end = result["projected_mix_interval_ms"]
                    if not lower <= start < end <= upper:
                        entry["overlay_rejection"] = "projected_interval_outside_resolved_occurrence"
                        continue
                    checks = [check for check in owner["mapping_checks"] if check.get("status") == "available"
                        and check.get("top1")
                        and check["top1"]["mix_start"] * 1000 <= start
                        and end <= check["top1"]["mix_end"] * 1000]
                    entry["overlay_contextual_mapping_checks"] = [json_sha(check) for check in checks]
                    entry["overlay_contextual_mapping_status"] = "covered" if checks else "not_checked_at_this_interval"
                    if any(check.get("ambiguous", True) for check in checks):
                        entry["overlay_rejection"] = "contextual_mapping_ambiguous"
                        continue
                    # Fan-out targets come only from strict block interiors;
                    # outer anchors are evidence and never become overlay rows.
                    hfa_overlay_records.append({"position": owner["target_position"], "record_id": record_id,
                        "target_segment_index": owner["target_segment_index"], "start_ms": start, "end_ms": end,
                        "lane_id": owner["occurrence_id"], "occurrence_path_id": owner["path_id"],
                        "map_path_id": owner["map_path_id"],
                        "contextual_mapping_checks": entry["overlay_contextual_mapping_checks"]})
        from lyric_aligner.alignment.source_context_hubertfa import compact_prepared_occurrence
        compact_hfa_occurrences = []
        for occurrence_entry in hfa_occurrences:
            compact_prepared = compact_prepared_occurrence(occurrence_entry["prepared"])
            compact_occurrence = {"occurrence_id": occurrence_entry["occurrence_id"],
                                  "prepared": compact_prepared,
                                  "source_evidence": occurrence_entry["source_evidence"]}
            compact_hfa_occurrences.append(compact_occurrence)
            payload = {"schema_version": "source-context-hubertfa-occurrence-result-1.2",
                "authority": SOURCE_CONTEXT_AUTHORITY, "automatic_production_mutation_allowed": False,
                "mode": hfa_config.mode, "prepared": compact_prepared,
                "source_evidence": occurrence_entry["source_evidence"],
                "batch": hfa_batch_artifacts}
            _write(staging / (occurrence_entry["occurrence_id"] + ".source_context_hubertfa.json"), payload)
        hfa_state = {"schema_version": "source-context-hubertfa-ledger-1.2", "authority": SOURCE_CONTEXT_AUTHORITY,
            "automatic_production_mutation_allowed": False, "mode": hfa_config.mode,
            "policy": hfa_config.identity(), "context_policy": hfa_context_policy,
            # ``eligible_record_count`` remains for existing readers.  A block
            # record can fan out to several owned target cues, so the explicit
            # target counts below must be used for coverage claims.
            "eligible_record_count": len(all_hfa_records),
            "inference_record_count": len(all_hfa_records),
            "eligible_target_count": sum(len(owners) for owners in hfa_record_owners.values()),
            "three_line_record_count": sum(len(item["prepared"].get("records", [])) for item in hfa_occurrences),
            "anchored_block_record_count": len(anchored_block_record_ids),
            "anchored_block_target_count": anchored_block_target_count,
            # These diagnostics preserve all eligible targets.  An outer-only
            # target is reported, never filtered, so it cannot masquerade as a
            # source-own-anchor gate.
            "anchored_path_target_with_own_band_count": anchored_path_target_with_own_band_count,
            "anchored_path_target_outer_only_band_count": anchored_path_target_outer_only_band_count,
            "direct_behavior_manifest": {name: producer_hashes[name] for name in hfa_direct_behavior_files},
            "direct_behavior_manifest_scope": "bounded project files directly controlling HFA qualification, G2P and target extraction; not a full transitive environment manifest",
            "observed_complete_interval_count": sum(
                entry.get("result", {}).get("status") == "observed_complete_interval"
                for occurrence_entry in hfa_occurrences for entry in occurrence_entry["prepared"]["ledger"]),
            "complete_target_interval_count": sum(
                entry.get("result", {}).get("status") == "observed_complete_interval"
                for occurrence_entry in hfa_occurrences for entry in occurrence_entry["prepared"]["ledger"]),
            "overlay_candidate_target_count": len(hfa_overlay_records),
            "overlay_selected_target_count": None,
            "occurrences": compact_hfa_occurrences, "batch": hfa_batch_artifacts}
        if hfa_batch_artifacts is not None:
            if set(hfa_batch_artifacts) != {"request", "response", "stdout", "stderr"}:
                raise ValueError("HuBERTFA batch artifact set is incomplete")
            for name, binding in hfa_batch_artifacts.items():
                _verify_relative_output_binding(staging, binding, label="HuBERTFA batch " + name)
        for occurrence_entry in compact_hfa_occurrences:
            _verify_relative_output_binding(staging, occurrence_entry["source_evidence"],
                                            label="HuBERTFA source evidence")

    nodes = []
    for position, (row, cue) in enumerate(zip(rows, before), 1):
        entry = ledger.get(position)
        lane = entry["occurrence_id"] if entry else row.get("occurrence_id") or row.get("track") or "baseline"
        keep = IntervalCandidate(candidate_id=str(position) + ":KEEP", start_ms=cue.start_ms,
            end_ms=cue.end_ms, lane_id=lane,
            occurrence_path_id=entry["occurrence_path_id"] if entry else "baseline",
            map_path_id=entry["map_path_id"] if entry else "baseline",
            is_baseline=True, selection_cost=1.0)
        candidates = [keep]
        if entry:
            candidates += [IntervalCandidate(**{k: v for k, v in c.items() if k in IntervalCandidate.__dataclass_fields__}) for c in entry["interval_candidates"]]
        nodes.append(IntervalNode(node_id=str(position), candidates=tuple(candidates), continuity_group_id=lane))
    selection = optimize_interval_sequence(nodes)
    if not selection.feasible:
        raise ValueError("shadow interval selection has no feasible baseline")
    selected_by_id = dict(zip(selection.selected_node_ids, selection.selected_intervals_ms))
    intervals = [tuple(selected_by_id[str(i)]) for i in range(1, len(before) + 1)]
    after_rows, after = _materialize(rows, before, intervals, staging)
    hfa_overlay = None
    if hfa_config is not None and hfa_config.mode == "hfa-only-overlay":
        hfa_by_position = {}
        for record in hfa_overlay_records:
            hfa_by_position.setdefault(record["position"], []).append(record)
        overlay_nodes = []
        for position, (row, cue) in enumerate(zip(rows, before), 1):
            regular_entry = ledger.get(position)
            lane = regular_entry["occurrence_id"] if regular_entry else row.get("occurrence_id") or row.get("track") or "baseline"
            keep = IntervalCandidate(candidate_id=str(position) + ":KEEP", start_ms=cue.start_ms, end_ms=cue.end_ms,
                lane_id=lane, occurrence_path_id=regular_entry["occurrence_path_id"] if regular_entry else "baseline",
                map_path_id=regular_entry["map_path_id"] if regular_entry else "baseline",
                is_baseline=True, selection_cost=1.0)
            candidates = [keep]
            for record in hfa_by_position.get(position, []):
                candidates.append(IntervalCandidate(candidate_id=str(position) + ":HFA:" + str(record["record_id"]),
                    start_ms=int(record["start_ms"]), end_ms=int(record["end_ms"]), lane_id=str(record["lane_id"]),
                    occurrence_path_id=str(record["occurrence_path_id"]), map_path_id=str(record["map_path_id"]),
                    is_baseline=False, selection_cost=0.0))
            overlay_nodes.append(IntervalNode(node_id=str(position), candidates=tuple(candidates), continuity_group_id=lane))
        overlay_selection = optimize_interval_sequence(overlay_nodes)
        if not overlay_selection.feasible:
            raise ValueError("HuBERTFA-only overlay has no feasible KEEP baseline")
        overlay_by_id = dict(zip(overlay_selection.selected_node_ids, overlay_selection.selected_intervals_ms))
        overlay_intervals = [tuple(overlay_by_id[str(i)]) for i in range(1, len(before) + 1)]
        overlay_staging = staging / "hfa_overlay_materialization"
        overlay_staging.mkdir()
        _overlay_rows, overlay_cues = _materialize(rows, before, overlay_intervals, overlay_staging,
            strategy_id="source-context-hubertfa-only-overlay-v1")
        (overlay_staging / "shadow.csv").replace(staging / "hfa_overlay.csv")
        (overlay_staging / "shadow.srt").replace(staging / "hfa_overlay.srt")
        overlay_staging.rmdir()
        _write(staging / "hfa_overlay.selection.json", asdict(overlay_selection))
        hfa_overlay = {"schema_version": "source-context-hubertfa-overlay-1.0", "authority": "experimental",
            "publish_ready": False, "automatic_production_mutation_allowed": False,
            "mode": "hfa-only-overlay", "baseline_input": "job current final interval",
            "candidate_policy": "HuBERTFA complete double endpoint=0; KEEP=1; geometry_dp",
            "candidate_count": len(hfa_overlay_records),
            "selected_hubertfa_interval_count": sum(not item.endswith(":KEEP") for item in overlay_selection.selected_candidate_ids),
            "changed_interval_count": sum((old.start_ms, old.end_ms) != (new.start_ms, new.end_ms)
                for old, new in zip(before, overlay_cues)),
            "start_changed_count": sum(old.start_ms != new.start_ms for old, new in zip(before, overlay_cues)),
            "end_changed_count": sum(old.end_ms != new.end_ms for old, new in zip(before, overlay_cues)),
            "selection": asdict(overlay_selection),
            "outputs": {name: sha256_file(staging / name) for name in
                ("hfa_overlay.csv", "hfa_overlay.srt", "hfa_overlay.selection.json")}}
        hfa_overlay["artifact_sha256"] = json_sha(hfa_overlay)
        _write(staging / "hfa_overlay.artifact.json", hfa_overlay)
        validate_pair(staging / "hfa_overlay.csv", staging / "hfa_overlay.srt")
        if hfa_state is not None:
            hfa_state["overlay_candidate_target_count"] = len(hfa_overlay_records)
            hfa_state["overlay_selected_target_count"] = hfa_overlay["selected_hubertfa_interval_count"]
    if joint_policy is not None:
        from scripts.source_joint_overlay import run_joint_overlay
        joint_summary = run_joint_overlay(config=hfa_config, contexts=joint_contexts,
            nodes=overlay_nodes, rows=rows, cues=before, staging=staging)
    # Materialize the HFA ledger once, after optional geometry selection has
    # supplied its separate target-selection count.  This avoids rewriting a
    # staged artifact immediately before publication.
    if hfa_state is not None:
        _write(staging / "source_context_hubertfa_ledger.json", hfa_state)
    for node_id, selected_id in zip(selection.selected_node_ids, selection.selected_candidate_ids):
        if int(node_id) in ledger:
            ledger[int(node_id)]["selected_candidate_id"] = selected_id
    _write(staging / "candidate_ledger.json", {"schema_version": "shadow-candidate-ledger-1.0",
        "strategy_id": SHADOW_STRATEGY_ID, "authority": "experimental_only",
        "scoring": "both_observed_edges=0; one_observed_edge=0.5; KEEP=1; uncalibrated_dimensionless",
        "source_sequences": source_sequences,
        "records": list(ledger.values())})
    _write(staging / "selection.json", asdict(selection))
    differences = [{"position": i, "text": old.text,
        "before_ms": [old.start_ms, old.end_ms], "after_ms": [new.start_ms, new.end_ms]}
        for i, (old, new) in enumerate(zip(before, after), 1) if (old.start_ms, old.end_ms) != (new.start_ms, new.end_ms)]
    _write(staging / "differences.json", differences)
    quality = None
    if job.get("regression"):
        # Evaluation-only inputs are deliberately first read after prediction.
        from scripts.v4_evaluate_product_boundaries import build_report
        lock, gold = regression_paths
        quality = {"baseline": build_report(lock, gold, [], srt_path, report_path),
                   "shadow": build_report(lock, gold, [], staging / "shadow.srt", staging / "shadow.csv")}
        from lyric_aligner.evaluation.product_boundary_quality import evaluate_boundaries
        paired = []
        for record in quality["shadow"]["records"]:
            record = dict(record)
            positions = [i for i, row in enumerate(rows) if row.get("track") == record["track"]
                and row.get("original_cue") == str(record["cue_number"])]
            if positions and record.get("final_ms") is not None:
                edge = 0 if record["boundary_kind"] == "start" else 1
                position = positions[0] if edge == 0 else positions[-1]
                original = before[position]
                current_ms = original.start_ms if edge == 0 else original.end_ms
                record["original_editor_ms"] = record["editor_ms"]
                record["editor_ms"] = current_ms
                record["partition"] = "regression"
                record["candidates"] = {"KEEP": current_ms}
                for candidate in ledger.get(position + 1, {}).get("interval_candidates", []):
                    record["candidates"][candidate["candidate_id"]] = candidate["start_ms" if edge == 0 else "end_ms"]
                record["selected_ms"] = intervals[position][edge]
                paired.append(record)
        quality["paired_vs_current_baseline"] = evaluate_boundaries(paired) if paired else None
        quality["evaluation_status"] = "historical_regression_not_new_blind"
        _write(staging / "historical_quality.json", quality)
    if any(sha256_file(Path(p)) != digest for p, digest in hashes.items()):
        raise ValueError("shadow input changed during execution")
    if any(sha256_file(code_root / name) != digest for name, digest in producer_hashes.items()):
        raise ValueError("shadow producer changed during execution; rerun with fixed code")
    selected_candidates = [candidate for entry in ledger.values() for candidate in entry["interval_candidates"]
        if candidate["candidate_id"] == entry.get("selected_candidate_id")]
    summary = {"schema_version": "subtitle-shadow-upgrade-result-1.0", "execution_mode": "shadow",
        "strategy_id": SHADOW_STRATEGY_ID, "authority": "experimental_only", "publish_ready": False,
        "task_fingerprint_sha256": fingerprint, "inputs": hashes, "source_observations": observations,
        "mapping_observations": mapping_observations,
        "source_sequences": source_sequences,
        "producer_files": producer_hashes,
        "before_cues": len(before), "after_cues": len(after), "target_cue_count": len(ledger),
        "candidate_cue_count": sum(bool(r["interval_candidates"]) for r in ledger.values()),
        "selected_source_interval_count": sum(not cid.endswith(":KEEP") for cid in selection.selected_candidate_ids),
        "selected_candidate_shapes": {shape: sum(candidate["candidate_shape"] == shape for candidate in selected_candidates)
            for shape in ("both", "start_only", "end_only")},
        "start_changed_count": sum(a.start_ms != b.start_ms for a, b in zip(before, after)),
        "end_changed_count": sum(a.end_ms != b.end_ms for a, b in zip(before, after)),
        "text_changed_count": sum(a.text != b.text for a, b in zip(before, after)),
        "new_human_annotations": 0, "elapsed_seconds": time.monotonic() - started,
        "quality_status": "historical_regression" if quality else "no_independent_boundary_truth",
        "selection": {"globally_optimal": selection.globally_optimal, "reason": selection.reason},
        "outputs": {p.name: sha256_file(p) for p in staging.iterdir() if p.is_file()}}
    if hfa_state is not None:
        summary["source_context_hubertfa"] = {"mode": hfa_state["mode"],
            "eligible_record_count": hfa_state["eligible_record_count"],
            "observed_complete_interval_count": hfa_state["observed_complete_interval_count"],
            "inference_record_count": hfa_state["inference_record_count"],
            "eligible_target_count": hfa_state["eligible_target_count"],
            "complete_target_interval_count": hfa_state["complete_target_interval_count"],
            "overlay_candidate_target_count": hfa_state["overlay_candidate_target_count"],
              "overlay_selected_target_count": hfa_state["overlay_selected_target_count"],
              "anchored_block_target_count": hfa_state["anchored_block_target_count"],
              "anchored_path_target_with_own_band_count": hfa_state["anchored_path_target_with_own_band_count"],
              "anchored_path_target_outer_only_band_count": hfa_state["anchored_path_target_outer_only_band_count"],
            "ledger_sha256": sha256_file(staging / "source_context_hubertfa_ledger.json")}
    if hfa_overlay is not None:
        summary["hfa_overlay"] = {"authority": hfa_overlay["authority"], "publish_ready": hfa_overlay["publish_ready"],
            "candidate_count": hfa_overlay["candidate_count"],
            "selected_hubertfa_interval_count": hfa_overlay["selected_hubertfa_interval_count"],
            "changed_interval_count": hfa_overlay["changed_interval_count"],
            "artifact_sha256": hfa_overlay["artifact_sha256"]}
    if joint_summary is not None:
        summary["joint_source_context"] = joint_summary
    summary["artifact_sha256"] = json_sha(summary)
    _write(staging / "shadow.artifact.json", summary)
    staging.rename(destination)
    validate_pair(destination / "shadow.csv", destination / "shadow.srt")
    return summary
