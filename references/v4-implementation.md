# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2-P9 都属于 evidence/diagnostic 层；P10 只优化 forced backend execution efficiency。canonical lyric 仍是 final text/order truth，Source-to-Mix 仍是 primary timing truth。

## 1. 当前分层

```text
lyric_aligner/
  alignment/
    backends.py          # backend discovery/readiness
    planner.py           # P3 bounded local jobs
    asr_executor.py      # P3 bounded faster-whisper
    asr_routing.py       # P5 weak -> second-pass plan
    asr_second_pass.py   # P6 second-pass composite
    forced_executor.py   # P7 protocol 1.0 single-job subprocess
    forced_batch.py      # P10 protocol 1.1 multi-job single subprocess
    forced_projection.py # P8 source forced evidence -> exact mix time
  evidence/
    editor.py
    fusion.py            # P4 + P9 multi-family shadow fusion
  assets/ audio/ contracts/ evaluation/ pipeline/ review/ text/ timeline/ qa/
```

关键 evidence CLI：

```text
v4_editor_evidence.py
v4_alignment_backends.py
v4_plan_alignment.py
v4_execute_asr_evidence.py
v4_plan_asr_second_pass.py
v4_execute_asr_second_pass.py
v4_execute_forced_alignment.py
v4_project_forced_alignment.py
v4_fuse_evidence.py
```

## 2. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> auxiliary shadow evidence in mix time
ASR            -> auxiliary acoustic evidence in mix time
Forced P7/P10 -> auxiliary acoustic evidence in source time
Forced P8      -> same forced evidence projected to mix time
Fusion P9      -> pairwise diagnostic shadow state
```

禁止：

```text
ASR/forced text -> final canonical lyric
P7/P10 source forced ms -> directly compare with mix-ms evidence
cross-cut forced line -> fake bridged mix interval
batch missing/extra response jobs -> partial success
multilingual batch -> silently switch checkpoints under one model_id
HIGH shadow state -> automatic timing mutation/release
fake protocol E2E -> claim real ML model accuracy
```

## 3. P7/P8/P9 baseline

P7 external protocol 已合入 main `9ad6df4f04b396871f757422bcb35f1fa7676678`；P7 validate #560 全绿。

P8 输出：

```text
stage = forced_alignment_mix_projection
role  = forced_alignment_mix_evidence
mode  = forced_alignment_mix_projection
```

`AFFINE` / `PIECEWISE_RATE` 复用 `mix_time_for_source()`；`CUT_AWARE` 对 gap/cross-cut line `unprojectable`，spans 独立投影。P8 artifact 绑定 source run、forced artifact 与实际 mapping artifacts。

P9 `fusion.py` 只接受 P8 mix-time forced evidence，并与 editor/ASR 做所有 auxiliary pair disagreement；任何 pair 超阈值 -> `CONFLICT`。`HIGH` 仍固定不可 release/auto-apply。

## 4. P10 `alignment/forced_batch.py`

### 4.1 Goal

P7 protocol 1.0 每个 source forced job 运行一次 external process。真实 CTC/singing model 可能在 process start 时加载 checkpoint，因此 N 个歌词 jobs 可能重复加载 N 次模型。

P10 protocol 1.1 把 selected bounded jobs 放入一个 temporary batch request，只启动 **1 个 subprocess**。

入口：

```python
execute_external_forced_alignment_batch(
    plan,
    bindings,
    canonical_text_by_job_id,
    config,
    selected_job_ids=None,
    runner=None,
)
```

### 4.2 Job selection

复用 P7 `_source_jobs(plan)`；只处理 `source_forced_alignment` capability。

```text
selected_job_ids is None -> all planned source-forced jobs
selected_job_ids = [...] -> exact subset
selected_job_ids = [] -> explicit zero work
```

Empty selection 返回 `command_invoked=false`，不调用 `resolve_command()`，因此不存在“空选择退化成全部执行”。

### 4.3 Request preparation

每个 batch job 在启动 external process 前再次验证：

```text
occurrence binding exists
track identity matches
source audio file exists
live source audio SHA matches TrackAsset
canonical text exists
canonical text SHA matches plan
source_window_ms is original bounded planner window
```

Temporary request job 包含 external adapter 真正需要的：

```text
job/occurrence/track/line identity
language_profile
source_audio_path + source_audio_sha256
source_window_ms
canonical_text + canonical_text_sha256
```

这些私有字段不进入 formal evidence。

### 4.4 Batch protocol 1.1

Process invocation：

```text
<resolved command>
  --batch-request <temporary request.json>
  --batch-response <temporary response.json>
