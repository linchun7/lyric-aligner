"""Materialize task-reviewed canonical spans without changing floor timing.

The review owns semantic/occurrence judgments. This verifier proves lineage,
canonical edit scope and invariants, not that a lyric was actually sung.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lyric_aligner.text_repair import (
    _normalize_for_match, _sha256_file, parse_canonical_files, parse_srt_text,
    render_repaired_srt, timeline_signature,
)
from lyric_aligner.timeline.anchor_repair import _cue_times, parse_timed_canonical_files

SCHEMA_VERSION = "reviewed-canonical-splices-1.0"


def file_ref(path: Path) -> dict[str, str]:
    path = path.resolve()
    return {"path": str(path), "sha256": _sha256_file(path)}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    _require(isinstance(value, dict), "expected a JSON object")
    return value


def _bound(reference: dict[str, Any], root: Path) -> Path:
    _require(isinstance(reference, dict) and set(reference) == {"path", "sha256"}, "invalid file reference")
    _require(isinstance(reference["path"], str) and isinstance(reference["sha256"], str), "invalid file reference types")
    path = (root / reference["path"]).resolve()
    _require(path.is_file(), "missing bound file: " + str(path))
    _require(file_ref(path)["sha256"] == reference["sha256"], "stale bound file: " + str(path))
    return path


def _index(value: Any, limit: int) -> int:
    _require(type(value) is int and 0 <= value < limit, "invalid index")
    return value


def _interval(raw: Any, size: int) -> tuple[int, int]:
    _require(isinstance(raw, list) and len(raw) == 2, "invalid character interval")
    a, b = raw
    _require(type(a) is int and type(b) is int and 0 <= a < b <= size, "invalid character interval")
    return a, b


def load_floor_context(plan_path: Path) -> dict[str, Any]:
    """One reader for inspection, candidate extraction and text materialization.

    Historical field names smart_srt/smart_report also accept a genuine
    Standard report. Alternate canonical wording remains explicitly reviewed;
    callers using baseline ordinal mappings must additionally bind its original
    ordered canonical inputs (the candidate scanner does so).
    """
    plan_path = plan_path.resolve()
    plan = _read(plan_path)
    root = plan_path.parent
    _require(set(plan) == {"schema_version", "floor", "smart_srt", "smart_report", "canonical", "proposals"}, "unknown or missing plan fields")
    _require(plan["schema_version"] == SCHEMA_VERSION, "schema mismatch")
    floor = _bound(plan["floor"], root)
    baseline = _bound(plan["smart_srt"], root)
    report = _read(_bound(plan["smart_report"], root))
    parts, cues = parse_srt_text(floor.read_text(encoding="utf-8-sig"))
    _, baseline_cues = parse_srt_text(baseline.read_text(encoding="utf-8-sig"))
    _require(timeline_signature(cues) == timeline_signature(baseline_cues), "floor/Smart timing or topology mismatch")
    _require(report.get("output_srt_sha256") == file_ref(baseline)["sha256"], "baseline report/SRT hash mismatch")
    if report.get("mode") == "text_only_preserve_timeline":
        _require(report.get("schema_version") == "2.2" and report.get("timeline_unchanged") is True
                 and report.get("cue_count_unchanged") is True, "invalid Standard floor report")
        rows = report.get("decisions")
    else:
        _require(report.get("schema_version") == "smart-1.1", "unsupported baseline report")
        rows = report.get("text_decisions")
    _require(isinstance(rows, list) and len(rows) == len(cues), "incomplete baseline decisions")
    _require(all(isinstance(r, dict) and type(r.get("cue_ordinal")) is int for r in rows), "invalid baseline cue identity")
    by_id = {r["cue_ordinal"]: r for r in rows}
    _require(set(by_id) == set(range(len(cues))), "duplicate or missing baseline cue identity")
    _require(isinstance(plan["canonical"], list) and bool(plan["canonical"]), "canonical files required")
    paths = [_bound(r, root) for r in plan["canonical"]]
    canonical = (parse_canonical_files(paths) if report.get("mode") == "text_only_preserve_timeline"
                 else parse_timed_canonical_files(paths)[1])
    _require(isinstance(plan["proposals"], list), "proposal list required")
    return {"plan": plan, "floor_path": floor, "parts": parts, "cues": cues,
            "baseline_cues": baseline_cues, "report": report, "by_id": by_id,
            "canonical_paths": paths, "canonical": canonical}


def cue_context(cues: list, i: int) -> dict[str, Any]:
    def describe(j: int) -> dict[str, Any]:
        c = cues[j]
        start, end = _cue_times(c)
        return {"cue_ordinal": j, "cue_number": c.number, "timing": c.timing,
                "start_ms": start, "end_ms": end, "text": c.text}
    current = describe(i)
    previous = describe(i - 1) if i else None
    following = describe(i + 1) if i + 1 < len(cues) else None
    return {"current": current, "previous": previous, "next": following,
            "previous_gap_ms": current["start_ms"] - previous["end_ms"] if previous else None,
            "next_gap_ms": following["start_ms"] - current["end_ms"] if following else None}


def inspect_splice_context(plan_path: Path) -> dict[str, Any]:
    """Ordinal adjacency is not acoustic continuity; no gap grants authority."""
    context = load_floor_context(plan_path)
    plan, cues = context["plan"], context["cues"]
    rows = []
    for p in plan["proposals"]:
        i = _index(p["cue_ordinal"], len(cues))
        c = cues[i]
        _require((p["cue_number"], p["timing"], p["before"]) == (c.number, c.timing, c.text), "stale cue identity/text")
        rows.append(cue_context(cues, i))
    return {"schema_version": SCHEMA_VERSION, "plan": file_ref(plan_path), "floor": file_ref(context["floor_path"]),
            "rows": rows, "ownership_authority": False,
            "notice": "Subtitle adjacency is not audio continuity. Review the retained raw observation and canonical character spans separately."}


def canonical_segment_text(segments: list, canonical: list) -> str:
    """Resolve ordered, non-overlapping same-source spans, never free lyrics."""
    _require(isinstance(segments, list) and bool(segments), "canonical segments required")
    texts = []
    previous = None
    source = None
    for segment in segments:
        _require(isinstance(segment, dict) and set(segment) <= {"canonical_ordinal", "span", "display"}
                 and {"canonical_ordinal", "span"} <= set(segment), "invalid canonical segment")
        ci = _index(segment["canonical_ordinal"], len(canonical))
        ca, cb = _interval(segment["span"], len(canonical[ci].text))
        current_source = canonical[ci].source_ordinal
        _require(source is None or source == current_source, "cross-source canonical splice")
        _require(previous is None or previous <= (ci, ca), "reordered or overlapping canonical segments")
        source, previous = current_source, (ci, cb)
        lexical = canonical[ci].text[ca:cb]
        display = segment.get("display", lexical)
        _require(isinstance(display, str) and display.strip() and not any(c in display for c in "\r\n\x00"), "invalid display text")
        _require(bool(_normalize_for_match(lexical)) and _normalize_for_match(display) == _normalize_for_match(lexical), "display changes lexical content")
        texts.append(display)
    return " ".join(texts)


def apply_reviewed_splices(*, plan_path: Path, review_path: Path, out_dir: Path) -> dict[str, Any]:
    """Validate the complete plan before writing; inherit every unapproved cue."""
    plan_path, review_path = plan_path.resolve(), review_path.resolve()
    root = plan_path.parent
    plan_ref, review_ref = file_ref(plan_path), file_ref(review_path)
    context = load_floor_context(plan_path)
    plan, cues = context["plan"], context["cues"]
    review = _read(review_path)
    _require(review.get("schema_version") == SCHEMA_VERSION, "schema mismatch")
    _require(review.get("plan_sha256") == plan_ref["sha256"], "review belongs to a different plan")
    _require(set(review) == {"schema_version", "plan_sha256", "reviewer", "decisions"}, "unknown or missing review fields")
    _require(isinstance(review["reviewer"], str) and bool(review["reviewer"].strip()), "reviewer required")
    _require(isinstance(review["decisions"], list), "review list required")
    decisions = {}
    for decision in review["decisions"]:
        _require(isinstance(decision, dict) and set(decision) == {"cue_ordinal", "decision", "scope", "wording_reason", "ownership_reason", "evidence_indices"}, "invalid review decision fields")
        i = _index(decision["cue_ordinal"], len(cues))
        _require(i not in decisions, "duplicate review decision")
        _require(decision["decision"] in {"apply", "keep"}, "invalid review decision")
        _require(decision["scope"] in {"whole_text", "partial_text"}, "invalid review scope")
        decisions[i] = decision
    proposed, replacements, applied = set(), {}, []
    observations = []
    for proposal in plan["proposals"]:
        _require(isinstance(proposal, dict) and set(proposal) == {"cue_ordinal", "cue_number", "timing", "before", "edits", "observations"}, "invalid proposal fields; no free replacement text or timing overrides")
        i = _index(proposal["cue_ordinal"], len(cues))
        _require(i not in proposed, "duplicate proposal")
        proposed.add(i)
        _require(i in decisions, "proposal has no explicit review")
        cue = cues[i]
        _require((proposal["cue_number"], proposal["timing"], proposal["before"]) == (cue.number, cue.timing, cue.text), "stale cue identity/text")
        _require(context["by_id"][i].get("action") == "review", "cannot change a resolved baseline cue")
        decision = decisions[i]
        _require(isinstance(proposal["observations"], list), "observations must be a list")
        for evidence in proposal["observations"]:
            _bound(evidence, root)
            observations.append(evidence)
        if decision["decision"] == "keep":
            continue
        _require(all(isinstance(decision[k], str) and decision[k].strip() for k in ("wording_reason", "ownership_reason")), "wording and ownership review required")
        _require(isinstance(decision["evidence_indices"], list) and bool(decision["evidence_indices"]), "external observation review required")
        for ei in decision["evidence_indices"]:
            _index(ei, len(proposal["observations"]))
        _require(isinstance(proposal["edits"], list) and bool(proposal["edits"]), "nonempty edits required")
        edits = []
        for edit in proposal["edits"]:
            _require(isinstance(edit, dict) and set(edit) == {"span", "segments"}, "invalid edit fields")
            a, b = _interval(edit["span"], len(cue.text))
            _require("\n" not in cue.text[a:b], "cannot remove a display line break")
            edits.append((a, b, canonical_segment_text(edit["segments"], context["canonical"])))
        edits.sort()
        _require(all(left[1] <= right[0] for left, right in zip(edits, edits[1:])), "overlapping edits")
        # Disjoint editor edits must also preserve canonical order as a group.
        canonical_segment_text([segment for edit in sorted(proposal["edits"], key=lambda e: e["span"])
                                for segment in edit["segments"]], context["canonical"])
        text = cue.text
        for a, b, replacement in reversed(edits):
            text = text[:a] + replacement + text[b:]
        _require(text != cue.text and text.count("\n") == cue.text.count("\n"), "empty change or display segmentation drift")
        replacements[i] = text
        applied.append({"cue_ordinal": i, "cue_number": cue.number, "before": cue.text, "after": text, "scope": decision["scope"]})
    _require(proposed == set(decisions), "review/proposal coverage mismatch")
    floor_path = context["floor_path"]
    rendered = render_repaired_srt(context["parts"], cues, replacements).encode("utf-8") if replacements else floor_path.read_bytes()
    _, result_cues = parse_srt_text(rendered.decode("utf-8-sig"))
    _require(timeline_signature(result_cues) == timeline_signature(cues), "renderer changed timing/topology")
    _require({a.ordinal: b.text for a, b in zip(cues, result_cues) if a.text != b.text} == replacements, "unapproved text change")
    # Recheck bound snapshots before output, including retained observations.
    for reference in [plan_ref, review_ref, plan["floor"], plan["smart_srt"], plan["smart_report"], *plan["canonical"], *observations]:
        _bound(reference, root)
    _require(not out_dir.exists(), "output directory already exists")
    out_dir.mkdir(parents=True)
    final = out_dir / "FINAL.srt"
    final.write_bytes(rendered)
    report = {
        "schema_version": SCHEMA_VERSION, "plan": plan_ref, "review": review_ref,
        "floor": {"path": str(floor_path), "sha256": plan["floor"]["sha256"]}, "final": file_ref(final), "cue_count": len(cues),
        "applied": applied, "applied_count": len(applied), "timing_changes": 0,
        "unapproved_text_changes": 0, "automatic_lexical_authority": False,
        "independent_gold_accuracy_measured": False,
        "authority": "bound_task_review; verifier proves only lineage and edit scope",
        "ledger": [{"cue_ordinal": c.ordinal, "cue_number": c.number,
                    "action": "applied" if c.ordinal in replacements else "keep_floor",
                    "reason": "reviewed_canonical_splice" if c.ordinal in replacements else "explicit_keep" if c.ordinal in proposed else "not_proposed"} for c in cues],
    }
    (out_dir / "QA.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
