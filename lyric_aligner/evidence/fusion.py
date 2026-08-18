"""Uncalibrated shadow fusion of independent lyric timing evidence families.

This layer is diagnostic only. It never changes canonical text or canonical
Source-to-Mix timing, and its LOW/MEDIUM/HIGH/CONFLICT labels are *shadow
support states*, not calibrated release confidence.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable


FUSION_SCHEMA_VERSION = "1.1"
FUSION_POLICY_ID = "evidence-fusion-shadow-2026-08-18-v2-forced"


class EvidenceFusionError(ValueError):
    """Raised when evidence cannot be bound to the same canonical line truth."""


@dataclass(frozen=True)
class EvidenceFusionConfig:
    conflict_boundary_ms: int = 500

    def validate(self) -> None:
        if self.conflict_boundary_ms < 0:
            raise EvidenceFusionError("conflict_boundary_ms must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _int_or_none(value: Any, *, label: str) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceFusionError(f"{label} is invalid") from exc
    if not math.isfinite(number):
        raise EvidenceFusionError(f"{label} must be finite")
    return int(round(number))


def _timeline_index(
    timeline_payloads: Iterable[dict[str, Any]],
) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]]]:
    lines: dict[tuple[str, int], dict[str, Any]] = {}
    occurrences: list[dict[str, Any]] = []
    for payload in timeline_payloads:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise EvidenceFusionError("canonical timeline has no result")
        occurrence_id = str(result.get("occurrence_id") or payload.get("occurrence_id") or "")
        track_id = str(result.get("track_id") or payload.get("track_id") or "")
        if not occurrence_id or not track_id:
            raise EvidenceFusionError("canonical timeline lacks occurrence/track identity")
        rows = result.get("lines")
        if not isinstance(rows, list):
            raise EvidenceFusionError("canonical timeline lines must be a list")
        occurrences.append(
            {
                "occurrence_id": occurrence_id,
                "track_id": track_id,
                "ordinal": int(result.get("ordinal", -1)),
                "language_profile": str(result.get("language_profile") or "auto"),
                "canonical_selection_sha256": str(result.get("canonical_selection_sha256") or ""),
            }
        )
        for row in rows:
            if not isinstance(row, dict):
                raise EvidenceFusionError("canonical timeline line must be an object")
            try:
                line_index = int(row["canonical_line_index"])
                start_ms = int(row["mix_start_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise EvidenceFusionError("canonical timeline line identity/timing is invalid") from exc
            key = (occurrence_id, line_index)
            if key in lines:
                raise EvidenceFusionError(
                    f"duplicate canonical line {occurrence_id}/{line_index}"
                )
            lines[key] = {
                "occurrence_id": occurrence_id,
                "track_id": track_id,
                "ordinal": int(result.get("ordinal", -1)),
                "language_profile": str(result.get("language_profile") or "auto"),
                "canonical_selection_sha256": str(result.get("canonical_selection_sha256") or ""),
                "canonical_line_index": line_index,
                "canonical_text_sha256": _sha(str(row.get("text") or "")),
                "source_start_ms": _int_or_none(row.get("source_start_ms"), label="source_start_ms"),
                "source_end_ms": _int_or_none(row.get("source_end_ms"), label="source_end_ms"),
                "mix_start_ms": start_ms,
                "mix_end_ms": _int_or_none(row.get("mix_end_ms"), label="mix_end_ms"),
            }
    occurrences.sort(key=lambda row: (row["ordinal"], row["occurrence_id"]))
    if not lines:
        raise EvidenceFusionError("fusion requires at least one canonical line")
    return lines, occurrences


def _editor_index(editor_evidence: dict[str, Any] | None) -> dict[tuple[str, int], dict[str, Any]]:
    if editor_evidence is None:
        return {}
    if editor_evidence.get("mode") != "shadow_only":
        raise EvidenceFusionError("editor evidence must be shadow_only")
    if editor_evidence.get("authority", {}).get("automatic_timing_change_allowed") is not False:
        raise EvidenceFusionError("editor evidence unexpectedly allows timing mutation")
    output: dict[tuple[str, int], dict[str, Any]] = {}
    occurrences = editor_evidence.get("occurrences")
    if not isinstance(occurrences, list):
        raise EvidenceFusionError("editor evidence occurrences must be a list")
    for occurrence in occurrences:
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        rows = occurrence.get("lines")
        if not occurrence_id or not isinstance(rows, list):
            raise EvidenceFusionError("editor evidence occurrence is malformed")
        for row in rows:
            try:
                line_index = int(row["canonical_line_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise EvidenceFusionError("editor evidence line index is invalid") from exc
            key = (occurrence_id, line_index)
            if key in output:
                raise EvidenceFusionError("duplicate editor evidence line identity")
            output[key] = row
    return output


def _asr_index(asr_evidence: dict[str, Any] | None) -> dict[tuple[str, int], list[dict[str, Any]]]:
    if asr_evidence is None:
        return {}
    if str(asr_evidence.get("backend") or "") != "faster_whisper":
        raise EvidenceFusionError("unsupported ASR evidence backend")
    jobs = asr_evidence.get("jobs")
    if not isinstance(jobs, list):
        raise EvidenceFusionError("ASR evidence jobs must be a list")
    output: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for job in jobs:
        if not isinstance(job, dict):
            raise EvidenceFusionError("ASR evidence job must be an object")
        line_index = job.get("canonical_line_index")
        occurrence_id = str(job.get("occurrence_id") or "")
        if line_index is None:
            # occurrence-level issue evidence has no line boundary proposal.
            continue
        try:
            index = int(line_index)
        except (TypeError, ValueError) as exc:
            raise EvidenceFusionError("ASR evidence line index is invalid") from exc
        output.setdefault((occurrence_id, index), []).append(job)
    return output


def _forced_index(
    forced_mix_evidence: dict[str, Any] | None,
) -> dict[tuple[str, int], dict[str, Any]]:
    if forced_mix_evidence is None:
        return {}
    if forced_mix_evidence.get("mode") != "forced_alignment_mix_projection":
        raise EvidenceFusionError("forced evidence must be projected into mix time")
    if str(forced_mix_evidence.get("source_evidence_backend") or "") != "external_forced_aligner":
        raise EvidenceFusionError("unsupported forced-alignment evidence backend")
    if forced_mix_evidence.get("primary_timing_authority") != "source_to_mix_only":
        raise EvidenceFusionError("forced evidence unexpectedly changes primary timing authority")
    if forced_mix_evidence.get("forced_alignment_authority") != "auxiliary_acoustic_evidence_only":
        raise EvidenceFusionError("forced evidence unexpectedly owns timing authority")
    jobs = forced_mix_evidence.get("jobs")
    if not isinstance(jobs, list):
        raise EvidenceFusionError("forced mix evidence jobs must be a list")
    output: dict[tuple[str, int], dict[str, Any]] = {}
    seen_job_ids: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise EvidenceFusionError("forced mix evidence job must be an object")
        job_id = str(job.get("job_id") or "").strip()
        occurrence_id = str(job.get("occurrence_id") or "").strip()
        if not job_id or job_id in seen_job_ids:
            raise EvidenceFusionError("forced mix evidence job IDs must be unique/non-empty")
        seen_job_ids.add(job_id)
        if not occurrence_id:
            raise EvidenceFusionError("forced mix evidence job has no occurrence_id")
        try:
            line_index = int(job["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceFusionError("forced mix evidence line index is invalid") from exc
        key = (occurrence_id, line_index)
        if key in output:
            raise EvidenceFusionError("duplicate forced evidence canonical line identity")
        status = str(job.get("projection_status") or "")
        if status not in {"projected", "unprojectable"}:
            raise EvidenceFusionError("forced mix evidence projection status is invalid")
        output[key] = job
    return output


def _asr_boundary(job: dict[str, Any]) -> tuple[int, int] | None:
    segments = job.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    intervals: list[tuple[int, int]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = _int_or_none(segment.get("start_ms"), label="ASR segment start")
        end = _int_or_none(segment.get("end_ms"), label="ASR segment end")
        if start is None or end is None or end <= start:
            continue
        intervals.append((start, end))
    if not intervals:
        return None
    return min(start for start, _ in intervals), max(end for _, end in intervals)


def _best_asr(jobs: list[dict[str, Any]]) -> tuple[dict[str, Any], tuple[int, int]] | None:
    candidates: list[tuple[float, str, dict[str, Any], tuple[int, int]]] = []
    for job in jobs:
        boundary = _asr_boundary(job)
        if boundary is None:
            continue
        support = job.get("canonical_text_support_score")
        try:
            score = -1.0 if support is None else float(support)
        except (TypeError, ValueError) as exc:
            raise EvidenceFusionError("ASR canonical support score is invalid") from exc
        if not math.isfinite(score):
            raise EvidenceFusionError("ASR canonical support score must be finite")
        candidates.append((score, str(job.get("job_id") or ""), job, boundary))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, job, boundary = candidates[0]
    return job, boundary


def _forced_boundary(job: dict[str, Any]) -> tuple[int, int] | None:
    status = str(job.get("projection_status") or "")
    if status == "unprojectable":
        if job.get("mix_start_ms") is not None or job.get("mix_end_ms") is not None:
            raise EvidenceFusionError("unprojectable forced evidence contains mix boundary")
        return None
    if status != "projected":
        raise EvidenceFusionError("forced mix evidence projection status is invalid")
    start = _int_or_none(job.get("mix_start_ms"), label="forced mix start")
    end = _int_or_none(job.get("mix_end_ms"), label="forced mix end")
    if start is None or end is None or end <= start:
        raise EvidenceFusionError("projected forced mix boundary is invalid")
    confidence = job.get("line_confidence")
    if confidence is not None:
        try:
            score = float(confidence)
        except (TypeError, ValueError) as exc:
            raise EvidenceFusionError("forced line confidence is invalid") from exc
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise EvidenceFusionError("forced line confidence must be within [0, 1]")
    return start, end


def _max_boundary_disagreement(
    left: tuple[int, int], right: tuple[int, int]
) -> int:
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


def _pair_disagreement(
    proposals: dict[str, tuple[int, int]], left: str, right: str
) -> int | None:
    if left not in proposals or right not in proposals:
        return None
    return _max_boundary_disagreement(proposals[left], proposals[right])


def _fuse_line(
    canonical: dict[str, Any],
    *,
    editor: dict[str, Any] | None,
    asr_jobs: list[dict[str, Any]],
    forced: dict[str, Any] | None,
    config: EvidenceFusionConfig,
) -> dict[str, Any]:
    source_boundary = (
        int(canonical["mix_start_ms"]),
        int(canonical["mix_end_ms"])
        if canonical["mix_end_ms"] is not None
        else int(canonical["mix_start_ms"] + 1),
    )
    families: list[dict[str, Any]] = [
        {
            "family": "source_timeline",
            "available": True,
            "authoritative_for_primary_timing": True,
            "boundary_ms": list(source_boundary),
        }
    ]
    proposals: dict[str, tuple[int, int]] = {}

    if editor is not None:
        if editor.get("canonical_text_sha256") != canonical["canonical_text_sha256"]:
            raise EvidenceFusionError("editor/canonical text identity mismatch")
        cue_number = editor.get("best_editor_cue_number")
        onset_delta = _int_or_none(
            editor.get("suggested_onset_delta_ms"), label="editor onset delta"
        )
        offset_delta = _int_or_none(
            editor.get("suggested_offset_delta_ms"), label="editor offset delta"
        )
        if cue_number is not None and onset_delta is not None:
            editor_start = source_boundary[0] + onset_delta
            editor_end = (
                source_boundary[1] + offset_delta
                if offset_delta is not None
                else source_boundary[1]
            )
            if editor_end <= editor_start:
                editor_end = editor_start + 1
            proposals["editor"] = (editor_start, editor_end)
            families.append(
                {
                    "family": "editor",
                    "available": True,
                    "authoritative_for_primary_timing": False,
                    "boundary_ms": [editor_start, editor_end],
                    "cue_number": int(cue_number),
                    "best_candidate_margin_uncalibrated": editor.get(
                        "best_candidate_margin_uncalibrated"
                    ),
                    "timing_support_score": (
                        editor.get("candidates", [{}])[0].get("timing_support_score")
                        if isinstance(editor.get("candidates"), list)
                        and editor.get("candidates")
                        else None
                    ),
                }
            )
        else:
            families.append(
                {
                    "family": "editor",
                    "available": False,
                    "authoritative_for_primary_timing": False,
                    "reason": "no_line_boundary_candidate",
                }
            )

    selected_asr = _best_asr(asr_jobs)
    if selected_asr is not None:
        job, boundary = selected_asr
        proposals["asr"] = boundary
        families.append(
            {
                "family": "asr",
                "available": True,
                "authoritative_for_primary_timing": False,
                "boundary_ms": list(boundary),
                "job_id": str(job.get("job_id") or ""),
                "canonical_text_support_score": job.get(
                    "canonical_text_support_score"
                ),
                "language_probability": job.get("language_probability"),
            }
        )
    elif asr_jobs:
        families.append(
            {
                "family": "asr",
                "available": False,
                "authoritative_for_primary_timing": False,
                "reason": "no_valid_segment_boundary",
            }
        )

    if forced is not None:
        if forced.get("canonical_text_sha256") != canonical["canonical_text_sha256"]:
            raise EvidenceFusionError("forced/canonical text identity mismatch")
        if str(forced.get("track_id") or "") != canonical["track_id"]:
            raise EvidenceFusionError("forced/canonical track identity mismatch")
        boundary = _forced_boundary(forced)
        if boundary is not None:
            proposals["forced_alignment"] = boundary
            families.append(
                {
                    "family": "forced_alignment",
                    "available": True,
                    "authoritative_for_primary_timing": False,
                    "boundary_ms": list(boundary),
                    "job_id": str(forced.get("job_id") or ""),
                    "line_confidence": forced.get("line_confidence"),
                    "backend_id": str(forced.get("backend_id") or ""),
                    "backend_version": str(forced.get("backend_version") or ""),
                    "model_id": str(forced.get("model_id") or ""),
                    "model_revision": str(forced.get("model_revision") or ""),
                    "projection_status": "projected",
                }
            )
        else:
            families.append(
                {
                    "family": "forced_alignment",
                    "available": False,
                    "authoritative_for_primary_timing": False,
                    "job_id": str(forced.get("job_id") or ""),
                    "projection_status": "unprojectable",
                    "reason": str(forced.get("projection_reason") or "unprojectable"),
                }
            )

    editor_asr = _pair_disagreement(proposals, "editor", "asr")
    editor_forced = _pair_disagreement(proposals, "editor", "forced_alignment")
    asr_forced = _pair_disagreement(proposals, "asr", "forced_alignment")
    disagreement_values = [
        value
        for value in (editor_asr, editor_forced, asr_forced)
        if value is not None
    ]
    max_disagreement = max(disagreement_values) if disagreement_values else None
    conflict = (
        max_disagreement is not None
        and max_disagreement > config.conflict_boundary_ms
    )

    auxiliary_count = len(proposals)
    if conflict:
        level = "CONFLICT"
    elif auxiliary_count >= 2:
        level = "HIGH"
    elif auxiliary_count == 1:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "occurrence_id": canonical["occurrence_id"],
        "track_id": canonical["track_id"],
        "ordinal": canonical["ordinal"],
        "language_profile": canonical["language_profile"],
        "canonical_selection_sha256": canonical["canonical_selection_sha256"],
        "canonical_line_index": canonical["canonical_line_index"],
        "canonical_text_sha256": canonical["canonical_text_sha256"],
        "source_timeline_boundary_ms": list(source_boundary),
        "shadow_level": level,
        "shadow_level_calibrated": False,
        "auxiliary_boundary_family_count": auxiliary_count,
        "editor_asr_boundary_disagreement_ms": editor_asr,
        "editor_forced_boundary_disagreement_ms": editor_forced,
        "asr_forced_boundary_disagreement_ms": asr_forced,
        "max_auxiliary_boundary_disagreement_ms": max_disagreement,
        "families": families,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
    }


def build_evidence_fusion(
    *,
    timeline_payloads: Iterable[dict[str, Any]],
    editor_evidence: dict[str, Any] | None = None,
    asr_evidence: dict[str, Any] | None = None,
    forced_mix_evidence: dict[str, Any] | None = None,
    config: EvidenceFusionConfig | None = None,
) -> dict[str, Any]:
    config = config or EvidenceFusionConfig()
    config.validate()
    canonical, occurrences = _timeline_index(timeline_payloads)
    editor = _editor_index(editor_evidence)
    asr = _asr_index(asr_evidence)
    forced = _forced_index(forced_mix_evidence)

    unknown_editor = sorted(set(editor) - set(canonical))
    unknown_asr = sorted(set(asr) - set(canonical))
    unknown_forced = sorted(set(forced) - set(canonical))
    if unknown_editor:
        raise EvidenceFusionError("editor evidence references unknown canonical line")
    if unknown_asr:
        raise EvidenceFusionError("ASR evidence references unknown canonical line")
    if unknown_forced:
        raise EvidenceFusionError("forced evidence references unknown canonical line")

    rows = [
        _fuse_line(
            canonical[key],
            editor=editor.get(key),
            asr_jobs=asr.get(key, []),
            forced=forced.get(key),
            config=config,
        )
        for key in sorted(
            canonical,
            key=lambda item: (
                canonical[item]["ordinal"],
                canonical[item]["mix_start_ms"],
                item[1],
            ),
        )
    ]
    counts = {level: 0 for level in ("LOW", "MEDIUM", "HIGH", "CONFLICT")}
    forced_counts = {"projected": 0, "unprojectable": 0, "absent": 0}
    for row in rows:
        counts[row["shadow_level"]] += 1
        forced_family = next(
            (family for family in row["families"] if family.get("family") == "forced_alignment"),
            None,
        )
        if forced_family is None:
            forced_counts["absent"] += 1
        elif forced_family.get("available"):
            forced_counts["projected"] += 1
        else:
            forced_counts["unprojectable"] += 1
    return {
        "schema_version": FUSION_SCHEMA_VERSION,
        "policy_id": FUSION_POLICY_ID,
        "mode": "shadow_only",
        "policy_calibrated": False,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
        "config": config.to_dict(),
        "summary": {
            "occurrence_count": len(occurrences),
            "canonical_line_count": len(rows),
            "shadow_level_counts": counts,
            "forced_alignment_line_counts": forced_counts,
        },
        "lines": rows,
        "authority": {
            "canonical_text": "canonical_lyrics_only",
            "primary_timing": "source_to_mix_only",
            "editor": "auxiliary_shadow_family",
            "asr": "auxiliary_shadow_family",
            "forced_alignment": "auxiliary_shadow_family_mix_time",
        },
    }
