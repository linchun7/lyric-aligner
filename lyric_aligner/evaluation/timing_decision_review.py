"""Create and ingest blinded human review material for a frozen timing-decision pack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from lyric_aligner.evaluation.timing_decision_pack import (
    GOLD_SCHEMA_VERSION,
    SCHEMA_VERSION,
    verify_selection_lock,
)

REVIEW_SCHEMA_VERSION = "timing-decision-review-manifest-1.0"
RESPONSE_SCHEMA_VERSION = "timing-decision-review-response-1.0"
REVIEW_PARTITIONS = frozenset({"development", "calibration", "blind", "holdout", "regression"})


def _stable_sha(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: Any, *, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{label} must be a SHA-256 hex string")
    return text


def build_review_manifest(
    pack: Mapping[str, Any],
    *,
    clip_dir_name: str = "clips",
    boundary_promotion_selection_sha256: str | None = None,
    boundary_promotion_partition: str | None = None,
) -> dict[str, Any]:
    lock = verify_selection_lock(pack)
    if pack.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported timing decision pack schema")
    has_promotion_selection = boundary_promotion_selection_sha256 is not None
    has_promotion_partition = boundary_promotion_partition is not None
    if has_promotion_selection != has_promotion_partition:
        raise ValueError(
            "boundary promotion selection SHA and partition must be provided together"
        )
    if boundary_promotion_partition is not None and boundary_promotion_partition not in REVIEW_PARTITIONS:
        raise ValueError("boundary promotion partition is invalid")
    cases = pack.get("cases")
    if not isinstance(cases, list):
        raise ValueError("timing decision pack cases must be a list")
    review_cases: list[dict[str, Any]] = []
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise ValueError("timing decision pack contains non-object case")
        case_id = raw.get("id")
        target_text = raw.get("target_text")
        boundary_kind = raw.get("boundary_kind")
        clip_start_ms = raw.get("clip_start_ms")
        clip_end_ms = raw.get("clip_end_ms")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("timing decision pack case is missing id")
        if not isinstance(target_text, str) or not target_text:
            raise ValueError("timing decision pack case is missing target_text")
        if boundary_kind not in {"start", "end", "internal"}:
            raise ValueError("timing decision pack case has invalid boundary_kind")
        if (
            isinstance(clip_start_ms, bool)
            or not isinstance(clip_start_ms, int)
            or isinstance(clip_end_ms, bool)
            or not isinstance(clip_end_ms, int)
            or clip_start_ms < 0
            or clip_end_ms <= clip_start_ms
        ):
            raise ValueError("timing decision pack case has invalid clip window")
        clip_name = f"{case_id}.wav"
        review_cases.append(
            {
                "id": case_id,
                "target_text": target_text,
                "boundary_kind": boundary_kind,
                "clip_file": f"{clip_dir_name}/{clip_name}",
                "clip_start_ms": clip_start_ms,
                "clip_duration_ms": clip_end_ms - clip_start_ms,
                "instructions": (
                    "Mark the audible lyric boundary in this clip. Enter milliseconds from clip start; "
                    "do not infer from subtitle candidates."
                ),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "selection_lock_sha256": lock,
        "final_mix_sha256": pack.get("final_mix_sha256"),
        "candidate_positions_hidden": True,
        "gold_hidden_during_selection": True,
        "case_count": len(review_cases),
        "cases": review_cases,
    }
    if boundary_promotion_selection_sha256 is not None:
        manifest["boundary_promotion_selection_sha256"] = _sha256_text(
            boundary_promotion_selection_sha256,
            label="boundary_promotion_selection_sha256",
        )
        manifest["boundary_promotion_partition"] = boundary_promotion_partition
    manifest["manifest_sha256"] = _stable_sha(manifest)
    return manifest


def validate_review_response(
    manifest: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    partition: str,
) -> dict[str, Any]:
    if manifest.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError("review manifest schema mismatch")
    expected_manifest_sha = manifest.get("manifest_sha256")
    bare = dict(manifest)
    bare.pop("manifest_sha256", None)
    if expected_manifest_sha != _stable_sha(bare):
        raise ValueError("review manifest hash mismatch")
    boundary_promotion_selection_sha256 = manifest.get("boundary_promotion_selection_sha256")
    boundary_promotion_partition = manifest.get("boundary_promotion_partition")
    has_promotion_selection = boundary_promotion_selection_sha256 is not None
    has_promotion_partition = boundary_promotion_partition is not None
    if has_promotion_selection != has_promotion_partition:
        raise ValueError("review manifest boundary promotion binding is incomplete")
    if boundary_promotion_selection_sha256 is not None:
        boundary_promotion_selection_sha256 = _sha256_text(
            boundary_promotion_selection_sha256,
            label="boundary_promotion_selection_sha256",
        )
        if boundary_promotion_partition not in REVIEW_PARTITIONS:
            raise ValueError("review manifest boundary promotion partition is invalid")
    if response.get("schema_version") != RESPONSE_SCHEMA_VERSION:
        raise ValueError("review response schema mismatch")
    if response.get("manifest_sha256") != expected_manifest_sha:
        raise ValueError("review response belongs to another manifest")
    if partition not in REVIEW_PARTITIONS:
        raise ValueError("review partition is invalid")
    if boundary_promotion_partition is not None and partition != boundary_promotion_partition:
        raise ValueError("review partition differs from frozen boundary promotion partition")
    raw_cases = manifest.get("cases")
    raw_records = response.get("records")
    if not isinstance(raw_cases, list) or not isinstance(raw_records, list):
        raise ValueError("review cases/records must be lists")
    case_by_id = {str(case["id"]): case for case in raw_cases}
    if len(case_by_id) != len(raw_cases):
        raise ValueError("review manifest has duplicate case ids")
    record_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            raise ValueError("review response contains non-object record")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or case_id not in case_by_id or case_id in record_by_id:
            raise ValueError("review response has unknown or duplicate case id")
        status = str(raw.get("status") or "valid").strip().lower()
        if status not in {"valid", "invalid"}:
            raise ValueError("review response status must be valid or invalid")
        if status == "invalid":
            reason = str(raw.get("invalid_reason") or "").strip()
            if not reason:
                raise ValueError("invalid review response requires invalid_reason")
            record_by_id[case_id] = {"status": "invalid", "invalid_reason": reason}
            continue
        relative_ms = raw.get("relative_ms")
        uncertainty_ms = raw.get("uncertainty_ms", 0)
        if isinstance(relative_ms, bool) or not isinstance(relative_ms, int):
            raise ValueError("relative_ms must be an integer")
        if isinstance(uncertainty_ms, bool) or not isinstance(uncertainty_ms, int) or uncertainty_ms < 0:
            raise ValueError("uncertainty_ms must be a nonnegative integer")
        case = case_by_id[case_id]
        if not 0 <= relative_ms <= int(case["clip_duration_ms"]):
            raise ValueError("relative_ms lies outside the review clip")
        record_by_id[case_id] = {
            "status": "valid",
            "relative_ms": relative_ms,
            "uncertainty_ms": uncertainty_ms,
        }
    if set(record_by_id) != set(case_by_id):
        raise ValueError("review response is incomplete")
    valid_ids = sorted(case_id for case_id, item in record_by_id.items() if item["status"] == "valid")
    invalid_ids = sorted(case_id for case_id, item in record_by_id.items() if item["status"] == "invalid")
    gold: dict[str, Any] = {
        "schema_version": GOLD_SCHEMA_VERSION,
        "selection_lock_sha256": manifest["selection_lock_sha256"],
        "partition": partition,
        "review_manifest_sha256": expected_manifest_sha,
        "population_count": len(case_by_id),
        "valid_count": len(valid_ids),
        "invalid_count": len(invalid_ids),
        "records": [
            {
                "id": case_id,
                "gold_ms": int(case_by_id[case_id]["clip_start_ms"]) + int(record_by_id[case_id]["relative_ms"]),
                "uncertainty_ms": int(record_by_id[case_id]["uncertainty_ms"]),
            }
            for case_id in valid_ids
        ],
        "invalid_records": [
            {
                "id": case_id,
                "reason": str(record_by_id[case_id]["invalid_reason"]),
            }
            for case_id in invalid_ids
        ],
    }
    if boundary_promotion_selection_sha256 is not None:
        gold["boundary_promotion_selection_sha256"] = boundary_promotion_selection_sha256
        gold["boundary_promotion_partition"] = boundary_promotion_partition
    return gold


def render_review_html(manifest: Mapping[str, Any]) -> str:
    """Return a standalone, candidate-blind review UI using only manifest fields."""
    payload = json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Timing Decision Blind Review</title>
<style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px}}.case{{border:1px solid #ccc;border-radius:8px;padding:14px;margin:12px 0}}audio{{width:100%}}input{{width:140px}}.reason{{width:360px}}.meta{{color:#555}}button,select{{padding:8px 12px}}</style></head><body>
<h1>Timing Decision Blind Review</h1><p>只按音频标记歌词边界。页面不显示 old/hybrid 候选位置；确实无法判定时请选择“无效/不可判”，不要猜。</p><div id=\"cases\"></div><button id=\"export\">导出 JSON</button>
<script>const M={payload};const root=document.getElementById('cases');
for(const c of M.cases){{const d=document.createElement('div');d.className='case';d.dataset.id=c.id;d.innerHTML=`<b>${{c.boundary_kind.toUpperCase()}}</b> · <span>${{c.target_text}}</span><p class=\"meta\">片段 ${{c.clip_duration_ms}} ms</p><audio controls preload=\"none\" src=\"${{c.clip_file}}\"></audio><p>状态：<select class=\"status\"><option value=\"valid\">可判</option><option value=\"invalid\">无效/不可判</option></select></p><p>边界（相对片段起点 ms）：<input class=\"pos\" type=\"number\" min=\"0\" max=\"${{c.clip_duration_ms}}\"> 不确定度 ±ms：<input class=\"unc\" type=\"number\" min=\"0\" value=\"50\"></p><p>无效原因：<input class=\"reason\" placeholder=\"如：重复句无法区分 / 目标边界不在片段内\"></p>`;root.appendChild(d)}}
document.getElementById('export').onclick=()=>{{const records=[];for(const d of root.querySelectorAll('.case')){{const status=d.querySelector('.status').value;if(status==='invalid'){{const reason=d.querySelector('.reason').value.trim();if(!reason){{alert('无效 case 必须填写原因');return}}records.push({{id:d.dataset.id,status:'invalid',invalid_reason:reason}});continue}}const p=d.querySelector('.pos').value;if(p===''){{alert('仍有可判 case 未标边界');return}}records.push({{id:d.dataset.id,status:'valid',relative_ms:Number(p),uncertainty_ms:Number(d.querySelector('.unc').value||0)}})}}const out={{schema_version:'timing-decision-review-response-1.0',manifest_sha256:M.manifest_sha256,records}};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{{type:'application/json'}}));a.download='review-response.json';a.click()}};</script></body></html>"""
