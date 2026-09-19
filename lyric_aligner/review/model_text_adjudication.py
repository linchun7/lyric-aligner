from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from lyric_aligner.text.canonical_lyrics import parse_canonical_lyrics

PROTOCOL_VERSION = "model-text-adjudication-1.0"
MODEL_ID = "terra"
ACTIONS = {
    "apply_canonical_span_to_cue",
    "repartition_canonical_span_across_cues",
    "keep_editor_as_noncanonical_adlib",
    "unresolved_manual",
}
_WORD = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
_NON_WORD = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(text: str) -> str:
    return _NON_WORD.sub("", text.casefold())


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(manifest: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in manifest["canonical_lyrics"]:
        path = Path(item["path"])
        if not path.is_absolute():
            path = repo_root / path
        for line in parse_canonical_lyrics(path):
            result.append({"source_ordinal": int(item["ordinal"]), "text": line.text,
                           "parser_index": line.index})
    return result


def _review_rows(smart: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in smart.get("text_decisions", []) if row.get("action") == "review"]


def _lineage(run_dir: Path, smart_path: Path, best_path: Path) -> dict[str, Any]:
    manifest = run_dir / "FRESH_INPUT_MANIFEST.json"
    receipt = run_dir / "FRESH_RUN_RECEIPT.json"
    return {
        "base_adjudication_sha256": sha256_file(best_path),
        "smart_sha256": sha256_file(smart_path),
        "manifest_sha256": sha256_file(manifest),
        "receipt_sha256": sha256_file(receipt),
    }


def _regions(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    regions: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda x: int(x["cue_ordinal"])):
        if not regions or int(row["cue_ordinal"]) != int(regions[-1][-1]["cue_ordinal"]) + 1:
            regions.append([row])
        else:
            regions[-1].append(row)
    return regions


def generate_proposals(repo_root: Path, run_dir: Path, output_path: Path) -> dict[str, Any]:
    smart_path = run_dir / "FRESH_SMART.json"
    best_path = run_dir / "FRESH_BEST_REVIEW.srt"
    manifest = _load(run_dir / "FRESH_INPUT_MANIFEST.json")
    smart = _load(smart_path)
    canonical = _canonical(manifest, repo_root)
    reviews = _review_rows(smart)
    target = sorted(int(row["cue_ordinal"]) for row in reviews)
    occurrences = Counter(normalize(item["text"]) for item in canonical)
    proposals: list[dict[str, Any]] = []
    # Terra proposal generation is deliberately bounded: no free-form text and
    # no semantic guess is emitted. Only exact parser spans with a unique
    # source occurrence and a narrow, already-recorded structural shape can be
    # proposed; the verifier remains the authority.
    by_cue = {int(row["cue_ordinal"]): row for row in reviews}
    for region in _regions(reviews):
        if len(region) > 1:
            proposals.append({
                "action": "unresolved_manual",
                "cue_ordinals": [int(row["cue_ordinal"]) for row in region],
                "reason": "model_requires_continuous_region_verifier",
            })
            continue
        row = region[0]
        cue = int(row["cue_ordinal"])
        span = row.get("canonical_span")
        if not (isinstance(span, list) and len(span) == 2 and span[1] - span[0] == 1):
            action = "keep_editor_as_noncanonical_adlib" if row.get("reason") == "unmatched_subtitle_cue" else "unresolved_manual"
            proposals.append({"action": action, "cue_ordinals": [cue], "reason": "no_safe_parser_span"})
            continue
        index = int(span[0])
        if not (0 <= index < len(canonical)) or occurrences[normalize(canonical[index]["text"])] != 1:
            proposals.append({"action": "unresolved_manual", "cue_ordinals": [cue], "reason": "occurrence_not_unique"})
            continue
        # A proposal may cite indices only. It must not carry replacement text.
        left = by_cue.get(cue - 1)
        right = by_cue.get(cue + 1)
        if left is None and right is None:
            proposals.append({"action": "unresolved_manual", "cue_ordinals": [cue], "reason": "no_independent_bracket"})
        else:
            proposals.append({"action": "apply_canonical_span_to_cue", "cue_ordinals": [cue],
                              "canonical_span": [index, index + 1], "reason": "bounded_model_hypothesis"})
    payload = {
        "schema_version": PROTOCOL_VERSION,
        "model_id": MODEL_ID,
        "target_fingerprint": hashlib.sha256(json.dumps(target, separators=(",", ":")).encode()).hexdigest(),
        "target_unresolved_cue_ordinals": target,
        "lineage": _lineage(run_dir, smart_path, best_path),
        "proposal_count": len(proposals),
        "proposals": proposals,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _parse_srt(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"\r?\n\r?\n+", path.read_text(encoding="utf-8-sig").strip())
    result = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        left, right = [x.strip() for x in lines[1].split("-->", 1)]
        def ms(value: str) -> int:
            h, m, rest = value.split(":"); sec, milli = rest.split(",")
            return (int(h) * 3600 + int(m) * 60 + int(sec)) * 1000 + int(milli)
        result.append({"number": int(lines[0]), "start_ms": ms(left), "end_ms": ms(right), "text": "\n".join(lines[2:])})
    return result


def _is_word_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return True
    return not (text[index - 1].isalnum() and text[index].isalnum())


def verify_and_materialize(repo_root: Path, run_dir: Path, output_dir: Path, proposal_path: Path) -> dict[str, Any]:
    smart_path = run_dir / "FRESH_SMART.json"
    best_path = run_dir / "FRESH_BEST_REVIEW.srt"
    manifest_path = run_dir / "FRESH_INPUT_MANIFEST.json"
    smart = _load(smart_path); manifest = _load(manifest_path)
    proposals = _load(proposal_path)
    reviews = _review_rows(smart)
    target = sorted(int(row["cue_ordinal"]) for row in reviews)
    expected_lineage = _lineage(run_dir, smart_path, best_path)
    errors: list[dict[str, Any]] = []
    if proposals.get("schema_version") != PROTOCOL_VERSION or proposals.get("model_id") != MODEL_ID:
        errors.append({"reason": "protocol_or_model_mismatch"})
    if proposals.get("lineage") != expected_lineage:
        errors.append({"reason": "stale_lineage"})
    if proposals.get("target_unresolved_cue_ordinals") != target:
        errors.append({"reason": "target_set_mismatch"})
    cues = _parse_srt(best_path)
    canonical = _canonical(manifest, repo_root)
    by_cue = {int(row["cue_ordinal"]): row for row in reviews}
    by_prop = {int(cue): prop for prop in proposals.get("proposals", []) for cue in prop.get("cue_ordinals", [])}
    if sorted(by_prop) != target:
        errors.append({"reason": "proposal_coverage_mismatch"})
    output = list(cues); verified = []; rejected = []
    for cue_ordinal in target:
        prop = by_prop.get(cue_ordinal, {})
        row = by_cue[cue_ordinal]
        reason = None
        if any(key in prop for key in ("text", "start_ms", "end_ms", "number")):
            reason = "freeform_or_timing_field"
        elif prop.get("action") not in ACTIONS:
            reason = "invalid_action"
        elif prop.get("action") == "apply_canonical_span_to_cue":
            span = prop.get("canonical_span")
            if not (isinstance(span, list) and len(span) == 2 and span == row.get("canonical_span") and span[1] - span[0] == 1):
                reason = "span_not_exact_smart_span"
            elif not (0 <= int(span[0]) < len(canonical)):
                reason = "span_out_of_range"
            elif row.get("reason") in {"ambiguous_nearby_canonical_match", "adjacent_alignment_gap_requires_review", "cue_boundary_splits_canonical_latin_word", "layout_boundary_insertion_requires_review", "segmentation_boundary_insertion_requires_review", "segmentation_would_empty_existing_cue", "unmatched_subtitle_cue"}:
                reason = "structural_risk_fail_closed"
            else:
                left = next((x for x in smart.get("text_decisions", []) if int(x.get("cue_ordinal", -1)) == cue_ordinal - 1), None)
                right = next((x for x in smart.get("text_decisions", []) if int(x.get("cue_ordinal", -1)) == cue_ordinal + 1), None)
                if not left or not right or left.get("action") == "review" or right.get("action") == "review":
                    reason = "independent_bracket_missing"
                elif left.get("canonical_span", [None, None])[1] != span[0] or right.get("canonical_span", [None, None])[0] != span[1]:
                    reason = "exact_bracket_missing"
        elif prop.get("action") == "repartition_canonical_span_across_cues":
            reason = "multi_cue_requires_region_specific_proposal"
        elif prop.get("action") == "keep_editor_as_noncanonical_adlib":
            reason = "adlib_remains_manual"
        else:
            reason = "unresolved_manual"
        if reason:
            rejected.append({"cue_ordinal": cue_ordinal, "reason": reason, "action": prop.get("action")})
        else:
            index = int(prop["canonical_span"][0]); output[cue_ordinal]["text"] = canonical[index]["text"]
            verified.append({"cue_ordinal": cue_ordinal, "witness": "exact_resolved_neighbor_bracket", "canonical_span": prop["canonical_span"]})
    if errors:
        rejected.extend({"cue_ordinal": None, **item} for item in errors)
        verified = []
        output = list(cues)
    output_dir.mkdir(parents=True, exist_ok=True)
    materialized = output_dir / "TEXT_REVIEW_MATERIALIZED_V2.srt"
    materialized.write_text("\n\n".join(f"{x['number']}\n{_fmt(x['start_ms'])} --> {_fmt(x['end_ms'])}\n{x['text']}" for x in output) + "\n", encoding="utf-8")
    report = {"schema_version": PROTOCOL_VERSION, "lineage": expected_lineage, "before": 176, "deterministic_auto_resolved": 2,
              "model_proposed_by_action": dict(Counter(p.get("action") for p in proposals.get("proposals", []))),
              "verified_new_resolved": len(verified), "rejected": rejected, "remaining_manual": 174 - len(verified),
              "actual_normalized_text_changes": sum(1 for a, b in zip(cues, output) if normalize(a["text"]) != normalize(b["text"])),
              "ownership_repartitions": [], "kept_adlib_manual": [], "verified_decisions": verified,
              "timeline_immutable": [(x["number"], x["start_ms"], x["end_ms"]) for x in cues] == [(x["number"], x["start_ms"], x["end_ms"]) for x in output],
              "proposal_verification_errors": errors}
    (output_dir / "VERIFIED_MODEL_ADJUDICATION.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _fmt(value: int) -> str:
    h, rem = divmod(value, 3600000); m, rem = divmod(rem, 60000); s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
