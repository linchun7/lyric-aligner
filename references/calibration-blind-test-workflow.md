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
