#!/usr/bin/env python3
"""Materialize the lexical quality floor while keeping the entire SRT timeline immutable."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.text.semantic_lexical import (
    build_semantic_request_bundle,
    materialize_semantic_shadow_candidate,
)
from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    PRODUCTION_MIN_AUTO_THRESHOLD,
    build_repair_plan_v2,
    parse_canonical_files,
    read_srt,
    write_repair_outputs,
)


def _validate_paths(args: argparse.Namespace) -> None:
    inputs = {args.source_srt.resolve(), *(path.resolve() for path in args.canonical_lrc)}
    if args.semantic_response is not None:
        inputs.add(args.semantic_response.resolve())
    outputs = [args.out.resolve(), args.report.resolve()]
    if args.semantic_request_out is not None:
        outputs.append(args.semantic_request_out.resolve())
    if args.semantic_candidate_out is not None:
        outputs.append(args.semantic_candidate_out.resolve())
    if args.semantic_candidate_report is not None:
        outputs.append(args.semantic_candidate_report.resolve())
    if len(outputs) != len(set(outputs)):
        raise ValueError("lexical-floor output paths must be distinct")
    if any(path in inputs for path in outputs):
        raise ValueError("lexical-floor outputs must not overwrite source SRT/canonical lyrics")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument("--canonical-lrc", required=True, action="append", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--semantic-request-out",
        type=Path,
        help=(
            "Optional bounded request bundle for LLM/foundation-model review of unresolved lexical spans. "
            "Generating this file never grants timing or production authority."
        ),
    )
    parser.add_argument(
        "--semantic-response",
        type=Path,
        help="Optional model response bundle bound to the generated semantic request bundle.",
    )
    parser.add_argument(
        "--semantic-candidate-out",
        type=Path,
        help="Write a non-production shadow SRT with validated model lexical partitions applied.",
    )
    parser.add_argument(
        "--semantic-candidate-report",
        type=Path,
        help="Write the non-production semantic shadow materialization report.",
    )
    parser.add_argument("--semantic-context-lines", type=int, default=2)
    parser.add_argument("--auto-threshold", type=float, default=DEFAULT_AUTO_THRESHOLD)
    args = parser.parse_args()

    try:
        _validate_paths(args)
        if args.auto_threshold < PRODUCTION_MIN_AUTO_THRESHOLD:
            raise ValueError(
                "production auto-threshold must be at least "
                f"{PRODUCTION_MIN_AUTO_THRESHOLD:.2f}"
            )
        if not args.source_srt.is_file():
            raise ValueError(f"source SRT does not exist: {args.source_srt}")
        missing = [path for path in args.canonical_lrc if not path.is_file()]
        if missing:
            raise ValueError(f"canonical lyric file does not exist: {missing[0]}")
        if args.semantic_response is not None and (
            args.semantic_candidate_out is None or args.semantic_candidate_report is None
        ):
            raise ValueError(
                "--semantic-response requires --semantic-candidate-out and --semantic-candidate-report"
            )
        if args.semantic_response is None and (
            args.semantic_candidate_out is not None or args.semantic_candidate_report is not None
        ):
            raise ValueError("semantic candidate outputs require --semantic-response")

        report = write_repair_outputs(
            args.source_srt,
            args.canonical_lrc,
            args.out,
            report_path=args.report,
            auto_threshold=args.auto_threshold,
        )
        semantic_request_count = 0
        semantic_shadow_summary = None
        if any((args.semantic_request_out, args.semantic_response)):
            source_text, had_bom, _, cues = read_srt(args.source_srt)
            canonical = parse_canonical_files(args.canonical_lrc)
            base_replacements, decisions, _ = build_repair_plan_v2(
                cues,
                canonical,
                auto_threshold=args.auto_threshold,
            )
            bundle = build_semantic_request_bundle(
                cues,
                canonical,
                decisions,
                context_lines=args.semantic_context_lines,
            )
            semantic_request_count = len(bundle["requests"])
            if args.semantic_request_out is not None:
                _write_json(args.semantic_request_out, bundle)
            if args.semantic_response is not None:
                response_bundle = json.loads(args.semantic_response.read_text(encoding="utf-8-sig"))
                candidate, semantic_shadow_summary = materialize_semantic_shadow_candidate(
                    source_text,
                    cues,
                    base_replacements,
                    bundle,
                    response_bundle,
                )
                candidate_payload = candidate.encode("utf-8")
                if had_bom:
                    candidate_payload = b"\xef\xbb\xbf" + candidate_payload
                args.semantic_candidate_out.parent.mkdir(parents=True, exist_ok=True)
                args.semantic_candidate_out.write_bytes(candidate_payload)
                semantic_shadow_summary["output_srt_sha256"] = __import__("hashlib").sha256(
                    candidate_payload
                ).hexdigest()
                _write_json(args.semantic_candidate_report, semantic_shadow_summary)
    except (OSError, ValueError, AssertionError) as exc:
        parser.error(str(exc))

    floor = dict(report["lexical_floor"])
    summary = {
        "mode": "lexical_floor_preserve_timeline",
        "legacy_text_repair_status": report["status"],
        "lexical_floor": floor,
        "replacement_count": report["replacement_count"],
        "cue_review_count": report["cue_review_count"],
        "unmatched_canonical_count": report["unmatched_canonical_count"],
        "semantic_request_count": semantic_request_count,
        "semantic_shadow": semantic_shadow_summary,
        "timeline_unchanged": report["timeline_unchanged"],
        "output_srt_sha256": report["output_srt_sha256"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if floor["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
