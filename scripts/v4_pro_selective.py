#!/usr/bin/env python3
"""Plan and optionally execute bounded Pro evidence from a Smart report.

Pro v1 is deliberately evidence-first: it only processes Smart-unresolved local
windows and never mutates subtitle timing by itself. Acoustic/ASR evidence can
then be calibrated before Pro automatic write-back is enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from lyric_aligner.alignment.asr_executor import (
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
)
from lyric_aligner.alignment.local_acoustic_match import (
    LocalAcousticMatchConfig,
    execute_local_source_match_jobs,
)
from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairConfig,
    build_selective_repair_plan,
    canonical_text_by_job_id,
)
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
    parser.add_argument("--max-jobs", type=int, default=100)

    parser.add_argument("--mix-audio", type=Path)
    parser.add_argument(
        "--source-audio",
        action="append",
        default=[],
        metavar="SOURCE=PATH",
        help="Source audio mapping for bounded source<->mix evidence",
    )
    parser.add_argument("--acoustic-out", type=Path)
    parser.add_argument("--acoustic-sr", type=int, default=16000)

    parser.add_argument("--asr-model-id")
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-compute-type", default="int8")
    parser.add_argument("--asr-beam-size", type=int, default=5)
    parser.add_argument("--asr-out", type=Path)
    parser.add_argument("--include-private-asr-text", action="store_true")
    args = parser.parse_args()

    try:
        smart_report = _load_json(args.smart_report)
        source_text = args.smart_srt.read_text(encoding="utf-8-sig")
        _, cues = parse_srt_text(source_text)
        timed, _ = parse_timed_canonical_files(args.canonical_lyrics)
        source_names = _source_names(timed)
        languages = _parse_string_mapping(args.source_language, source_names)
        plan = build_selective_repair_plan(
            smart_report=smart_report,
            cues=cues,
            canonical=timed,
            language_by_source=languages,
            config=SelectiveRepairConfig(
                mix_context_ms=args.mix_context_ms,
                max_jobs=args.max_jobs,
            ),
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
            "plan": str(args.plan_out),
            "job_count": plan["summary"]["job_count"],
            "planned_mix_audio_ms_unmerged": plan["summary"]["planned_mix_audio_ms_unmerged"],
            "timing_mutation_performed": False,
        }

        if args.acoustic_out is not None:
            if args.mix_audio is None:
                parser.error("--acoustic-out requires --mix-audio")
            source_values = _parse_string_mapping(args.source_audio, source_names)
            source_paths = {index: Path(value) for index, value in source_values.items()}
            evidence = execute_local_source_match_jobs(
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

        print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
