from __future__ import annotations

from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.qa.semantic_sync import (
    DEFAULT_LARGE_ERROR_MS,
    DEFAULT_MAX_LARGE_ERROR_FRACTION,
    DEFAULT_MAX_MEDIAN_ABS_ERROR_MS,
    DEFAULT_MIN_AUDIO_ANCHOR_FRACTION,
)
from task_contract import load_task_manifest, resolve_manifest_record, sha256, write_json_atomic


def write_passing_semantic_sync_fixture(
    *,
    manifest_path: Path,
    final_srt: Path,
    final_report: Path,
    out_dir: Path,
) -> tuple[Path, Path, Path]:
    """Create explicitly synthetic passed semantic-QA fixtures for release tests.

    This helper is test-only. Production code must use v4_audit_semantic_sync.py
    with real evidence fusion; the synthetic fusion here exists only to exercise
    release lineage/binding contracts.
    """

    manifest = load_task_manifest(manifest_path)
    fingerprint = str(manifest["task_fingerprint_sha256"])
    out_dir.mkdir(parents=True, exist_ok=True)
    run_path = out_dir / "synthetic.semantic.run.json"
    write_json_atomic(
        run_path,
        {
            "schema_version": "synthetic-semantic-run-1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "purpose": "release-contract-test-only",
        },
    )
    fusion_path = out_dir / "synthetic.semantic-fusion.json"
    write_json_atomic(
        fusion_path,
        {
            "schema_version": "synthetic-semantic-fusion-1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "mode": "shadow_only",
            "purpose": "release-contract-test-only",
        },
    )
    inputs = manifest["inputs"]
    source_srt = resolve_manifest_record(manifest_path, inputs["source_srt"])
    audio = resolve_manifest_record(manifest_path, inputs["audio"])
    song_list = resolve_manifest_record(manifest_path, inputs["song_list"])
    passed_layer = {
        "passed": True,
        "track_count": 1,
        "failed_track_count": 0,
        "errors": [],
        "thresholds": {
            "max_median_abs_error_ms": DEFAULT_MAX_MEDIAN_ABS_ERROR_MS,
            "large_error_ms": DEFAULT_LARGE_ERROR_MS,
            "max_large_error_fraction": DEFAULT_MAX_LARGE_ERROR_FRACTION,
            "min_audio_anchor_fraction": DEFAULT_MIN_AUDIO_ANCHOR_FRACTION,
        },
        "tracks": [
            {
                "ordinal": 1,
                "track_id": "synthetic-track",
                "evidence_basis": "forced_alignment",
                "passed": True,
                "errors": [],
            }
        ],
    }
    qa_path = out_dir / "synthetic.semantic-sync.json"
    write_json_atomic(
        qa_path,
        {
            "schema_version": "semantic-sync-qa-1.1",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "mode": "independent_audio_semantic_release_gate",
            "passed": True,
            "audio_evidence_policy": "forced_alignment_or_asr_plus_reliable_editor_v1",
            "editor_witness": {"authority": "auxiliary_only"},
            "bindings": {
                "source_srt_sha256": sha256(source_srt),
                "audio_sha256": sha256(audio),
                "song_list_sha256": sha256(song_list),
                "run_sha256": sha256(run_path),
                "fusion_sha256": sha256(fusion_path),
                "final_srt_sha256": sha256(final_srt),
                "final_report_sha256": sha256(final_report),
            },
            "projection_sync": dict(passed_layer),
            "final_sync": dict(passed_layer),
            "test_fixture_only": True,
        },
    )
    return run_path, fusion_path, qa_path
