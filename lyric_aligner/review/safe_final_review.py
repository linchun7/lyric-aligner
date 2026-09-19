"""Read-only lexical/gap review candidates on a frozen Standard/Smart floor.

No score, script, occurrence label or review flag grants SRT writeback. Existing
canonical_splices is the only lexical materializer; gap timing stays review-only.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from lyric_aligner.evidence.editor import phonetic_form
from lyric_aligner.review.canonical_splices import (
    _bound, _index, _read, _require, cue_context, file_ref, load_floor_context,
)
from lyric_aligner.text.bijective_han import fold_unambiguous_han
from lyric_aligner.text_repair import _normalize_for_match
from lyric_aligner.timeline.anchor_repair import _cue_times

SCHEMA_VERSION = "safe-final-review-1.0"
LATIN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
HANGUL = re.compile(r"[\uac00-\ud7a3]")
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
GAP_CLASSES = {"missing_subtitle", "not_sung", "instrumental", "cut", "overlap",
               "occurrence_uncertain", "version_mismatch", "uncertain"}


def _comparison(text: str) -> str:
    # Strict bijective OpenCC pairs ONLY for comparison; never for display.
    return fold_unambiguous_han(_normalize_for_match(text))


def _mapping(row: dict, count: int) -> tuple[int, int] | None:
    span = row.get("canonical_span")
    if span is None:
        ci = row.get("canonical_ordinal")
        return None if ci is None else (_index(ci, count), ci + 1)
    _require(isinstance(span, list) and len(span) == 2, "invalid baseline canonical span")
    a, b = span
    _require(type(a) is int and type(b) is int and 0 <= a <= b <= count, "invalid baseline canonical span")
    return (a, b) if a < b else None


def build_candidates(plan_path: Path, *, final_mix: Path | None = None) -> dict[str, Any]:
    """Expose complete text/context and occurrence alternatives, without edits.

    A mapped line can be split across cues, so differing text is a review reason,
    not proof of error. Adjacent local lines are context, never a new mapping.
    """
    plan_ref = file_ref(plan_path)
    ctx = load_floor_context(plan_path)
    plan, cues, canonical, rows = ctx["plan"], ctx["cues"], ctx["canonical"], ctx["by_id"]
    original = ctx["report"].get("inputs", {}).get("canonical_lyrics")
    _require(isinstance(original, list) and bool(original), "baseline canonical lineage required for scanning")
    actual = [{"name": p.name, "sha256": file_ref(p)["sha256"]} for p in ctx["canonical_paths"]]
    _require(actual[:len(original)] == original, "baseline canonical order/version mismatch")
    base_count = sum(c.source_ordinal < len(original) for c in canonical)
    _require(ctx["report"].get("canonical_line_count") == base_count, "baseline canonical ordinal space mismatch")
    mappings = [_mapping(rows[i], base_count) for i in range(len(cues))]
    repeats = defaultdict(list)
    source_text = defaultdict(list)
    for c in canonical:
        repeats[(c.source_ordinal, _comparison(c.text))].append(c.ordinal)
        source_text[c.source_ordinal].append(c.text)
    sources_with_hangul = {s for s, texts in source_text.items() if HANGUL.search(" ".join(texts))}
    catalog = [{"canonical_ordinal": c.ordinal, "source_ordinal": c.source_ordinal,
                "source": c.source, "text": c.text,
                "same_source_equal_occurrences": repeats[(c.source_ordinal, _comparison(c.text))],
                "romanization_hint": phonetic_form("ko", c.text)[0] if HANGUL.search(c.text) else None}
               for c in canonical]
    lexical, controls, anchors = [], [], {}
    for i, cue in enumerate(cues):
        mapped = mappings[i]
        mapped_ids = list(range(*mapped)) if mapped else []
        # Immediate cue context only; do not search the entire song for a match.
        nearby = set(mapped_ids)
        for j in (i - 1, i + 1):
            if 0 <= j < len(cues) and mappings[j]:
                nearby.update(range(*mappings[j]))
        if mapped_ids:
            source = canonical[mapped_ids[0]].source_ordinal
            nearby = {j for j in nearby if canonical[j].source_ordinal == source}
            for j in (mapped_ids[0] - 1, mapped_ids[-1] + 1):
                if 0 <= j < base_count and canonical[j].source_ordinal == source:
                    nearby.add(j)
        candidate_ids = sorted(nearby)
        exact = bool(mapped_ids) and _comparison(cue.text) == _comparison(" ".join(canonical[j].text for j in mapped_ids))
        latin = [{"span": [m.start(), m.end()], "text": m.group()} for m in LATIN.finditer(cue.text)]
        reasons = []
        ko_context = any(canonical[j].source_ordinal in sources_with_hangul for j in candidate_ids)
        if rows[i].get("action") == "review":
            reasons.append("baseline_review")
        if latin and ko_context and not exact:
            reasons.append("korean_latin_requires_lexical_review")
        if (HAN.search(cue.text) or any(HAN.search(canonical[j].text) for j in mapped_ids)) and not exact:
            reasons.append("han_mismatch_or_editor_split")
        if not mapped:
            reasons.append("occurrence_unresolved")
        if reasons and any(len(catalog[j]["same_source_equal_occurrences"]) > 1 for j in mapped_ids):
            reasons.append("repeated_occurrence_requires_context")
        if exact:
            controls.append({"cue_ordinal": i, "canonical_span": list(mapped),
                             "reason": "comparison_equal_not_automatic_ownership_truth"})
        if reasons and (not exact or rows[i].get("action") == "review"):
            # A reviewed baseline mapping may be off by a line. Surface real
            # English from the complete local context, not just that bad claim.
            tokens = {m.group().casefold() for j in candidate_ids for m in LATIN.finditer(canonical[j].text)}
            for token in latin:
                token["canonical_english_token_present"] = token["text"].casefold() in tokens
            phonetic = []
            if latin and ko_context:
                observed = "".join(x["text"] for x in latin).casefold()
                for j in candidate_ids:
                    if HANGUL.search(canonical[j].text):
                        hint = catalog[j]["romanization_hint"]
                        phonetic.append({"canonical_ordinal": j, "romanization_hint": hint,
                                         "orthographic_similarity_only": round(SequenceMatcher(None, observed, hint, autojunk=False).ratio(), 4)})
            lexical.append({**cue_context(cues, i), "baseline_decision": rows[i], "reasons": reasons,
                            "candidate_canonical_ordinals": candidate_ids, "latin_spans": latin,
                            "phonetic_hints": phonetic,
                            "lexical_class": "unreviewed", "automatic_replacement_allowed": False})
        # Exact whole-line baseline anchors only. Split lines are not gap anchors.
        if (exact and rows[i].get("action") in {"replace", "unchanged", "keep"}
                and rows[i].get("cue_span") == [i, i + 1] and len(mapped_ids) == 1):
            anchors[i] = mapped_ids[0]
    times = [_cue_times(c) for c in cues]
    gaps = []
    for i in range(len(cues) - 1):
        if i not in anchors or i + 1 not in anchors:
            continue
        left, right = anchors[i], anchors[i + 1]
        a, b = times[i][1], times[i + 1][0]
        if a >= b or right <= left + 1:
            continue
        missing = list(range(left + 1, right))
        source = canonical[left].source_ordinal
        if any(canonical[j].source_ordinal != source for j in [*missing, right]):
            continue
        # A long earlier cue, out-of-order cue or another mapping may own the gap.
        if any(start < b and end > a for start, end in times):
            continue
        if any(span and span[0] < right and span[1] > left + 1 for span in mappings):
            continue
        gaps.append({"gap_id": f"gap-{i}-{i + 1}", "left": cue_context(cues, i)["current"],
                     "right": cue_context(cues, i + 1)["current"], "interval_ms": [a, b],
                     "canonical_span": [left + 1, right], "source_ordinal": source,
                     "segments": [{"canonical_ordinal": j, "span": [0, len(canonical[j].text)]} for j in missing],
                     "anchor_canonical_ordinals": [left, right],
                     "repeated_anchor": any(len(catalog[j]["same_source_equal_occurrences"]) > 1 for j in (left, right)),
                     "classification": "uncertain", "production_writeback_permitted": False})
    for reference in [plan_ref, plan["floor"], plan["smart_srt"], plan["smart_report"], *plan["canonical"]]:
        _bound(reference, plan_path.parent)
    return {"schema_version": SCHEMA_VERSION, "kind": "candidates", "plan": plan_ref,
            "floor": {"path": str(ctx["floor_path"]), "sha256": plan["floor"]["sha256"]},
            "final_mix": file_ref(final_mix) if final_mix else None,
            "canonical": catalog, "lexical_candidates": lexical, "equal_controls": controls,
            "gap_candidates": gaps, "cue_count": len(cues), "audio_decoded": False,
            "production_writeback_permitted": False,
            "limits": "Script/romanization/edit distance are routing hints, not semantic evidence. English/ad-libs, splits, repeats, cuts and versions need task review. Gaps cover only wholly uncovered lines between exact whole-line baseline anchors; no frontier or partial-line inference."}


def verify_gap_review(pack_path: Path, review_path: Path) -> dict[str, Any]:
    """Verify a complete task-bound review ledger, never insert a subtitle.

    Even a geometrically valid interval is not boundary authority. A future
    writer must ingest retained UI 3.2 raw timing truth, not these boolean flags.
    """
    pack_path, review_path = pack_path.resolve(), review_path.resolve()
    pack_ref, review_ref = file_ref(pack_path), file_ref(review_path)
    pack, review = _read(pack_path), _read(review_path)
    _require(pack.get("schema_version") == SCHEMA_VERSION and pack.get("kind") == "candidates", "unsupported candidate pack")
    plan_path = _bound(pack["plan"], pack_path.parent)
    audio = _bound(pack["final_mix"], pack_path.parent) if pack.get("final_mix") else None
    rebuilt = build_candidates(plan_path, final_mix=audio)
    # Dict equality would accept bool/int substitutions (False == 0).
    _require(json.dumps(pack, sort_keys=True, allow_nan=False) == json.dumps(rebuilt, sort_keys=True, allow_nan=False), "stale or modified candidate pack")
    _require(set(review) == {"schema_version", "kind", "pack_sha256", "reviewer", "decisions"}, "invalid gap review fields")
    _require(review["schema_version"] == SCHEMA_VERSION and review["kind"] == "gap-decisions", "unsupported gap review")
    _require(review["pack_sha256"] == file_ref(pack_path)["sha256"], "gap review belongs to another pack")
    _require(isinstance(review["reviewer"], str) and review["reviewer"].strip(), "gap reviewer required")
    decisions = review["decisions"]
    _require(isinstance(decisions, list) and all(isinstance(d, dict) for d in decisions), "gap decisions required")
    expected = {g["gap_id"]: g for g in pack["gap_candidates"]}
    seen, ledger = set(), []
    for d in decisions:
        _require(set(d) == {"gap_id", "classification", "text_confirmed", "ownership_confirmed", "reason", "timing", "evidence"}, "invalid gap decision fields; insertion authority is unavailable")
        gid = d["gap_id"]
        _require(isinstance(gid, str) and gid in expected and gid not in seen, "unknown or duplicate gap identity")
        seen.add(gid)
        _require(isinstance(d["classification"], str) and d["classification"] in GAP_CLASSES, "invalid gap classification")
        _require(type(d["text_confirmed"]) is bool and type(d["ownership_confirmed"]) is bool, "explicit boolean confirmations required")
        _require(isinstance(d["reason"], str) and d["reason"].strip(), "gap review reason required")
        _require(isinstance(d["evidence"], list), "gap evidence references required")
        evidence = [_bound(ref, review_path.parent) for ref in d["evidence"]]
        confirmed = d["text_confirmed"] and d["ownership_confirmed"]
        if d["text_confirmed"] or d["ownership_confirmed"]:
            _require(bool(evidence), "confirmed gap review requires retained evidence")
        timing = d["timing"]
        if timing is not None:
            _require(d["classification"] == "missing_subtitle" and confirmed and audio is not None, "timing needs confirmed text/ownership and frozen final mix")
            _require(isinstance(timing, dict) and set(timing) == {"start_ms", "end_ms", "basis", "observation"}, "invalid timing fields")
            start, end = timing["start_ms"], timing["end_ms"]
            a, b = expected[gid]["interval_ms"]
            _require(type(start) is int and type(end) is int and a <= start < end <= b, "timing would overlap/move existing floor")
            _require(timing["basis"] in {"human_final_mix", "source_to_mix_mapping", "local_asr_observation"}, "canonical timestamp is not mix timing evidence")
            observation = _bound(timing["observation"], review_path.parent)
            _require(observation in evidence, "timing observation missing from retained evidence")
        state = ("keep_floor" if d["classification"] != "missing_subtitle" else
                 "text_or_ownership_review" if not confirmed else
                 "timing_review" if timing is None else "timing_authority_unavailable")
        ledger.append({"gap_id": gid, "state": state, "classification": d["classification"],
                       "canonical_span": expected[gid]["canonical_span"], "timing_basis_recorded": timing is not None,
                       "production_writeback_permitted": False})
    _require(seen == set(expected), "incomplete gap review ledger")
    _bound(pack_ref, pack_path.parent)
    _bound(review_ref, review_path.parent)
    return {"schema_version": SCHEMA_VERSION, "kind": "gap-verification", "pack": pack_ref,
            "review": review_ref, "floor": pack["floor"], "contract_valid": True,
            "production_writeback_permitted": False, "subtitle_mutation_count": 0, "ledger": ledger}
