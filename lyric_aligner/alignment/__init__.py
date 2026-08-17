"""Planning/contracts for optional local acoustic evidence backends.

The alignment package does not replace canonical lyrics or Source-to-Mix.  It
selects small evidence jobs, reports backend availability, and optionally runs
bounded evidence executors when their real runtime prerequisites are present.
"""

from lyric_aligner.alignment.asr_executor import (
    ASR_EVIDENCE_SCHEMA_VERSION,
    AsrExecutionError,
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
)
from lyric_aligner.alignment.backends import (
    BackendCapability,
    BackendStatus,
    inspect_backends,
)
from lyric_aligner.alignment.planner import (
    ALIGNMENT_PLAN_SCHEMA_VERSION,
    AlignmentPlanningError,
    AlignmentPlannerConfig,
    build_alignment_plan,
)

__all__ = [
    "ALIGNMENT_PLAN_SCHEMA_VERSION",
    "ASR_EVIDENCE_SCHEMA_VERSION",
    "AlignmentPlanningError",
    "AlignmentPlannerConfig",
    "AsrExecutionError",
    "BackendCapability",
    "BackendStatus",
    "FasterWhisperExecutionConfig",
    "build_alignment_plan",
    "execute_faster_whisper_jobs",
    "inspect_backends",
]
