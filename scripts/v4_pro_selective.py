#!/usr/bin/env python3
"""Plan and optionally execute bounded Pro evidence from a Smart report.

Pro v1.1 routes evidence by failure reason, reuses nearby mix regions, can run
source-local acoustic matching, faster-whisper, and the existing external
forced-alignment protocol, and still never mutates subtitle timing by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lyric_aligner.alignment.asr_executor import (
    AsrExecutionError,
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
)
from lyric_aligner.alignment.forced_executor import (
    ExternalForcedAlignmentConfig,
    ForcedAlignmentExecutionError,
    execute_external_forced_alignment_jobs,
)
from lyric_aligner.alignment.local_acoustic_match import LocalAcousticMatchConfig
from lyric_aligner.alignment.local_acoustic_v11 import execute_region_source_match_jobs
from lyric_aligner.alignment.selective_policy import build_selective_repair_plan_v11
from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairConfig,
    canonical_text_by_job_id,
)
from lyric_aligner.assets.bindings import CanonicalOriginal, ResolvedAssetBinding
from lyric_aligner.assets.resolver import canonical_selection_sha256
from lyric_aligner.text_repair import parse_srt_text
from lyric_aligner.timeline.anchor_repair import parse_timed_canonical_files


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_smart_bindings(
    smart_report: dict,
    *,
    smart_srt: Path,
    canonical_lyrics: list[Path],
) -> None:
    expected_srt = str(smart_report.get("output_srt_sha256") or "").strip()
    if not expected_srt:
        raise ValueError("Smart report is missing output_srt_sha256 binding")
    if _sha256(smart_srt) != expected_srt:
        raise ValueError("Smart report/SRT SHA-256 mismatch")

    inputs = smart_report.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("Smart report is missing input bindings")
    expected_canonical = inputs.get("canonical_lyrics")
    if not isinstance(expected_canonical, list) or len(expected_canonical) != len(canonical_lyrics):
        raise ValueError("Smart report/canonical input count mismatch")
    for expected, actual in zip(expected_canonical, canonical_lyrics):
        if not isinstance(expected, dict):
            raise ValueError("Smart report has invalid canonical input binding")
        if str(expected.get("name") or "") != actual.name:
            raise ValueError("Smart report/canonical filename or order mismatch")
        if str(expected.get("sha256") or "") != _sha256(actual):
            raise ValueError(f"Smart report/canonical SHA-256 mismatch: {actual.name}")


def _source_names(timed) -> list[str]:
    names: list[str] = []
    for row in timed:
        if row.source_ordinal == len(names):
            names.append(row.source)
    return names


def _source_key(raw_key: str, source_names: list[str]) -> int:
    key = raw_key.strip()
    try:
        index = int(key)
    except ValueError:
        matches = [index for index, name in enumerate(source_names) if name == key]
        if len(matches) != 1:
            raise ValueError(
                f"source key {key!r} must be a unique canonical filename or zero-based ordinal"
            )
        return matches[0]
    if index < 0 or index >= len(source_names):
        raise ValueError(f"source ordinal {index} is out of range")
    return index


def _parse_string_mapping(values: list[str], source_names: list[str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected SOURCE=VALUE, got {value!r}")
        key, raw = value.split("=", 1)
        if not raw.strip():
            raise ValueError(f"empty value in {value!r}")
        result[_source_key(key, source_names)] = raw.strip()
    return result


def _forced_bindings(
    *,
    timed,
    source_names: list[str],
    canonical_lyrics: list[Path],
    source_paths: dict[int, Path],
    languages: dict[int, str],
) -> list[ResolvedAssetBinding]:
    by_source: dict[int, list] = {}
    for row in timed:
        by_source.setdefault(row.source_ordinal, []).append(row)

    bindings: list[ResolvedAssetBinding] = []
    for source_ordinal, source_path in sorted(source_paths.items()):
        if not source_path.is_file():
            raise ValueError(f"source audio does not exist: {source_path}")
        rows = by_source.get(source_ordinal, [])
        if not rows:
            raise ValueError(f"canonical source ordinal has no rows: {source_ordinal}")
        originals = tuple(
            CanonicalOriginal(
                timestamp_ms=int(row.time_ms),
                alternative_index=0,
                text=row.text,
            )
            for row in rows
        )
        selection_payload = [item.to_dict() for item in originals]
        lyric_path = canonical_lyrics[source_ordinal]
        bindings.append(
            ResolvedAssetBinding(
                ordinal=source_ordinal,
                occurrence_id=f"smart-source-{source_ordinal:03d}",
                track_id=source_names[source_ordinal],
                artist="Smart Pro",
                title=source_names[source_ordinal],
                version_id="smart-pro-v1.1",
                nominal_start_ms=0,
                middle_cut="unknown",
                language_profile=str(languages.get(source_ordinal, "auto") or "auto"),
                source_audio_path=str(source_path),
                source_audio_sha256=_sha256(source_path),
                canonical_lyric_path=str(lyric_path),
                canonical_lyric_sha256=_sha256(lyric_path),
                canonical_selection_sha256=canonical_selection_sha256(selection_payload),
                canonical_originals=originals,
            )
        )
    return bindings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smart-report", required=True, type=Path)
    parser.add_argument("--smart-srt", required=True, type=Path)
    parser.add_argument("--canonical-lyrics", required=True, nargs="+", type=Path)
    parser.add_argument(
        "--source-language",
        action="append",
        default=[],
        metavar="SOURCE=LANG",
        help="Optional track language (zh/en/ko/ja/yue/auto); local line routing still wins",
    )
    parser.add_argument("--plan-out", required=True, type=Path)
    parser.add_argument("--mix-context-ms", type=int, default=2500)
    parser.add_argument("--region-merge-gap-ms", type=int, default=750)
    parser.add_argument("--max-jobs", type=int, default=100)

    parser.add_argument("--mix-audio", type=Path)
    parser.add_argument(
        "--source-audio",
        action="append",
        default=[],
        metavar="SOURCE=PATH",
        help="Source audio mapping used by local acoustic and forced evidence",
    )
    parser.add_argument("--acoustic-out", type=Path)
    parser.add_argument("--acoustic-sr", type=int, default=16000)

    parser.add_argument("--asr-model-id")
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-compute-type", default="int8")
    parser.add_argument("--asr-beam-size", type=int, default=5)
    parser.add_argument("--asr-out", type=Path)
    parser.add_argument("--include-private-asr-text", action="store_true")

    parser.add_argument("--forced-out", type=Path)
    parser.add_argument("--forced-command")
    parser.add_argument("--forced-backend-id")
    parser.add_argument("--forced-backend-version")
    parser.add_argument("--forced-model-id")
    parser.add_argument("--forced-model-revision")
    parser.add_argument("--forced-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()

    try:
        smart_report = _load_json(args.smart_report)
        _validate_smart_bindings(
            smart_report,
            smart_srt=args.smart_srt,
            canonical_lyrics=args.canonical_lyrics,
        )
        source_text = args.smart_srt.read_text(encoding="utf-8-sig")
        _, cues = parse_srt_text(source_text)
        timed, _ = parse_timed_canonical_files(args.canonical_lyrics)
        source_names = _source_names(timed)
        languages = _parse_string_mapping(args.source_language, source_names)
        plan = build_selective_repair_plan_v11(
            smart_report=smart_report,
            cues=cues,
            canonical=timed,
            language_by_source=languages,
            config=SelectiveRepairConfig(
                mix_context_ms=args.mix_context_ms,
                max_jobs=args.max_jobs,
            ),
            region_merge_gap_ms=args.region_merge_gap_ms,
        )
        plan["inputs"] = {
            "smart_report": args.smart_report.name,
            "smart_report_sha256": _sha256(args.smart_report),
            "smart_srt": args.smart_srt.name,
            "smart_srt_sha256": _sha256(args.smart_srt),
            "canonical_lyrics": [
                {"name": path.name, "sha256": _sha256(path)}
                for path in args.canonical_lyrics
            ],
        }
        _write_json(args.plan_out, plan)

        summary: dict[str, object] = {
            "product_mode": "Pro",
            "policy_id": plan.get("policy_id"),
            "plan": str(args.plan_out),
            "primary_job_count": plan["summary"].get("primary_job_count", 0),
            "boundary_competitor_job_count": plan["summary"].get("boundary_competitor_job_count", 0),
            "region_count": plan["summary"].get("region_count", 0),
            "planned_mix_audio_ms_unmerged": plan["summary"].get("planned_mix_audio_ms_unmerged", 0),
            "planned_mix_audio_ms_merged": plan["summary"].get("planned_mix_audio_ms_merged", 0),
            "timing_mutation_performed": False,
        }

        source_values = _parse_string_mapping(args.source_audio, source_names)
        source_paths = {index: Path(value) for index, value in source_values.items()}

        if args.acoustic_out is not None:
            if args.mix_audio is None:
                parser.error("--acoustic-out requires --mix-audio")
            evidence = execute_region_source_match_jobs(
                mix_audio_path=args.mix_audio,
                plan=plan,
                source_audio_by_source_ordinal=source_paths,
                config=LocalAcousticMatchConfig(sr=args.acoustic_sr),
            )
            evidence["mix_audio_sha256"] = _sha256(args.mix_audio)
            evidence["source_audio_sha256_by_source"] = {
                source_names[index]: _sha256(path)
                for index, path in sorted(source_paths.items())
            }
            _write_json(args.acoustic_out, evidence)
            summary["acoustic_out"] = str(args.acoustic_out)
            summary["acoustic_job_count"] = evidence["job_count"]
            summary["mix_feature_region_count"] = evidence["mix_feature_region_count"]

        if args.asr_out is not None:
            if args.mix_audio is None:
                parser.error("--asr-out requires --mix-audio")
            if not args.asr_model_id:
                parser.error("--asr-out requires --asr-model-id")
            lookup = canonical_text_by_job_id(plan, timed)
            evidence = execute_faster_whisper_jobs(
                audio_path=args.mix_audio,
                plan=plan,
                canonical_text_by_job_id=lookup,
                config=FasterWhisperExecutionConfig(
                    model_id=args.asr_model_id,
                    device=args.asr_device,
                    compute_type=args.asr_compute_type,
                    beam_size=args.asr_beam_size,
                    include_private_text=args.include_private_asr_text,
                ),
            )
            evidence["mix_audio_sha256"] = _sha256(args.mix_audio)
            _write_json(args.asr_out, evidence)
            summary["asr_out"] = str(args.asr_out)
            summary["asr_job_count"] = evidence["job_count"]

        if args.forced_out is not None:
            required = {
                "--forced-command": args.forced_command,
                "--forced-backend-id": args.forced_backend_id,
                "--forced-backend-version": args.forced_backend_version,
                "--forced-model-id": args.forced_model_id,
                "--forced-model-revision": args.forced_model_revision,
            }
            missing = [label for label, value in required.items() if not str(value or "").strip()]
            if missing:
                parser.error("--forced-out requires " + ", ".join(missing))
            if not source_paths:
                parser.error("--forced-out requires --source-audio mappings")
            bindings = _forced_bindings(
                timed=timed,
                source_names=source_names,
                canonical_lyrics=args.canonical_lyrics,
                source_paths=source_paths,
                languages=languages,
            )
            lookup = canonical_text_by_job_id(plan, timed)
            evidence = execute_external_forced_alignment_jobs(
                plan=plan,
                bindings=bindings,
                canonical_text_by_job_id=lookup,
                config=ExternalForcedAlignmentConfig(
                    command=args.forced_command,
                    backend_id=args.forced_backend_id,
                    backend_version=args.forced_backend_version,
                    model_id=args.forced_model_id,
                    model_revision=args.forced_model_revision,
                    timeout_seconds=args.forced_timeout_seconds,
                ),
            )
            _write_json(args.forced_out, evidence)
            summary["forced_out"] = str(args.forced_out)
            summary["forced_job_count"] = evidence["job_count"]

        print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
        return 0
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        AsrExecutionError,
        ForcedAlignmentExecutionError,
    ) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
