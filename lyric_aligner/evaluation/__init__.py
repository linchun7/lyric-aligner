"""Privacy-safe calibration and blind-test evaluation contracts."""

from lyric_aligner.evaluation.protocol import (
    EVALUATION_PROTOCOL_VERSION,
    EvaluationProtocolError,
    augment_evaluation,
    dataset_ground_truth_identity,
    load_dataset_manifest,
    validate_dataset_manifest,
)

__all__ = [
    "EVALUATION_PROTOCOL_VERSION",
    "EvaluationProtocolError",
    "augment_evaluation",
    "dataset_ground_truth_identity",
    "load_dataset_manifest",
    "validate_dataset_manifest",
]
