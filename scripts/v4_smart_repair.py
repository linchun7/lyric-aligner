#!/usr/bin/env python3
"""Run Smart no-audio lyric + anchor-timeline repair."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lyric_aligner.timeline.anchor_repair import (
    parse_timed_canonical_files,
    smart_repair_srt_text,
)
from lyric_aligner.text_repair import DEFAULT_AUTO_THRESHOLD, PRODUCTION_MIN_AUTO_THRESHOLD

_UTF8_BOM = b"\xef\xbb\xbf"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        help="Exact Source-to-Mix slope; SOURCE is canonical filename or zero-based ordinal",
    )
    parser.add_argument(
        "--source-bpm",
        action="append",
        default=[],
        metavar="SOURCE=BPM",
        help="Source BPM; combine with --target-bpm to derive target/source rate prior",
    )
    parser.add_argument("--target-bpm", type=float)
    parser.add_argument(
        "--auto-threshold",
        type=float,
        default=DEFAULT_AUTO_THRESHOLD,
        help="Text Repair similarity threshold; production floor remains V2.1 default",
    )
    args = parser.parse_args()

    source = args.source_srt.resolve()
    output = args.output_srt.resolve()
    report_path = args.report.resolve()
    if source == output:
        parser.error("Smart mode never overwrites the source SRT; choose a separate --output-srt")
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
        rate_priors = _parse_key_value(args.rate_prior, source_names)
        source_bpms = _parse_key_value(args.source_bpm, source_names)
    except ValueError as exc:
        parser.error(str(exc))

    if source_bpms and args.target_bpm is None:
        parser.error("--source-bpm requires --target-bpm")
    if args.target_bpm is not None and args.target_bpm <= 0:
        parser.error("--target-bpm must be positive")
    for source_ordinal, source_bpm in source_bpms.items():
        if source_bpm <= 0:
            parser.error("source BPM values must be positive")
        derived = args.target_bpm / source_bpm
        rate_priors.setdefault(source_ordinal, derived)

    payload = args.source_srt.read_bytes()
    had_bom = payload.startswith(_UTF8_BOM)
    try:
        source_text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        parser.error(f"source SRT is not valid UTF-8: {exc}")

    rendered, report = smart_repair_srt_text(
        source_text,
        timed,
        repair,
        auto_threshold=args.auto_threshold,
        rate_prior_by_source=rate_priors,
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
    report["output_srt_sha256"] = _sha256(args.output_srt)
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
        "output_srt": str(args.output_srt),
        "report": str(args.report),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
