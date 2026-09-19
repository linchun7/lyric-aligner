"""Authorize only lyric-sequential resolution of ambiguous adjacent occurrences.

The base Max transition issue is an occurrence ambiguity.  This resolver may
clear that review block when final-mix editor lexical witnesses uniquely prove
left/right lyric ownership and non-overlapping order.  Source-audio crossfade
observed by positional evidence remains diagnostic and is never promoted to a
confirmed overlap by this layer.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from lyric_aligner.review.decisions import build_review_template

SCHEMA_VERSION = "transition-lyric-resolution-1.0"
POLICY_ID = "final-mix-editor-lexical-occurrence-resolution-1.0"
LEXICAL_SCHEMA = "adjacent-transition-editor-lexical-evidence-1.0"
POSITIONAL_SCHEMA = "adjacent-transition-positional-v2-report-1"


class TransitionLyricResolutionError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _pair(row: Mapping[str, Any]) -> tuple[str, str]:
    return _text(row.get("left_occurrence_id")), _text(row.get("right_occurrence_id"))


def _validate_common(
    *,
    run: Mapping[str, Any],
    base_run_artifact_id: str,
    run_sha256: str,
    source_srt_sha256: str,
    lexical: Mapping[str, Any],
    positional: Mapping[str, Any],
) -> None:
    fingerprint = _text(run.get("task_fingerprint_sha256"))
    if run.get("status") != "review_required" or run.get("legacy_fallback_used") is not False:
        raise TransitionLyricResolutionError("base run is not a fail-closed review_required Max run")
    if not fingerprint or len(run_sha256) != 64 or len(source_srt_sha256) != 64:
        raise TransitionLyricResolutionError("invalid base lineage identity")
    if lexical.get("schema_version") != LEXICAL_SCHEMA:
        raise TransitionLyricResolutionError("unsupported lexical evidence schema")
    if lexical.get("task_fingerprint_sha256") != fingerprint:
        raise TransitionLyricResolutionError("lexical evidence belongs to another task")
    if lexical.get("run_sha256") != run_sha256:
        raise TransitionLyricResolutionError("lexical evidence belongs to another Max run")
    if lexical.get("run_artifact_id") != base_run_artifact_id:
        raise TransitionLyricResolutionError("lexical evidence base artifact mismatch")
    if lexical.get("source_srt_sha256") != source_srt_sha256:
        raise TransitionLyricResolutionError("lexical evidence source SRT mismatch")
    if lexical.get("automatic_authority_granted") is not False:
        raise TransitionLyricResolutionError("lexical evidence unexpectedly claims automatic authority")
    if lexical.get("timing_mutation_performed") is not False:
        raise TransitionLyricResolutionError("lexical evidence unexpectedly mutated timing")

    if positional.get("schema_version") != POSITIONAL_SCHEMA:
        raise TransitionLyricResolutionError("unsupported positional evidence schema")
    if positional.get("task_fingerprint_sha256") != fingerprint:
        raise TransitionLyricResolutionError("positional evidence belongs to another task")
    if positional.get("run_sha256") != run_sha256:
        raise TransitionLyricResolutionError("positional evidence belongs to another Max run")
    if positional.get("run_artifact_id") != base_run_artifact_id:
        raise TransitionLyricResolutionError("positional evidence base artifact mismatch")
    authority = positional.get("authority")
    if not isinstance(authority, Mapping) or authority.get("shadow_only") is not True:
        raise TransitionLyricResolutionError("positional evidence is not shadow-only")
    if authority.get("automatic_review_decision") is not False:
        raise TransitionLyricResolutionError("positional evidence unexpectedly claims review authority")
    if authority.get("timing_mutation_performed") is not False:
        raise TransitionLyricResolutionError("positional evidence unexpectedly claims mutation")


def _lexical_clear(row: Mapping[str, Any]) -> tuple[bool, str]:
    if row.get("recommendation") != "clear_candidate":
        return False, "lexical_not_clear_candidate"
    identity = row.get("identity_support")
    order = row.get("order_support")
    if not isinstance(identity, Mapping) or not isinstance(order, Mapping):
        return False, "lexical_evidence_shape_invalid"
    if identity.get("enough_identity") is not True:
        return False, "lexical_identity_insufficient"
    if int(identity.get("left_unique_cue_count") or 0) < 1 or int(identity.get("right_unique_cue_count") or 0) < 1:
        return False, "lexical_side_identity_missing"
    if int(identity.get("left_unique_normalized_chars") or 0) < 10 or int(identity.get("right_unique_normalized_chars") or 0) < 10:
        return False, "lexical_side_identity_too_short"
    if int(identity.get("ambiguous_cue_count") or 0) != 0:
        return False, "lexical_identity_ambiguous"
    if order.get("lexical_order_clear") is not True:
        return False, "lexical_order_not_clear"
    last_left = order.get("last_left_editor_end_ms")
    first_right = order.get("first_right_editor_start_ms")
    gap = order.get("sequential_gap_ms")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (last_left, first_right, gap)):
        return False, "lexical_order_timing_missing"
    if first_right < last_left or gap < 0 or gap != first_right - last_left:
        return False, "lexical_order_negative_or_inconsistent"
    return True, "final_mix_editor_lexical_sequence_clear"


def authorize_transition_lyrics(
    *,
    run: Mapping[str, Any],
    base_run_artifact_id: str,
    run_sha256: str,
    source_srt_sha256: str,
    lexical: Mapping[str, Any],
    positional: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an official review-template-shaped decision file plus audit report."""
    _validate_common(
        run=run,
        base_run_artifact_id=base_run_artifact_id,
        run_sha256=run_sha256,
        source_srt_sha256=source_srt_sha256,
        lexical=lexical,
        positional=positional,
    )
    template = build_review_template(dict(run), base_run_artifact_id=base_run_artifact_id)
    lexical_rows = lexical.get("transitions")
    positional_rows = positional.get("evidence")
    transitions = run.get("transitions")
    if not isinstance(lexical_rows, list) or not isinstance(positional_rows, list) or not isinstance(transitions, list):
        raise TransitionLyricResolutionError("transition evidence/run collections are invalid")
    if len(lexical_rows) != len(transitions) or len(positional_rows) != len(transitions):
        raise TransitionLyricResolutionError("transition evidence does not cover the exact base population")

    lexical_by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in lexical_rows:
        if not isinstance(row, Mapping):
            raise TransitionLyricResolutionError("lexical transition row is invalid")
        pair = _pair(row)
        if not all(pair) or pair in lexical_by_pair:
            raise TransitionLyricResolutionError("lexical transition pair is missing/duplicate")
        lexical_by_pair[pair] = row

    positional_by_index: dict[int, Mapping[str, Any]] = {}
    for row in positional_rows:
        if not isinstance(row, Mapping):
            raise TransitionLyricResolutionError("positional transition row is invalid")
        raw_index = row.get("transition_index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 1 or raw_index > len(transitions):
            raise TransitionLyricResolutionError("positional transition index is invalid")
        if raw_index in positional_by_index:
            raise TransitionLyricResolutionError("positional transition index is duplicate")
        base_transition = transitions[raw_index - 1]
        if float(row.get("nominal_boundary")) != float(base_transition.get("nominal_boundary")):
            raise TransitionLyricResolutionError("positional transition boundary mismatch")
        if row.get("left_occurrence_id") not in (None, "", base_transition.get("left_occurrence_id")):
            raise TransitionLyricResolutionError("positional left occurrence mismatch")
        if row.get("right_occurrence_id") not in (None, "", base_transition.get("right_occurrence_id")):
            raise TransitionLyricResolutionError("positional right occurrence mismatch")
        positional_by_index[raw_index] = row
    if set(positional_by_index) != set(range(1, len(transitions) + 1)):
        raise TransitionLyricResolutionError("positional evidence population is incomplete")

    transition_index_by_pair = {
        (_text(row.get("left_occurrence_id")), _text(row.get("right_occurrence_id"))): index
        for index, row in enumerate(transitions, start=1)
        if isinstance(row, Mapping)
    }
    if len(transition_index_by_pair) != len(transitions):
        raise TransitionLyricResolutionError("base run transition pairs are invalid/duplicate")

    output = deepcopy(template)
    decisions: list[dict[str, Any]] = []
    for item in output["review_items"]:
        issue = item["issue"]
        if issue.get("kind") != "transition_ambiguity" or issue.get("code") != "ambiguous_source_occurrence":
            raise TransitionLyricResolutionError("unexpected non-occurrence review issue in transition-only run")
        pair = _pair(issue)
        lexical_row = lexical_by_pair.get(pair)
        index = transition_index_by_pair.get(pair)
        if lexical_row is None or index is None:
            raise TransitionLyricResolutionError("review issue has no exact lexical/transition pair")
        if _text(lexical_row.get("issue_candidate_id")) != _text(issue.get("candidate_id")):
            raise TransitionLyricResolutionError("lexical candidate identity mismatch")
        lexical_review_id = _text(lexical_row.get("review_issue_id"))
        if lexical_review_id and lexical_review_id != _text(item.get("issue_id")):
            raise TransitionLyricResolutionError("lexical review issue identity mismatch")
        positional_row = positional_by_index[index]
        positional_action = _text((positional_row.get("recommendation") or {}).get("action"))
        clear, reason = _lexical_clear(lexical_row)
        if clear:
            crossfade_note = (
                " positional source evidence also sees same-window dual-source support; "
                "this resolves lyric occurrence/order only and does not claim absence of instrumental crossfade."
                if positional_action == "overlap_candidate_advisory"
                else ""
            )
            item["decision"] = {
                "action": "resolved_clear",
                "rationale": (
                    "Unique final-mix editor lexical witnesses identify both adjacent lyric occurrences "
                    "and show non-overlapping left-to-right lyric order; the original ambiguous_source_occurrence "
                    "review block is therefore resolved." + crossfade_note
                ),
            }
            resolution = "resolved_clear"
        else:
            item["decision"] = None
            resolution = "unresolved"
        decisions.append(
            {
                "transition_index": index,
                "issue_id": item["issue_id"],
                "candidate_id": _text(issue.get("candidate_id")),
                "left_occurrence_id": pair[0],
                "right_occurrence_id": pair[1],
                "nominal_boundary": float(transitions[index - 1]["nominal_boundary"]),
                "resolution": resolution,
                "lexical_reason": reason,
                "positional_context": positional_action or "missing",
                "source_audio_overlap_possible": positional_action == "overlap_candidate_advisory",
                "timing_mutation_performed": False,
            }
        )

    resolved_count = sum(row["resolution"] == "resolved_clear" for row in decisions)
    report = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "task_fingerprint_sha256": _text(run.get("task_fingerprint_sha256")),
        "base_run_artifact_id": base_run_artifact_id,
        "run_sha256": run_sha256,
        "source_srt_sha256": source_srt_sha256,
        "transition_count": len(decisions),
        "resolved_clear_count": resolved_count,
        "remaining_unresolved_count": len(decisions) - resolved_count,
        "decisions": decisions,
        "authority": "review_decision_authorization_only",
        "confirmed_overlap_authority_granted": False,
        "timing_mutation_performed": False,
        "text_mutation_performed": False,
    }
    return output, report


__all__ = (
    "POLICY_ID",
    "SCHEMA_VERSION",
    "TransitionLyricResolutionError",
    "authorize_transition_lyrics",
)
