"""Validate source-clock tracks that earned release authority on frozen final-mix holdout.

Production source-clock transforms are not automatically semantic release truth.
This module grants a stronger QA authority only when the exact transform was
promoted by the frozen V2 final-mix protocol and the current timeline proves it
was rendered with the exact same map.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from lyric_aligner.timeline.source_clock import (
    SOURCE_CLOCK_MAP_SCHEMA_VERSION,
    SOURCE_CLOCK_POLICY_ID,
    SourceClockError,
    source_clock_from_mapping,
    source_clock_map_rows,
)

SOURCE_CLOCK_PROMOTION_SCHEMA_VERSION = "source-clock-final-mix-promotion-v2-analysis-1.0"
SOURCE_CLOCK_PROMOTION_SELECTION_SCHEMA_VERSION = "source-clock-final-mix-promotion-v2-selection-lock-1.0"
SOURCE_CLOCK_PROMOTION_PROTOCOL_SCHEMA_VERSION = "source-clock-final-mix-promotion-protocol-2.0"
SOURCE_CLOCK_SEMANTIC_AUTHORITY_POLICY_ID = "verified-source-clock-final-mix-holdout-1.1"


class SourceClockAuthorityError(ValueError):
    """Raised when source-clock release authority cannot be replayed exactly."""


def _sha(value: object, label: str) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise SourceClockAuthorityError(f"{label} must be lowercase SHA-256")
    return digest


def _same_number(left: object, right: object) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _number(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SourceClockAuthorityError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise SourceClockAuthorityError(f"{label} must be finite")
    return result


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SourceClockAuthorityError(f"{label} must be an integer")
    return value


def _p90(values: Sequence[float]) -> float:
    """The producer's linear 90th percentile convention."""

    if not values:
        raise SourceClockAuthorityError("cannot calculate p90 from no values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = 0.9 * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _metrics(errors: Sequence[int]) -> dict[str, float | int]:
    absolute = [abs(value) for value in errors]
    if not absolute:
        raise SourceClockAuthorityError("cannot calculate metrics from no valid holdout anchors")
    return {
        "count": len(absolute),
        "median_abs_error_ms": float(statistics.median(absolute)),
        "p90_abs_error_ms": _p90(absolute),
        "worst_abs_error_ms": float(max(absolute)),
    }


def _require_metrics(
    value: object,
    expected: Mapping[str, float | int],
    *,
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        raise SourceClockAuthorityError(f"{label} must be an object")
    if _integer(value.get("count"), f"{label}.count") != expected["count"]:
        raise SourceClockAuthorityError(f"{label} count does not replay from ledger")
    for key in ("median_abs_error_ms", "p90_abs_error_ms", "worst_abs_error_ms"):
        if not _same_number(value.get(key), expected[key]):
            raise SourceClockAuthorityError(f"{label} {key} does not replay from ledger")


def _indexed_rows(value: object, *, label: str) -> dict[int, Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SourceClockAuthorityError(f"{label} must be a list")
    result: dict[int, Mapping[str, Any]] = {}
    for position, row in enumerate(value, start=1):
        if not isinstance(row, Mapping):
            raise SourceClockAuthorityError(f"{label} row {position} must be an object")
        ordinal = _integer(row.get("ordinal"), f"{label} row {position}.ordinal")
        if ordinal < 1 or ordinal in result:
            raise SourceClockAuthorityError(f"{label} has invalid or duplicate ordinal")
        result[ordinal] = row
    return result


def _require_same_track_identity(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    ordinal: int,
    left_label: str,
    right_label: str,
) -> None:
    for key in (
        "occurrence_id",
        "track_id",
        "source_audio_sha256",
        "canonical_lyric_sha256",
        "canonical_selection_sha256",
    ):
        if str(left.get(key) or "") != str(right.get(key) or ""):
            raise SourceClockAuthorityError(
                f"source clock authority ordinal {ordinal} {key} differs between {left_label} and {right_label}"
            )


def _require_same_transform(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    ordinal: int,
    left_label: str,
    right_label: str,
    compare_provenance: bool,
) -> None:
    if str(left.get("policy_id") or "") != str(right.get("policy_id") or ""):
        raise SourceClockAuthorityError(
            f"source clock authority ordinal {ordinal} policy differs between {left_label} and {right_label}"
        )
    for key in ("rate", "offset_ms"):
        if not _same_number(left.get(key), right.get(key)):
            raise SourceClockAuthorityError(
                f"source clock authority ordinal {ordinal} {key} differs between {left_label} and {right_label}"
            )
    if compare_provenance and str(left.get("provenance_sha256") or "") != str(right.get("provenance_sha256") or ""):
        raise SourceClockAuthorityError(
            f"source clock authority ordinal {ordinal} provenance differs between {left_label} and {right_label}"
        )


def _replay_human_pass(
    row: Mapping[str, Any],
    *,
    maximum_new_abs_error_ms: float,
    must_not_regress: bool,
) -> bool:
    human = row.get("human_check")
    if human is None:
        return True
    if not isinstance(human, Mapping):
        raise SourceClockAuthorityError("source clock promotion human_check must be null or an object")
    human_start = _number(human.get("human_start_ms"), "human_check.human_start_ms")
    old_start = _number(human.get("old_projection_start_ms"), "human_check.old_projection_start_ms")
    new_start = _number(human.get("new_projection_start_ms"), "human_check.new_projection_start_ms")
    old_abs = abs(old_start - human_start)
    new_abs = abs(new_start - human_start)
    if not _same_number(human.get("old_abs_error_ms"), old_abs):
        raise SourceClockAuthorityError("human_check old_abs_error_ms does not replay")
    if not _same_number(human.get("new_abs_error_ms"), new_abs):
        raise SourceClockAuthorityError("human_check new_abs_error_ms does not replay")
    return new_abs <= maximum_new_abs_error_ms and (not must_not_regress or new_abs <= old_abs)


def _replay_track_promotion(
    *,
    ordinal: int,
    analysis_row: Mapping[str, Any],
    selection_row: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> tuple[list[int], int]:
    """Recompute the producer's ledger summary and promotion predicates.

    The frozen analysis does not retain raw ASR shard observations.  ``valid`` is
    therefore a bounded ledger fact, while all metrics, risk and human verdicts
    are recomputed from the ledger and frozen protocol below.
    """

    if selection_row.get("testable") is not True:
        expected_testable = False
    else:
        expected_testable = True
    if analysis_row.get("testable") is not expected_testable:
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} analysis testable flag differs from frozen selection")
    if not expected_testable:
        if _integer(analysis_row.get("valid_final_mix_anchor_count"), "analysis valid_final_mix_anchor_count") != 0:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} untestable track has holdout anchors")
        if any(analysis_row.get(key) is not None for key in ("old_metrics", "new_metrics", "old_risk_score", "new_risk_score", "risk_improvement_ms")):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} untestable track has holdout summary")
        if analysis_row.get("metric_pass") not in (False, None) or analysis_row.get("risk_pass") not in (False, None):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} untestable track has promotion pass")
        human_policy = protocol["human_check_if_present"]
        human_pass = _replay_human_pass(
            analysis_row,
            maximum_new_abs_error_ms=_number(human_policy.get("maximum_new_abs_error_ms"), "protocol maximum human error"),
            must_not_regress=human_policy.get("must_not_regress_vs_old_projection") is True,
        )
        if analysis_row.get("human_pass") not in (None, human_pass) or analysis_row.get("promoted") not in (False, None):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} untestable track promotion does not replay")
        return [], 0
    probes = selection_row.get("probes")
    if not isinstance(probes, Sequence) or isinstance(probes, (str, bytes)):
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} selection probes must be a list")
    if _integer(selection_row.get("selected_probe_count"), "selection selected_probe_count") != len(probes):
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} selection probe count mismatch")
    probe_by_line: dict[int, Mapping[str, Any]] = {}
    for probe in probes:
        if not isinstance(probe, Mapping):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} selection probe must be an object")
        line = _integer(probe.get("canonical_line_index"), "selection probe canonical_line_index")
        if line < 0 or line in probe_by_line:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} selection probes duplicate canonical line")
        probe_by_line[line] = probe

    ledger = analysis_row.get("ledger")
    if not isinstance(ledger, Sequence) or isinstance(ledger, (str, bytes)):
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} lacks holdout ledger")
    if len(ledger) != len(probe_by_line):
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} holdout ledger/probe count mismatch")
    valid_new_errors: list[int] = []
    valid_old_errors: list[int] = []
    seen_lines: set[int] = set()
    anchor_policy = protocol["valid_final_mix_anchor"]
    for item in ledger:
        if not isinstance(item, Mapping):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} holdout ledger row must be an object")
        line = _integer(item.get("canonical_line_index"), "holdout ledger canonical_line_index")
        probe = probe_by_line.get(line)
        if probe is None or line in seen_lines:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} holdout ledger does not bind selection probe")
        seen_lines.add(line)
        for key in ("old_projection_start_ms", "new_projection_start_ms"):
            if not _same_number(item.get(key), probe.get(key)):
                raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} holdout ledger {key} differs from selection")
        valid = item.get("valid")
        if not isinstance(valid, bool):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} holdout ledger valid must be boolean")
        if not valid:
            continue
        support = _number(item.get("support"), "holdout ledger support")
        probability = _number(item.get("mean_word_probability"), "holdout ledger mean_word_probability")
        if item.get("ambiguous") is not False or support < _number(anchor_policy.get("minimum_canonical_match_support_score"), "protocol minimum support") or probability < _number(anchor_policy.get("minimum_mean_word_probability"), "protocol minimum word probability"):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} valid ledger row violates frozen anchor policy")
        asr_start = _integer(item.get("asr_start_ms"), "holdout ledger asr_start_ms")
        old_start = _integer(item.get("old_projection_start_ms"), "holdout ledger old_projection_start_ms")
        new_start = _integer(item.get("new_projection_start_ms"), "holdout ledger new_projection_start_ms")
        old_error = old_start - asr_start
        new_error = new_start - asr_start
        if _integer(item.get("old_signed_error_ms"), "holdout ledger old_signed_error_ms") != old_error:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} old signed error does not replay")
        if _integer(item.get("new_signed_error_ms"), "holdout ledger new_signed_error_ms") != new_error:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} new signed error does not replay")
        valid_old_errors.append(old_error)
        valid_new_errors.append(new_error)
    if seen_lines != set(probe_by_line):
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} holdout ledger misses selection probe")

    if not valid_new_errors:
        if analysis_row.get("old_metrics") is not None or analysis_row.get("new_metrics") is not None:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} records metrics without valid holdout anchors")
        new_metrics = old_metrics = None
    else:
        old_metrics = _metrics(valid_old_errors)
        new_metrics = _metrics(valid_new_errors)
        _require_metrics(analysis_row.get("old_metrics"), old_metrics, label="old_metrics")
        _require_metrics(analysis_row.get("new_metrics"), new_metrics, label="new_metrics")
    anchor_count = len(valid_new_errors)
    if _integer(analysis_row.get("valid_final_mix_anchor_count"), "analysis valid_final_mix_anchor_count") != anchor_count:
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} holdout ledger/count mismatch")

    thresholds = protocol["new_projection_thresholds_ms"]
    minimum_anchors = _integer(protocol.get("minimum_valid_final_mix_anchors"), "protocol minimum_valid_final_mix_anchors")
    if minimum_anchors < 1:
        raise SourceClockAuthorityError("protocol minimum_valid_final_mix_anchors must be positive")
    metric_pass = bool(
        new_metrics is not None
        and anchor_count >= minimum_anchors
        and new_metrics["median_abs_error_ms"] <= _number(thresholds.get("maximum_median_abs_error"), "protocol maximum median error")
        and new_metrics["p90_abs_error_ms"] <= _number(thresholds.get("maximum_p90_abs_error"), "protocol maximum p90 error")
        and new_metrics["worst_abs_error_ms"] <= _number(thresholds.get("maximum_worst_abs_error"), "protocol maximum worst error")
    )
    if analysis_row.get("metric_pass") is not metric_pass:
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} metric_pass does not replay")

    if metric_pass:
        assert old_metrics is not None and new_metrics is not None
        old_risk = old_metrics["median_abs_error_ms"] + 0.5 * old_metrics["p90_abs_error_ms"] + 0.25 * old_metrics["worst_abs_error_ms"]
        new_risk = new_metrics["median_abs_error_ms"] + 0.5 * new_metrics["p90_abs_error_ms"] + 0.25 * new_metrics["worst_abs_error_ms"]
        risk_improvement = old_risk - new_risk
        _number(analysis_row.get("old_risk_score"), "analysis old_risk_score")
        _number(analysis_row.get("new_risk_score"), "analysis new_risk_score")
        _number(analysis_row.get("risk_improvement_ms"), "analysis risk_improvement_ms")
        if not _same_number(analysis_row.get("old_risk_score"), old_risk) or not _same_number(analysis_row.get("new_risk_score"), new_risk) or not _same_number(analysis_row.get("risk_improvement_ms"), risk_improvement):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} risk summary does not replay")
        risk_pass = risk_improvement >= _number(protocol.get("minimum_risk_score_improvement_ms"), "protocol minimum risk improvement")
    else:
        if any(analysis_row.get(key) is not None for key in ("old_risk_score", "new_risk_score", "risk_improvement_ms")):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} records risk summary without metric pass")
        risk_pass = False
    if analysis_row.get("risk_pass") is not risk_pass:
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} risk_pass does not replay")

    human_policy = protocol["human_check_if_present"]
    if human_policy.get("must_not_regress_vs_old_projection") is not True or human_policy.get("human_result_never_changes_rate_or_offset") is not True:
        raise SourceClockAuthorityError("source clock promotion protocol weakens required human check")
    human_pass = _replay_human_pass(
        analysis_row,
        maximum_new_abs_error_ms=_number(human_policy.get("maximum_new_abs_error_ms"), "protocol maximum human error"),
        must_not_regress=True,
    )
    if analysis_row.get("human_pass") is not human_pass:
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} human_pass does not replay")
    promoted = expected_testable and metric_pass and risk_pass and human_pass
    if analysis_row.get("promoted") is not promoted:
        raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} promoted does not replay")
    return valid_new_errors, anchor_count


