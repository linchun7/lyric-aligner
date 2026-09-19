"""Reuse existing human confirmations on their exact audio and lexical target.

This is annotation application, not model calibration or a learned selector.
It never grants a row-wide authority marker to an unconfirmed opposite edge.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from lyric_aligner.evaluation.editor_risk_profile import build_editor_risk_profile_artifact

from lyric_aligner.timeline.joint_boundary_geometry import select_compatible_edits

POLICY_ID = "exact-human-outer-boundary-reuse-1.2"


def apply_confirmed_boundaries(
    *, report_rows: Sequence[Mapping[str, Any]], selection_lock: Mapping[str, Any],
    selection_lock_file_sha256: str, human_gold: Mapping[str, Any],
    final_audio_sha256: str, task_fingerprint: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if final_audio_sha256 != human_gold.get("final_audio_sha256"):
        raise ValueError("human confirmations belong to another final audio")
    # Reuse the established lock/gold/partition/record validation, not a new
    # authority inferred from a caller's boolean flag.
    for kind in ("start", "end"):
        for population in ("calibration", "holdout"):
            build_editor_risk_profile_artifact(
                selection_lock=selection_lock,
                selection_lock_file_sha256=selection_lock_file_sha256,
                human_gold_artifact=human_gold,
                boundary_kind=kind, population=population,
            )
    if not report_rows or len(task_fingerprint) != 64:
        raise ValueError("report/task identity missing")
    output = [dict(row) for row in report_rows]
    indices = defaultdict(list)
    for index, row in enumerate(output):
        if row.get("task_fingerprint_sha256") != task_fingerprint:
            raise ValueError("report belongs to another task")
        start, end = int(row["start_ms"]), int(row["end_ms"])
        if start < 0 or end <= start:
            raise ValueError("invalid report geometry")
        original = str(row.get("original_cue", ""))
        if original.isdigit():
            indices[(row["track"], int(original))].append(index)
    targets = {row["case_id"]: row for row in selection_lock["selection"]["populations"]["outer"]}
    gold_by_case = defaultdict(list)
    for gold in human_gold["records"]:
        if gold["population"] == "outer":
            gold_by_case[gold["case_id"]].append(gold)
    decisions = []
    normalize = lambda text: "".join(str(text).split())
    for case_id, golds in sorted(gold_by_case.items()):
        target = targets[case_id]
        positions = indices.get((target["track"], target["cue_number"]), [])
        reason = None
        if not positions:
            reason = "target_not_present"
        elif positions != list(range(positions[0], positions[-1]+1)):
            reason = "noncontiguous_ownership"
        elif normalize("".join(output[i]["text"] for i in positions)) != normalize(target["canonical_text"]):
            reason = "lexical_target_changed"
        pending = []
        for gold in sorted(golds, key=lambda g: g["boundary_kind"]):
            kind = gold["boundary_kind"]
            index = (positions[0] if kind == "start" else positions[-1]) if positions else None
            current = int(output[index][kind+"_ms"]) if index is not None else None
            decision = {
                "record_id": gold["id"], "case_id": case_id, "boundary_kind": kind,
                "output_position": None if index is None else index+1,
                "original_cue": target["cue_number"], "track": target["track"],
                "before_ms": current, "confirmed_ms": gold["gold_ms"],
                "uncertainty_ms": gold["gold_uncertainty_ms"],
                "selected_ms": current, "action": "keep", "reason": reason,
            }
            if reason is None:
                if abs(current-gold["gold_ms"]) <= gold["gold_uncertainty_ms"]:
                    decision["reason"] = "already_within_confirmed_tolerance"
                else:
                    decision.update(selected_ms=gold["gold_ms"], action="reuse_human_confirmation", reason="exact_audio_and_lexical_target")
            pending.append(decision)
        decisions.extend(pending)
    changes = [d for d in decisions if d['action'] == 'reuse_human_confirmation']
    accepted = select_compatible_edits(output, [
        {'id': d['record_id'], 'row_index': d['output_position']-1,
         'boundary_kind': d['boundary_kind'], 'selected_ms': d['selected_ms']}
        for d in changes
    ])
    for decision in changes:
        if decision['record_id'] not in accepted:
            decision.update(action='keep', selected_ms=decision['before_ms'], reason='geometry_conflict')
            continue
        row = output[decision['output_position']-1]
        kind = decision['boundary_kind']
        row[kind+'_ms'] = decision['selected_ms']
        row['human_confirmed_'+kind+'_record'] = decision['record_id']
        row['human_confirmation_artifact_sha256'] = human_gold['artifact_sha256']
    return output, decisions
