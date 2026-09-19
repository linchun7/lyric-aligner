# External Forced-Alignment Batch Protocol 1.1

状态：backward-compatible optional production protocol  
日期：2026-08-19

## 1. Why

P7 protocol `1.0` is intentionally simple and truthful, but executes one external subprocess per bounded lyric job. A real CTC/singing aligner may load a large checkpoint on process start, so hundreds of lyric jobs can cause hundreds of redundant model loads.

Batch protocol `1.1` sends all selected bounded source jobs to one external process. The adapter can load/cache a model once and process the jobs sequentially or in its own internal batches.

P7 single-job protocol remains valid and remains the default for compatibility/debugging.

## 2. Safety remains unchanged

Batching changes execution efficiency, not authority:

```text
canonical_text_authority = canonical_lyrics_only
timing_authority = auxiliary_source_forced_alignment_evidence
```

Each job still binds exact:

```text
job_id
occurrence/track/line identity
source audio path + SHA
source_window_ms
canonical text + SHA
```

Formal evidence still omits raw canonical text and local source paths.

## 3. Invocation

```text
<configured command> \
  --batch-request <temporary-request.json> \
  --batch-response <temporary-response.json>
```

One successful batch call must produce exactly one response entry for every requested job and no extras.

`v4_execute_forced_alignment.py` selects this protocol with:

```text
--execution-mode batch
```

Without that flag, the existing protocol 1.0 single-job path is unchanged.

## 4. Request schema

Top level:

```json
{
  "protocol_version": "1.1",
  "backend_id": "...",
  "backend_version": "...",
  "model_id": "...",
  "model_revision": "...",
  "jobs": [],
  "response_contract": {
    "status": "aligned_batch",
    "timebase": "absolute_source_milliseconds",
    "span_offsets": "python_unicode_character_offsets",
    "job_result_status": "aligned"
  }
}
```

Each request job contains exact local source path/hash/window plus canonical text/SHA. These fields exist only in the temporary local request.

## 5. Response schema

Top level must echo exact backend/model identity:

```json
{
  "protocol_version": "1.1",
  "backend_id": "...",
  "backend_version": "...",
  "model_id": "...",
  "model_revision": "...",
  "status": "aligned_batch",
  "jobs": []
}
```

Each job response:

```json
{
  "job_id": "...",
  "status": "aligned",
  "source_window_ms": [0, 1000],
  "line_source_start_ms": 100,
  "line_source_end_ms": 900,
  "line_confidence": 0.95,
  "spans": []
}
```

Per-job boundaries/spans are normalized through the same P7 validation used by protocol `1.0`.

## 6. Fail-closed

Reject:

- missing/duplicate/extra response job IDs;
- backend/model identity mismatch;
- non-`aligned_batch` top-level status;
- nonzero process exit;
- timeout/no response/invalid JSON;
- source audio SHA drift;
- canonical SHA mismatch;
- per-job source window/boundary/span violations.

Explicit selected job list `[]` means zero work and must not resolve/start the configured executable.

## 7. Timeout semantics

`--timeout-seconds` applies to the entire batch subprocess. Large real-model batches may therefore require a larger explicit timeout than single-job protocol 1.0. The orchestrator does not silently scale or disable the timeout.

## 8. Model identity and multilingual adapters

Batch protocol assumes the configured backend/model identity is auditable for the entire invocation. An adapter must not silently load different language checkpoints while reporting one misleading checkpoint ID.

Safe production options:

1. batch jobs by language/model identity; or
2. define a versioned model-bundle manifest whose ID/revision/hash fully records the per-language checkpoint map.

Until a real adapter is independently reviewed against its current upstream runtime, same-language / same-model batches are the conservative choice.

## 9. Relation to P8/P9

Batch output remains the same P7 source-time evidence family. It can therefore flow unchanged through:

```text
batch forced evidence
-> P8 exact Source-to-Mix projection
-> P9 editor/ASR/forced multi-family shadow fusion
```

Batch mode does not bypass P8 CUT_AWARE projection rules or P9 shadow-only authority.

## 10. Adapter boundary

Protocol 1.1 is backend-neutral. No WhisperX/SOFA/MFA implementation is promoted by this protocol alone.

A previously developed WhisperX reference branch was intentionally not merged during the 2026-08-19 final integration because its upstream runtime assumptions had drifted. Future real adapters must be implemented or refreshed against the then-current upstream API and validated with private calibration/blind data.

## 11. CI boundary

Fake-runner and real-subprocess tests prove one process handles multiple jobs, exact response-set validation, lineage/privacy behavior, and backward compatibility. They do not prove real model throughput or singing accuracy.