def _legacy_overlap_source_clock_replay(
    timeline: Mapping[str, Any],
    map_clock: Mapping[str, Any],
) -> bool:
    """Prove an a20 overlap timeline inherited the exact source-clock transform.

    Early a20 overlap recomposition omitted only the top-level map SHA while
    copying the primary canonical result.  Accept that legacy shape only when
    the field is absent (never wrong), the producer identity is the known
    overlap extension, and every canonical onset numerically replays through
    the exact promoted transform into the recorded source onset.
    """

    if str(timeline.get("mapping_source") or "") != "confirmed_overlap_primary_timeline_extension":
        return False
    result = timeline.get("result")
    if not isinstance(result, Mapping):
        return False
    result_clock = result.get("source_clock")
    if not isinstance(result_clock, Mapping):
        return False
    if (
        str(result_clock.get("policy_id") or "") != str(map_clock.get("policy_id") or "")
        or not _same_number(result_clock.get("rate"), map_clock.get("rate"))
        or not _same_number(result_clock.get("offset_ms"), map_clock.get("offset_ms"))
        or str(result_clock.get("provenance_sha256") or "")
        != str(map_clock.get("provenance_sha256") or "")
    ):
        return False
    lines = result.get("lines")
    if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)) or not lines:
        return False
    try:
        transform = source_clock_from_mapping(map_clock)
    except SourceClockError:
        return False
    if transform is None:
        return False
    checked = 0
    for line in lines:
        if not isinstance(line, Mapping):
            return False
        canonical_start = line.get("canonical_clock_start_ms")
        canonical_end = line.get("canonical_clock_end_ms")
        source_start = line.get("source_start_ms")
        source_end = line.get("source_end_ms")
        if (
            not isinstance(canonical_start, int)
            or isinstance(canonical_start, bool)
            or not isinstance(source_start, int)
            or isinstance(source_start, bool)
        ):
            return False
        if (canonical_end is None) != (source_end is None):
            return False
        if canonical_end is not None and (
            not isinstance(canonical_end, int)
            or isinstance(canonical_end, bool)
            or not isinstance(source_end, int)
            or isinstance(source_end, bool)
        ):
            return False
        try:
            expected_start = transform.map_ms(canonical_start)
            expected_end = (
                None if canonical_end is None else transform.map_ms(canonical_end)
            )
        except SourceClockError:
            return False
        if source_start != expected_start or source_end != expected_end:
            return False
        checked += 1
    return checked == len(lines)


