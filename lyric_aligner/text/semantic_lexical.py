"""Bounded LLM/foundation-model review protocol for lexical-only subtitle recovery.

The model never receives timing mutation authority. It may only review or partition a
finite canonical span already proposed by deterministic monotonic text alignment. The
consumer must re-check all ownership and timeline invariants before materialization.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Mapping, Sequence

from lyric_aligner.text_repair import (
    CanonicalLine,
    MatchDecision,
    SubtitleCue,
    _content_characters,
    _render_preserving_layout,
    parse_srt_text,
    render_repaired_srt,
    timeline_signature,
    _normalize_for_match,
)

REQUEST_SCHEMA = "lexical-semantic-request-bundle-1.0"
RESPONSE_SCHEMA = "lexical-semantic-response-bundle-1.0"
POLICY_ID = "bounded-canonical-span-semantic-review-1.0"


def _stable_sha(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decision_groups(
    decisions: Sequence[MatchDecision],
) -> list[list[MatchDecision]]:
    grouped: dict[tuple[tuple[int, int], tuple[int, int]], list[MatchDecision]] = defaultdict(list)
    for item in decisions:
        if item.action != "review" or item.cue_span is None or item.canonical_span is None:
            continue
        if item.cue_span[0] >= item.cue_span[1] or item.canonical_span[0] >= item.canonical_span[1]:
            continue
        grouped[(item.cue_span, item.canonical_span)].append(item)
    return [
        sorted(group, key=lambda item: item.cue_ordinal)
        for _, group in sorted(grouped.items(), key=lambda entry: entry[0])
    ]


def build_semantic_request_bundle(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[CanonicalLine],
    decisions: Sequence[MatchDecision],
    *,
    context_lines: int = 2,
) -> dict[str, Any]:
    """Build finite semantic-review requests from already-bounded review spans.

    The request does not ask a model to search the whole lyric or invent an
    occurrence. The deterministic aligner has already proposed one bounded canonical
    span; the model may accept/partition it or abstain.
    """
    if not 0 <= context_lines <= 4:
        raise ValueError("context_lines must be between 0 and 4")
    requests: list[dict[str, Any]] = []
    for group in _decision_groups(decisions):
        cue_start, cue_end = group[0].cue_span or (0, 0)
        canonical_start, canonical_end = group[0].canonical_span or (0, 0)
        if any(item.cue_span != group[0].cue_span or item.canonical_span != group[0].canonical_span for item in group):
            raise ValueError("semantic review group contains inconsistent spans")
        if [item.cue_ordinal for item in group] != list(range(cue_start, cue_end)):
            continue
        line_group = list(canonical[canonical_start:canonical_end])
        if not line_group:
            continue
        if len({line.source_ordinal for line in line_group}) != 1:
            continue
        cue_group = list(cues[cue_start:cue_end])
        lexical_stream = "".join(
            char for line in line_group for char in _content_characters(line.text)
        )
        if not lexical_stream or len(cue_group) > 8 or len(line_group) > 8:
            continue
        identity = {
            "cue_ordinals": [cue.ordinal for cue in cue_group],
            "cue_text_sha256": [
                hashlib.sha256(cue.text.encode("utf-8")).hexdigest() for cue in cue_group
            ],
            "canonical_ordinals": [line.ordinal for line in line_group],
            "canonical_text_sha256": [
                hashlib.sha256(line.text.encode("utf-8")).hexdigest() for line in line_group
            ],
        }
        request_id = "lex-" + _stable_sha(identity)[:20]
        left_start = max(0, canonical_start - context_lines)
        right_end = min(len(canonical), canonical_end + context_lines)
        request = {
            "request_id": request_id,
            "cue_ordinals": identity["cue_ordinals"],
            "cue_texts": [cue.text for cue in cue_group],
            "proposed_canonical_span": [canonical_start, canonical_end],
            "proposed_canonical_lines": [line.text for line in line_group],
            "canonical_lexical_stream": lexical_stream,
            "left_canonical_context": [line.text for line in canonical[left_start:canonical_start]],
            "right_canonical_context": [line.text for line in canonical[canonical_end:right_end]],
            "instructions": {
                "task": (
                    "Decide whether the editor cues plausibly express this already-bounded canonical span. "
                    "If yes, partition the exact canonical_lexical_stream monotonically across the existing cues."
                ),
                "allowed_verdicts": ["accept_partition", "abstain"],
                "hard_constraints": [
                    "do_not_change_or_invent_lyric_characters",
                    "do_not_change_cue_count",
                    "do_not_propose_timestamps",
                    "partition_must_cover_the_exact_stream_once_without_gaps_or_overlap",
                    "abstain_when_semantic_or_occurrence_identity_is_uncertain",
                ],
            },
        }
        request["request_sha256"] = _stable_sha(request)
        requests.append(request)
    bundle: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA,
        "policy_id": POLICY_ID,
        "purpose": "semantic_shadow_or_review_for_lexical_floor_never_timing_authority",
        "requests": requests,
    }
    bundle["bundle_sha256"] = _stable_sha(bundle)
    return bundle


def validate_semantic_response(
    request_bundle: Mapping[str, Any],
    response_bundle: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate a model response without granting production authority."""
    if request_bundle.get("schema_version") != REQUEST_SCHEMA or request_bundle.get("policy_id") != POLICY_ID:
        raise ValueError("semantic request bundle identity mismatch")
    expected_bundle_sha = request_bundle.get("bundle_sha256")
    bare_request_bundle = dict(request_bundle)
    bare_request_bundle.pop("bundle_sha256", None)
    if expected_bundle_sha != _stable_sha(bare_request_bundle):
        raise ValueError("semantic request bundle hash mismatch")
    if response_bundle.get("schema_version") != RESPONSE_SCHEMA:
        raise ValueError("semantic response schema mismatch")
    if response_bundle.get("policy_id") != POLICY_ID:
        raise ValueError("semantic response policy mismatch")
    if response_bundle.get("request_bundle_sha256") != expected_bundle_sha:
        raise ValueError("semantic response is bound to a different request bundle")
    model_identity = response_bundle.get("model_identity")
    if not isinstance(model_identity, Mapping):
        raise ValueError("semantic response must include model_identity")
    for field in ("provider", "model_id", "prompt_policy_id"):
        if not isinstance(model_identity.get(field), str) or not str(model_identity[field]).strip():
            raise ValueError(f"semantic model identity missing {field}")
    raw_decisions = response_bundle.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("semantic response decisions must be a list")
    requests: dict[str, Mapping[str, Any]] = {}
    for item in request_bundle.get("requests", []):
        if not isinstance(item, Mapping):
            raise ValueError("semantic request bundle contains a non-object request")
        request_id = item.get("request_id")
        if not isinstance(request_id, str) or not request_id or request_id in requests:
            raise ValueError("semantic request bundle contains invalid or duplicate request_id")
        expected_request_sha = item.get("request_sha256")
        bare_request = dict(item)
        bare_request.pop("request_sha256", None)
        if expected_request_sha != _stable_sha(bare_request):
            raise ValueError("semantic request hash mismatch")
        requests[request_id] = item
    validated: dict[str, dict[str, Any]] = {}
    for raw in raw_decisions:
        if not isinstance(raw, Mapping):
            raise ValueError("semantic decision must be an object")
        request_id = raw.get("request_id")
        if request_id not in requests or request_id in validated:
            raise ValueError("semantic response contains unknown or duplicate request_id")
        request = requests[str(request_id)]
        if raw.get("request_sha256") != request.get("request_sha256"):
            raise ValueError("semantic decision request hash mismatch")
        verdict = raw.get("verdict")
        if verdict not in {"accept_partition", "abstain"}:
            raise ValueError("semantic decision has invalid verdict")
        decision: dict[str, Any] = {
            "request_id": request_id,
            "verdict": verdict,
            "request_sha256": raw.get("request_sha256"),
        }
        if verdict == "accept_partition":
            spans = raw.get("cue_content_spans")
            cue_count = len(request["cue_ordinals"])
            stream_len = len(str(request["canonical_lexical_stream"]))
            if not isinstance(spans, list) or len(spans) != cue_count:
                raise ValueError("semantic partition count must equal cue count")
            parsed: list[list[int]] = []
            cursor = 0
            for span in spans:
                if (
                    not isinstance(span, list)
                    or len(span) != 2
                    or any(isinstance(value, bool) or not isinstance(value, int) for value in span)
                ):
                    raise ValueError("semantic partition spans must be integer [start,end] pairs")
                start, end = span
                if start != cursor or end <= start or end > stream_len:
                    raise ValueError("semantic partition must be contiguous, nonempty and in range")
                parsed.append([start, end])
                cursor = end
            if cursor != stream_len:
                raise ValueError("semantic partition must cover the complete canonical stream")
            decision["cue_content_spans"] = parsed
        validated[str(request_id)] = decision
    return validated