```

Top-level response 必须 exact echo：

```text
protocol_version = 1.1
backend_id
backend_version
model_id
model_revision
status = aligned_batch
```

`jobs` 必须是 list，job IDs 必须 unique/non-empty，并且 response job ID set 必须 **exactly equal** request job ID set。

### 4.5 Reuse P7 per-job validation

P10 不复制一套 line/span validator。每个 batch response job 被转换成 P7 protocol 1.0 single response shape，再调用 P7 `_normalize_response()`。

因此继续继承：

```text
source_window exact match
line boundary within source window
span char offsets monotonic/non-overlapping
span source times monotonic/non-overlapping
confidence finite within [0,1]
backend/model revision identity
formal canonical fragment hashes
```

### 4.6 Formal output

Batch output：

```text
backend = external_forced_aligner
execution_mode = batch_subprocess
batch_protocol_version = 1.1
command_invoked = true
command_invocation_count = 1
job_count = N
canonical_text_authority = canonical_lyrics_only
timing_authority = auxiliary_source_forced_alignment_evidence
```

Artifact stage/role 与 P7 不变，因此 P8/P9 无需新增 compatibility adapter。

## 5. Production CLI

`scripts/v4_execute_forced_alignment.py` 新增：

```text
--execution-mode {single,batch}
```

默认 `single`，保持 P7 兼容。CLI 根据 mode 选择 executor，并记录：

```text
protocol_version
requested_execution_mode
execution_mode
command_invocation_count
```

Single mode formal execution mode：

```text
single_job_subprocess
```

Batch：

```text
batch_subprocess
```

## 6. Model identity / multilingual batching

Protocol 1.1 的 top-level model identity 对整个 invocation 生效。因此真实 adapter 不能在一个 batch 内根据每个 job language 静默换 checkpoint，却仍只回显一个 `model_id/model_revision`。

第一版 WhisperX reference adapter 应：

```text
group jobs by language + exact align checkpoint identity
one group -> one P10 batch invocation
```

未来如需要跨语言 persistent process，必须引入 versioned model-bundle manifest，完整记录 per-language checkpoint/revision/hash。

## 7. Tests

`test_v4_forced_alignment_batch.py`：

- 2 jobs -> 1 runner call；
- selected subset -> 1 runner call；
- empty selection -> 0 call；
- missing response job -> fail closed；
- duplicate response job -> fail closed；
- temporary request 有 canonical text，formal output 无 raw text。

`test_v4_forced_alignment_batch_end_to_end.py`：

- 真实 subprocess 启动临时 Python adapter；
- 2 bounded jobs 一次 batch request；
- disk marker 证明只启动 1 次；
- artifact 记录 protocol 1.1、requested/actual execution mode、invocation count；
- plan/assets/run/timeline upstreams 完整；
- formal output/artifact 不保存 raw lyrics/full command。

## 8. Compatibility / downstream

P10 formal evidence 仍是：

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
```

因此 downstream 保持：

```text
P7 single OR P10 batch
-> P8 exact forced source-to-mix projection
-> P9 editor/asr/forced shadow fusion
```

P10 不改变 P8 CUT_AWARE fail-closed，也不改变 P9 shadow/release authority。

## 9. CI / real-data boundary

公共 CI 应验证 compile、documentation contract、unit/E2E、Skill/privacy/diff-check，以及 full validate Python matrix。

公共 CI 不能证明：

```text
real forced-aligner singing accuracy
real model load/runtime throughput improvement size
WhisperX/SOFA checkpoint deployment correctness
language/G2P quality
calibrated release confidence
```

P10 合入后不应继续扩大抽象层。下一步应做 isolated real backend adapter（优先 same-language/same-model batch）和 private real-song calibration/blind。
