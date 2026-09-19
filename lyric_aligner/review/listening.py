"""Task data and raw human responses for the sole UI 3.2 listening surface.

This store does not edit subtitles or grant production/Gold authority. Visible
subtitle comparison is source-label-blind, never candidate-text-blind dictation.
"""
from __future__ import annotations
import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

PACK_SCHEMA = "listening-review-pack-1.0"
RESPONSE_SCHEMA = "listening-review-response-1.0"
UI_REVISION = "3.2"
_LOCK = threading.Lock()
AXES = ("lexical", "ownership", "timing")
SOURCE_ROLES = frozenset({"baseline", "candidate"})
LEGACY_SOURCE_ROLE_ALIASES = {"safe": "baseline", "balanced": "candidate"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_ref(path):
    p = Path(path).resolve()
    return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def require(ok, message):
    if not ok:
        raise ValueError(message)


class ListeningReviewApp:
    def __init__(self, directory):
        self.pack = Path(directory).resolve()
        self.path = self.pack / "review.pack.json"
        self.manifest = read(self.path)
        self.identity = self.manifest.get("pack_sha256")
        self.verify()

    def verify(self):
        m = read(self.path)
        require(m.get("schema_version") == PACK_SCHEMA and m.get("ui_revision") == UI_REVISION, "unsupported review pack/UI")
        require(m.get("pack_sha256") == self.identity == digest({k: v for k, v in m.items() if k != "pack_sha256"}), "review pack changed")
        require(m.get("partition") in {"development", "calibration", "blind", "holdout", "regression"}, "partition must be frozen before review")
        require(m.get("source_labels_hidden") is True, "comparison requires hidden source labels")
        require(bool(m.get("cases")), "empty review pack")
        ids = set()
        for c in m["cases"]:
            require(isinstance(c["id"], str) and c["id"] and c["id"] not in ids, "duplicate/invalid case")
            ids.add(c["id"])
            require(c["language"] in {"ko", "ja", "en", "zh", "mixed", "unknown"}, "language required")
            duration = c["duration_ms"]
            require(type(duration) is int and duration > 0, "invalid duration")
            require(len(c["focus_ms"]) == 2 and all(type(x) is int for x in c["focus_ms"]) and 0 <= c["focus_ms"][0] < c["focus_ms"][1] <= duration, "invalid listening focus")
            options = c["options"]
            require(1 <= len(options) <= 2 and [o["id"] for o in options] == [f"v{i+1}" for i in range(len(options))], "invalid option identity")
            for o in options:
                require(set(o) == {"id", "cues"} and bool(o["cues"]), "options contain private labels or no subtitles")
                for cue in o["cues"]:
                    require(set(cue) == {"start_ms", "end_ms", "text"} and isinstance(cue["text"], str), "invalid displayed subtitle")
                    require(type(cue["start_ms"]) is int and type(cue["end_ms"]) is int and 0 <= cue["start_ms"] < cue["end_ms"] <= duration, "subtitle outside clip")
            audio = (self.pack / c["audio"]["path"]).resolve()
            require(audio.is_relative_to(self.pack) and audio.suffix.lower() == ".wav", "audio must be a local pack WAV")
            require(file_ref(audio)["sha256"] == c["audio"]["sha256"], "audio changed")
        for r in m.get("input_bindings", []):
            require(file_ref(self.pack / r["path"])["sha256"] == r["sha256"], "bound input changed")
        self.manifest = m

    def responses(self):
        rows = []
        for p in sorted((self.pack / "responses").glob("*.json")):
            r = read(p)
            require(r.get("schema_version") == RESPONSE_SCHEMA and r.get("pack_sha256") == self.identity, "foreign response")
            self.validate(r["response"])
            rows.append(r)
        return rows

    def state(self):
        saved = {}
        for r in self.responses():
            saved[r["response"]["case_id"]] = r["response"]
        rows = []
        for c in self.manifest["cases"]:
            a, b = c["focus_ms"]
            response = saved.get(c["id"], {})
            rows.append({"case_id": c["id"], "population": "outer", "track": c["id"],
                         "canonical_text": "", "clip_relpath": c["audio"]["path"], "clip_duration_ms": c["duration_ms"],
                         "editor_start_clip_ms": a, "editor_end_clip_ms": b,
                         "start_candidate_clip_ms": a, "end_candidate_clip_ms": b,
                         "start_candidate_source": "listening_reference_only", "end_candidate_source": "listening_reference_only",
                         "gold_start_clip_ms": "", "gold_end_clip_ms": "", "gold_start_clarity": "", "gold_end_clarity": "",
                         "segment_count": 1, "auditor_notes": response.get("notes", ""),
                         "complete": bool(response), "stored_complete": bool(response), "needs_recheck": False,
                         "options": c["options"], "language": c["language"], "response": response,
                         "focus_ms": c["focus_ms"],
                         "review_question": c.get("question", "逐版判断歌词、归属与明显时间错误；无法判断就选不确定。")})
        return {"ui_revision": "3.0", "ui_ux_revision": UI_REVISION, "review_mode": "subtitle_comparison",
                "pack_sha256": self.identity, "title": self.manifest["title"], "partition": self.manifest["partition"],
                "workflow_revision": "字幕人工复核", "anchor_pack_revision": "已冻结复核包",
                "machine_consensus_revision": "版本来源隐藏", "rows": rows, "total": len(rows), "complete": len(saved),
                "remaining_unstarted": len(rows)-len(saved), "pending_recheck": 0, "machine_consensus_loaded": False,
                "candidate_text_blind": False, "production_authority": False}

    def validate(self, p):
        require(set(p) == {"pack_sha256", "case_id", "reviewer", "language_proficiency", "prior_exposure", "audio_listened", "ratings", "notes"}, "invalid response fields")
        require(p["pack_sha256"] == self.identity, "stale review page")
        cases = {c["id"]: c for c in self.manifest["cases"]}
        require(p["case_id"] in cases, "unknown case")
        require(isinstance(p["reviewer"], str) and bool(p["reviewer"].strip()), "请填写听审者")
        require(p["language_proficiency"] in {"fluent", "working", "limited", "none"}, "请选择该语种理解程度")
        require(p["prior_exposure"] in {"unseen", "seen", "unsure"}, "请选择是否看过本例结果")
        require(p["audio_listened"] is True, "请实际试听后确认")
        require(isinstance(p["notes"], str), "invalid notes")
        ratings = p["ratings"]
        require(isinstance(ratings, list) and [r.get("id") for r in ratings] == [o["id"] for o in cases[p["case_id"]]["options"]], "incomplete/duplicate option judgments")
        for r in ratings:
            require(set(r) == {"id", *AXES} and all(r[k] in {"correct", "wrong", "uncertain"} for k in AXES), "每版都需判断歌词、归属和时间；听不清可选不确定")
        return p

    def save(self, payload):
        with _LOCK:
            self.verify()
            self.validate(payload)
            folder = self.pack / "responses"
            folder.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            receipt = {"schema_version": RESPONSE_SCHEMA, "ui_revision": UI_REVISION,
                       "pack_sha256": self.identity, "saved_at": stamp, "response": dict(payload),
                       "authority": "raw_human_response_not_automatic_gold_or_production", "candidate_text_blind": False}
            with (folder / (stamp + "-" + uuid.uuid4().hex + ".json")).open("x", encoding="utf-8") as f:
                json.dump(receipt, f, ensure_ascii=False, indent=2)
        return self.state()


def evaluate_review(directory):
    """Descriptive paired metrics only; reuse the existing strict promotion workflow.

    Unit = frozen listening region, not a cue invented by either renderer.
    Repeated saves by one listener count once. Disagreeing qualified listeners
    exclude that region instead of a fabricated majority/automatic Gold label.
    """
    app = ListeningReviewApp(directory)
    selection_path = app.pack / "selection.private.json"
    require(file_ref(selection_path)['sha256'] == app.manifest['selection_sha256'], 'private selection changed')
    selection = read(selection_path)
    require(selection['partition'] == app.manifest['partition'], 'selection partition changed')
    source_by_id = {c['id']: c['variant_sources'] for c in selection['cases']}
    require(set(source_by_id) == {c['id'] for c in app.manifest['cases']}, 'selection coverage differs')
    latest = {}
    for r in app.responses():
        p = r['response']
        latest[p['case_id'], p['reviewer'].strip()] = p
    grouped = {}
    excluded = []
    for (case_id, reviewer), p in latest.items():
        if p['language_proficiency'] != 'fluent':
            excluded.append({'case_id': case_id, 'reason': 'language_proficiency_not_fluent'})
            continue
        if app.manifest['partition'] in {'blind', 'holdout'} and p['prior_exposure'] != 'unseen':
            excluded.append({'case_id': case_id, 'reason': 'prior_exposure_or_unknown'})
            continue
        normalized = {}
        for r in p['ratings']:
            for raw_source in source_by_id[case_id][r['id']]:
                source = LEGACY_SOURCE_ROLE_ALIASES.get(raw_source, raw_source)
                require(source in SOURCE_ROLES and source not in normalized, 'ambiguous source map')
                normalized[source] = {axis: r[axis] for axis in AXES}
        require(set(normalized) == SOURCE_ROLES, 'paired source mapping incomplete')
        grouped.setdefault(case_id, []).append(normalized)
    pairs = []
    for case_id, values in grouped.items():
        if any(v != values[0] for v in values[1:]):
            excluded.append({'case_id': case_id, 'reason': 'qualified_reviewers_disagree'})
        else:
            pairs.append({'case_id': case_id, **values[0]})
    complete = [p for p in pairs if all(p[s][a] != 'uncertain' for s in ('baseline','candidate') for a in AXES)]
    def correct(row, source):
        return all(row[source][a] == 'correct' for a in AXES)
    bad_before = [p for p in complete if not correct(p,'baseline')]
    good_before = [p for p in complete if correct(p,'baseline')]
    repaired = sum(correct(p,'candidate') for p in bad_before)
    introduced = sum(not correct(p,'candidate') for p in good_before)
    def rate(n, d): return n/d if d else None
    per_axis = {}
    for axis in AXES:
        per_axis[axis] = {}
        for source in ('baseline','candidate'):
            resolved = [p for p in pairs if all(p[s][axis] != 'uncertain' for s in ('baseline', 'candidate'))]
            n = sum(p[source][axis] == 'wrong' for p in resolved)
            per_axis[axis][source] = {'wrong': n, 'assessed': len(resolved), 'rate': rate(n,len(resolved))}
    return {'schema_version':'listening-review-metrics-1.0','pack_sha256':app.identity,'partition':app.manifest['partition'],
            'unit':'frozen_listening_region_not_output_cue','sampling':selection.get('sampling'),
            'population_regions':len(app.manifest['cases']),'saved_listener_case_pairs':len(latest),
            'fully_assessed_regions':len(complete),'qualified_pairs_with_possible_unknown_axes':len(pairs),
            'repaired_regions':repaired,'baseline_wrong_assessed':len(bad_before),'repair_rate':rate(repaired,len(bad_before)),
            'introduced_error_regions':introduced,'baseline_correct_assessed':len(good_before),'introduced_error_rate':rate(introduced,len(good_before)),
            'net_new_correct_regions':repaired-introduced if complete else None,'per_axis':per_axis,'excluded':excluded,
            'candidate_text_blind':False,'source_labels_hidden':True,'human_repair_minutes_saved':None,
            'production_authority_granted':False,'new_untouched_corpus_verified':selection.get('new_untouched_corpus_verified',False),
            'promotion_workflow':'scripts/v4_calibration_workflow.py; this descriptive report creates no alternative PASS gate'}
