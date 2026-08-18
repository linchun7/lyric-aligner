# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> 当前代码链已覆盖 production reconstruction、editor shadow、local ASR first/second pass、external source forced alignment、forced source→mix projection，以及 editor/ASR/forced 三 family 的 shadow fusion。canonical lyric 与 Source-to-Mix 仍分别拥有 final text/order 与 primary timing authority。

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

## 5. P8：forced evidence 投影到 mix time

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

只有这个 mix-time 产物才允许进入 fusion。**禁止直接比较 P7 source-ms 与 editor/ASR mix-ms。**

### P8 CUT_AWARE 规则

- line start/end 必须同在一个 retained source segment；
- boundary 在 confirmed source gap -> `unprojectable`；
- line 跨 confirmed cut -> `unprojectable`；
- spans 独立投影；
- unrelated blocked occurrence 不阻塞当前 forced job；
- relevant mapping/artifact lineage 不完整 -> 非零失败；
- 不得手工把 `unprojectable` line 跨 cut 补成连续 interval。

## 6. P9：三 family shadow fusion

完整命令：

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
  --artifact-out "output/<任务>/v4/evidence/fusion.artifact.json" `
  --git-commit "<commit>"
```

Editor、ASR、forced 参数都是可选 family；但 payload 与 artifact 必须成对提供。

### P9 shadow state

```text
0 auxiliary boundary family -> LOW
1 auxiliary boundary family -> MEDIUM
>=2, all available pairs within threshold -> HIGH
any available pair over threshold -> CONFLICT
```

Pairwise diagnostics：

```text
editor_asr_boundary_disagreement_ms
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
```

`forced_alignment_line_counts` 会统计 `projected / unprojectable / absent`。

**注意：LOW/MEDIUM/HIGH/CONFLICT 都是未校准 shadow support state。即使 HIGH，也不会自动改 timing 或通过 release gate。**

## 7. P7/P8/P9 fail-closed / privacy

P7 拒绝 task/input/source SHA/canonical/backend/model/window 漂移；P8 拒绝 source run/mapping/forced artifact provenance 漂移；P9 拒绝 cross-run evidence、artifact hash 漂移、unknown canonical line、track/text hash mismatch，以及 `unprojectable` forced line 夹带 mix boundary。

正式 evidence/artifact 不应包含：

```text
canonical raw lyric
local source path
full external command
backend stdout/stderr
```

Fusion 输出只保留 hash/identity/timing/confidence/backend-model lineage 与 diagnostics。

## 8. 推荐的本地 Codex 生产顺序

```text
1. 准备 task + canonical LRC + source audio + edited mix/editor SRT
2. 运行 v4_run；处理 review/cut/overlap，得到 authoritative effective run
3. 生成 editor evidence
4. 生成 local ASR first-pass；只对弱区跑 second-pass
5. 需要时运行 external forced aligner
6. 把 P7 forced evidence 经 P8 投影到 mix time
7. 运行 P9 fusion，先看 CONFLICT / unprojectable / missing family
8. 人工核查高风险行，不从 HIGH 直接推断“可自动发布”
9. render
10. validate_release
11. 将真实人工 ground truth 写入 private calibration/blind 数据集
12. 评估各语言/风险桶 family error，冻结 threshold 后再跑 blind
```

Codex 在每个任务开始时应先读取：

```text
SKILL.md
references/v4-runtime-guide.md
references/v4-status.md
references/v4-implementation.md
```

遇到 artifact/identity/blocking error 时先修输入或重新生成上游，**不要手改 JSON 绕过 provenance**。

## 9. 第一次真实生产建议

第一批真实数据不要直接追求“全自动”。建议每种主要语言先取 3–5 个 30–90 秒片段，至少覆盖：正常 global-rate、动态 local stretch、cut 附近、overlap、弱人声/强伴奏、editor 识别差的语言。先比较：

```text
Source-to-Mix boundary error
Editor boundary error
ASR boundary error
Forced boundary error
Fusion conflict / coverage
```

再决定真实 forced backend、模型、G2P 与 `conflict_boundary_ms` 是否需要按语言/风险桶拆分。

## 10. Actions 能验证 / 不能验证

CI 可验证 package/CLI、fake external subprocess、projection math、cut semantics、pairwise conflict、artifact lineage、privacy。

CI 不能证明：

- WhisperX/SOFA/MFA 或其他 production backend 已安装/运行；
- 某 checkpoint/G2P 对真实歌声准确；
- editor/ASR/forced family 统计上真正独立；
- family 权重/阈值已校准；
- auxiliary evidence 可以自动改 final timing。

真实 backend 上线必须锁定 package/command version、model/checkpoint revision、language/G2P resources、runtime/device、license/source identity，并用 private real-song calibration/blind 验证。
