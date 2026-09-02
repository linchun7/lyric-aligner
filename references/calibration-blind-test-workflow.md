# v4 Calibration / Blind-Test Workflow

更新：2026-08-17  
状态：P1 measurement framework（不包含真实准确率结论）

## 1. 目的

架构 P0 已覆盖 Source-to-Mix、confirmed overlap、confirmed middle cut 以及可证明互不冲突的 cut+overlap composition。下一步不是继续堆模型，而是建立**可审计、不能用 blind_test 调参**的真实任务测量流程。

本流程只回答：

- baseline 与 candidate 在同一批真实真值上谁更好；
- calibration 上哪个 candidate 可以被锁定；
- 锁定后的 candidate 在 blind_test 上是否通过非回归/最低质量门禁；
- 哪类错误（边界、cut、overlap、track、fragment、语言）仍是主要瓶颈。

在真实私有数据执行完成前，**不得宣称固定百分比准确率提升**。

## 2. Canonical CLI

官方入口：

```text
scripts/v4_calibration_workflow.py
```

低层 `evaluate_dataset.py`、`evaluate_calibration_dataset.py`、`calibration_gate.py` 可用于内部分析/兼容，但生产 P1 验收以 `v4_calibration_workflow.py` 的 strict contract 为准。

## 3. Dataset schema 1.1

每份 baseline/candidate manifest：

```json
{
  "schema_version": "1.1",
  "dataset": "private-real-v1",
  "dataset_revision": "2026-08-17-r1",
  "cases": [
    {
      "id": "opaque-case-001",
      "split": "calibration",
      "language": "zh",
      "source_group": "opaque-source-family-001",
      "reference_srt": "...",
      "predicted_srt": "...",
      "qa_json": "...",
      "audio_duration_seconds": 60.0,
      "expected_cuts": [],
      "expected_overlaps": [],
      "expected_occurrences": []
    }
  ]
}
```

### `dataset_revision`

同一次 calibration/blind protocol 的所有 baseline/candidate manifests 必须使用同一个 opaque revision。它只描述“这版私有数据定义”，不包含歌词内容。

### `source_group`

用于隔离同一首歌/同一源版本/高度相关片段。相同 `source_group` **禁止跨 train / calibration / blind_test**。

例如同一首歌切成两个 45 秒片段，不应一个放 calibration、一个放 blind_test；它们应共享同一 source_group，并整体进入一个 split。

## 4. Split isolation

### Calibration 阶段

```powershell
python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/.../baseline.dataset.json" `
  --split calibration `
  --candidate-id baseline `
  --candidate-revision "<baseline commit/revision>" `
  --out "private/.../baseline.calibration.eval.json"
```

严格规则：

- 检查所有 case 的 metadata/source_group 是否跨 split 泄漏；
- **只读取 calibration case 的 reference/prediction/QA 文件**；
- blind_test prediction/QA 可以此时根本不存在；
- 不输出 blind_test 指标。

这条规则防止“校准时顺手看了 blind 结果”。

### Blind 阶段

只有 calibration candidate 已锁定后，才 materialize / evaluate blind predictions：

```powershell
python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/.../candidate.dataset.json" `
  --split blind_test `
  --candidate-id candidate-a `
  --candidate-revision "<same locked revision>" `
  --out "private/.../candidate.blind.eval.json"
```

## 5. Candidate identity lock

每个 selected split 的所有 `qa_json` 必须具有完全相同 runtime identity：

```text
algorithm_version
calibration_profile_version
calibration_profile_id
```

Evaluation 还必须显式记录：

```text
candidate_id
candidate_revision
```

Calibration selection 最终锁定：

```text
selected_candidate_id
selected_candidate_revision
selected_runtime_identity
selected_calibration_evaluation_sha256
policy_sha256
selection_payload_sha256
```

Blind gate 会逐项重新核对。即使 candidate 名字不变，只要 revision/profile/algorithm 发生变化，也不能冒充 calibration 时被选中的 candidate。

## 6. Ground-truth identity

Ground-truth SHA 由以下内容构成：

- dataset + dataset_revision；
- selected split；
- opaque case id/language/source_group；
- reference SRT SHA-256；
- expected cuts/overlaps/occurrences；
- audio duration metadata。

**Prediction/QA path 和预测内容不进入 ground-truth identity。**

因此 baseline 与 candidate 可以使用不同 prediction/QA 文件，但只要真值完全一致，就得到相同 ground-truth SHA。

## 7. Calibration selection

先分别评估 baseline 和一个或多个 candidate：

```powershell
python scripts/v4_calibration_workflow.py select `
  --baseline "baseline.calibration.eval.json" `
  --candidate "candidate-a.calibration.eval.json" `
  --candidate "candidate-b.calibration.eval.json" `
  --policy "calibration.policy.json" `
  --out "selection.json"
```

规则：

1. 每个 candidate 先通过 policy 全部 gates；
2. 不通过 gate 的 candidate 不参与 ranking；
3. 通过者按 policy 的显式 ranking 做确定性选择；
4. selection artifact 锁定 candidate/revision/runtime/evaluation/policy。

## 8. Blind gate

```powershell
python scripts/v4_calibration_workflow.py blind `
  --baseline "baseline.blind.eval.json" `
  --candidate "candidate-a.blind.eval.json" `
  --selection "selection.json" `
  --policy "blind.policy.json" `
  --out "blind.gate.json"
```

Blind gate 要求：

- candidate ID 与 selection 一致；
- candidate revision 一致；
- runtime identity 一致；
- dataset name/revision 一致；
- baseline/candidate blind ground-truth SHA、case IDs 完全一致；
- blind policy 全部 gates 通过。