def semantic_partition_targets(
    request: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> list[str]:
    """Return exact canonical lexical targets after a validated accept decision."""
    if decision.get("verdict") != "accept_partition":
        raise ValueError("semantic decision is not an accepted partition")
    stream = str(request["canonical_lexical_stream"])
    targets = [stream[start:end] for start, end in decision["cue_content_spans"]]
    if _normalize_for_match("".join(targets)) != _normalize_for_match(stream):
        raise ValueError("semantic targets changed canonical lexical content")
    return targets


def _render_semantic_content(original: str, target: str) -> str:
    """Replace cue content while retaining only its outer layout characters."""
    prefix = original[: len(original) - len(original.lstrip())]
    suffix = original[len(original.rstrip()) :]
    return prefix + target + suffix


def materialize_semantic_shadow_candidate(
    source_text: str,
    cues: Sequence[SubtitleCue],
    base_replacements: Mapping[int, str],
    request_bundle: Mapping[str, Any],
    response_bundle: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Apply validated semantic partitions to a separate non-production candidate.

    The candidate preserves the source cue count/number/timestamps byte-for-byte at
    the parsed timing-signature level. It is always publish_ready=false until a
    separately calibrated promotion policy exists.
    """
    parts, parsed_cues = parse_srt_text(source_text)
    if timeline_signature(parsed_cues) != timeline_signature(cues):
        raise ValueError("semantic candidate source cues do not match the bound source")
    validated = validate_semantic_response(request_bundle, response_bundle)
    requests = {str(item["request_id"]): item for item in request_bundle.get("requests", [])}
    replacements = dict(base_replacements)
    accepted_request_ids: list[str] = []
    affected_cues: set[int] = set()
    for request_id, decision in validated.items():
        if decision.get("verdict") != "accept_partition":
            continue
        request = requests[request_id]
        cue_ordinals = list(request["cue_ordinals"])
        targets = semantic_partition_targets(request, decision)
        if len(cue_ordinals) != len(targets):
            raise ValueError("semantic target count differs from cue count")
        rendered_targets: list[str] = []
        for cue_ordinal, target in zip(cue_ordinals, targets):
            if cue_ordinal in affected_cues:
                raise ValueError("semantic requests overlap the same cue")
            if not 0 <= cue_ordinal < len(cues):
                raise ValueError("semantic request cue ordinal is out of range")
            # Semantic partitions are exact canonical lexical streams.  Do not
            # preserve source-internal spacing here: it can split a recovered
            # phrase (for example, ``소녀시대``) while leaving the cue layout and
            # timeline unchanged.
            output_text = _render_semantic_content(
                cues[cue_ordinal].text,
                "".join(_content_characters(target)),
            )
            if _normalize_for_match(output_text) != _normalize_for_match(target):
                raise ValueError("semantic materialization changed canonical lexical target")
            replacements[cue_ordinal] = output_text
            rendered_targets.append(output_text)
            affected_cues.add(cue_ordinal)
        if _normalize_for_match("".join(rendered_targets)) != _normalize_for_match(
            str(request["canonical_lexical_stream"])
        ):
            raise ValueError("semantic materialization does not cover exact canonical stream")
        accepted_request_ids.append(request_id)

    rendered = render_repaired_srt(parts, cues, replacements)
    _, output_cues = parse_srt_text(rendered)
    if len(output_cues) != len(cues) or timeline_signature(output_cues) != timeline_signature(cues):
        raise AssertionError("semantic lexical candidate changed SRT cue count, numbering or timing")
    report = {
        "schema_version": "lexical-semantic-shadow-materialization-1.0",
        "policy_id": POLICY_ID,
        "publish_ready": False,
        "timing_authority_used": False,
        "model_identity": dict(response_bundle["model_identity"]),
        "request_bundle_sha256": request_bundle["bundle_sha256"],
        "accepted_request_count": len(accepted_request_ids),
        "affected_cue_count": len(affected_cues),
        "accepted_request_ids": accepted_request_ids,
        "timeline_unchanged": True,
        "meaning": (
            "semantic model suggestions are materialized only as a shadow lexical candidate; "
            "this artifact cannot pass production/release without separate frozen evaluation and promotion"
        ),
    }
    return rendered, report
