# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 production reconstruction、P1/P1.1、P2 editor shadow、P3 local acoustic evidence、P4 shadow fusion、P5/P6 ASR second-pass、P7 external source forced alignment。P8 在此基础上把 P7 source-ms evidence 投影到 edited-mix time；canonical lyric 与 final timeline authority 均不改变。

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

P6 composite 输出 `asr_evidence_local / asr_evidence`，可直接传给 P4 fusion。

## 3. P7 backend readiness

在真正执行 forced alignment 前先检查：

```powershell
python scripts/v4_alignment_backends.py `
  --external-forced-aligner-command '"<executable>" <adapter-args>'
```

`available/execution_ready=true` 只表示 executable 可解析；**不等于** checkpoint/G2P 对歌声已验证。

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

P7 输出 source-time evidence：

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
```

## 5. P8：投影 forced evidence 到 mix time

P7 运行完成后，使用 exact source run + mapping artifacts 执行：

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

输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

只有这个 mix-time 产物才可以与 editor/ASR 的 mix-time evidence 做多 family 比较。**禁止直接把 P7 source-ms 与 P4 mix-ms evidence 比较。**

## 6. P8 mapping 规则

- `AFFINE` / `PIECEWISE_RATE`：调用现有 `mix_time_for_source()`；
- `CUT_AWARE`：line start/end 必须同在一个 retained source segment；
- boundary 在 confirmed source gap -> line `unprojectable`；
- line 跨 confirmed cut -> `unprojectable`；
- spans 分别投影，合法局部 span 可以保留；
- 只解析 forced evidence 实际引用 occurrences；
- unrelated blocked occurrence 不阻塞本地 projection；
- relevant mapping 缺失、blocked、artifact lineage 不一致 -> 非零失败。

不要手工把 `unprojectable` 线跨 cut 补成连续区间。

## 7. P7/P8 fail-closed 与 privacy

P7 会拒绝 task/input/source SHA/canonical/backend/model/window 漂移；P8 会拒绝 forced evidence/source run/mapping provenance 漂移。

正式 evidence/artifact 不应包含：

```text
canonical raw lyric
local source path
full external command
backend stdout/stderr
```

P8 输出保留 hash、identity、source/mix boundaries、projection status/reason 与 backend/model lineage。

## 8. 推荐的本地生产顺序

```text
1. 准备 task + canonical LRC + source audio + edited mix/editor SRT
2. v4_run / review / cut-overlap materialization，得到 source-to-mix authoritative run
3. editor evidence
4. local ASR first-pass -> second-pass routing/execution（按需）
5. external source forced alignment（按需）
6. P8 forced source-to-mix projection
7. fusion / diagnostics（forced family 接入后）
8. 人工处理 blocked/conflict/high-risk cases
9. render
10. validate_release
11. private calibration/blind 记录真实误差
```

生产前至少保留 task manifest、source run/artifact、所有 evidence/artifact、最终 render/release manifest，便于复现与 Codex 审核。

## 9. Actions 能验证 / 不能验证

CI 可验证 package/CLI、fake external subprocess、projection math、cut semantics、artifact lineage、privacy。

CI 不能证明：

- WhisperX/SOFA/MFA production backend 已安装/运行；
- 某 checkpoint/G2P 对真实歌声准确；
- forced/editor/ASR family 权重与阈值已校准；
- auxiliary evidence 可以直接改 final timing。

真实 backend 上线必须在本地锁定 package/command version、model/checkpoint revision、language/G2P resources、runtime/device、license/source identity，并用 private real-song calibration/blind 验证。
