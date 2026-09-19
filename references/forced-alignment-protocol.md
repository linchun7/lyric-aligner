# External Source Forced-Alignment Protocol

状态：P7 implementation / backend-neutral external adapter  
日期：2026-08-18

## 1. 目标

P7 为 source-side forced alignment 提供一个**真实可执行但不伪装具体模型已安装**的接口。

它不把 faster-whisper ASR 冒充 forced alignment，也不在仓库中硬编码 WhisperX/SOFA/MFA 的未验证调用方式。任何后端只要实现本协议，就可以通过 `v4_execute_forced_alignment.py` 接入；没有配置可执行命令时，存在实际工作必须 fail-closed。

权威边界：

```text
canonical lyric -> final text/order truth
Source-to-Mix   -> primary timing truth
forced alignment -> auxiliary source-side acoustic evidence
```

## 2. CLI

```text
scripts/v4_execute_forced_alignment.py
```

必须提供：

```text
task manifest
P3 alignment plan + artifact
track_assets + asset_resolution artifact
source effective run + artifact
external command
backend id/version
model id/revision
output + artifact output
```

可选重复 `--job-id`：

- 不提供：执行 plan 中全部 `source_forced_alignment` jobs；
- 提供：只执行指定 jobs；
- package API 显式传空 selection：执行 0 jobs，且不要求 executable 真正存在。

## 3. Source identity

P7 不重新猜源音频。

`track_assets.json` 通过既有 fail-closed binding 提供：

```text
occurrence_id
track_id
source_audio_path
source_audio_sha256
canonical lyric path/hash
canonical selection hash
```

执行前再次检查：

```text
source file exists
live SHA == source_audio_sha256
occurrence identity matches plan
track identity matches plan
canonical timeline text SHA == planner canonical_text_sha256
asset_resolution artifact is upstream of source run
```

任一变化都停止。

## 4. External command invocation

Command string 使用 `shlex` 拆分，**不通过 shell**运行。

首个 token 必须能由 `shutil.which()` 找到。参数可以跟在 executable 后，例如：

```text
"python" "/private/adapter.py" --device cuda
```

Backend registry 同样只检查 executable token，而不是把整条 command 当 executable。

Runtime 调用形式：

```text
<configured command> --request <temp-request.json> --response <temp-response.json>
```

每个 job 使用独立临时目录；结束后 request/response 临时文件由 TemporaryDirectory 清理。

## 5. Request JSON

协议版本：

```text
1.0
```

Request 包含：

```json
{
  "protocol_version": "1.0",
  "job_id": "...",
  "backend_id": "...",
  "backend_version": "...",
  "model_id": "...",
  "model_revision": "...",
  "language_profile": "en",
  "source_audio_path": "<local private path>",
  "source_audio_sha256": "...",
  "source_window_ms": [500, 2500],
  "canonical_text": "<private canonical line>",
  "canonical_text_sha256": "...",
  "response_contract": {
    "timebase": "absolute_source_milliseconds",
    "span_offsets": "python_unicode_character_offsets",
    "status": "aligned"
  }
}
```

`canonical_text` 和 local source path 只存在于**临时私有 request**，不会复制到正式 evidence artifact。

## 6. Response JSON

后端必须回显 exact identity：

```text
protocol_version
job_id
backend_id
backend_version
model_id
model_revision
source_window_ms
status = aligned
```

并返回：

```text
line_source_start_ms
line_source_end_ms
optional line_confidence [0,1]
optional spans[]
```

Span：

```json
{
  "char_start": 0,
  "char_end": 5,
  "source_start_ms": 900,
  "source_end_ms": 1300,
  "confidence": 0.95
}
```

要求：

- char offsets 为 Python Unicode 字符索引；
- char spans 单调、不重叠、在 canonical line 范围内；
- source timestamps 使用**绝对 source milliseconds**；
- line/spans 均必须落在 planner 的 `source_window_ms` 内；
- time spans 单调、不重叠；
- confidence 若存在必须在 `[0,1]`。

## 7. 正式 evidence

正式 payload 不保存 canonical raw text，只保存：

```text
job/occurrence/track/line identity
canonical_text_sha256
source_window_ms
source_audio_sha256
line source boundary
line confidence
span char_start/char_end
canonical_fragment_sha256
span source boundary/confidence
backend/model identity
```

stdout/stderr 只用于失败诊断，不写入 evidence artifact，避免外部 backend 打印歌词时泄漏。

## 8. Artifact

```text
stage = source_forced_alignment_evidence
role  = forced_alignment_evidence
```

Upstream：

```text
alignment plan artifact
asset_resolution artifact
source run artifact
exact canonical timeline artifacts
```

Normalized config 保存：

```text
protocol_version
backend_id/backend_version
model_id/model_revision
timeout_seconds
command_sha256
command_executable_basename
selected_job_ids
source artifact IDs
```

不保存完整 external command/path。

## 9. GitHub Actions 能真实验证什么

Actions 可以使用临时 Python fake backend 通过**真实 subprocess**验证：

- request/response JSON 协议；
- command-with-arguments 解析；
- executable-not-found fail-closed；
- source audio SHA drift；
- canonical SHA mismatch；
- model revision mismatch；
- response window/span bounds；
- artifact lineage；
- raw canonical text不进入正式 evidence。

## 10. GitHub Actions 当前不能证明什么

当前 repo/workflow 没有安装和配置真实 WhisperX/SOFA/MFA production model，也没有 private real-song/reference truth。因此 P7 **不能声称**：

- WhisperX/SOFA/MFA 已在 Actions 中真实跑通；
- 某个 forced-aligner 对歌声的真实准确率；
- 某语言/G2P/checkpoint 已 production-ready；
- forced alignment 可以自动改 final timing 或直接通过 release gate。

真实 backend 上线还必须锁定：

```text
backend package/command version
model/checkpoint ID + revision/hash
language/G2P resources
runtime/device
license/usage assumptions
source audio SHA/cache identity
private calibration + blind-test result
```
