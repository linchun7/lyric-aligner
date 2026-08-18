# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 production reconstruction、P1/P1.1、P2 editor shadow、P3 local acoustic evidence、P4 shadow fusion、P5 second-pass routing、P6 second-pass execution/composite。P7 新增 external source forced-alignment protocol，仍不改变 canonical lyric 或 final timeline authority。

## 1. 主链 / calibration

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py ...
python scripts/v4_recompose_overlap.py ...
python scripts/v4_rebuild_cut.py ...
python scripts/v4_compose_materializations.py ...
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

```powershell
python scripts/v4_dataset_readiness.py scaffold ...
python scripts/v4_dataset_readiness.py clone-candidate ...
python scripts/v4_dataset_readiness.py check ...
python scripts/v4_calibration_workflow.py evaluate ...
python scripts/v4_calibration_workflow.py select ...
python scripts/v4_calibration_workflow.py blind ...
```

## 2. Editor / ASR / fusion

```powershell
python scripts/v4_editor_evidence.py ...
python scripts/v4_alignment_backends.py
python scripts/v4_plan_alignment.py ...
python scripts/v4_execute_asr_evidence.py ...
python scripts/v4_plan_asr_second_pass.py ...
python scripts/v4_execute_asr_second_pass.py ...
python scripts/v4_fuse_evidence.py ...
```

P6 composite 继续输出 `asr_evidence_local / asr_evidence`，可直接传给 P4。

## 3. P7 backend readiness

在真正执行 forced alignment 前先检查：

```powershell
python scripts/v4_alignment_backends.py `
  --external-forced-aligner-command '"<executable>" <adapter-args>'
```

`available/execution_ready=true` 只表示配置的 executable 能找到；**不等于**模型/checkpoint/G2P 已经在歌声上验证。

Command 可包含参数；P7 只用第一个 token 做 executable discovery，运行时保留其余参数，不通过 shell。

## 4. 执行 external source forced alignment

```powershell
python scripts/v4_execute_forced_alignment.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --track-assets-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --external-command '"<aligner-executable>" <adapter-args>' `
  --backend-id "<backend-id>" `
  --backend-version "<backend-version>" `
  --model-id "<model/checkpoint-id>" `
  --model-revision "<revision/hash>" `
  --out "output/<任务>/v4/alignment/forced_evidence.json" `
  --artifact-out "output/<任务>/v4/alignment/forced_evidence.artifact.json" `
  --git-commit "<commit>"
```

默认执行 plan 中所有请求 `source_forced_alignment` 的 jobs。可重复：

```powershell
--job-id "<job-id>"
```

只执行指定 jobs。

## 5. External adapter 必须实现的接口

P7 每个 job 调用：

```text
<configured command> --request <temp-request.json> --response <temp-response.json>
```

Request 包含 exact：

```text
protocol_version=1.0
job/backend/model identity
language_profile
source_audio_path + SHA
source_window_ms
canonical_text + SHA
```

Response 必须：

```text
status=aligned
回显 protocol/job/backend/model identity
回显 exact source_window_ms
line_source_start_ms/line_source_end_ms
optional line_confidence
optional spans with char offsets + source ms + confidence
```

时间必须是 absolute source milliseconds。

完整协议见 `references/forced-alignment-protocol.md`。

## 6. Fail-closed 条件

CLI 会拒绝：

- task input hash 变化；
- plan/source run 不一致；
- track-assets artifact 不是 source run upstream；
- asset/timeline artifact output hash 不一致；
- source audio 文件缺失或 live SHA 变化；
- occurrence/track identity 不一致；
- planner canonical SHA 与 current timeline 不一致；
- configured executable 不存在且确实有 selected work；
- backend/model identity response 不匹配；
- line/span 超出 original source window；
- span char/time 非单调或重叠；
- external process timeout/nonzero/no response/invalid JSON。

不要手改 artifact/SHA 绕过。

## 7. P7 输出

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
```

正式 evidence 不包含 canonical raw text/local source path/full command/stdout/stderr。

每 job 保存：

```text
occurrence/track/line identity
canonical_text_sha256
source_audio_sha256
source_window_ms
line source boundary/confidence
span char offsets + fragment SHA + source boundary/confidence
backend/model identity
```

Artifact normalized config 只记录 `command_sha256` + executable basename，不记录完整 command/path。

## 8. Actions 能验证 / 不能验证

CI 可通过临时 Python fake aligner 的**真实 subprocess**验证 P7 request/response 协议、artifact lineage、source SHA、model identity、privacy 和 executable-not-found failure。

CI 当前不能证明：

- WhisperX/SOFA/MFA production backend 已安装/运行；
- 某 checkpoint/G2P 对真实歌声准确；
- forced alignment 可以自动改 final timing；
- forced-alignment family 已通过 blind release gate。

真实 backend 上线必须在 private/local runtime 锁定 package/command、model revision、language resources、license/runtime identity，再做 private calibration/blind-test。

## 9. 下一步

P7 合入后，下一阶段应把 source forced-alignment boundary 通过 current Source-to-Mix mapping 投影为 mix boundary，然后作为 P4 独立 family；**不能直接拿 source ms 与 editor/ASR 的 mix ms 比较**。
