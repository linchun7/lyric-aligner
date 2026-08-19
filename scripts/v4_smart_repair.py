#!/usr/bin/env python3
"""Run Smart no-audio lyric + anchor-timeline repair."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from lyric_aligner.io.path_safety import PathCollisionError, validate_separate_artifact_paths
from lyric_aligner.timeline.anchor_repair import parse_timed_canonical_files
from lyric_aligner.timeline.smart_policy import smart_repair_srt_text_v11
from lyric_aligner.text_repair import DEFAULT_AUTO_THRESHOLD, PRODUCTION_MIN_AUTO_THRESHOLD

_UTF8_BOM = b"\xef\xbb\xbf"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    """Convert non-finite diagnostic floats to JSON null recursively."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _resolve_source_key(key: str, source_names: list[str]) -> int:
    try:
        index = int(key)
    except ValueError:
        matches = [index for index, name in enumerate(source_names) if name == key]
        if len(matches) != 1:
            raise ValueError(
                f"source key {key!r} must be a unique canonical filename or source ordinal"
            )
        return matches[0]
    if index < 0 or index >= len(source_names):
        raise ValueError(f"source ordinal {index} is out of range")
    return index


def _parse_key_value(values: list[str], source_names: list[str]) -> dict[int, float]:
    result: dict[int, float] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected SOURCE=VALUE, got {value!r}")
        raw_key, raw_number = value.split("=", 1)
        source_ordinal = _resolve_source_key(raw_key.strip(), source_names)
        number = float(raw_number)
        if not math.isfinite(number):
            raise ValueError(f"non-finite numeric value is not allowed: {raw_number!r}")
        result[source_ordinal] = number
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-srt", type=Path, required=True)
    parser.add_argument(
        "--canonical-lyrics",
        type=Path,
        nargs="+",
        required=True,
        help="Timed LRC/QRC files in mix/song order",
    )
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--rate-prior",
        action="append",
        default=[],
        metavar="SOURCE=RATIO",
        help="Exact DAW Source-to-Mix slope; SOURCE is canonical filename or zero-based ordinal",
    )
    parser.add_argument(
        "--source-bpm",
        action="append",
        default=[],
        metavar="SOURCE=BPM",
        help="Source BPM; combine with --target-bpm to derive a softer target/source rate prior",
    )
    parser.add_argument("--target-bpm", type=float)
    parser.add_argument(
        "--auto-threshold",
        type=float,
        default=DEFAULT_AUTO_THRESHOLD,
        help="Text Repair similarity threshold; production floor remains V2.1 default",
    )
    args = parser.parse_args()

    try:
        validate_separate_artifact_paths(
            inputs={
                "source_srt": args.source_srt,
                **{
                    f"canonical_lyrics[{index}]": path
                    for index, path in enumerate(args.canonical_lyrics)
                },
            },
            outputs={
                "output_srt": args.output_srt,
                "report": args.report,
            },
        )
    except PathCollisionError as exc:
        parser.error(str(exc))

    if not math.isfinite(args.auto_threshold):
        parser.error("--auto-threshold must be finite")
    if args.auto_threshold < PRODUCTION_MIN_AUTO_THRESHOLD:
        parser.error(
            f"production --auto-threshold cannot be below {PRODUCTION_MIN_AUTO_THRESHOLD}"
        )
    if not 0.5 <= args.auto_threshold <= 1.0:
        parser.error("--auto-threshold must be between 0.5 and 1.0")

    timed, repair = parse_timed_canonical_files(args.canonical_lyrics)
    source_names: list[str] = []
    for item in timed:
        if item.source_ordinal == len(source_names):
            source_names.append(item.source)

    try:
        exact_rate_priors = _parse_key_value(args.rate_prior, source_names)
        source_bpms = _parse_key_value(args.source_bpm, source_names)
    except ValueError as exc:
        parser.error(str(exc))

    if source_bpms and args.target_bpm is None:
        parser.error("--source-bpm requires --target-bpm")
    if args.target_bpm is not None and (
        not math.isfinite(args.target_bpm) or args.target_bpm <= 0
    ):
        parser.error("--target-bpm must be a finite positive number")

    rate_priors = dict(exact_rate_priors)
    rate_metadata: dict[int, dict[str, object]] = {
        source_ordinal: {"value": value, "provenance": "exact_daw"}
        for source_ordinal, value in exact_rate_priors.items()
    }
    for source_ordinal, source_bpm in source_bpms.items():
        if source_bpm <= 0:
            parser.error("source BPM values must be positive")
        derived = args.target_bpm / source_bpm
        if source_ordinal not in rate_priors:
            rate_priors[source_ordinal] = derived
            rate_metadata[source_ordinal] = {
                "value": derived,
                "provenance": "bpm_derived",
                "source_bpm": source_bpm,
                "target_bpm": args.target_bpm,
            }

    payload = args.source_srt.read_bytes()
    had_bom = payload.startswith(_UTF8_BOM)
    try:
        source_text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        parser.error(f"source SRT is not valid UTF-8: {exc}")

    rendered, report = smart_repair_srt_text_v11(
        source_text,
        timed,
        repair,
        auto_threshold=args.auto_threshold,
        rate_prior_by_source=rate_priors,
        rate_prior_metadata_by_source=rate_metadata,
    )

    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    output_payload = rendered.encode("utf-8")
    if had_bom:
        output_payload = _UTF8_BOM + output_payload
    args.output_srt.write_bytes(output_payload)

    report["inputs"] = {
        "source_srt": args.source_srt.name,
        "source_srt_sha256": _sha256(args.source_srt),
        "canonical_lyrics": [
            {"name": path.name, "sha256": _sha256(path)}
            for path in args.canonical_lyrics
        ],
    }
    report["rate_prior_by_source"] = {
        source_names[index]: value for index, value in sorted(rate_priors.items())
    }
    report["rate_prior_metadata_by_source"] = {
        source_names[index]: metadata
        for index, metadata in sorted(rate_metadata.items())
    }
    report["output_srt_sha256"] = _sha256(args.output_srt)
    report = _json_safe(report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "status": report["status"],
        "audio_read": False,
        "text_replacement_count": report["text_replacement_count"],
        "timing_repair_count": report["timing_repair_count"],
        "timing_review_count": report["timing_review_count"],
        "pro_escalation_required": report["pro_escalation_required"],
        "output_srt": str(args.output_srt),
        "report": str(args.report),
    }, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
