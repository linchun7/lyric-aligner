# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 production reconstruction、P1 strict calibration/blind、P1.1 dataset readiness、P2 editor shadow evidence、P3 local acoustic evidence。P4 新增 **shadow-only evidence fusion**；未经 private calibration，它不会修改 canonical lyric、Source-to-Mix 或 FINAL.srt。

## 1. 现有生产入口

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
python scripts/v4_recompose_overlap.py ...
python scripts/v4_rebuild_cut.py ...
python scripts/v4_compose_materializations.py ...
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

Private dataset / calibration：

```powershell
python scripts/v4_dataset_readiness.py scaffold ...
python scripts/v4_dataset_readiness.py clone-candidate ...
python scripts/v4_dataset_readiness.py check ...
python scripts/v4_calibration_workflow.py evaluate ...
python scripts/v4_calibration_workflow.py select ...
python scripts/v4_calibration_workflow.py blind ...
```

Editor shadow：

```powershell
python scripts/v4_editor_evidence.py ...
```

## 2. P3 acoustic backend / planner / ASR

Backend check：

```powershell
python scripts/v4_alignment_backends.py
```

Local planner：

```powershell
python scripts/v4_plan_alignment.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --editor-evidence "output/<任务>/v4/editor/editor_evidence.json" `
  --editor-evidence-artifact "output/<任务>/v4/editor/editor_evidence.artifact.json" `
  --out "output/<任务>/v4/alignment/plan.json" `
  --artifact-out "output/<任务>/v4/alignment/plan.artifact.json"
```

Local faster-whisper evidence：

```powershell
python scripts/v4_execute_asr_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --model-id "<faster-whisper-model-id>" `
  --device cpu `
  --compute-type int8 `
  --out "output/<任务>/v4/alignment/asr_evidence.json" `
  --artifact-out "output/<任务>/v4/alignment/asr_evidence.artifact.json"
```

默认 ASR evidence 不写 raw text；只有 private output 明确需要时才使用 `--include-private-text`。

GitHub Actions #493 已验证 faster-whisper 依赖环境可以安装/检查，但 CI 不下载/运行真实 Whisper model。

## 3. P4 生成 Evidence Fusion Shadow

最完整输入：source run + P2 editor evidence + P3 ASR evidence。

```powershell
python scripts/v4_fuse_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --editor-evidence "output/<任务>/v4/editor/editor_evidence.json" `
  --editor-evidence-artifact "output/<任务>/v4/editor/editor_evidence.artifact.json" `
  --asr-evidence "output/<任务>/v4/alignment/asr_evidence.json" `
  --asr-evidence-artifact "output/<任务>/v4/alignment/asr_evidence.artifact.json" `
  --out "output/<任务>/v4/evidence/fusion.json" `
  --artifact-out "output/<任务>/v4/evidence/fusion.artifact.json" `
  --git-commit "<commit>"
```

也允许只提供 editor 或只提供 ASR；完全没有 auxiliary evidence 时 output 会是 source-only LOW shadow state。

默认：

```text
conflict_boundary_ms = 500
```

可显式覆盖：

```powershell
... --conflict-boundary-ms 500
```

该参数是未校准 shadow diagnostic threshold，不是 release threshold。

## 4. P4 输出怎么读

Artifact：

```text
stage = evidence_fusion_shadow
role  = evidence_fusion
```

Root/line 固定：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

Shadow levels：

```text
LOW      source only
MEDIUM   source + exactly one auxiliary boundary family
HIGH     source + editor + ASR，且 editor/ASR 分歧 <= threshold
CONFLICT editor + ASR 分歧 > threshold
```

**不要把 HIGH 当成“可发布”。** 当前 HIGH 只说明两个 auxiliary family 在 bootstrap threshold 下相互接近。

## 5. P4 lineage 失败时怎么办

Fusion 会拒绝：

- task input hash 已变化；
- source run artifact 不匹配；
- timeline artifact 不属于 current run；
- editor/asr evidence 来自另一个 source run；
- auxiliary artifact 没有把 current source run 作为 upstream；
- editor canonical text SHA 与当前 timeline 不一致；
- auxiliary evidence 指向不存在的 canonical line。

遇到这些问题要从对应 upstream stage 重跑，不手改 artifact ID/SHA。

## 6. P4 privacy

Fusion output 不复制：

```text
canonical raw lyric text
editor raw text
ASR raw text
```

即使 ASR input artifact 是 private-text opt-in，fusion 也只保留 identity/hash/boundary/score，不复制正文。

## 7. Forced Alignment 当前如何用

当前 main 仍没有内置 production forced-aligner executor。

已有：

```text
source_forced_alignment capability
source local-window planning
external command readiness check
```

如果没有明确安装/配置/验证 backend，必须保持 unavailable/unready，不自动回退假结果。

## 8. GitHub Actions 能 / 不能做

CI 可以真实验证：

- P4 LOW/MEDIUM/HIGH/CONFLICT deterministic rules；
- HIGH 仍不可 release；
- artifact lineage / cross-run fail；
- privacy；
- P0-P3 全部 regressions；
- faster-whisper dependency environment。

CI 当前不能证明：

- real-song ASR/forced-alignment timing accuracy；
- P4 shadow level 与真实错误率的统计关系；
- 500ms conflict threshold 是最优值；
- HIGH 可以安全自动改 timing/发布。

这些只能通过授权 private calibration + blind_test 完成。

## 9. 下一步

1. P4 latest-head CI 全绿并合入；
2. P5 two-pass ASR routing：只对第一遍弱证据 local jobs 调度 accuracy pass；
3. 选择并实现具体 forced-aligner adapter 前先锁 model/license/language/cache lineage；
4. 用 private calibration 评估 editor/ASR/forced-alignment family；
5. 只有 blind gate 证明收益后，才设计 calibrated boundary application/release gate。
