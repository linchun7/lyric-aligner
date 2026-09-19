#!/usr/bin/env python3
"""Run one blind backend prediction scope after human acoustic gold is locked.

The command deliberately refuses to run from a selection lock alone.  A completed,
validated human-gold artifact must already exist, and its audit CSV/file/audio hashes
must still match the locked pack.  Backend requests never include gold timing values.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.alignment.production_backend_profiles import (
    PRODUCTION_BOUNDARY_BACKEND_PROFILES,
    get_production_boundary_backend_profile,
)
from lyric_aligner.evaluation.human_gold_prediction import (
    ExternalHumanGoldPredictionConfig,
    execute_human_gold_prediction_scope,
)
from lyric_aligner.evaluation.production_calibration import (
    validate_human_boundary_gold_artifact,
)
from scripts.v4_ingest_human_boundary_gold import sha256_file, verify_lock


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError("human-gold prediction output must be a new path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--boundary-kind", choices=("start", "end", "internal"), required=True)
    parser.add_argument(
        "--backend-profile",
        choices=tuple(sorted(PRODUCTION_BOUNDARY_BACKEND_PROFILES)),
        help="Use a versioned production backend identity/runtime profile.",
    )
    parser.add_argument("--command")
    parser.add_argument("--family")
    parser.add_argument("--correlation-group")
    parser.add_argument("--backend-id")
    parser.add_argument("--backend-version")
    parser.add_argument("--model-id")
    parser.add_argument("--model-revision")
    parser.add_argument("--language")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    lock = load_json(args.lock, label="selection lock")
    gold_artifact = load_json(args.gold, label="human gold")
    verified_gold = validate_human_boundary_gold_artifact(gold_artifact)
    outer, internal = verify_lock(lock, args.lock)

    if verified_gold["selection_lock_sha256"] != str(lock.get("lock_sha256") or ""):
        raise ValueError("human gold belongs to another selection lock")
    if str(gold_artifact.get("selection_lock_file_sha256") or "") != sha256_file(args.lock):
        raise ValueError("selection lock file changed after human-gold ingest")
    if str(gold_artifact.get("outer_audit_csv_sha256") or "") != sha256_file(outer["audit_path"]):
        raise ValueError("outer human audit changed after human-gold ingest")
    if str(gold_artifact.get("internal_audit_csv_sha256") or "") != sha256_file(internal["audit_path"]):
        raise ValueError("internal human audit changed after human-gold ingest")

    final_audio = Path(str(lock["inputs"]["final_audio_path"]))
    population = internal if args.boundary_kind == "internal" else outer
    language = str(args.language or verified_gold["language_scope"]).strip().lower()
    manual_identity = {
        "command": args.command,
        "family": args.family,
        "correlation_group": args.correlation_group,
        "backend_id": args.backend_id,
        "backend_version": args.backend_version,
        "model_id": args.model_id,
        "model_revision": args.model_revision,
    }
    if args.backend_profile:
        if any(value is not None for value in manual_identity.values()):
            raise ValueError("--backend-profile cannot be combined with manual backend identity flags")
        resolved = get_production_boundary_backend_profile(args.backend_profile).resolve(
            REPOSITORY_ROOT,
            language=language,
        )
        config = ExternalHumanGoldPredictionConfig(
            command=resolved["command"],
            family=resolved["family"],
            correlation_group=resolved["correlation_group"],
            backend_id=resolved["backend_id"],
            backend_version=resolved["backend_version"],
            model_id=resolved["model_id"],
            model_revision=resolved["model_revision"],
            language=resolved["language"],
            implementation_revision=resolved["implementation_revision"],
            adapter_contract_revision=resolved["adapter_contract_revision"],
            profile_id=resolved["profile_id"],
            window_policy_id=resolved["window_policy_id"],
            timeout_seconds=args.timeout_seconds,
        )
    else:
        missing = sorted(key for key, value in manual_identity.items() if not str(value or "").strip())
        if missing:
            raise ValueError("manual backend mode is missing: " + ", ".join(missing))
        config = ExternalHumanGoldPredictionConfig(
            command=str(args.command),
            family=str(args.family),
            correlation_group=str(args.correlation_group),
            backend_id=str(args.backend_id),
            backend_version=str(args.backend_version),
            model_id=str(args.model_id),
            model_revision=str(args.model_revision),
            language=language,
            timeout_seconds=args.timeout_seconds,
        )
    artifact = execute_human_gold_prediction_scope(
        human_gold_artifact=gold_artifact,
        locked_cases=population["records"],
        final_audio_path=final_audio,
        boundary_kind=args.boundary_kind,
        config=config,
    )
    atomic_json(args.out, artifact)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "boundary_kind": args.boundary_kind,
                "backend_id": config.backend_id,
                "backend_profile_id": config.profile_id or None,
                "record_count": artifact["record_count"],
                "predicted_count": sum(row["predicted_ms"] is not None for row in artifact["records"]),
                "artifact_sha256": artifact["artifact_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
