# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已经进入代码的行为、兼容/迁移与验证边界。

## 2026-08-18 — main 当前基线

已合入 main：

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native final render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review decisions：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed-overlap dual-track recomposition：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed-cut Source-to-Mix / canonical timeline rebuild：`096210fbdbb8a55ee908b592bba20b1244c2821f`；
- a8 cut + overlap materialization composition：`5c458d8327d2641ba053423fff3066d7fdd8ba3b`。

a8 PR #7 head `d70101412549817d6ef3c474ee9babc647c9dfe9` 的 validate #407 在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后 squash merge。

---

## 2026-08-18 — P1 Split-Isolated Calibration / Blind-Test Framework（PR #8）

### 1. 目标

此前 evaluator 可以计算 sequence/cue/boundary/cut/overlap 指标，但“calibration 如何选候选、blind_test 是否真的未参与调参、candidate 是否在两阶段之间被替换”主要依赖人工纪律。

P1 把这些规则变成机器可拒绝的 contract：

```text
calibration split only
 -> evaluate baseline/candidate(s)
 -> explicit gates
 -> deterministic ranking
 -> immutable selection artifact
 -> freeze candidate identity
 -> blind_test evaluation
 -> blind acceptance/non-regression gate
```

本里程碑不修改 a8 声学阈值，也不宣称真实准确率提升。

### 2. 新 evaluation layer

新增/扩展：

```text
lyric_aligner/evaluation/protocol.py
lyric_aligner/evaluation/gate.py
lyric_aligner/evaluation/strict_workflow.py
scripts/evaluate_calibration_dataset.py
scripts/calibration_gate.py
scripts/v4_calibration_workflow.py
```

`v4_calibration_workflow.py` 是严格 P1 正式入口；较低层 CLI/模块仍可用于测试与诊断，但不能绕过 strict workflow 的 identity / split gates 来宣称 blind-test 结果。

### 3. Dataset schema 1.1 / split isolation

严格工作流要求：

```text
dataset_revision = opaque stable revision
source_group     = opaque source/version family ID
```

同一 `source_group` 不得跨：

```text
train
calibration
blind_test
```

同曲 remix、现场版、伴奏版、同一 source/version 的不同切片应分到同一个 source_group，避免泄漏。

Calibration 阶段只要求当前 calibration split 的 prediction/QA 文件存在。Blind prediction/QA 在 candidate selection 前可以完全不存在；这用于机器证明 calibration 没有顺手读取 blind 预测结果。

### 4. Immutable ground-truth identity

Selected split 生成 SHA-256 identity，至少绑定：

- dataset + dataset_revision；
- opaque case ID / source_group / language / split；
- reference SRT SHA-256；
- expected cuts / overlap / occurrence annotations；
- audio duration metadata。

Prediction SRT/QA 内容不进入 ground-truth hash，因此 baseline/candidate 可以有不同 prediction 路径，但必须对应同一套真值。

### 5. Candidate lock

Calibration selection 不只记录 candidate name，而锁：

```text
candidate_id
candidate_revision
algorithm_version
calibration_profile_version
calibration_profile_id
calibration evaluation SHA
policy SHA
selection payload SHA
```

Selected split 的全部 `FINAL.qa.json` 必须报告一致 runtime identity。Blind 阶段即使 `candidate_id` 相同，只要 revision/profile/runtime identity 改变，也必须拒绝。

### 6. Explicit policy gates

Policy 每条 gate 显式声明：

```text
scope
metric
direction = higher | lower
max_regression_abs
optional min_candidate
optional max_candidate
optional min_improvement_abs
```

Calibration policy 还可声明 deterministic ranking。只有通过全部 gates 的候选进入 ranking；没有 passing candidate 则 selection 失败。

代码不通过隐藏阈值降低门禁。

### 7. Metrics

保留现有 sequence-aware evaluation：

- sequence edit / unit P-R-F1；
- line exact P-R-F1；
- missing/extra/wrong-order；
- split/merge；
- onset/offset MAE/P50/P90/P95；
- cut precision/recall；
- overlap duration/event P-R、IoU；
- track attribution；
- review density/runtime/publish-ready。

P1 新增 matched-cut boundary：

```text
cut_boundary_match_count
cut_boundary_mae_ms
cut_boundary_p50_ms
cut_boundary_p90_ms
cut_boundary_p95_ms
cut_boundary_within_250ms_rate
cut_boundary_within_500ms_rate
```

必须同时 gate cut recall/coverage，禁止通过只命中容易 cut 来制造漂亮 boundary MAE。

### 8. Privacy

Public/committable evaluation artifacts 只包含：

- aggregate metrics；
- opaque IDs；
- runtime/profile/candidate identities；
- SHA-256。

不输出歌词正文、真实曲名、音频、reference/prediction SRT 内容或 raw per-case evidence。

### 9. Regression coverage

P1 当前加入：

- source_group 跨 split 泄漏拒绝；
- dataset/reference ground-truth identity；
- cut boundary MAE/P95；
- baseline/candidate gate direction / regression tolerance；
- deterministic calibration candidate selection；
- selected candidate lock；
- strict workflow E2E：calibration 成功时 blind prediction 尚不存在；
- blind 阶段 candidate revision/runtime identity 改变时拒绝；
- CLI bootstrap / privacy-safe output contracts。

### 10. CI history

PR #8 head `57a9634d59a80713cacaf32eb58df0ba13e918d5`，validate #431：

- ASR environment：SUCCESS；
- Python 3.10/3.12/3.14 compileall：SUCCESS；
- Documentation Contract：FAIL；
- unit/E2E 因 docs gate 未过而没有执行。

失败明确要求同步：

```text
references/v4-change-record.md
references/v4-status.md
SKILL.md or references/v4-runtime-guide.md or references/workflow.md
```

当前提交补齐这些 owning docs。必须使用新 head 的新 CI 结果验收，不能把 #431 描述成算法测试失败或成功。

### 11. Known boundary

P1 framework 合并不等于完成真实 calibration。

当前仓库没有可用于公开 CI 的真实授权歌曲音频 + 人工 reference truth，因此 GitHub Actions 可以验证**框架与 synthetic contracts**，但不能凭空生成真实准确率。

真实 private dataset 执行后，才能报告：

- cue boundary MAE/P95；
- cut/overlap P/R；
- 语言分组准确率；
- review density/runtime；
- baseline vs selected candidate blind delta。

### 12. Next

P1 framework 全绿合并后优先：

1. 建立/运行真实 private calibration + blind dataset；
2. 根据 real error breakdown 决定 P2 Editor Evidence + LanguageSpan final fusion；
3. 只有真实边界误差证明需要时再把 Forced Alignment / ASR v2 提升为生产依赖。

## 验证纪律

PR #8 latest head 必须经过 Python 3.10/3.12/3.14、ASR、Documentation Contract、full unit/E2E、Skill/privacy/environment/diff-check 全绿后才可合并。没有 real blind data 时，不得宣称固定百分比准确率提升。
