# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> 当前代码链已覆盖 production reconstruction、editor shadow、local ASR first/second pass、external source forced alignment、forced source→mix projection，以及 editor/ASR/forced 三 family 的 shadow fusion。P10 增加 forced-alignment batch protocol 1.1，仅优化真实 backend 的进程/模型加载效率，不改变 canonical lyric 或 Source-to-Mix authority。

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

## 2. Editor / ASR

```powershell
python scripts/v4_editor_evidence.py ...
python scripts/v4_alignment_backends.py
python scripts/v4_plan_alignment.py ...
python scripts/v4_execute_asr_evidence.py ...
python scripts/v4_plan_asr_second_pass.py ...
python scripts/v4_execute_asr_second_pass.py ...
```

P6 composite 输出 `asr_evidence_local / asr_evidence`，可直接传给 fusion。

## 3. P7/P10 backend readiness

在真正执行 forced alignment 前先检查：

```powershell
python scripts/v4_alignment_backends.py `
  --external-forced-aligner-command '"<executable>" <adapter-args>'
```

`available/execution_ready=true` 只表示 executable 可解析；**不等于** checkpoint/G2P 对歌声已验证。

## 4. 执行 external source forced alignment

默认保持 P7 single-job protocol 1.0：

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
  --execution-mode single `
  --out "output/<任务>/v4/alignment/forced_evidence.json" `
  --artifact-out "output/<任务>/v4/alignment/forced_evidence.artifact.json"
```

P10 batch mode 只需改：

```powershell
--execution-mode batch
```

Batch adapter invocation contract：

```text
<external command>
  --batch-request <temporary-request.json>
  --batch-response <temporary-response.json>
```

Protocol 1.1 会把当前 selected source-forced jobs 放进一个 request，外部 adapter 只启动一次。成功 response 必须包含与 request **完全相同**的 job ID 集合。

### P10 artifact fields

```text
protocol_version = 1.0 | 1.1
requested_execution_mode = single | batch
execution_mode = single_job_subprocess | batch_subprocess
command_invocation_count
```

Batch 成功时通常：

```text
execution_mode = batch_subprocess
command_invocation_count = 1
```

显式空选择：

```text
selected_job_ids = []
command_invoked = false
command_invocation_count = 0
job_count = 0
```

不得把空选择解释为“跑全部”。

P7/P10 formal 输出仍是：

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
```

## 5. P10 batch adapter 的 model lineage

同一次 batch invocation 必须有可审计的一致 backend/model identity：

```text
backend_id/backend_version
model_id/model_revision
```

如果真实 WhisperX adapter 按语言使用不同 align checkpoint，第一版生产实现应按：

```text
same language + same align model -> one batch
```

不要在同一 batch 内静默切模型却只写一个 model ID。以后如需跨语言 batch，应引入 versioned model-bundle manifest。

## 6. P8：forced evidence 投影到 mix time

```powershell
python scripts/v4_project_forced_alignment.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --forced-evidence "output/<任务>/v4/alignment/forced_evidence.json" `
  --forced-evidence-artifact "output/<任务>/v4/alignment/forced_evidence.artifact.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --out "output/<任务>/v4/alignment/forced_mix_evidence.json" `
  --artifact-out "output/<任务>/v4/alignment/forced_mix_evidence.artifact.json"
```

P8 不关心上游 forced evidence 是 single 还是 batch，只要 P7/P10 formal artifact contract 成立。

输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

只有这个 mix-time 产物才允许进入 fusion。禁止直接比较 source-ms 与 editor/ASR mix-ms。

CUT_AWARE：line start/end 必须同在一个 retained source segment；gap/cross-cut -> `unprojectable`；不得跨 confirmed cut 补假连续 interval。

## 7. P9：三 family shadow fusion

```powershell
python scripts/v4_fuse_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --editor-evidence "output/<任务>/v4/evidence/editor.json" `
  --editor-evidence-artifact "output/<任务>/v4/evidence/editor.artifact.json" `
  --asr-evidence "output/<任务>/v4/alignment/asr_composite.json" `
  --asr-evidence-artifact "output/<任务>/v4/alignment/asr_composite.artifact.json" `
  --forced-mix-evidence "output/<任务>/v4/alignment/forced_mix_evidence.json" `
  --forced-mix-evidence-artifact "output/<任务>/v4/alignment/forced_mix_evidence.artifact.json" `
  --conflict-boundary-ms 500 `
  --out "output/<任务>/v4/evidence/fusion.json" `
  --artifact-out "output/<任务>/v4/evidence/fusion.artifact.json"
```

Shadow state：

```text
0 auxiliary boundary family -> LOW
1 -> MEDIUM
>=2, all pairs within threshold -> HIGH
any pair over threshold -> CONFLICT
```

即使 HIGH，也不会自动改 timing 或通过 release gate。

## 8. P7/P10/P8/P9 fail-closed / privacy

P7/P10 拒绝 task/input/source SHA/canonical/backend/model/window 漂移；batch 额外拒绝 missing/extra/duplicate response job ID。P8 拒绝 source run/mapping/forced artifact provenance 漂移；P9 拒绝 cross-run evidence、artifact hash 漂移、unknown canonical line、track/text hash mismatch，以及 `unprojectable` forced line 夹带 mix boundary。

正式 evidence/artifact 不应包含：

```text
canonical raw lyric
local source path
full external command
backend stdout/stderr
```

Batch temporary request 可以把 canonical text/source path 交给本地 external adapter，但这些只存在临时目录，不进入 formal evidence。

## 9. 推荐的本地 Codex 生产顺序

```text
1. task + canonical LRC + source audio + edited mix/editor SRT
2. v4_run；处理 review/cut/overlap
3. editor evidence
4. ASR first-pass + bounded second-pass
5. external forced aligner：同模型多个 jobs 推荐 batch
6. P8 forced source->mix projection
7. P9 fusion，先看 CONFLICT / unprojectable / missing family
8. 人工核查高风险行
9. render + validate_release
10. ground truth -> private calibration/blind
```

遇到 artifact/identity/blocking error 时先修输入或重新生成上游，**不要手改 JSON 绕过 provenance**。

## 10. Actions 能验证 / 不能验证

CI 可验证 package/CLI、fake external subprocess、batch one-process semantics、exact response-set contract、projection math、cut semantics、pairwise conflict、artifact lineage、privacy。

CI 不能证明：

- WhisperX/SOFA/MFA 或其他 production backend 已真实安装/运行；
- 某 checkpoint/G2P 对歌声准确；
- batch 对真实模型的吞吐收益大小；
- editor/ASR/forced family 统计上真正独立；
- auxiliary evidence 可以自动改 final timing。

P10 合入后，下一阶段应停止继续扩抽象层，直接做 isolated real backend adapter + private real-song calibration/blind。
