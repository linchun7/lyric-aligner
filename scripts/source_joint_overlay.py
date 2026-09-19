"""Experimental all-or-none adjacent-pair source-context overlay.

The existing HuBERTFA overlay produces one candidate per cue.  When two
adjacent candidates cannot coexist under the same interval geometry, this
module requests a fresh two-target source window and admits both targets only
as one atomic proposal.  It is deliberately a producer-side shadow surface:
it never edits the input nodes, never reuses an old response, and never marks
the resulting CSV/SRT publishable.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.alignment.source_context_hubertfa import (
    execute_batch,
    finalize_records,
    json_sha,
)
from lyric_aligner.alignment.source_joint_context import (
    SOURCE_JOINT_CONTEXT_POLICY_ID,
    SOURCE_JOINT_EXACT_CONTEXT_POLICY_ID,
    prepare_joint_pairs,
)
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.boundary_sequence_optimizer import (
    IntervalCandidate,
    IntervalNode,
    _interval_pair_allowed,
    optimize_interval_sequence,
)


JOINT_OVERLAY_SCHEMA_VERSION = "source-context-joint-overlay-1.0"
JOINT_OVERLAY_POLICY_ID = "experimental atomic-adjacent-pair-v1"
JOINT_OVERLAY_AUTHORITY = "experimental"


class JointOverlayError(ValueError):
    """Raised when an atomic overlay cannot prove its input/output contract."""


def _write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _relative_hash(
    path: Path,
    root: Path,
    *,
    label: str,
    expected_sha256: str | None = None,
) -> dict[str, str]:
    path = Path(path).resolve()
    root = Path(root).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise JointOverlayError(f"{label} is outside joint staging") from exc
    if not path.is_file():
        raise JointOverlayError(f"{label} does not exist")
    from lyric_aligner.contracts.artifacts import sha256_file

    actual_sha256 = sha256_file(path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise JointOverlayError(f"{label} SHA mismatch")
    return {"relative_path": relative.as_posix(), "sha256": actual_sha256}


def _node_positions(nodes: Sequence[IntervalNode]) -> dict[int, IntervalNode]:
    """Map the existing node identities to cue positions without reordering."""

    result: dict[int, IntervalNode] = {}
    for index, node in enumerate(nodes, 1):
        if not isinstance(node, IntervalNode):
            raise JointOverlayError("joint overlay requires IntervalNode inputs")
        node.validate()
        try:
            position = int(node.node_id)
        except (TypeError, ValueError):
            position = index
        if position <= 0 or position in result:
            raise JointOverlayError("joint overlay node positions must be positive and unique")
        result[position] = node
    return result


def _baseline(node: IntervalNode) -> IntervalCandidate:
    baselines = [candidate for candidate in node.candidates if candidate.is_baseline]
    if len(baselines) != 1:
        raise JointOverlayError(f"node {node.node_id} must have exactly one KEEP baseline")
    return baselines[0]


def _hfa_candidates(node: IntervalNode) -> list[IntervalCandidate]:
    """The caller supplies the already-built HFA overlay nodes.

    Those nodes have exactly one KEEP plus their non-baseline HFA proposals.
    Keeping this predicate structural avoids tying the producer to WALK or to
    one candidate-id spelling; the input surface itself is the authority.
    """

    return [candidate for candidate in node.candidates if not candidate.is_baseline]


def _context_identity(item: Mapping[str, Any], fallback: str) -> str:
    value = item.get("occurrence_id", fallback)
    if not isinstance(value, str) or not value.strip():
        raise JointOverlayError("joint overlay context occurrence_id must be non-empty")
    return value


def _context_for_candidate(
    candidate: IntervalCandidate,
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[str, Mapping[str, Any]] | None:
    """Resolve a candidate lane to a supplied occurrence context."""

    lane = str(candidate.lane_id)
    direct = contexts.get(lane)
    if isinstance(direct, Mapping):
        return lane, direct
    for key, item in contexts.items():
        if not isinstance(item, Mapping):
            continue
        try:
            identity = _context_identity(item, str(key))
        except JointOverlayError:
            continue
        if identity == lane or str(item.get("path_id")) == str(candidate.occurrence_path_id):
            return str(key), item
    return None


def _mapping_checks_covering_interval(
    mapping_checks: Any,
    interval: tuple[int, int],
) -> list[Mapping[str, Any]]:
    """Use the same available/top1 coverage rule as the existing HFA overlay."""

    if mapping_checks is None:
        return []
    if not isinstance(mapping_checks, Sequence) or isinstance(mapping_checks, (str, bytes)):
        raise JointOverlayError("joint overlay mapping_checks must be a sequence")
    covered: list[Mapping[str, Any]] = []
    for check in mapping_checks:
        if not isinstance(check, Mapping) or check.get("status") != "available":
            continue
        top1 = check.get("top1")
        if not isinstance(top1, Mapping):
            continue
        try:
            if "mix_start_ms" in top1 or "mix_end_ms" in top1:
                low, high = int(top1["mix_start_ms"]), int(top1["mix_end_ms"])
            else:
                low = int(round(float(top1["mix_start"]) * 1000.0))
                high = int(round(float(top1["mix_end"]) * 1000.0))
        except (KeyError, TypeError, ValueError):
            continue
        if low <= interval[0] and interval[1] <= high:
            covered.append(check)
    return covered


def _primary_interval_ms(context: Mapping[str, Any]) -> tuple[int, int]:
    raw = context.get("primary_interval_ms")
    if raw is None:
        raw = context.get("primary_interval")
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 2:
            try:
                raw = [float(raw[0]) * 1000.0, float(raw[1]) * 1000.0]
            except (TypeError, ValueError):
                raw = None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 2:
        raise JointOverlayError("joint overlay context requires primary_interval or primary_interval_ms")
    try:
        start, end = int(round(float(raw[0]))), int(round(float(raw[1])))
    except (TypeError, ValueError) as exc:
        raise JointOverlayError("joint overlay primary interval is invalid") from exc
    if not 0 <= start < end:
        raise JointOverlayError("joint overlay primary interval must be positive")
    return start, end


def _interval_from_result(result: Mapping[str, Any]) -> tuple[int, int] | None:
    raw = result.get("projected_mix_interval_ms")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 2:
        return None
    try:
        start, end = int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    return start, end


def _response_words_sha(result: Mapping[str, Any], response: Mapping[str, Any]) -> str | None:
    for key in ("words_sha256", "words_sha"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    words = response.get("words")
    if isinstance(words, list):
        return json_sha(words)
    return None


def _serialize_selection(selection: Any) -> dict[str, Any]:
    if hasattr(selection, "to_dict"):
        return selection.to_dict()
    return asdict(selection)


def _copy_nodes_with_candidates(
    nodes: Sequence[IntervalNode],
    additions: Mapping[str, Sequence[IntervalCandidate]],
) -> list[IntervalNode]:
    copied: list[IntervalNode] = []
    for node in nodes:
        copied.append(
            IntervalNode(
                node_id=node.node_id,
                candidates=tuple(node.candidates) + tuple(additions.get(node.node_id, ())),
                continuity_group_id=node.continuity_group_id,
            )
        )
    return copied


def _materialize_joint_overlay(
    rows: Sequence[Mapping[str, Any]],
    cues: Sequence[Cue],
    intervals: Sequence[tuple[int, int]],
    staging: Path,
    *, strategy_id: str = JOINT_OVERLAY_POLICY_ID,
) -> tuple[list[dict[str, Any]], list[Cue], dict[str, dict[str, str]]]:
    """Use the existing shadow materializer, then expose joint names only."""

    from scripts.v4_shadow_upgrade import _materialize

    if [str(row.get("text", "")) for row in rows] != [cue.text for cue in cues]:
        raise JointOverlayError("joint overlay input rows/cues text/order mismatch")
    materialization = staging / "joint_overlay_materialization"
    if materialization.exists():
        raise JointOverlayError("joint overlay materialization directory already exists")
    materialization.mkdir(parents=True)
    output_rows, output_cues = _materialize(
        list(rows), list(cues), list(intervals), materialization,
        strategy_id=strategy_id,
    )
    if [cue.text for cue in output_cues] != [cue.text for cue in cues]:
        raise JointOverlayError("joint overlay materializer changed cue text/order")
    if [str(row.get("text", "")) for row in output_rows] != [cue.text for cue in cues]:
        raise JointOverlayError("joint overlay CSV changed cue text/order")
    outputs: dict[str, dict[str, str]] = {}
    for source_name, destination_name in (("shadow.csv", "joint_overlay.csv"),
                                          ("shadow.srt", "joint_overlay.srt")):
        source = materialization / source_name
        destination = staging / destination_name
        if destination.exists():
            raise JointOverlayError(f"joint overlay output already exists: {destination_name}")
        source.replace(destination)
        outputs[destination_name] = _relative_hash(destination, staging, label=destination_name)
    materialization.rmdir()
    return output_rows, output_cues, outputs


def _prepare_conflict_pairs(
    contexts: Mapping[str, Mapping[str, Any]],
    nodes: Sequence[IntervalNode],
    node_by_position: Mapping[int, IntervalNode],
) -> tuple[list[dict[str, Any]], dict[str, list[tuple[int, int]]]]:
    """Find only geometry conflicts that can benefit from a pair request."""

    pair_outcomes: list[dict[str, Any]] = []
    requests: dict[str, set[tuple[int, int]]] = {}
    positions = [int(node.node_id) if str(node.node_id).lstrip("-").isdigit() else index
                 for index, node in enumerate(nodes, 1)]
    for index in range(len(nodes) - 1):
        left, right = nodes[index], nodes[index + 1]
        if left.continuity_group_id != right.continuity_group_id:
            continue
        left_hfa = _hfa_candidates(left)
        if not left_hfa:
            continue
        right_hfa = _hfa_candidates(right)
        right_options = right_hfa or [_baseline(right)]
        left_base, right_base = _baseline(left), _baseline(right)
        combinations = []
        for left_candidate in left_hfa:
            for right_candidate in right_options:
                allowed = _interval_pair_allowed(
                    previous=left_candidate,
                    current=right_candidate,
                    previous_baseline=left_base,
                    current_baseline=right_base,
                    same_continuity_group=True,
                )
                combinations.append({
                    "left_candidate_id": left_candidate.candidate_id,
                    "right_candidate_id": right_candidate.candidate_id,
                    "allowed": bool(allowed),
                })
        pair = (positions[index], positions[index + 1])
        outcome: dict[str, Any] = {
            "pair": list(pair),
            "left_node_id": left.node_id,
            "right_node_id": right.node_id,
            "continuity_group_id": left.continuity_group_id,
            "existing_combinations": combinations,
            "status": "compatible_existing_path" if any(item["allowed"] for item in combinations)
            else "conflict_triggered",
        }
        if any(item["allowed"] for item in combinations):
            pair_outcomes.append(outcome)
            continue
        context_keys: set[str] = set()
        for candidate in left_hfa:
            resolved = _context_for_candidate(candidate, contexts)
            if resolved is not None:
                context_keys.add(resolved[0])
            else:
                outcome.setdefault("rejections", []).append({
                    "candidate_id": candidate.candidate_id,
                    "reason": "left_candidate_context_missing",
                })
        if not context_keys:
            outcome["status"] = "rejected"
            outcome.setdefault("rejections", []).append({"reason": "left_candidate_context_missing"})
        else:
            for context_key in sorted(context_keys):
                requests.setdefault(context_key, []).append(pair)
            outcome["context_keys"] = sorted(context_keys)
        pair_outcomes.append(outcome)
    # A pair can be encountered once per adjacent node, but callers may have
    # repeated context identities.  Keep request order deterministic.
    for key, values in list(requests.items()):
        requests[key] = sorted(set(values))
    return pair_outcomes, requests


def run_joint_overlay(
    config: Any,
    contexts: Mapping[str, Mapping[str, Any]],
    nodes: Sequence[IntervalNode],
    rows: Sequence[Mapping[str, Any]],
    cues: Sequence[Cue],
    staging: Path,
) -> dict[str, Any]:
    """Run fresh adjacent-pair inference and materialize an experimental overlay.

    ``contexts`` is keyed by occurrence and each value must contain the full
    anchored-path ``prepared`` payload plus ``mapping``, ``primary_interval``,
    ``mapping_checks``, ``path_id`` and ``map_path_id``.  Existing HFA nodes are
    copied before adding candidates.  A pair is added to the copy only when
    both target segments finalize completely from the same response.
    """

    if not isinstance(contexts, Mapping):
        raise JointOverlayError("joint overlay contexts must be a mapping")
    if not isinstance(nodes, Sequence) or not nodes:
        raise JointOverlayError("joint overlay requires non-empty HFA overlay nodes")
    if not isinstance(rows, Sequence) or not isinstance(cues, Sequence) or len(rows) != len(cues):
        raise JointOverlayError("joint overlay rows/cues must have equal lengths")
    if len(nodes) != len(cues):
        raise JointOverlayError("joint overlay nodes/cues must have equal lengths")
    staging = Path(staging).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    node_by_position = _node_positions(nodes)
    exact_enabled = any("exact_observed_words" in item for item in contexts.values())
    provider_policy = (SOURCE_JOINT_EXACT_CONTEXT_POLICY_ID if exact_enabled
                       else SOURCE_JOINT_CONTEXT_POLICY_ID)
    overlay_policy = "experimental " + provider_policy
    for key, item in contexts.items():
        if not isinstance(item, Mapping) or not isinstance(item.get("prepared"), Mapping):
            raise JointOverlayError(f"joint overlay context {key!r} lacks full prepared payload")
        _context_identity(item, str(key))
        for field in ("mapping", "primary_interval", "mapping_checks", "path_id", "map_path_id", "cue_specs"):
            if field not in item:
                raise JointOverlayError(f"joint overlay context {key!r} missing {field}")
        _primary_interval_ms(item)
        if not isinstance(item["cue_specs"], Sequence) or isinstance(item["cue_specs"], (str, bytes)):
            raise JointOverlayError(f"joint overlay context {key!r} cue_specs must be a sequence")

    pair_outcomes, requests = _prepare_conflict_pairs(contexts, nodes, node_by_position)
    preparation: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    record_context: dict[str, tuple[str, Mapping[str, Any], tuple[int, int]]] = {}
    for context_key in sorted(requests):
        context = contexts[context_key]
        prepared = context["prepared"]
        pairs = requests[context_key]
        report: dict[str, Any] = {
            "occurrence_id": _context_identity(context, context_key),
            "context_key": context_key,
            "pair_requests": [list(pair) for pair in pairs],
            "prepared_sha256": json_sha(prepared),
            "status": "prepared",
            "records": [],
            "rejections": [],
        }
        try:
            extra = ({"observed_words": context["exact_observed_words"]}
                     if "exact_observed_words" in context else {})
            prepared_result = prepare_joint_pairs(
                prepared,
                pairs,
                cue_specs=context["cue_specs"],
                dictionary_path=Path(config.dictionary_path),
                **extra,
            )
        except Exception as exc:  # preparation is per-context and fail-closed
            report["status"] = "preparation_failed"
            report["rejections"] = [{"reason": type(exc).__name__ + ":" + str(exc)}]
            preparation.append(report)
            continue
        report["joint_proposal_policy_id"] = prepared_result.get("joint_proposal_policy_id")
        if "exact_source_anchor_evidence" in prepared_result:
            report["exact_source_anchor_evidence"] = prepared_result["exact_source_anchor_evidence"]
        report["pair_results"] = list(prepared_result.get("pair_results", []))
        report["rejections"] = [item for item in report["pair_results"] if item.get("status") != "ready"]
        for record in prepared_result.get("records", []):
            if not isinstance(record, Mapping):
                report["rejections"].append({"reason": "prepared_record_not_mapping"})
                continue
            record = dict(record)
            record_id = str(record.get("record_id") or "")
            outputs = record.get("target_outputs")
            if not record_id or record_id in record_context:
                report["rejections"].append({"record_id": record_id, "reason": "duplicate_or_missing_record_id"})
                continue
            expected_policy = (SOURCE_JOINT_EXACT_CONTEXT_POLICY_ID if "exact_observed_words" in context
                               else SOURCE_JOINT_CONTEXT_POLICY_ID)
            if record.get("joint_proposal_policy_id") != expected_policy:
                report["rejections"].append({"record_id": record_id, "reason": "joint_policy_identity_mismatch"})
                continue
            if record.get("context_policy") != "anchored-path-v1":
                report["rejections"].append({"record_id": record_id, "reason": "provider_context_policy_not_anchored_path"})
                continue
            if not isinstance(outputs, list) or len(outputs) != 2:
                report["rejections"].append({"record_id": record_id, "reason": "joint_record_must_have_two_targets"})
                continue
            try:
                positions = tuple(int(item["position"]) for item in outputs)
            except (KeyError, TypeError, ValueError):
                report["rejections"].append({"record_id": record_id, "reason": "joint_target_positions_invalid"})
                continue
            if positions[0] >= positions[1] or positions[1] != positions[0] + 1:
                report["rejections"].append({"record_id": record_id, "reason": "joint_targets_not_adjacent"})
                continue
            record_context[record_id] = (context_key, context, positions)
            records.append(record)
            report["records"].append({"record_id": record_id, "pair": list(positions)})
        preparation.append(report)

    batch: dict[str, Any] | None = None
    batch_bindings: dict[str, dict[str, str]] = {}
    response_by_id: dict[str, Mapping[str, Any]] = {}
    if records:
        batch = execute_batch(
            config,
            records,
            work_dir=staging / "joint_source_context_batch",
        )
        response_payload = batch.get("response") if isinstance(batch, Mapping) else None
        response_records = response_payload.get("records") if isinstance(response_payload, Mapping) else None
        if not isinstance(response_records, list):
            raise JointOverlayError("joint source-context batch has no response records")
        response_by_id = {str(item.get("record_id")): item for item in response_records if isinstance(item, Mapping)}
        if list(response_by_id) != [str(record["record_id"]) for record in records]:
            raise JointOverlayError("joint source-context response ids/order mismatch")
        artifacts = batch.get("artifacts") if isinstance(batch, Mapping) else None
        if not isinstance(artifacts, Mapping):
            raise JointOverlayError("joint source-context batch artifacts missing")
        for name, binding in artifacts.items():
            if not isinstance(binding, Mapping) or "path" not in binding:
                raise JointOverlayError(f"joint batch artifact {name} binding invalid")
            expected_sha = binding.get("sha256")
            if not isinstance(expected_sha, str) or not expected_sha:
                raise JointOverlayError(f"joint batch artifact {name} SHA binding invalid")
            batch_bindings[str(name)] = _relative_hash(
                Path(binding["path"]), staging,
                label="joint batch " + str(name), expected_sha256=expected_sha,
            )

    additions: dict[str, list[IntervalCandidate]] = {}
    atomic_proposals: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {}
    record_evaluations: list[dict[str, Any]] = []
    accepted_records = 0
    for record in records:
        record_id = str(record["record_id"])
        context_key, context, positions = record_context[record_id]
        response = response_by_id[record_id]
        outputs = record["target_outputs"]
        target_evaluations: list[dict[str, Any]] = []
        finalized: list[tuple[Mapping[str, Any], Mapping[str, Any], tuple[int, int], str]] = []
        reject_reason: str | None = None
        try:
            for output in outputs:
                target_record = {**record, "target_segment_index": int(output["target_segment_index"])}
                result = finalize_records(
                    [target_record], [response], effective_mapping=context["mapping"]
                )[record_id]
                interval = _interval_from_result(result)
                words_sha = _response_words_sha(result, response)
                covered_checks = (
                    _mapping_checks_covering_interval(context.get("mapping_checks"), interval)
                    if interval is not None else []
                )
                target_evaluations.append({
                    "position": int(output["position"]),
                    "target_segment_index": int(output["target_segment_index"]),
                    "status": result.get("status"),
                    "interval_ms": list(interval) if interval is not None else None,
                    "words_sha256": words_sha,
                    "mapping_check_sha256": [json_sha(check) for check in covered_checks],
                    "mapping_check_status": "covered" if covered_checks else "not_checked_at_this_interval",
                    "reason": result.get("reason", ""),
                })
                if reject_reason is None and result.get("status") != "observed_complete_interval":
                    reject_reason = "joint_target_incomplete"
                if reject_reason is None and interval is None:
                    reject_reason = "joint_target_interval_invalid"
                if reject_reason is None:
                    lower, upper = _primary_interval_ms(context)
                    if not lower <= interval[0] < interval[1] <= upper:
                        reject_reason = "joint_target_outside_primary_interval"
                if reject_reason is None and any(check.get("ambiguous", True) for check in covered_checks):
                    reject_reason = "existing_mapping_check_ambiguous"
                if reject_reason is None:
                    finalized.append((output, result, interval, words_sha or ""))
        except Exception as exc:
            reject_reason = "joint_target_finalize_failed:" + type(exc).__name__
        if reject_reason is None:
            hashes = [item[3] for item in finalized]
            if len(finalized) != 2 or not hashes[0] or hashes[0] != hashes[1]:
                reject_reason = "joint_targets_words_sha_mismatch"
        if reject_reason is None:
            left_node = node_by_position.get(positions[0])
            right_node = node_by_position.get(positions[1])
            if left_node is None or right_node is None:
                reject_reason = "joint_target_node_missing"
        if reject_reason is None:
            # Do not mutate nodes until both targets have passed every gate.
            left_output, left_result, left_interval, _left_sha = finalized[0]
            right_output, right_result, right_interval, _right_sha = finalized[1]
            context_identity = _context_identity(context, context_key)
            candidate_ids = (
                f"{positions[0]}:JOINT:{record_id}:0",
                f"{positions[1]}:JOINT:{record_id}:1",
            )
            left_candidate = IntervalCandidate(
                candidate_id=candidate_ids[0], start_ms=left_interval[0], end_ms=left_interval[1],
                lane_id=context_identity, occurrence_path_id=str(context["path_id"]),
                map_path_id=str(context["map_path_id"]), is_baseline=False, selection_cost=0.0,
            )
            right_candidate = IntervalCandidate(
                candidate_id=candidate_ids[1], start_ms=right_interval[0], end_ms=right_interval[1],
                lane_id=context_identity, occurrence_path_id=str(context["path_id"]),
                map_path_id=str(context["map_path_id"]), is_baseline=False, selection_cost=0.0,
            )
            additions.setdefault(node_by_position[positions[0]].node_id, []).append(left_candidate)
            additions.setdefault(node_by_position[positions[1]].node_id, []).append(right_candidate)
            proposal_id = f"{overlay_policy}:{record_id}"
            atomic_proposals[proposal_id] = (
                (node_by_position[positions[0]].node_id, candidate_ids[0]),
                (node_by_position[positions[1]].node_id, candidate_ids[1]),
            )
            accepted_records += 1
        record_evaluations.append({
            "record_id": record_id,
            "context_key": context_key,
            "pair": list(positions),
            "status": "accepted_atomic_pair" if reject_reason is None else "rejected",
            "reason": reject_reason or "",
            "target_evaluations": target_evaluations,
        })

    updated_nodes = _copy_nodes_with_candidates(nodes, additions)
    selection = optimize_interval_sequence(updated_nodes, atomic_proposals=atomic_proposals)
    if not selection.feasible:
        raise JointOverlayError("joint overlay interval selection has no feasible KEEP baseline")
    selected_by_node = dict(zip(selection.selected_node_ids, selection.selected_intervals_ms))
    intervals = [tuple(selected_by_node[str(node.node_id)]) for node in nodes]
    _output_rows, _output_cues, overlay_outputs = _materialize_joint_overlay(
        rows, cues, intervals, staging, strategy_id=overlay_policy)

    selection_path = staging / "joint_overlay.selection.json"
    _write_json(selection_path, _serialize_selection(selection))
    preparation_path = staging / "joint_preparation.json"
    _write_json(preparation_path, {
        "schema_version": JOINT_OVERLAY_SCHEMA_VERSION,
        "policy_id": overlay_policy,
        "provider_policy_id": provider_policy,
        "contexts": preparation,
        "record_evaluations": record_evaluations,
    })
    outcomes_path = staging / "joint_pair_outcomes.json"
    _write_json(outcomes_path, {
        "schema_version": JOINT_OVERLAY_SCHEMA_VERSION,
        "policy_id": overlay_policy,
        "pair_outcomes": pair_outcomes,
        "record_evaluations": record_evaluations,
    })
    overlay_outputs["joint_overlay.selection.json"] = _relative_hash(selection_path, staging, label="joint selection")
    overlay_outputs["joint_preparation.json"] = _relative_hash(preparation_path, staging, label="joint preparation")
    overlay_outputs["joint_pair_outcomes.json"] = _relative_hash(outcomes_path, staging, label="joint pair outcomes")

    selected_joint_count = sum(
        candidate_id.startswith(":JOINT:") or ":JOINT:" in candidate_id
        for candidate_id in selection.selected_candidate_ids
    )
    changed_count = sum(
        (cue.start_ms, cue.end_ms) != interval
        for cue, interval in zip(cues, intervals)
    )
    artifact: dict[str, Any] = {
        "schema_version": JOINT_OVERLAY_SCHEMA_VERSION,
        "policy_id": overlay_policy,
        "provider_joint_policy_id": provider_policy,
        "authority": JOINT_OVERLAY_AUTHORITY,
        "publish_ready": False,
        "automatic_production_mutation_allowed": False,
        "input_node_count": len(nodes),
        "conflict_pair_count": sum(item.get("status") == "conflict_triggered" for item in pair_outcomes),
        "prepared_record_count": len(records),
        "accepted_atomic_pair_count": accepted_records,
        "selected_joint_candidate_count": selected_joint_count,
        "changed_interval_count": changed_count,
        "start_changed_count": sum(cue.start_ms != interval[0] for cue, interval in zip(cues, intervals)),
        "end_changed_count": sum(cue.end_ms != interval[1] for cue, interval in zip(cues, intervals)),
        "batch": batch_bindings,
        "outputs": overlay_outputs,
        "selection": _serialize_selection(selection),
    }
    artifact["artifact_sha256"] = json_sha(artifact)
    artifact_path = staging / "joint_overlay.artifact.json"
    _write_json(artifact_path, artifact)
    artifact_binding = _relative_hash(artifact_path, staging, label="joint overlay artifact")
    return {
        **artifact,
        "artifact": artifact_binding,
        "pair_outcomes": pair_outcomes,
        "record_evaluations": record_evaluations,
    }


__all__ = [
    "JOINT_OVERLAY_AUTHORITY",
    "JOINT_OVERLAY_POLICY_ID",
    "JOINT_OVERLAY_SCHEMA_VERSION",
    "JointOverlayError",
    "run_joint_overlay",
]
