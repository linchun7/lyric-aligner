# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2-P7 都是 evidence/diagnostic 层；canonical lyric 仍是 final text/order truth，Source-to-Mix 仍是 primary timing truth。

## 1. 当前分层

```text
lyric_aligner/
  alignment/
    backends.py        # backend discovery/readiness
    planner.py         # P3 bounded local jobs
    asr_executor.py    # P3 bounded faster-whisper
    asr_routing.py     # P5 weak -> second-pass plan
    asr_second_pass.py # P6 second-pass composite
    forced_executor.py # P7 external source forced alignment
  evidence/
    editor.py
    fusion.py
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
v4_fuse_evidence.py
```

## 2. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> auxiliary shadow
ASR            -> auxiliary mix acoustic evidence
Forced align   -> auxiliary source acoustic evidence
Fusion         -> uncalibrated diagnostic
```

禁止：

```text
ASR text -> final canonical lyric
forced-aligner output -> final canonical text
source forced ms -> directly compare with mix-ms evidence
missing backend -> fake result
fake protocol E2E -> claim real ML model accuracy
```

## 3. P6 baseline

P6 已把 P5 selected local ASR jobs 安全执行并与 first-pass 合成完整 `asr_evidence_local`：

- empty selection = zero execution/no model load；
- exact original P3 windows；
- first/second model lineage；
- retained private ASR text默认剥离；
- P4 可直接消费 composite。

P6 validate #545 全绿后合入 main `6eacacc50e885684b0265e3abea729b19b1b7725`。

## 4. P7 `alignment/forced_executor.py`

### 4.1 Backend-neutral external protocol

Config：

```python
ExternalForcedAlignmentConfig(
    command,
    backend_id,
    backend_version,
    model_id,
    model_revision,
    timeout_seconds=120.0,
)
```

`command_argv()` 使用 `shlex`，`resolve_command()` 只用首 token 做 `shutil.which()`；整个执行不通过 shell。

### 4.2 Source jobs

只消费 planner 中请求：

```text
source_forced_alignment
```

且必须存在：

```text
job_id
canonical_line_index
canonical_text_sha256
valid source_window_ms
```

Package API 支持显式 `selected_job_ids=[]`：这代表 0 work，command 不解析、不启动。

### 4.3 Resolved asset binding

每 job 用 `ResolvedAssetBinding` 查：

```text
occurrence_id -> track_id
source_audio_path
source_audio_sha256
```

执行前 live hash 再计算；source file drift 直接失败。

Canonical text 不从 filename/LRC 再猜，而由 CLI 从 exact current canonical timeline 建立 `(occurrence_id, line_index)` lookup，再核 planner SHA。

## 5. Request/response protocol

### Request

临时 JSON：

```text
protocol_version=1.0
job/backend/model identity
language_profile
source_audio_path + sha
source_window_ms
canonical_text + sha
response timebase/offset contract
```

Canonical raw text/local path 只存在 TemporaryDirectory request。

### Response

必须回显：

```text
protocol_version
job_id
backend_id/backend_version
model_id/model_revision
status=aligned
exact source_window_ms
```

并提供 line source boundary；可选 character spans。

### Validation

- line/spans 必须在 original source window 内；
- char offsets 必须落在 canonical Python Unicode range；
- char spans 单调、非重叠；
- source time spans 单调、非重叠；
- confidence 若存在必须 `[0,1]`。

## 6. Formal evidence privacy

Normalized result 不保存 canonical fragment正文，而保存：

```text
canonical_text_sha256
canonical_fragment_sha256
char_start/char_end
source_start/end
confidence
source_audio_sha256
backend/model identity
```

External stdout/stderr 不写 artifact。

## 7. `v4_execute_forced_alignment.py`

输入：

```text
task manifest
alignment plan + artifact
track_assets + asset_resolution artifact
source run + artifact
external backend/model identity
```

CLI 强制：

- plan belongs exact source run；
- plan artifact upstream source run；
- asset_resolution artifact output valid；
- asset artifact 是 source run upstream；
- `bindings_from_payload(..., verify_files=True)`；
- canonical timeline artifacts 是 source run upstream；
- planner canonical SHA 与 current timeline 文本一致。

## 8. P7 artifact

```text
stage = source_forced_alignment_evidence
role  = forced_alignment_evidence
```

Upstream：

```text
source run
alignment plan
asset_resolution
canonical timelines
```

Config 保存 backend/model/protocol/timeout、`command_sha256`、executable basename；不保存完整 command。

## 9. Backend registry fix

旧 external backend check 对整条 command 做 `shutil.which(command)`，带参数时会误报 unavailable。

P7 改为与 executor 同样的 shlex 解析，只检查 executable token；args 保留给 runtime。

## 10. Tests

Package fake-runner：

- exact source request；
- canonical text只出现在 temp request；
- empty selection no command；
- source SHA drift；
- canonical SHA mismatch；
- model revision mismatch；
- span bounds；
- command-with-args discovery。

CLI E2E：

- 临时 Python fake aligner 通过**真实 subprocess**执行；
- 完整 task/run/plan/asset/timeline lineage；
- formal output 无 canonical raw text；
- command 不进入 artifact；
- nonexistent executable with work -> nonzero。

## 11. 真实 backend 边界

P7 实现的是 production **adapter/protocol layer**，不是具体 ML backend 安装声明。

WhisperX/SOFA/MFA 任一真正上线仍需单独锁：

```text
package/command version
model/checkpoint ID + revision/hash
language/G2P resources
runtime/device
license assumptions
source/cache identity
private real-song calibration/blind
```

公共 Actions 中的 fake aligner 只证明 protocol，不证明歌声准确率。

## 12. 下一步

Forced alignment 当前是 source-time evidence。P8 必须通过当前 occurrence 的 Source-to-Mix mapping 把 line/span source boundaries 投影到 mix time，才能作为 P4 独立 family 与 editor/ASR 比较；禁止直接比较 source ms 与 mix ms。