def build_verified_source_clock_authority(
    *,
    source_clock_map: Mapping[str, Any],
    promotion_analysis: Mapping[str, Any],
    promotion_selection: Mapping[str, Any],
    promotion_protocol: Mapping[str, Any],
    run: Mapping[str, Any],
    timelines_by_ordinal: Mapping[int, Mapping[str, Any]],
    source_clock_map_sha256: str,
    promotion_analysis_sha256: str,
    promotion_selection_sha256: str,
    promotion_protocol_sha256: str,
) -> dict[int, dict[str, Any]]:
    """Replay frozen V2 promotion into exact-track semantic QA authority."""

    map_sha = _sha(source_clock_map_sha256, "source_clock_map_sha256")
    analysis_sha = _sha(promotion_analysis_sha256, "promotion_analysis_sha256")
    selection_sha = _sha(promotion_selection_sha256, "promotion_selection_sha256")
    protocol_sha = _sha(promotion_protocol_sha256, "promotion_protocol_sha256")
    try:
        map_rows = source_clock_map_rows(source_clock_map)
    except SourceClockError as exc:
        raise SourceClockAuthorityError(str(exc)) from exc

    fingerprint = str(source_clock_map.get("task_fingerprint_sha256") or "")
    algorithm = str(source_clock_map.get("algorithm_version") or "")
    if not fingerprint or not algorithm:
        raise SourceClockAuthorityError("source clock map lacks task/algorithm binding")
    if run.get("task_fingerprint_sha256") != fingerprint:
        raise SourceClockAuthorityError("source clock map/run task fingerprint mismatch")
    if str(run.get("algorithm_version") or "") != algorithm:
        raise SourceClockAuthorityError("source clock map/run algorithm mismatch")
    if str(source_clock_map.get("schema_version") or "") != SOURCE_CLOCK_MAP_SCHEMA_VERSION:
        raise SourceClockAuthorityError("unexpected source clock map schema")
    if str(source_clock_map.get("policy_id") or "") != SOURCE_CLOCK_POLICY_ID:
        raise SourceClockAuthorityError("unexpected source clock policy")

    if source_clock_map.get("final_mix_promotion_analysis_sha256") != analysis_sha:
        raise SourceClockAuthorityError("source clock map/promotion analysis SHA mismatch")
    if _sha(source_clock_map.get("final_mix_promotion_selection_sha256"), "promotion selection SHA") != selection_sha:
        raise SourceClockAuthorityError("source clock map/promotion selection SHA mismatch")
    if _sha(source_clock_map.get("final_mix_promotion_protocol_sha256"), "promotion protocol SHA") != protocol_sha:
        raise SourceClockAuthorityError("source clock map/promotion protocol SHA mismatch")

    if str(promotion_analysis.get("schema_version") or "") != SOURCE_CLOCK_PROMOTION_SCHEMA_VERSION:
        raise SourceClockAuthorityError("unexpected source clock promotion analysis schema")
    if promotion_analysis.get("purpose") != "fresh unseen final-mix holdout verdict for source-clock production promotion":
        raise SourceClockAuthorityError("source clock promotion purpose is not release holdout")
    if promotion_analysis.get("human_used_for_clock_estimation") is not False:
        raise SourceClockAuthorityError("source clock promotion reused human result for clock estimation")
    if promotion_analysis.get("human_used_only_as_final_nonregression_check") is not True:
        raise SourceClockAuthorityError("source clock promotion lacks bounded human-use declaration")
    if promotion_analysis.get("selection_lock_sha256") != selection_sha:
        raise SourceClockAuthorityError("source clock promotion selection-lock mismatch")
    if promotion_analysis.get("promotion_protocol_sha256") != protocol_sha:
        raise SourceClockAuthorityError("source clock promotion protocol mismatch")

    if str(promotion_selection.get("schema_version") or "") != SOURCE_CLOCK_PROMOTION_SELECTION_SCHEMA_VERSION:
        raise SourceClockAuthorityError("unexpected source clock promotion selection schema")
    if promotion_selection.get("purpose") != "fresh final-mix holdout selection excluding previously observed canonical identities":
        raise SourceClockAuthorityError("source clock promotion selection purpose is invalid")
    if _sha(promotion_selection.get("promotion_protocol_sha256"), "selection promotion protocol SHA") != protocol_sha:
        raise SourceClockAuthorityError("source clock promotion selection/protocol SHA mismatch")
    if promotion_selection.get("final_mix_asr_result_read") is not False or promotion_selection.get("human_result_used_for_selection") is not False:
        raise SourceClockAuthorityError("source clock promotion selection is not independent")
    if str(promotion_protocol.get("schema_version") or "") != SOURCE_CLOCK_PROMOTION_PROTOCOL_SCHEMA_VERSION:
        raise SourceClockAuthorityError("unexpected source clock promotion protocol schema")
    if promotion_protocol.get("purpose") != "fresh final-mix holdout promotion for BPM-fixed source-clock candidates":
        raise SourceClockAuthorityError("source clock promotion protocol purpose is invalid")
    if promotion_protocol.get("risk_score") != "median_abs_error + 0.5*p90_abs_error + 0.25*worst_abs_error":
        raise SourceClockAuthorityError("source clock promotion protocol risk formula is unsupported")
    for key in ("valid_final_mix_anchor", "new_projection_thresholds_ms", "human_check_if_present"):
        if not isinstance(promotion_protocol.get(key), Mapping):
            raise SourceClockAuthorityError(f"source clock promotion protocol {key} must be an object")
    anchor_policy = promotion_protocol["valid_final_mix_anchor"]
    if anchor_policy.get("canonical_start_covered") is not True or anchor_policy.get("canonical_match_ambiguous") is not False:
        raise SourceClockAuthorityError("source clock promotion protocol weakens required anchor policy")

    promoted_raw = promotion_analysis.get("promoted_ordinals")
    if not isinstance(promoted_raw, Sequence) or isinstance(promoted_raw, (str, bytes)):
        raise SourceClockAuthorityError("promotion analysis has invalid promoted_ordinals")
    promoted = [int(value) for value in promoted_raw]
    if len(promoted) != len(set(promoted)):
        raise SourceClockAuthorityError("promotion analysis duplicates promoted ordinal")
    if int(promotion_analysis.get("promoted_count", -1)) != len(promoted):
        raise SourceClockAuthorityError("promotion analysis promoted_count mismatch")
    if set(promoted) != set(map_rows):
        raise SourceClockAuthorityError("source clock map tracks differ from promoted holdout set")

    selection_by_ordinal = _indexed_rows(promotion_selection.get("tracks"), label="promotion selection tracks")
    if _integer(promotion_selection.get("candidate_count"), "promotion selection candidate_count") != len(selection_by_ordinal):
        raise SourceClockAuthorityError("promotion selection candidate_count mismatch")
    analysis_by_ordinal = _indexed_rows(promotion_analysis.get("tracks"), label="promotion analysis tracks")
    if set(analysis_by_ordinal) != set(selection_by_ordinal):
        raise SourceClockAuthorityError("promotion analysis tracks differ from frozen selection")

    run_rows = run.get("occurrences")
    if not isinstance(run_rows, Sequence) or isinstance(run_rows, (str, bytes)):
        raise SourceClockAuthorityError("run occurrences must be a list")
    run_by_ordinal = {int(row["ordinal"]): row for row in run_rows if isinstance(row, Mapping)}

    replayed_by_ordinal: dict[int, tuple[list[int], int]] = {}
    for ordinal, analysis_row in analysis_by_ordinal.items():
        selection_row = selection_by_ordinal[ordinal]
        map_row = map_rows.get(ordinal)
        if map_row is not None:
            _require_same_track_identity(
                map_row,
                selection_row,
                ordinal=ordinal,
                left_label="source clock map",
                right_label="promotion selection",
            )
            selection_clock = selection_row.get("source_clock")
            if not isinstance(selection_clock, Mapping):
                raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} selection lacks transform")
            _require_same_transform(
                map_row,
                selection_clock,
                ordinal=ordinal,
                left_label="source clock map",
                right_label="promotion selection",
                compare_provenance=False,
            )
        analysis_clock = analysis_row.get("source_clock")
        selection_clock = selection_row.get("source_clock")
        if not isinstance(analysis_clock, Mapping) or not isinstance(selection_clock, Mapping):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} lacks frozen transform")
        _require_same_transform(
            analysis_clock,
            selection_clock,
            ordinal=ordinal,
            left_label="promotion analysis",
            right_label="promotion selection",
            compare_provenance=True,
        )
        if str(selection_clock.get("provenance_sha256") or "") != str(promotion_selection.get("expanded_candidate_sha256") or ""):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} selection transform provenance mismatch")
        replayed_by_ordinal[ordinal] = _replay_track_promotion(
            ordinal=ordinal,
            analysis_row=analysis_row,
            selection_row=selection_row,
            protocol=promotion_protocol,
        )

    authority: dict[int, dict[str, Any]] = {}
    for ordinal in promoted:
        map_row = map_rows[ordinal]
        analysis_row = analysis_by_ordinal.get(ordinal)
        run_row = run_by_ordinal.get(ordinal)
        timeline = timelines_by_ordinal.get(ordinal)
        if analysis_row is None or run_row is None or timeline is None:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} lacks replay input")
        if analysis_row.get("promoted") is not True:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} was not promoted")
        holdout_errors, anchor_count = replayed_by_ordinal[ordinal]
        if anchor_count < 3:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} has fewer than 3 holdout anchors")

        map_clock = map_row
        analysis_clock = analysis_row.get("source_clock")
        run_clock = run_row.get("source_clock")
        if not isinstance(analysis_clock, Mapping) or not isinstance(run_clock, Mapping):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} lacks transform replay")
        for clock in (analysis_clock, run_clock):
            if str(clock.get("policy_id") or "") != str(map_clock.get("policy_id") or ""):
                raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} policy mismatch")
            if not _same_number(clock.get("rate"), map_clock.get("rate")) or not _same_number(clock.get("offset_ms"), map_clock.get("offset_ms")):
                raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} transform mismatch")
        if str(run_clock.get("provenance_sha256") or "") != analysis_sha:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} run provenance mismatch")
        if str(map_clock.get("provenance_sha256") or "") != analysis_sha:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} map provenance mismatch")

        occurrence_id = str(map_row.get("occurrence_id") or "")
        track_id = str(map_row.get("track_id") or "")
        if str(run_row.get("occurrence_id") or "") != occurrence_id:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} occurrence mismatch")
        if timeline.get("task_fingerprint_sha256") != fingerprint or str(timeline.get("algorithm_version") or "") != algorithm:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} timeline lineage mismatch")
        timeline_map_sha = timeline.get("source_clock_map_sha256")
        if timeline_map_sha == map_sha:
            timeline_map_binding = "exact_sha"
        elif timeline_map_sha in (None, ""):
            if not _legacy_overlap_source_clock_replay(timeline, map_clock):
                raise SourceClockAuthorityError(
                    f"source clock authority ordinal {ordinal} legacy overlap clock replay failed"
                )
            timeline_map_binding = "legacy_overlap_numeric_replay"
        else:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} timeline map SHA mismatch")
        if str(timeline.get("occurrence_id") or "") != occurrence_id or str(timeline.get("track_id") or "") != track_id:
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} timeline identity mismatch")
        result = timeline.get("result")
        if not isinstance(result, Mapping) or str(result.get("canonical_selection_sha256") or "") != str(map_row.get("canonical_selection_sha256") or ""):
            raise SourceClockAuthorityError(f"source clock authority ordinal {ordinal} canonical selection mismatch")

        authority[ordinal] = {
            "policy_id": SOURCE_CLOCK_SEMANTIC_AUTHORITY_POLICY_ID,
            "ordinal": ordinal,
            "occurrence_id": occurrence_id,
            "track_id": track_id,
            "source_clock_map_sha256": map_sha,
            "timeline_map_binding": timeline_map_binding,
            "promotion_analysis_sha256": analysis_sha,
            "holdout_anchor_count": anchor_count,
            "holdout_signed_errors_ms": holdout_errors,
            "holdout_metrics": analysis_row.get("new_metrics"),
        }
    return authority
