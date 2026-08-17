"""Planning/contracts for optional local acoustic evidence backends.

The alignment package does not replace canonical lyrics or Source-to-Mix. It
selects bounded evidence jobs, reports backend availability, optionally runs
local ASR, routes weak first-pass evidence, and can compose a second accuracy
pass without widening scope to the full mix.
"""

from lyric_aligner.alignment.asr_executor import (
    ASR_EVIDENCE_SCHEMA_VERSION,
    AsrExecutionError,
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
)
from lyric_aligner.alignment.asr_routing import (
    ASR_SECOND_PASS_POLICY_ID,
    ASR_SECOND_PASS_SCHEMA_VERSION,
    AsrRoutingError,
    AsrSecondPassRoutingConfig,
    build_second_pass_plan,
)
from lyric_aligner.alignment.asr_second_pass import (
    ASR_COMPOSITE_SCHEMA_VERSION,
    AsrSecondPassExecutionError,
    execute_second_pass_and_compose,
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
    "ASR_COMPOSITE_SCHEMA_VERSION",
    "ASR_EVIDENCE_SCHEMA_VERSION",
    "ASR_SECOND_PASS_POLICY_ID",
    "ASR_SECOND_PASS_SCHEMA_VERSION",
    "AlignmentPlanningError",
    "AlignmentPlannerConfig",
    "AsrExecutionError",
    "AsrRoutingError",
    "AsrSecondPassExecutionError",
    "AsrSecondPassRoutingConfig",
    "BackendCapability",
    "BackendStatus",
    "FasterWhisperExecutionConfig",
    "build_alignment_plan",
    "build_second_pass_plan",
    "execute_faster_whisper_jobs",
    "execute_second_pass_and_compose",
    "inspect_backends",
]
