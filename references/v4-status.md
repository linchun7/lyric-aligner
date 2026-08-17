# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-external-forced-alignment`  
当前 main：`6eacacc50e885684b0265e3abea729b19b1b7725`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

已合入增量：

```text
P1    strict calibration/blind framework
      1c6babe37067c217d14a7404aa0ed6a1c4779a00
P1.1  private dataset scaffold/readiness
      ad6c403a56209e945a9a61a1eeab1a4bc3c204b4
P2    editor/Jianying multilingual shadow evidence
      2e96569189ac6eb16d987fb2f304403696bc809b
P3    local acoustic planner/backend/faster-whisper executor
      cd3420750c06a55fa1af7d6314ec56971e728928
P4    shadow multi-family evidence fusion
      bc4e10760ffee2e5990ca580d5edbadd7d561eaf
P5    bounded ASR second-pass routing
      1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d
P6    ASR second-pass execution + composite evidence
      6eacacc50e885684b0265e3abea729b19b1b7725
```

P3 validate #493、P4 #517、P5 #530、P6 #545 均在 ASR environment + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后合入。

## 2. P6 已完成的关键能力

P6 已经真正把 P5 selected local jobs 接回 P3 bounded faster-whisper executor，并输出 P4 可直接消费的 composite ASR evidence：

```text
stage = asr_evidence_local
role = asr_evidence
mode = composite_second_pass_evidence
```

硬安全规则：

- P5 `selected_job_ids=[]` -> second-pass 0 execution，模型不加载；
- second-pass 只能执行 original P3 exact local window；
- selected job identity/window/canonical SHA 被改写即失败；
- first/second model identity 必须不同且与 P5 记录一致；
- 未升级 jobs 保留 first-pass，升级 jobs 被 second-pass result 替换；
- composite 默认会剥离 retained first-pass 的 raw observed/segment/word text。

P6 validate #545 已真实覆盖 fake-model selected execution、zero-selection/no-model-load、CLI E2E、lineage/privacy；公共 Actions **没有下载/运行真实 accuracy Whisper model**。

## 3. 当前 P7：External Source Forced Alignment

P7 不把 ASR 冒充 forced alignment，也不假设 WhisperX/SOFA/MFA 已安装。它建立一个 backend-neutral、真实 subprocess 可执行的 JSON 协议。

新增：

```text
lyric_aligner/alignment/forced_executor.py
scripts/v4_execute_forced_alignment.py
references/forced-alignment-protocol.md
```

并更新：

```text
lyric_aligner/alignment/backends.py
lyric_aligner/alignment/__init__.py
```

Artifact：

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
```

Forced alignment 仍是：

```text
canonical_text_authority = canonical_lyrics_only
timing_authority = auxiliary_source_forced_alignment_evidence
```

不直接拥有 final timing。

## 4. P7 source / canonical identity

P7 同时验证：

```text
task fingerprint + live input hashes
source run artifact
alignment plan artifact
asset_resolution artifact
canonical timeline artifacts
track/occurrence identity
source audio live SHA
canonical line text SHA
```

`asset_resolution` artifact 必须是当前 source run upstream。源音频不重新猜文件，直接使用已 fail-closed 的 `track_assets.json` binding。

## 5. External protocol

配置必须显式提供：

```text
external command
backend_id / backend_version
model_id / model_revision
```

Command 可带参数，但不用 shell；只解析 executable token 并用 `shutil.which()` 验证。

每 job 临时调用：

```text
<command> --request <temp-request.json> --response <temp-response.json>
```

Request 中可以暂时包含 local source path + canonical text，供本地 forced aligner 工作；TemporaryDirectory 结束后清理。

Response 必须回显 exact protocol/job/backend/model identity，并返回绝对 source-ms 的 line boundary 与可选 character spans。

## 6. P7 evidence privacy

正式 evidence 不保存：

```text
canonical raw text
source local path
external command full path/string
backend stdout/stderr
```

只保存：

```text
canonical text SHA
canonical fragment SHA
char offsets
source boundaries/confidence
source audio SHA
backend/model identity
```

Artifact 只保存 `command_sha256` + executable basename，不保存完整 command。

## 7. P7 tests 已写入

Package tests：

- fake runner exact source window；
- raw canonical text只存在临时 request，不进入 evidence；
- explicit empty selection 不解析/执行不存在的 command；
- source audio SHA drift fail；
- canonical SHA mismatch fail；
- response model_revision mismatch fail；
- out-of-window span fail；
- backend registry 正确处理 command + arguments。

CLI E2E：

- 使用临时 Python fake aligner **真实 subprocess**读 request/写 response；
- 完整 task/run/plan/asset/timeline artifact lineage；
- source boundary/span output；
- command 未写入 artifact；
- configured executable 不存在且有工作时必须非零失败。

P7 尚未通过本分支 latest-head GitHub Actions，因此当前不能宣称已可合并。

## 8. GitHub Actions 明确能 / 不能做

Actions 能验证 P7 的 external protocol/subprocess/lineage/privacy/fail-closed 行为。

Actions 当前**不能**证明：

- WhisperX/SOFA/MFA production backend 已安装或已跑通；
- 某 forced-aligner/checkpoint/G2P 对真实歌声的准确率；
- forced alignment 可以自动改 final timing；
- P4/P7 family 的发布阈值已校准。

这些需要实际 backend/model/language resources + private real-song calibration/blind-test。

## 9. 仍未完成

优先剩余：

1. P7 latest-head CI 全绿并合入；
2. 将 forced-alignment source boundaries 通过当前 Source-to-Mix mapping 投影到 mix time，并作为 P4 独立 evidence family；
3. 选择/配置一个真实 forced-aligner backend 后做 private runtime 验证；
4. private calibration/blind 数据真实填充与误差分析；
5. local vocal separation / singing refinement（仅高风险窗口）；
6. calibrated evidence-family boundary application/release gate；
7. same-region cut+overlap joint acoustic model。

> **当前正确表述：P0/P1/P1.1/P2/P3/P4/P5/P6 已进入 main；P7 已实现 backend-neutral external forced-alignment 执行协议与生产 CLI，等待 latest-head CI 验收。真实 WhisperX/SOFA/MFA 仍未在 Actions 中运行。**
