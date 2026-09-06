#!/usr/bin/env python3
"""Local directional-listening UI for the 24-clip production human-anchor pack.

A machine/legacy/editor candidate initializes each boundary.  UX 3.2 supports coarse
(0.5--2 s) moves, a local 10 ms timeline, direct millisecond entry, fine moves, and
configurable A/B context/pause before the human finishes with "too early / correct /
too late". Final uncertainty is represented by "clear / ambiguous" and converted by
ingest policy to 50/100 ms.

Machine consensus is optional input and is never written into selection.lock.json.
It is a listening aid only and can never become production authority without a
human save in this UI.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.v4_build_human_boundary_anchor_pack import (
    ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
    ANCHOR_PACK_SCHEMA_VERSIONS,
    ANCHOR_UI_REVISION,
    ANCHOR_UI_UX_REVISION,
    sha256_json,
)
from scripts.v4_ingest_human_boundary_anchor import _verify_lock


HOST = "127.0.0.1"
DEFAULT_PORT = 8765
CONSENSUS_SCHEMA_VERSION = "human-boundary-machine-consensus-1.0"
AUDIT_UX_REVISION = ANCHOR_UI_UX_REVISION
_LOCK = threading.Lock()


class AnchorAuditApp:
    def __init__(self, pack_dir: Path, *, consensus_path: Path | None = None):
        self.pack = pack_dir.resolve()
        self.outer_csv = self.pack / "outer" / "human_audit.csv"
        self.internal_csv = self.pack / "internal" / "human_audit.csv"
        self.recheck_json = self.pack / "HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json"
        self.consensus_path = consensus_path.resolve() if consensus_path else None
        self.consensus_payload: dict[str, Any] = {}
        self.consensus = self._load_consensus()
        self.lock = self._validate_pack_contract()

    @staticmethod
    def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise RuntimeError(f"CSV has no header: {path}")
            return list(reader.fieldnames), [dict(row) for row in reader]

    @staticmethod
    def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)

    @staticmethod
    def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def _read_recheck(self) -> dict[str, Any]:
        payload = json.loads(self.recheck_json.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise RuntimeError("anchor audit recheck sentinel must be an object")
        if payload.get("schema_version") != ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION:
            raise RuntimeError("unsupported anchor audit recheck schema")
        if str(payload.get("required_ui_revision") or "") != ANCHOR_UI_REVISION:
            raise RuntimeError("anchor audit recheck sentinel targets another UI revision")
        required_ux = str(payload.get("required_ui_ux_revision") or "").strip()
        if required_ux and required_ux != AUDIT_UX_REVISION:
            raise RuntimeError("anchor audit recheck sentinel targets another UI UX revision")
        pending = payload.get("pending_case_ids")
        confirmed = payload.get("confirmed_case_ids")
        if not isinstance(pending, list) or not isinstance(confirmed, list):
            raise RuntimeError("anchor audit recheck case lists are invalid")
        return payload

    def _load_consensus(self) -> dict[str, dict[str, Any]]:
        if self.consensus_path is None:
            return {}
        payload = json.loads(self.consensus_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version") != CONSENSUS_SCHEMA_VERSION:
            raise RuntimeError("unsupported machine-consensus artifact")
        if payload.get("authority") != "machine_candidate_only_never_human_gold":
            raise RuntimeError("machine-consensus authority marker is invalid")
        claimed = str(payload.get("artifact_sha256") or "")
        unsigned = dict(payload)
        unsigned.pop("artifact_sha256", None)
        if len(claimed) != 64 or claimed != sha256_json(unsigned):
            raise RuntimeError("machine-consensus artifact SHA is invalid")
        records = payload.get("records")
        if not isinstance(records, list) or int(payload.get("record_count") or -1) != len(records):
            raise RuntimeError("machine-consensus records are invalid")
        result: dict[str, dict[str, Any]] = {}
        for raw in records:
            if not isinstance(raw, Mapping):
                raise RuntimeError("machine-consensus record must be an object")
            record_id = str(raw.get("id") or "").strip()
            if not record_id or record_id in result:
                raise RuntimeError("machine-consensus IDs must be unique/non-empty")
            candidate = raw.get("candidate_ms")
            if candidate is not None and (isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0):
                raise RuntimeError("machine-consensus candidate_ms must be nonnegative integer or null")
            result[record_id] = dict(raw)
        self.consensus_payload = dict(payload)
        return result

    def _validate_pack_contract(self) -> dict[str, Any]:
        if not self.pack.is_dir():
            raise RuntimeError(f"anchor pack missing: {self.pack}")
        lock_path = self.pack / "selection.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
        if not isinstance(lock, dict) or lock.get("schema_version") not in ANCHOR_PACK_SCHEMA_VERSIONS:
            raise RuntimeError("unexpected anchor pack lock schema")
        claimed = str(lock.get("lock_sha256") or "")
        unsigned = dict(lock)
        unsigned.pop("lock_sha256", None)
        if len(claimed) != 64 or claimed != sha256_json(unsigned):
            raise RuntimeError("anchor pack lock SHA mismatch")
        _verify_lock(lock, lock_path)
        selection = lock["selection"]
        if self.consensus:
            source = lock.get("source_full_pack")
            if not isinstance(source, Mapping):
                raise RuntimeError("anchor pack source-full-pack provenance is missing")
            if str(self.consensus_payload.get("selection_lock_sha256") or "") != str(source.get("selection_lock_sha256") or ""):
                raise RuntimeError("machine consensus belongs to another locked benchmark")
            if str(self.consensus_payload.get("final_audio_sha256") or "") != str(lock["inputs"]["final_audio_sha256"]):
                raise RuntimeError("machine consensus belongs to another final mix")
        for purpose, path in (("outer", self.outer_csv), ("internal", self.internal_csv)):
            _, rows = self._read_csv(path)
            expected = [str(row["case_id"]) for row in selection["populations"][purpose]]
            actual = [str(row.get("case_id") or "") for row in rows]
            if actual != expected:
                raise RuntimeError(f"{purpose} anchor audit identity/order differs from lock")
            if self.consensus:
                kinds = ("start", "end") if purpose == "outer" else ("internal",)
                expected_record_ids = {f"{case_id}:{kind}" for case_id in expected for kind in kinds}
                if not expected_record_ids <= set(self.consensus):
                    raise RuntimeError("machine consensus does not cover every selected human anchor")
        return lock

    @staticmethod
    def _complete(population: str, row: Mapping[str, Any]) -> bool:
        if population == "outer":
            keys = ("gold_start_clip_ms", "gold_start_clarity", "gold_end_clip_ms", "gold_end_clarity")
        else:
            keys = ("gold_internal_clip_ms", "gold_internal_clarity")
        return all(str(row.get(key) or "").strip() for key in keys)

    def _candidate(
        self,
        population: str,
        row: Mapping[str, Any],
        kind: str,
        *,
        prefer_stored_human: bool = False,
    ) -> tuple[int, str, dict[str, Any] | None]:
        case_id = str(row["case_id"])
        record_id = f"{case_id}:{kind}"
        machine = self.consensus.get(record_id)
        clip_start = int(row["clip_start_ms"])
        clip_end = int(row["clip_end_ms"])
        duration = clip_end - clip_start
        if population == "outer":
            stored_key = "gold_start_clip_ms" if kind == "start" else "gold_end_clip_ms"
        else:
            stored_key = "gold_internal_clip_ms"
        stored = str(row.get(stored_key) or "").strip()
        if prefer_stored_human and stored:
            local = int(stored)
            if 0 <= local <= duration:
                return local, "confirmed_human_mark", None
        machine_status = str(machine.get("consensus_status") or "") if machine is not None else ""
        machine_direct_count = int(machine.get("direct_prediction_count") or 0) if machine is not None else 0
        use_machine = (
            machine is not None
            and machine.get("candidate_ms") is not None
            and machine_status != "disagreement"
            and not (population == "internal" and machine_direct_count == 0)
        )
        if use_machine:
            local = int(machine["candidate_ms"]) - clip_start
            if 0 <= local <= duration:
                return local, "machine_consensus", machine
        if stored:
            local = int(stored)
            if 0 <= local <= duration:
                return local, "legacy_or_saved_human_mark", None
        machine_fallback = machine if machine is not None else None
        if kind == "start":
            source = "editor_prior_machine_disagreement" if machine_status == "disagreement" else "editor_prior_fallback"
            return max(0, min(duration, int(row["editor_start_ms_reference_only"]) - clip_start)), source, machine_fallback
        if kind == "end":
            source = "editor_prior_machine_disagreement" if machine_status == "disagreement" else "editor_prior_fallback"
            return max(0, min(duration, int(row["editor_end_ms_reference_only"]) - clip_start)), source, machine_fallback
        editor_left = int(row["editor_start_ms_reference_only"]) - clip_start
        editor_right = int(row["editor_end_ms_reference_only"]) - clip_start
        source = (
            "editor_midpoint_machine_disagreement"
            if machine_status == "disagreement"
            else "editor_midpoint_no_semantic_machine_boundary"
            if machine is not None and machine_direct_count == 0
            else "editor_midpoint_fallback"
        )
        return max(0, min(duration, int(round((editor_left + editor_right) / 2)))), source, machine_fallback

    def _public_row(self, population: str, row: Mapping[str, str], *, pending: set[str]) -> dict[str, Any]:
        case_id = str(row["case_id"])
        stored_complete = self._complete(population, row)
        needs_recheck = case_id in pending
        result: dict[str, Any] = {
            "population": population,
            "case_id": case_id,
            "track": row["track"],
            "canonical_text": row["canonical_text"],
            "clip_relpath": row["clip_relpath"].replace("\\", "/"),
            "clip_duration_ms": int(row["clip_end_ms"]) - int(row["clip_start_ms"]),
            "editor_start_clip_ms": int(row["editor_start_ms_reference_only"]) - int(row["clip_start_ms"]),
            "editor_end_clip_ms": int(row["editor_end_ms_reference_only"]) - int(row["clip_start_ms"]),
            "segment_count": int(row["segment_count"]),
            "auditor_notes": row.get("auditor_notes", ""),
            "stored_complete": stored_complete,
            "needs_recheck": needs_recheck,
            "complete": stored_complete and not needs_recheck,
        }
        kinds = ("start", "end") if population == "outer" else ("internal",)
        for kind in kinds:
            candidate, source, machine = self._candidate(
                population,
                row,
                kind,
                prefer_stored_human=stored_complete and not needs_recheck,
            )
            result[f"{kind}_candidate_clip_ms"] = candidate
            result[f"{kind}_candidate_source"] = source
            result[f"{kind}_machine"] = machine
        if population == "outer":
            result.update(
                {
                    "gold_start_clip_ms": row.get("gold_start_clip_ms", ""),
                    "gold_start_clarity": row.get("gold_start_clarity", ""),
                    "gold_end_clip_ms": row.get("gold_end_clip_ms", ""),
                    "gold_end_clarity": row.get("gold_end_clarity", ""),
                }
            )
        else:
            result.update(
                {
                    "internal_boundary_index": int(row["internal_boundary_index"]),
                    "internal_left_text": row["internal_left_text"],
                    "internal_right_text": row["internal_right_text"],
                    "internal_owner_start_clip_ms": int(row["editor_start_ms_reference_only"]) - int(row["clip_start_ms"]),
                    "internal_owner_end_clip_ms": int(row["editor_end_ms_reference_only"]) - int(row["clip_start_ms"]),
                    "gold_internal_clip_ms": row.get("gold_internal_clip_ms", ""),
                    "gold_internal_clarity": row.get("gold_internal_clarity", ""),
                }
            )
        return result

    def state(self) -> dict[str, Any]:
        _, outer = self._read_csv(self.outer_csv)
        _, internal = self._read_csv(self.internal_csv)
        recheck = self._read_recheck()
        pending = {str(value) for value in recheck.get("pending_case_ids", [])}
        rows = [self._public_row("outer", row, pending=pending) for row in outer]
        rows.extend(self._public_row("internal", row, pending=pending) for row in internal)
        complete = sum(bool(row["complete"]) for row in rows)
        pack_match = re.search(r"_v(\d+)$", self.pack.name, flags=re.IGNORECASE)
        consensus_match = (
            re.search(r"_v(\d+)$", self.consensus_path.stem, flags=re.IGNORECASE)
            if self.consensus_path is not None
            else None
        )
        return {
            "ui_revision": ANCHOR_UI_REVISION,
            "ui_ux_revision": AUDIT_UX_REVISION,
            "workflow_revision": "V4 boundary-authority",
            "anchor_pack_revision": f"V{pack_match.group(1)}" if pack_match else self.pack.name,
            "machine_consensus_revision": (
                f"V{consensus_match.group(1)}" if consensus_match else "not loaded"
            ),
            "rows": rows,
            "complete": complete,
            "pending_recheck": sum(bool(row["needs_recheck"]) for row in rows),
            "remaining_unstarted": sum(not bool(row["stored_complete"]) for row in rows),
            "total": len(rows),
            "machine_consensus_loaded": bool(self.consensus),
        }

    @staticmethod
    def _parse_int(value: Any, *, name: str) -> int:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if number < 0:
            raise ValueError(f"{name} must be nonnegative")
        return number

    @staticmethod
    def _clarity(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text not in {"clear", "ambiguous"}:
            raise ValueError("clarity must be clear or ambiguous")
        return text

    def save(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if str(payload.get("ui_revision") or "") != ANCHOR_UI_REVISION:
            raise ValueError("audit page is stale; reopen the current anchor UI")
        population = str(payload.get("population") or "")
        if population not in {"outer", "internal"}:
            raise ValueError("invalid population")
        case_id = str(payload.get("case_id") or "").strip()
        reviewed_fields = payload.get("reviewed_fields")
        if not isinstance(reviewed_fields, list):
            raise ValueError("each boundary must be directionally reviewed before save")
        reviewed = {str(value or "").strip() for value in reviewed_fields}
        expected_reviewed = {"start", "end"} if population == "outer" else {"internal"}
        if reviewed != expected_reviewed:
            raise ValueError("every required boundary must finish directional A/B review")
        path = self.outer_csv if population == "outer" else self.internal_csv
        with _LOCK:
            fieldnames, rows = self._read_csv(path)
            matches = [row for row in rows if row.get("case_id") == case_id]
            if len(matches) != 1:
                raise ValueError("case_id is not uniquely present in anchor audit sheet")
            row = matches[0]
            duration = int(row["clip_end_ms"]) - int(row["clip_start_ms"])
            if population == "outer":
                start = self._parse_int(payload.get("gold_start_clip_ms"), name="start")
                end = self._parse_int(payload.get("gold_end_clip_ms"), name="end")
                if not 0 <= start < end <= duration:
                    raise ValueError("outer marks must satisfy 0 <= start < end <= clip duration")
                row["gold_start_clip_ms"] = str(start)
                row["gold_start_clarity"] = self._clarity(payload.get("gold_start_clarity"))
                row["gold_end_clip_ms"] = str(end)
                row["gold_end_clarity"] = self._clarity(payload.get("gold_end_clarity"))
            else:
                mark = self._parse_int(payload.get("gold_internal_clip_ms"), name="internal mark")
                if not 0 < mark < duration:
                    raise ValueError("internal mark must lie strictly inside the locked audit clip")
                row["gold_internal_clip_ms"] = str(mark)
                row["gold_internal_clarity"] = self._clarity(payload.get("gold_internal_clarity"))
            row["auditor_notes"] = str(payload.get("auditor_notes") or "").strip()
            self._atomic_write_csv(path, fieldnames, rows)

            recheck = self._read_recheck()
            pending = [str(value) for value in recheck.get("pending_case_ids", [])]
            confirmed = [str(value) for value in recheck.get("confirmed_case_ids", [])]
            if case_id in pending:
                pending = [value for value in pending if value != case_id]
            if case_id not in confirmed:
                confirmed.append(case_id)
            recheck["pending_case_ids"] = pending
            recheck["confirmed_case_ids"] = confirmed
            recheck["last_confirmed_ui_revision"] = ANCHOR_UI_REVISION
            recheck["last_confirmed_ui_ux_revision"] = AUDIT_UX_REVISION
            self._atomic_write_json(self.recheck_json, recheck)
        return self.state()


HTML = r'''<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>V4 Boundary Authority · Production human anchors · UI 3.2</title>
<style>
body{font-family:system-ui,Segoe UI,sans-serif;max-width:1180px;margin:20px auto;padding:0 18px;background:#111;color:#eee}button,select,textarea,input{font:inherit}button{padding:8px 11px;margin:3px}.card{background:#1b1b1b;border:1px solid #444;border-radius:10px;padding:18px}.muted{color:#aaa}.big{font-size:24px;line-height:1.5}.split{display:grid;grid-template-columns:1fr 1fr;gap:12px}.mark{background:#232323;padding:14px;border-radius:8px}.row{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.tag{padding:4px 8px;border:1px solid #666;border-radius:999px}.hidden{display:none}.ok{color:#8f8}.warn{color:#fc8}.bad{color:#f88}.decision button{min-width:112px}.correct{font-weight:700}.coarse button{min-width:70px}.timebox{margin:8px 0;padding:8px 10px;background:#191919;border-radius:7px;font-variant-numeric:tabular-nums}.clock{font-size:20px;font-weight:700}.timeline{width:100%;margin:8px 0}.timelineLabels{display:flex;justify-content:space-between;font-size:12px;color:#aaa;font-variant-numeric:tabular-nums}.numberInput{width:105px;background:#181818;color:#eee;border:1px solid #555;padding:6px}.prefs{margin:10px 0 14px;padding:9px;background:#181818;border-radius:8px}textarea{width:100%;min-height:55px;background:#222;color:#eee;border:1px solid #555}.pending{border-color:#a76}select{background:#222;color:#eee;padding:7px}.source{font-size:12px;color:#aaa}.step{font-variant-numeric:tabular-nums}.help{border-left:3px solid #666;padding-left:10px}.listen button{min-width:82px}@media(max-width:820px){.split{grid-template-columns:1fr}}
</style>
<h2>华语青春180 · V4 Boundary Authority · 人工边界复核 <span class="tag">UI 3.2</span></h2>
<p id="versionLine" class="muted">正在读取当前实例版本…</p>
<p class="muted">24 个 clip / 36 个边界。先用粗调把明显偏差拉回，再用局部时间轴/细调靠近，最后用 A/B 确认“偏早 / 基本正确 / 偏晚”。保存仍只认你最终确认的人耳边界。</p>
<div class="prefs row"><b>试听设置</b><label>点前/点后 <select id="contextMs"><option value="450">450ms</option><option value="700" selected>700ms</option><option value="1000">1000ms</option></select></label><label>中间停顿 <select id="pauseMs"><option value="400">400ms</option><option value="700" selected>700ms</option><option value="1000">1000ms</option></select></label><button onclick="setRate(.75)">0.75×</button><button onclick="setRate(1)">1×</button><span class="muted">推荐先用 700ms / 700ms；难听清时改成 1000ms。</span></div>
<div id="progress"></div>
<div class="card" id="card">
 <div class="row"><span id="kind" class="tag"></span><b id="track"></b><span id="status"></span></div>
 <div id="canonical" class="big"></div>
 <div id="internalTexts" class="split hidden"><div class="mark"><div class="muted">左段</div><div id="leftText"></div></div><div class="mark"><div class="muted">右段（找第一个真正唱出的音）</div><div id="rightText"></div></div></div>
 <audio id="audio" controls preload="metadata"></audio>
 <p class="muted help">开头/内部分界：理想状态是目标字的完整起音出现在停顿后。结尾：理想状态是最后一个字完整唱完后才进入停顿。明显差 0.5–2 秒时直接用粗调，不要连续点几十次“偏早/偏晚”。</p>
 <div id="outerMarks" class="split">
  <div class="mark" id="startMark"><b>开头：第一字/音素真正开始</b><div id="startSource" class="source"></div><div class="timebox"><span id="startClock" class="clock"></span> <span id="startEditorDelta" class="muted"></span></div><input id="startTimeline" class="timeline" type="range" step="10" oninput="timelineInput('start',this.value)" onchange="timelineCommit('start')"><div class="timelineLabels"><span id="startTimelineLeft"></span><span>局部 ±2 秒放大时间轴 · 10ms 步进</span><span id="startTimelineRight"></span></div><div class="row"><label>候选(ms) <input id="startNumber" class="numberInput" type="number" step="10"></label><button onclick="jumpFromInput('start')">跳到并试听</button></div><div class="row listen"><button onclick="playSide('start','A')">只听 A</button><button onclick="playSide('start','B')">只听 B</button><button onclick="replayMark('start',1)">A/B 一次</button><button onclick="replayMark('start',2)">A/B ×2</button><span id="startReview" class="warn">待判断</span></div><div class="row coarse"><b>粗调</b><button onclick="shiftMark('start',-2000)">-2000</button><button onclick="shiftMark('start',-1000)">-1000</button><button onclick="shiftMark('start',-500)">-500</button><button onclick="shiftMark('start',500)">+500</button><button onclick="shiftMark('start',1000)">+1000</button><button onclick="shiftMark('start',2000)">+2000</button></div><div class="row"><b>细调</b><select id="startFine"><option value="200">200ms</option><option value="100" selected>100ms</option><option value="50">50ms</option><option value="25">25ms</option><option value="10">10ms</option></select><button onclick="fineShift('start',-1)">← 向前</button><button onclick="fineShift('start',1)">向后 →</button><span id="startStep" class="step"></span></div><div class="decision"><button onclick="judge('start','early')">偏早 → 往后</button><button class="correct" onclick="judge('start','correct')">基本正确</button><button onclick="judge('start','late')">← 往前 偏晚</button></div><div>边界清晰度 <select id="startClarity"><option value="">选择</option><option value="clear">清晰</option><option value="ambiguous">模糊</option></select></div></div>
  <div class="mark" id="endMark"><b>结尾：最后一字/音素真正结束</b><div id="endSource" class="source"></div><div class="timebox"><span id="endClock" class="clock"></span> <span id="endEditorDelta" class="muted"></span></div><input id="endTimeline" class="timeline" type="range" step="10" oninput="timelineInput('end',this.value)" onchange="timelineCommit('end')"><div class="timelineLabels"><span id="endTimelineLeft"></span><span>局部 ±2 秒放大时间轴 · 10ms 步进</span><span id="endTimelineRight"></span></div><div class="row"><label>候选(ms) <input id="endNumber" class="numberInput" type="number" step="10"></label><button onclick="jumpFromInput('end')">跳到并试听</button></div><div class="row listen"><button onclick="playSide('end','A')">只听 A</button><button onclick="playSide('end','B')">只听 B</button><button onclick="replayMark('end',1)">A/B 一次</button><button onclick="replayMark('end',2)">A/B ×2</button><span id="endReview" class="warn">待判断</span></div><div class="row coarse"><b>粗调</b><button onclick="shiftMark('end',-2000)">-2000</button><button onclick="shiftMark('end',-1000)">-1000</button><button onclick="shiftMark('end',-500)">-500</button><button onclick="shiftMark('end',500)">+500</button><button onclick="shiftMark('end',1000)">+1000</button><button onclick="shiftMark('end',2000)">+2000</button></div><div class="row"><b>细调</b><select id="endFine"><option value="200">200ms</option><option value="100" selected>100ms</option><option value="50">50ms</option><option value="25">25ms</option><option value="10">10ms</option></select><button onclick="fineShift('end',-1)">← 向前</button><button onclick="fineShift('end',1)">向后 →</button><span id="endStep" class="step"></span></div><div class="decision"><button onclick="judge('end','early')">偏早 → 往后</button><button class="correct" onclick="judge('end','correct')">基本正确</button><button onclick="judge('end','late')">← 往前 偏晚</button></div><div>边界清晰度 <select id="endClarity"><option value="">选择</option><option value="clear">清晰</option><option value="ambiguous">模糊</option></select></div></div>
 </div>
 <div id="internalMark" class="mark hidden"><b>内部分界：右段第一个真正唱出的音</b><div class="source">editor cue 只作参考，不限制你的标点；以锁定 clip 中真实听到的起音为准。</div><div id="internalSource" class="source"></div><div class="timebox"><span id="internalClock" class="clock"></span> <span id="internalEditorDelta" class="muted"></span></div><input id="internalTimeline" class="timeline" type="range" step="10" oninput="timelineInput('internal',this.value)" onchange="timelineCommit('internal')"><div class="timelineLabels"><span id="internalTimelineLeft"></span><span>局部 ±2 秒放大时间轴 · 10ms 步进</span><span id="internalTimelineRight"></span></div><div class="row"><label>候选(ms) <input id="internalNumber" class="numberInput" type="number" step="10"></label><button onclick="jumpFromInput('internal')">跳到并试听</button></div><div class="row listen"><button onclick="playSide('internal','A')">只听 A</button><button onclick="playSide('internal','B')">只听 B</button><button onclick="replayMark('internal',1)">A/B 一次</button><button onclick="replayMark('internal',2)">A/B ×2</button><span id="internalReview" class="warn">待判断</span></div><div class="row coarse"><b>粗调</b><button onclick="shiftMark('internal',-2000)">-2000</button><button onclick="shiftMark('internal',-1000)">-1000</button><button onclick="shiftMark('internal',-500)">-500</button><button onclick="shiftMark('internal',500)">+500</button><button onclick="shiftMark('internal',1000)">+1000</button><button onclick="shiftMark('internal',2000)">+2000</button></div><div class="row"><b>细调</b><select id="internalFine"><option value="200">200ms</option><option value="100" selected>100ms</option><option value="50">50ms</option><option value="25">25ms</option><option value="10">10ms</option></select><button onclick="fineShift('internal',-1)">← 向前</button><button onclick="fineShift('internal',1)">向后 →</button><span id="internalStep" class="step"></span></div><div class="decision"><button onclick="judge('internal','early')">偏早 → 往后</button><button class="correct" onclick="judge('internal','correct')">基本正确</button><button onclick="judge('internal','late')">← 往前 偏晚</button></div><div>边界清晰度 <select id="internalClarity"><option value="">选择</option><option value="clear">清晰</option><option value="ambiguous">模糊</option></select></div></div>
 <p class="muted">“模糊”用于气声、连音、拖尾等边界本身难以精确切开的情况。它不是鼠标反应时间；如果边界确实没有唯一瞬间，就选“模糊”。</p>
 <p><label>备注（可空）</label><textarea id="notes"></textarea></p>
 <div class="row"><button onclick="prev()">上一条</button><button onclick="save(false)">保存</button><button class="correct" onclick="save(true)">保存并下一条</button><button onclick="nextUnfinished()">下一条未完成/待复核</button><span id="message"></span></div>
</div>
<script>
let state={rows:[]},idx=0,marks={},autoIndex={},heard=new Set(),sideHeard={},reviewed=new Set(),replay=null,replayFrame=0,replayTimer=0;
const autoLevels=[200,100,50,25],el=id=>document.getElementById(id),audio=el('audio');
function sourceLabel(x){return x==='confirmed_human_mark'?'已确认人耳标记':x==='machine_consensus'?'机器候选（仅辅助）':x==='legacy_or_saved_human_mark'?'已有标记候选（需当前UI确认）':x==='editor_prior_machine_disagreement'?'机器分歧较大 · 从编辑时间开始':x==='editor_midpoint_machine_disagreement'?'机器分歧较大 · 从编辑区间中点开始':x==='editor_midpoint_no_semantic_machine_boundary'?'机器无可用语义内部分界 · 从编辑区间中点开始':x==='editor_prior_fallback'?'编辑时间候选（仅兜底）':'编辑区间中点候选（仅兜底）'}
async function load(){const r=await fetch('/api/state',{cache:'no-store'});state=await r.json();if(state.ui_revision!=='3.0'||state.ui_ux_revision!=='3.2')throw Error('服务端 UI 版本不匹配');el('versionLine').textContent=`主流程 ${state.workflow_revision} · Anchor Pack ${state.anchor_pack_revision} · Machine Consensus ${state.machine_consensus_revision} · UI ${state.ui_ux_revision}`;const first=state.rows.findIndex(x=>!x.complete);idx=first>=0?first:0;render()}
function setRate(x){audio.playbackRate=x}
function kindsFor(r){return r.population==='outer'?['start','end']:['internal']}
function allowedBounds(which){const r=state.rows[idx],dur=Math.max(0,Number(r.clip_duration_ms)||0);let lo=0,hi=dur;if(r.population==='outer'&&which==='start')hi=Math.max(lo,marks.end-1);if(r.population==='outer'&&which==='end')lo=Math.min(hi,marks.start+1);if(which==='internal'){lo=Math.max(lo,1);hi=Math.min(hi,Math.max(1,dur-1))}return[lo,hi]}
function editorReference(which){const r=state.rows[idx];if(which==='start')return Number(r.editor_start_clip_ms);if(which==='end')return Number(r.editor_end_clip_ms);return Math.round((Number(r.internal_owner_start_clip_ms)+Number(r.internal_owner_end_clip_ms))/2)}
function formatMs(value){const ms=Math.max(0,Math.round(Number(value)||0)),m=Math.floor(ms/60000),s=Math.floor((ms%60000)/1000),rest=ms%1000;return`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(rest).padStart(3,'0')}`}
function signedMs(value){const n=Math.round(Number(value)||0);return`${n>=0?'+':'−'}${Math.abs(n)}ms`}
function setStatus(which){el(which+'Review').textContent=reviewed.has(which)?'已确认':'待判断';el(which+'Review').className=reviewed.has(which)?'ok':'warn';const step=autoLevels[Math.min(autoIndex[which]||0,autoLevels.length-1)];el(which+'Step').textContent=reviewed.has(which)?'':`“偏早/偏晚”自动步长 ${step}ms`}
function updateTimeLabels(which){const mark=Math.round(marks[which]);el(which+'Clock').textContent=`片段内 ${formatMs(mark)}`;el(which+'EditorDelta').textContent=`距编辑参考 ${signedMs(mark-editorReference(which))}`;el(which+'Number').value=String(mark)}
function updateMarkUI(which,recenter=true){const [lo,hi]=allowedBounds(which),mark=Math.max(lo,Math.min(hi,Math.round(marks[which])));marks[which]=mark;const slider=el(which+'Timeline');if(recenter){slider.min=String(Math.max(lo,mark-2000));slider.max=String(Math.min(hi,mark+2000))}slider.value=String(mark);el(which+'Number').min=String(lo);el(which+'Number').max=String(hi);el(which+'TimelineLeft').textContent=formatMs(Number(slider.min));el(which+'TimelineRight').textContent=formatMs(Number(slider.max));updateTimeLabels(which);setStatus(which)}
function render(){const r=state.rows[idx];heard=new Set();sideHeard={};reviewed=new Set();marks={};autoIndex={};cancelReplay();el('progress').innerHTML=`${idx+1}/${state.total} · <span class="ok">已完成 ${state.complete}</span> · <span class="warn">待复核 ${state.pending_recheck}</span> · 未开始 ${state.remaining_unstarted}`;el('kind').textContent=r.population==='outer'?'OUTER 起止':'INTERNAL 内部分界';el('track').textContent=r.track;el('status').innerHTML=r.needs_recheck?'<span class="warn">已有标记保留为候选 · 请重新复核</span>':(r.complete?'<span class="ok">已确认</span>':'<span class="warn">未完成</span>');el('card').classList.toggle('pending',!!r.needs_recheck);el('canonical').textContent=r.canonical_text;audio.pause();audio.src='/file?path='+encodeURIComponent(r.clip_relpath)+'&v=31';audio.load();const kinds=kindsFor(r);for(const k of kinds){const saved=Number(r['gold_'+k+'_clip_ms']);marks[k]=Number.isFinite(saved)&&String(r['gold_'+k+'_clip_ms']).trim()?Math.round(saved):Number(r[k+'_candidate_clip_ms']);autoIndex[k]=0;sideHeard[k]=new Set();el(k+'Source').textContent=sourceLabel(r[k+'_candidate_source']);el(k+'Clarity').value=r.needs_recheck?'':(r['gold_'+k+'_clarity']||'')}const outer=r.population==='outer';el('outerMarks').classList.toggle('hidden',!outer);el('internalMark').classList.toggle('hidden',outer);el('internalTexts').classList.toggle('hidden',outer);if(!outer){el('leftText').textContent=r.internal_left_text;el('rightText').textContent=r.internal_right_text}for(const k of kinds)updateMarkUI(k,true);el('notes').value=r.auditor_notes||'';el('message').textContent='';}
function invalidate(which){heard.delete(which);if(sideHeard[which])sideHeard[which].clear();reviewed.delete(which);setStatus(which)}
function moveMark(which,next,{autoplay=true,recenter=true}={}){const [lo,hi]=allowedBounds(which);if(hi<lo){el('message').textContent='候选无法继续移动：允许区间无效';return false}const clamped=Math.max(lo,Math.min(hi,Math.round(Number(next))));if(!Number.isFinite(clamped)){el('message').textContent='请输入有效的毫秒数';return false}if(clamped===marks[which]){el('message').textContent='候选已在该位置';return false}marks[which]=clamped;invalidate(which);updateMarkUI(which,recenter);if(autoplay)replayMark(which,1);return true}
function shiftMark(which,delta){if(moveMark(which,marks[which]+delta)){autoIndex[which]=0;setStatus(which);el('message').textContent=`已${delta<0?'向前':'向后'}粗调 ${Math.abs(delta)}ms，并自动 A/B 试听`}}
function fineShift(which,direction){const step=Number(el(which+'Fine').value)||100;if(moveMark(which,marks[which]+direction*step)){el('message').textContent=`已${direction<0?'向前':'向后'}细调 ${step}ms，并自动 A/B 试听`}}
function jumpFromInput(which){const value=Number(el(which+'Number').value);if(!Number.isFinite(value)){el('message').textContent='请输入有效毫秒数';return}moveMark(which,value)}
function timelineInput(which,value){const [lo,hi]=allowedBounds(which),next=Math.max(lo,Math.min(hi,Math.round(Number(value))));if(next!==marks[which]){marks[which]=next;invalidate(which)}updateTimeLabels(which)}
function timelineCommit(which){updateMarkUI(which,true);el('message').textContent='时间轴位置已更新，自动 A/B 试听';replayMark(which,1)}
function cancelReplay(){if(replayFrame)cancelAnimationFrame(replayFrame);if(replayTimer)clearTimeout(replayTimer);replayFrame=0;replayTimer=0;replay=null}
function finishSide(){if(!replay)return;const x=replay;cancelReplay();audio.pause();audio.currentTime=x.mark/1000;if(!sideHeard[x.which])sideHeard[x.which]=new Set();sideHeard[x.which].add(x.side);if(sideHeard[x.which].has('A')&&sideHeard[x.which].has('B'))heard.add(x.which);el('message').textContent=heard.has(x.which)?'A、B 两侧都听过了，可以判断偏早 / 基本正确 / 偏晚':`已听 ${x.side}，建议再听另一侧或完整 A/B`}
function finishAB(){if(!replay)return;const x=replay;cancelReplay();audio.pause();audio.currentTime=x.mark/1000;heard.add(x.which);el('message').textContent='已听完候选 A/B，可以判断偏早 / 基本正确 / 偏晚'}
function monitor(){if(!replay)return;const now=audio.currentTime*1000;if(replay.mode==='side'){if(now>=replay.stop-8){finishSide();return}}else if(replay.phase==='before'&&now>=replay.mark-8){audio.pause();audio.currentTime=replay.mark/1000;replay.phase='pause';const pause=Number(el('pauseMs').value)||700;replayTimer=setTimeout(()=>{replayTimer=0;if(!replay)return;replay.phase='after';audio.currentTime=replay.mark/1000;audio.play().then(()=>replayFrame=requestAnimationFrame(monitor)).catch(failReplay)},pause);return}else if(replay.phase==='after'&&now>=replay.stop-8){if(replay.repeatsLeft>1){replay.repeatsLeft--;audio.pause();replay.phase='gap';replayTimer=setTimeout(()=>{replayTimer=0;if(!replay)return;replay.phase='before';audio.currentTime=replay.start/1000;audio.play().then(()=>replayFrame=requestAnimationFrame(monitor)).catch(failReplay)},300);return}finishAB();return}replayFrame=requestAnimationFrame(monitor)}
function failReplay(e){cancelReplay();el('message').textContent='播放失败：'+e}
function playSide(which,side){cancelReplay();const r=state.rows[idx],mark=marks[which],dur=Number(r.clip_duration_ms),context=Number(el('contextMs').value)||700,start=side==='A'?Math.max(0,mark-context):mark,stop=side==='A'?mark:Math.min(dur,mark+context);replay={mode:'side',which,side,mark,stop};audio.pause();audio.currentTime=start/1000;audio.play().then(()=>replayFrame=requestAnimationFrame(monitor)).catch(failReplay)}
function replayMark(which,repeats=1){cancelReplay();const r=state.rows[idx],mark=marks[which],dur=Number(r.clip_duration_ms),context=Number(el('contextMs').value)||700,start=Math.max(0,mark-context),stop=Math.min(dur,mark+context);replay={mode:'ab',which,mark,start,stop,phase:'before',repeatsLeft:Math.max(1,Number(repeats)||1)};audio.pause();audio.currentTime=start/1000;audio.play().then(()=>replayFrame=requestAnimationFrame(monitor)).catch(failReplay)}
function judge(which,decision){if(!heard.has(which)){el('message').textContent='请先听完整 A/B，或分别听过 A 和 B';return}if(decision==='correct'){reviewed.add(which);setStatus(which);el('message').textContent='这个边界已确认；请选择清晰/模糊';return}const direction=decision==='early'?1:-1,index=Math.min(autoIndex[which]||0,autoLevels.length-1),step=autoLevels[index];if(moveMark(which,marks[which]+direction*step)){autoIndex[which]=Math.min(index+1,autoLevels.length-1);setStatus(which);el('message').textContent=decision==='early'?`候选向后 ${step}ms，自动复听`:`候选向前 ${step}ms，自动复听`}}
function prev(){idx=Math.max(0,idx-1);render()}function nextUnfinished(){for(let n=1;n<=state.rows.length;n++){const j=(idx+n)%state.rows.length;if(!state.rows[j].complete){idx=j;render();return}}el('message').textContent='全部完成'}
async function save(goNext){const r=state.rows[idx],required=kindsFor(r);if(required.some(x=>!reviewed.has(x))){el('message').textContent='保存被阻止：每个边界都要最终点一次“基本正确”';return}if(required.some(x=>!el(x+'Clarity').value)){el('message').textContent='保存被阻止：请选择清晰/模糊';return}const p={ui_revision:'3.0',population:r.population,case_id:r.case_id,auditor_notes:el('notes').value,reviewed_fields:required};if(r.population==='outer')Object.assign(p,{gold_start_clip_ms:marks.start,gold_start_clarity:el('startClarity').value,gold_end_clip_ms:marks.end,gold_end_clarity:el('endClarity').value});else Object.assign(p,{gold_internal_clip_ms:marks.internal,gold_internal_clarity:el('internalClarity').value});const resp=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const data=await resp.json();if(!resp.ok){el('message').textContent=data.error||'保存失败';return}state=data;if(goNext)nextUnfinished();else render()}
document.addEventListener('keydown',e=>{if(e.target.matches('textarea,select,input'))return;if(e.code==='Space'){e.preventDefault();audio.paused?audio.play():audio.pause()}if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();save(true)}});load().catch(e=>document.body.innerHTML='<h2 class="bad">审听工具启动失败</h2><pre>'+String(e)+'</pre>');
</script>'''


def _parse_http_byte_range(header: str, size: int) -> tuple[int, int] | None:
    value = str(header or "").strip()
    if not value:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or size <= 0:
        raise ValueError("unsupported byte range")
    left, right = match.groups()
    if not left and not right:
        raise ValueError("empty byte range")
    if left:
        start = int(left)
        end = int(right) if right else size - 1
    else:
        suffix = int(right)
        if suffix <= 0:
            raise ValueError("invalid suffix byte range")
        start = max(0, size - suffix)
        end = size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError("unsatisfiable byte range")
    return start, min(end, size - 1)


def make_handler(app: AnchorAuditApp):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: Any, status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _serve_wav(self, target: Path, *, head_only: bool = False) -> None:
            size = target.stat().st_size
            requested = self.headers.get("Range", "")
            try:
                byte_range = _parse_http_byte_range(requested, size) if requested else None
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return
            start, end = byte_range if byte_range is not None else (0, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT if byte_range is not None else HTTPStatus.OK
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            if byte_range is not None:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return
            with target.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    block = handle.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def _audio_target(self) -> Path | None:
            parsed = urlparse(self.path)
            rel = unquote(parse_qs(parsed.query).get("path", [""])[0])
            target = (app.pack / rel).resolve()
            try:
                target.relative_to(app.pack)
            except ValueError:
                return None
            if not target.is_file() or target.suffix.lower() != ".wav":
                return None
            return target

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                raw = HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            if parsed.path == "/api/state":
                self._json(app.state())
                return
            if parsed.path == "/file":
                target = self._audio_target()
                if target is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._serve_wav(target)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_HEAD(self) -> None:
            if urlparse(self.path).path != "/file":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            target = self._audio_target()
            if target is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._serve_wav(target, head_only=True)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/save":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
                self._json(app.save(payload))
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def _bind_server(app: AnchorAuditApp, port: int) -> ThreadingHTTPServer:
    try:
        return ThreadingHTTPServer((HOST, port), make_handler(app))
    except OSError as exc:
        raise RuntimeError(
            f"could not bind requested local audit port {port}; "
            "refusing silent fallback to another port so the review link cannot point at a stale UI"
        ) from exc


def check(app: AnchorAuditApp) -> int:
    state = app.state()
    print(
        json.dumps(
            {
                "ok": True,
                "ui_revision": ANCHOR_UI_REVISION,
                "ui_ux_revision": AUDIT_UX_REVISION,
                "pack": str(app.pack),
                "lock_sha256": app.lock["lock_sha256"],
                "complete": state["complete"],
                "pending_recheck": state["pending_recheck"],
                "remaining_unstarted": state["remaining_unstarted"],
                "clip_count": state["total"],
                "human_boundary_point_count": app.lock["summary"]["human_boundary_point_count"],
                "machine_consensus_loaded": state["machine_consensus_loaded"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--candidate-consensus", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    app = AnchorAuditApp(args.pack_dir, consensus_path=args.candidate_consensus)
    if args.check:
        return check(app)
    check(app)
    server = _bind_server(app, args.port)
    url = f"http://{HOST}:{args.port}/"
    print(f"Anchor audit UI {ANCHOR_UI_REVISION}: {url}")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
