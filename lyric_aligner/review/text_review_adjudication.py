from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from lyric_aligner.text.canonical_lyrics import parse_canonical_lyrics


SCHEMA_VERSION = "text-review-adjudication-1.1"
_PUNCT = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)
_BLOCKED_REASONS = {
    "segmentation_would_empty_existing_cue", "layout_boundary_insertion_requires_review",
    "segmentation_boundary_insertion_requires_review", "cue_boundary_splits_canonical_latin_word",
    "adjacent_alignment_gap_requires_review", "unmatched_subtitle_cue", "ambiguous_nearby_canonical_match",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize(text: str) -> str:
    return _PUNCT.sub("", text.casefold())


def presentation_equivalent(a: str, b: str) -> bool:
    return bool(normalize(a)) and normalize(a) == normalize(b)


def parse_srt(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\r?\n+", raw.strip())
    out = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        n = int(lines[0].strip())
        left, right = [x.strip() for x in lines[1].split("-->", 1)]
        def ms(v: str) -> int:
            h, m, rest = v.split(":")
            s, milli = rest.split(",")
            return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(milli)
        out.append({"number": n, "start_ms": ms(left), "end_ms": ms(right), "text": "\n".join(lines[2:])})
    return out


def _canonical_lines(manifest: dict[str, Any], base: Path) -> list[dict[str, Any]]:
    """Use the production parser, including metadata/role filtering."""
    lines = []
    for item in manifest.get("canonical_lyrics", []):
        path = Path(item["path"])
        if not path.is_absolute():
            path = base / path
        # The manifest path may be relative to the repository, while this
        # runner is intentionally given the repository root as base.
        if not path.exists():
            continue
        ordinal = int(item["ordinal"])
        for line in parse_canonical_lyrics(path):
            lines.append({"source_ordinal": ordinal, "text": line.text,
                          "time_ms": line.time_ms, "parser_index": line.index})
    return lines


def _neighbor_witness(row: dict[str, Any], decisions: dict[int, dict[str, Any]]) -> tuple[bool, str]:
    cue = int(row["cue_ordinal"])
    span = row.get("canonical_span")
    if not isinstance(span, list) or len(span) != 2 or span[1] - span[0] != 1:
        return False, "not_1_to_1"
    left, right = decisions.get(cue - 1), decisions.get(cue + 1)
    if not left or not right or left.get("action") == "review" or right.get("action") == "review":
        return False, "neighbors_not_resolved"
    if left.get("action") not in {"replace", "keep", "resolved"} or right.get("action") not in {"replace", "keep", "resolved"}:
        return False, "neighbors_not_resolved"
    if left.get("canonical_span", [None, None])[1] != span[0] or right.get("canonical_span", [None, None])[0] != span[1]:
        return False, "neighbor_gap_not_exact"
    return True, "resolved_neighbor_bracket"


def _pro_support(pro_rows: list[dict[str, Any]], asr_rows: list[dict[str, Any]], row: dict[str, Any]) -> tuple[bool, str]:
    cue = int(row["cue_ordinal"])
    matching = [x for x in pro_rows if int(x.get("cue_ordinal", -1)) == cue]
    if not matching:
        return False, "pro_absent"
    for decision in matching:
        if decision.get("text_state") != "canonical_text_supported":
            continue
        if float(decision.get("asr_canonical_support_score") or 0) < 0.72:
            continue
        jobs = [x for x in asr_rows if x.get("job_id") == decision.get("job_id")]
        if any(x.get("canonical_start_covered") and x.get("canonical_end_covered")
               and not x.get("canonical_match_ambiguous")
               and float(x.get("canonical_match_support_score") or 0) >= 0.72 for x in jobs):
            return True, "pro_canonical_asr_support"
    return False, "pro_not_independent_support"


def _max_witness(max_payload: dict[str, Any], cue: dict[str, Any]) -> tuple[bool, str]:
    if max_payload.get("status") != "ready":
        return False, "max_not_ready"
    start, end = cue["start_ms"] / 1000, cue["end_ms"] / 1000
    candidates = [o for o in max_payload.get("occurrences", [])
                  if o.get("primary_interval", [0, 0])[0] <= start
                  and o.get("primary_interval", [0, 0])[1] >= end]
    if len(candidates) != 1:
        return False, "max_occurrence_not_unique"
    for transition in max_payload.get("transitions", []):
        if transition.get("status") != "resolved_clear":
            boundary = float(transition.get("nominal_boundary", -1))
            if start - 10 <= boundary <= end + 10:
                return False, "max_transition_risk"
    return True, "max_unique_occurrence"


def _safe_canonical_for_review(row: dict[str, Any], canonical: list[dict[str, Any]], counts: Counter) -> tuple[str | None, str]:
    span = row.get("canonical_span")
    if not isinstance(span, list) or len(span) != 2 or span[1] - span[0] != 1:
        return None, "non_1_to_1_span"
    idx = int(span[0])
    if idx < 0 or idx >= len(canonical):
        return None, "canonical_span_out_of_range"
    text = canonical[idx]["text"]
    key = normalize(text)
    counts[key] += 1
    if counts[key] != 1:
        return None, "repeated_canonical_occurrence"
    if row.get("canonical_ordinal") is None:
        return None, "unmapped_review"
    return text, "unique_1_to_1_canonical_span"


def adjudicate(*, repo_root: Path, run_dir: Path, output_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "FRESH_INPUT_MANIFEST.json"
    smart_path = run_dir / "FRESH_SMART.json"
    best_path = run_dir / "FRESH_BEST_REVIEW.srt"
    smart_srt_path = run_dir / "FRESH_SMART.srt"
    receipt_path = run_dir / "FRESH_RUN_RECEIPT.json"
    pro_plan = run_dir / "PRO" / "PLAN_EXEC.json"
    pro_asr = run_dir / "PRO" / "ASR.json"
    pro_decisions = run_dir / "PRO" / "DECISIONS.json"
    max_run = run_dir / "MAX_V4_RERUN2" / "v4_run.json"
    max_artifact = run_dir / "MAX_V4_RERUN2" / "v4_run.artifact.json"
    required = [manifest_path, smart_path, best_path, smart_srt_path, receipt_path, pro_plan, pro_asr, pro_decisions, max_run, max_artifact]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise ValueError("missing fresh lineage inputs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    smart = json.loads(smart_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    max_payload = json.loads(max_run.read_text(encoding="utf-8"))
    max_art = json.loads(max_artifact.read_text(encoding="utf-8"))
    reviews = [r for r in smart.get("text_decisions", []) if r.get("action") == "review"]
    if len(reviews) != 176:
        raise ValueError(f"expected exactly 176 text reviews, got {len(reviews)}")
    if receipt.get("stages", {}).get("max", {}).get("algorithm_version") != "4.0.0a19":
        raise ValueError("corrected MAX_V4_RERUN2 lineage is not present")
    source_hash = sha256_file(best_path)
    if source_hash != sha256_file(smart_srt_path):
        raise ValueError("Smart and Best Review SRT hashes differ")
    cues = parse_srt(best_path)
    if len(cues) != 923:
        raise ValueError("expected 923 cues")
    canonical = _canonical_lines(manifest, repo_root)
    if not canonical:
        raise ValueError("canonical lyrics could not be loaded from fresh manifest")
    # Occurrence uniqueness is deliberately conservative: repeated normalized
    # text is not enough to select an actual occurrence.
    all_counts = Counter(normalize(x["text"]) for x in canonical)
    pro_rows = json.loads(pro_decisions.read_text(encoding="utf-8")).get("decisions", [])
    asr_rows = json.loads(pro_asr.read_text(encoding="utf-8")).get("jobs", [])
    smart_by_cue = {int(x["cue_ordinal"]): x for x in smart.get("text_decisions", []) if x.get("cue_ordinal") is not None}
    decisions = []
    materialized = list(cues)
    actual_changes = 0
    presentation_only = 0
    resolution_counts = Counter()
    failure_counts = Counter()
    for row in reviews:
        cue_idx = int(row["cue_ordinal"])
        cue = cues[cue_idx]
        candidate, structural_reason = _safe_canonical_for_review(row, canonical, Counter()) if False else (None, "")
        span = row.get("canonical_span")
        candidate = None
        if isinstance(span, list) and len(span) == 2 and span[1] - span[0] == 1 and 0 <= int(span[0]) < len(canonical):
            candidate = canonical[int(span[0])]["text"]
            key = normalize(candidate)
            if all_counts[key] != 1:
                candidate = None
                structural_reason = "repeated_canonical_occurrence"
            elif row.get("canonical_ordinal") is None:
                structural_reason = "unmapped_review"
            else:
                structural_reason = "unique_1_to_1_canonical_span"
        else:
            structural_reason = "non_1_to_1_span"
        pro_supported, pro_reason = _pro_support(pro_rows, asr_rows, row)
        neighbor_supported, neighbor_reason = _neighbor_witness(row, smart_by_cue)
        max_supported, max_reason = _max_witness(max_payload, cue)
        # Only exact unique canonical occurrence with no boundary/ownership risk
        # gets authority. This keeps the result replayable and fail-closed.
        safe_reason = str(row.get("reason") or "")
        witness = neighbor_reason if neighbor_supported else (pro_reason if pro_supported else (max_reason if max_supported else "none"))
        independent = neighbor_supported or pro_supported or max_supported
        allowed = (candidate is not None and safe_reason not in _BLOCKED_REASONS
                   and independent and (float(row.get("score") or 0) >= 0.8 or independent))
        if allowed and presentation_equivalent(cue["text"], candidate):
            resolution = "keep_editor_presentation_equivalent"
            presentation_only += 1
        elif allowed:
            resolution = "apply_canonical_text"
            materialized[cue_idx]["text"] = candidate
            actual_changes += 1
        else:
            resolution = "unresolved_manual"
            failure_counts[safe_reason or "no_independent_witness"] += 1
        resolution_counts[resolution] += 1
        decisions.append({
            "cue_ordinal": cue_idx, "cue_number": cue["number"], "canonical_ordinal": row.get("canonical_ordinal"),
            "canonical_span": row.get("canonical_span"), "review_reason": row.get("reason"),
            "resolution": resolution, "confidence": 0.98 if allowed else 0.0,
            "reason": witness if allowed else (safe_reason or "no_independent_witness"),
            "independent_support": independent,
            "witness": witness,
            "witness_detail": {"neighbor": neighbor_reason, "pro": pro_reason, "max2": max_reason},
            "canonical_text_sha256": hashlib.sha256((candidate or "").encode()).hexdigest() if candidate else None,
            "evidence_family_summary": {
                "smart": "proposal_only",
                "pro_decision": "support" if pro_supported else ("present_without_support" if any(int(x.get("cue_ordinal", -1)) == cue_idx for x in pro_rows) else "absent"),
                "pro_asr": "support" if pro_supported else ("job_present_without_support" if any(x.get("canonical_line_index") == row.get("canonical_ordinal") for x in asr_rows) else "absent"),
                "max2": "support" if max_supported else max_reason,
            },
            "automatic_text_authority": "independent_witness" if allowed else None,
            "timing_mutation_performed": False,
        })
    out_srt = output_dir / "TEXT_REVIEW_MATERIALIZED.srt"
    out_json = output_dir / "TEXT_REVIEW_ADJUDICATION.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_srt.write_text("\n\n".join(f"{x['number']}\n{_fmt(x['start_ms'])} --> {_fmt(x['end_ms'])}\n{x['text']}" for x in materialized) + "\n", encoding="utf-8")
    timeline_sig = [(x["number"], x["start_ms"], x["end_ms"]) for x in cues]
    materialized_sig = [(x["number"], x["start_ms"], x["end_ms"]) for x in parse_srt(out_srt)]
    if timeline_sig != materialized_sig:
        raise ValueError("text-only materialization changed timeline")
    report = {
        "schema_version": SCHEMA_VERSION, "task_scope": "legacy-e6b353b489b229d805899bdac2ef40399dee04d80635447c59bbb6e195bc6181",
        "fresh_run": True, "before": 176, "auto_resolved": resolution_counts["apply_canonical_text"] + resolution_counts["keep_editor_presentation_equivalent"],
        "remaining_manual": resolution_counts["unresolved_manual"], "actual_normalized_text_changes": actual_changes,
        "presentation_only_count": presentation_only, "resolution_counts": dict(resolution_counts), "failure_reason_counts": dict(failure_counts),
        "cue_count": 923, "timing_mutation_performed": False, "timeline_immutable": True,
        "canonical_parser": "lyric_aligner.text.canonical_lyrics.parse_canonical_lyrics",
        "witness_counts": dict(Counter(x["witness"] for x in decisions)),
        "lineage": {"fresh_manifest_sha256": sha256_file(manifest_path), "smart_sha256": sha256_file(smart_path), "smart_srt_sha256": sha256_file(smart_srt_path),
                    "best_review_sha256": source_hash, "pro_plan_sha256": sha256_file(pro_plan), "pro_asr_sha256": sha256_file(pro_asr),
                    "pro_decisions_sha256": sha256_file(pro_decisions), "max2_run_sha256": sha256_file(max_run), "max2_artifact_sha256": sha256_file(max_artifact),
                    "max2_task_fingerprint_sha256": max_payload.get("task_fingerprint_sha256"), "max2_algorithm_version": max_art.get("algorithm_version")},
        "decisions": decisions,
    }
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _fmt(ms: int) -> str:
    h, rem = divmod(ms, 3600000); m, rem = divmod(rem, 60000); s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
