"""Offline oracle decomposition against a complete, fixed editor cue sequence.

Gold is used only to measure a candidate family's attainable error. This module
does not select production changes or write subtitles. Unannotated cues remain
fixed, so sparse evaluation cannot silently move their boundaries out of the way.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def _interval(value: object) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("interval must contain two integer milliseconds")
    if any(isinstance(t, bool) or not isinstance(t, int) for t in value):
        raise ValueError("interval must contain two integer milliseconds")
    start, end = value
    if not 0 <= start < end:
        raise ValueError("interval must be nonnegative and have positive duration")
    return start, end


def _best_path(prepared, truth):
    # Cost: absolute endpoint error, then changed cue count. Backpointers keep
    # memory linear in sequence length instead of copying each partial SRT.
    layers = []
    for index, (key, baseline, options) in enumerate(prepared):
        layer = []
        for _, interval in options:
            error = sum(abs(a-b) for a,b in zip(interval, truth[key])) if key in truth else 0
            local = (error, int(interval != baseline))
            if index == 0:
                layer.append((local, None))
                continue
            predecessors = [
                ((state[0][0]+local[0], state[0][1]+local[1]), j)
                for j, state in enumerate(layers[-1]) if state is not None
                and prepared[index-1][2][j][1][1] <= interval[0]
            ]
            layer.append(min(predecessors) if predecessors else None)
        layers.append(layer)
    cost, position = min((state[0], j) for j,state in enumerate(layers[-1]) if state is not None)
    path = []
    for index in range(len(prepared)-1, -1, -1):
        key, _, options = prepared[index]
        name, interval = options[position]
        path.append({"cue_id": key, "candidate": name, "interval_ms": list(interval)})
        position = layers[index][position][1]
    path.reverse()
    return cost, path


def evaluate_interval_bottleneck(
    cues: Sequence[Mapping], gold: Mapping[str, Sequence[int]],
) -> dict:
    """Compare pointwise and whole-interval/nonoverlap oracles on the same cues.

    Each cue supplies ``id``, ``editor`` and optional ``candidates`` (name to
    interval or None). Supply *every* editor cue, including unannotated neighbors.
    Missing gold IDs are reported, never interpreted as correctly omitted cues.
    IDs must already identify canonical ownership; this function cannot infer it.
    Equal-cost paths prefer fewer changes. The whole-interval oracle uses atomic
    candidates. A separate mixed-edge oracle measures hypothetical local edge
    repair; it does not claim a mixed interval was emitted by an actual model.
    """
    if not cues:
        raise ValueError("complete editor sequence must not be empty")
    truth = {key: _interval(value) for key, value in gold.items()}
    if any(not isinstance(key, str) or not key for key in truth):
        raise ValueError("gold IDs must be nonempty strings")
    ids = set()
    prepared = []
    previous_end = 0
    invalid = []
    coverage = {}
    for cue in cues:
        key = cue.get("id")
        if not isinstance(key, str) or not key or key in ids:
            raise ValueError("cue IDs must be unique nonempty strings")
        ids.add(key)
        baseline = _interval(cue.get("editor"))
        if baseline[0] < previous_end:
            raise ValueError("editor sequence must be ordered and nonoverlapping")
        previous_end = baseline[1]
        options = [("editor", baseline)]
        candidates = cue.get("candidates", {})
        if not isinstance(candidates, Mapping):
            raise ValueError("candidates must map names to intervals")
        for name in sorted(candidates, key=str):
            if not isinstance(name, str) or not name or name == "editor":
                raise ValueError("candidate names must be nonempty and distinct from editor")
            coverage.setdefault(name, 0)
            value = candidates[name]
            if value is None:
                continue
            try:
                interval = _interval(value)
            except ValueError as exc:
                invalid.append({"cue_id": key, "candidate": name, "reason": str(exc)})
                continue
            if key in truth:
                coverage[name] += 1
                options.append((name, interval))
        prepared.append((key, baseline, options))

    cost, path = _best_path(prepared, truth)
    mixed = []
    for key, baseline, options in prepared:
        combined = list(options)
        seen = {iv for _,iv in options}
        for start_name, start_iv in options:
            for end_name, end_iv in options:
                iv = (start_iv[0], end_iv[1])
                if iv[0] < iv[1] and iv not in seen:
                    seen.add(iv)
                    combined.append((f"mixed(start={start_name},end={end_name})", iv))
        mixed.append((key, baseline, combined))
    mixed_cost, mixed_path = _best_path(mixed, truth)
    scored = [(key, baseline, options) for key,baseline,options in prepared if key in truth]
    n = 2*len(scored)
    baseline_error = sum(abs(a-b) for key,iv,_ in scored for a,b in zip(iv,truth[key]))
    pointwise_error = sum(min(abs(iv[edge]-truth[key][edge]) for _,iv in options)
        for key,_,options in scored for edge in (0,1))
    missing = sorted(set(truth)-ids)
    return {
        "schema_version": "interval-bottleneck-1.0",
        "purpose": "offline_oracle_diagnostic_only",
        "gold_cue_count": len(truth), "scored_cue_count": len(scored),
        "editor_cue_count": len(cues), "unrepresented_gold_ids": missing,
        "status": "incomplete_ownership" if missing else "evaluated" if n else "no_gold",
        "candidate_coverage": {name: {"available_cues": count,
            "population_cues": len(truth), "fraction": count/len(truth) if truth else None}
            for name,count in sorted(coverage.items())},
        "invalid_candidates": invalid,
        "editor_mae_ms": baseline_error/n if n else None,
        "pointwise_oracle_mae_ms": pointwise_error/n if n else None,
        "legal_interval_oracle_mae_ms": cost[0]/n if n else None,
        "legal_oracle_changed_cues": cost[1], "legal_oracle_path": path,
        "legal_mixed_edge_oracle_mae_ms": mixed_cost[0]/n if n else None,
        "legal_mixed_edge_changed_cues": mixed_cost[1],
        "legal_mixed_edge_path": mixed_path,
        "limitation": "timing-only oracle with fixed ownership and cue count; supplied gold coverage is explicit; not a production selector",
    }