Gate 失败时 CLI 返回非零状态码。

## 9. Metrics

沿用现有 sequence-aware evaluator 的核心指标：

```text
unit/sequence edit metrics
line exact precision/recall/F1
missing / extra / wrong-order lines
cue split / merge
onset / offset MAE, P50/P90/P95
cut precision / recall / F1
overlap precision / recall / F1
track attribution
review density / runtime
```

P1 增加 matched-cut boundary：

```text
cut_boundary_match_count
cut_boundary_mae_ms
cut_boundary_p50_ms
cut_boundary_p90_ms
cut_boundary_p95_ms
cut_boundary_within_250ms_rate
cut_boundary_within_500ms_rate
```

注意：cut boundary 误差只统计 matched cuts，因此 policy 必须同时约束 cut recall / match coverage，不能只看很漂亮的 MAE/P95。

## 10. Privacy

公开/可提交产物只允许：

- aggregate metrics；
- opaque case/source IDs；
- SHA-256 identities；
- candidate/runtime/profile IDs；
- gate pass/fail。

私有音频、reference SRT、prediction SRT、歌词正文、逐 case 原始证据继续留在 private workspace，不进入公开仓库。

## 11. 下一步真实执行

框架 merge 后，真实工作顺序是：

1. 建立小而高质量的 private dataset；
2. 同源歌曲严格 source_group 隔离；
3. baseline 跑 calibration；
4. candidate(s) 跑 calibration；
5. 按 policy select 并冻结；
6. 首次生成 blind predictions；
7. blind gate；
8. 根据 blind error breakdown 决定下一工程优先级。

只有第 7 步完成后，才有资格讨论“准确率提升了多少”。

### 11.1 2026-09-02 private baseline 演进

第一版 r1 benchmark（8 个 opaque case，4 calibration / 4 blind_test，8 个 source_group 完全隔离）验证了 split-isolated workflow 与 blind 禁读机制，但后续审计确认其 reference/prediction authority 混用了旧人工 segmentation 与已人工闭合 production 结果。r1 因此仅保留为 exploratory history，不再用于正式 candidate selection。

正式 baseline 升级为 `2026-09-02-r2-auto`。case/source_group 划分不变，但 authority 改为：reference = 已验收 pre-display production SRT；prediction = raw `v4_run` per-occurrence timeline 按 occurrence authority window 物化，不应用人工 review、overlap recomposition、reference-retime、editor reconciliation 或 display override。`mix_end_ms=null` 按 renderer 的 5 秒 open-line 语义处理后再做 occurrence clamp。r2 已生成独立 baseline lock，仍明确记录 `blind_predictions_materialized=false` / `blind_metrics_observed=false`。

r2 calibration aggregate（仅公开 aggregate，不含歌词/逐 case私有证据）：

```text
unit_f1                          = 0.999221
sequence_wer                     = 0.001560
line_exact_f1                    = 0.999167
cue_text_exact_match_rate        = 1.000000
boundary_mae_ms                  = 17.982
boundary_p95_ms                  = 6.000
review_candidates_per_10_min     = 1.739130
publish_ready_rate               = 0.000000
cut_recall                       = 0.000000
overlap_event_recall             = 0.000000
```

这说明 calibration 范围内普通歌词 timing/text 已非常接近 production truth，主要剩余差距是结构事件与 review authority，而不是继续压低普通 cue timing 误差。

### 11.2 首个 B 候选已在 calibration 淘汰

Fine-anchored multiscale transition diagnostic 曾作为第一个 B 候选，仅在 calibration 上验证。结果 2/2 已确认真实 overlap 都被错误建议为 sequential clear，构成不可接受的 false-clear。随后追加 aligned dual-source STFT/NNLS mixture-gain 可行性检查，clear 与 overlap 的分数区间仍明显重叠，不能形成安全阈值。

因此该候选未进入 production/public code，blind prediction/metrics 仍未触碰。后续不得通过继续堆相同 retrieval 分数/阈值来自动 clear transition；必须切换到独立结构证据或维持人工 review。任何新 candidate 仍只能读取 calibration，必须先完成 calibration evaluation + selection lock，之后才允许首次 materialize blind predictions。

### 11.3 第二个 B 候选：fresh blind gate 失败

第二个 candidate 改用 prepared stem 作为 same-track splice 的独立结构证据。真实 calibration 中 candidate 在不改变 SRT/QA 的前提下把唯一已标注 cut 的 precision/recall 从 `0/0` 提升到 `1/1`，因此通过 calibration policy 并锁定 public revision `1dbf82b`。

随后方法学审计发现：r2 manifest 的原 blind case 结构标签已被人工查看，即使 blind prediction/QA/metrics 当时未生成，该 split 也已不再满足严格 blind 定义。原 4 个 r2 blind case 因此永久 quarantine，不能用于 official gate。

为恢复真正的 blind，r3 在 candidate selection **之前**使用 commit-locked deterministic generator 固定 8 个未见 synthetic structural case，并写入独立 blind-truth lock；candidate blind prediction 在 selection 之后才首次 materialize。预先锁定的 blind policy 要求 timing/text/review 零回归、cut precision=1.0、cut recall>=0.75。最终 gate 返回 `passed=false`：candidate 的 cut precision/recall 均为 `0`，其它受控指标无回归。

该 blind 结果出现后禁止继续针对 blind 调 threshold、fixture 或 selection policy。prepared-stem candidate 因此被淘汰并从 public code 撤回；private r3 lock/selection/evaluation/gate 继续保存为审计证据。
