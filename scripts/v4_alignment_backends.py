#!/usr/bin/env python3
"""Report optional ASR/forced-alignment backend availability without loading models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.alignment.backends import (
    BackendCapability,
    capability_available,
    inspect_backends,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--faster-whisper-model-id")
    parser.add_argument("--whisperx-model-id")
    parser.add_argument("--whisperx-align-model-id")
    parser.add_argument("--external-forced-aligner-command")
    parser.add_argument(
        "--require-capability",
        action="append",
        choices=tuple(capability.value for capability in BackendCapability),
        default=[],
    )
    parser.add_argument(
        "--require-execution-ready",
        action="store_true",
        help="For required capabilities, also require model/config prerequisites.",
    )
    args = parser.parse_args()

    statuses = inspect_backends(
        faster_whisper_model_id=args.faster_whisper_model_id,
        whisperx_model_id=args.whisperx_model_id,
        whisperx_align_model_id=args.whisperx_align_model_id,
        external_forced_aligner_command=args.external_forced_aligner_command,
    )
    required = list(args.require_capability)
    missing = [
        capability
        for capability in required
        if not capability_available(
            statuses,
            capability,
            require_execution_ready=args.require_execution_ready,
        )
    ]
    payload = {
        "status": "available" if not missing else "requirements_unmet",
        "model_loading_performed": False,
        "backends": [status.to_dict() for status in statuses],
        "required_capabilities": required,
        "require_execution_ready": args.require_execution_ready,
        "missing_required_capabilities": missing,
        "note": (
            "Package/command discovery is not proof that a model is downloaded, licensed for a use, "
            "or accurate on singing."
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
