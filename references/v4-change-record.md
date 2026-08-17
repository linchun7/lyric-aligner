# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已经进入代码的行为、兼容/迁移与验证边界。

## 已合入 main 的主要里程碑

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native final render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review decisions：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed-overlap dual-track recomposition：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed-cut Source-to-Mix / canonical timeline rebuild：`096210fbdbb8a55ee908b592bba20b1244c2821f`；
- a8 cut + overlap materialization composition：`5c458d8327d2641ba053423fff3066d7fdd8ba3b`；
- P1 split-isolated calibration/blind-test framework：`1c6babe37067c217d14a7404aa0ed6a1c4779a00`。

P1 PR #8 latest head `c3ad2e6b2e57655fd69f2edd935ea3f01386a318` 的 validate #447 在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后 squash merge。

---

## 2026-08-18 — P1.1 Private Dataset Readiness / Scaffold（当前分支）

### 1. 目的

P1 已经能严格评估真实 calibration/blind 数据，但 private dataset 的 manifest、candidate prediction 路径、policy 和 readiness 仍容易手工出错。

P1.1 新增一个明确不制造假数据的准备工具：

```text
scripts/v4_dataset_readiness.py
```

以及纯逻辑层：

```text
lyric_aligner/evaluation/readiness.py
```

### 2. `scaffold`

输入 dataset/revision/case counts/candidate ID，生成：

```text
<private-dataset>/
  baseline.dataset.json
  calibration-policy.template.json
  blind-policy.template.json
  READINESS.json
  reference/
  predictions/baseline/
```

Manifest 使用 strict schema 1.1，case/source_group 都是 opaque IDs。

关键安全规则：

- calibration 与 blind_test 至少各 1 case；
- 每个 case 默认使用不同 source_group，避免模板本身产生跨 split 泄漏；
- candidate ID 禁止 `../` 等路径穿越字符；
- 已存在 manifest/policy 拒绝覆盖；
- **只创建目录和 JSON，不创建任何 reference SRT、prediction SRT 或 QA 内容**；
- initial readiness 因文件缺失而明确为 false。

因此 scaffold 只是“待填真实数据的骨架”，不是测试数据、更不是准确率证据。

### 3. `clone-candidate`

用途：从 baseline manifest 派生候选输出路径，避免人工复制时改坏 ground truth。

保持不变：

```text
dataset / revision
case id
source_group
split / language
reference_srt
expected cuts / overlaps / occurrences
audio duration metadata
```

只重写：

```text
predicted_srt -> predictions/<candidate>/...
qa_json       -> predictions/<candidate>/...
predicted_cuts / predicted_overlaps -> []
```

候选预测事实不会从 baseline 继承。

### 4. `check`

支持 readiness 级别：

```text
metadata
references
predictions
evaluation
```

以及可选单 split：

```text
train
calibration
blind_test
```

报告：

- case/language/scenario counts；
- cut/overlap annotation coverage；
- missing reference/prediction/QA opaque case IDs；
- QA runtime identity 是否有效；
- 同一 selected split 是否出现多个 algorithm/profile identity；
- selected split 是否真正可交给 `v4_calibration_workflow.py evaluate`。

输出不包含歌词正文和文件系统路径。

### 5. Regression coverage

新增测试锁定：

- scaffold metadata 本身满足 source_group isolation；
- scaffold 不会伪造 SRT/QA；
- candidate clone 不修改 ground-truth fields；
- calibration 可以 ready 而 blind files 不存在；
- mixed runtime identity 阻断 evaluation readiness；
- candidate ID 路径穿越拒绝；
- CLI scaffold/check/clone end-to-end；
- scaffold 重复执行拒绝覆盖。

### 6. Algorithm / calibration

P1.1 不改变：

```text
algorithm_version = 4.0.0a8
calibration profile = production-bootstrap-2026-08-17-a7
```

这是 private-data workflow tooling，不改变 Source-to-Mix、timeline、renderer 或声学阈值。

### 7. GitHub Actions 边界

CI 可以验证 scaffold/readiness 工具、synthetic fixtures、privacy 和既有全部 regression。

CI 当前没有授权 real-song private dataset，因此仍不能产生真实：

- language accuracy；
- real boundary MAE/P95；
- cut/overlap P/R；
- production runtime/review density。

P1.1 的成功标准是“准确告诉用户缺什么、已有数据是否真的 ready”，不是生成好看的假数字。

### 8. 下一步

P1.1 全绿后：

1. 用户将授权真实 reference/prediction/QA 放入 scaffold 路径；
2. `v4_dataset_readiness.py check --require evaluation` 通过；
3. 执行 P1 calibration -> selection -> blind；
4. 并行推进 P2 Editor Evidence + LanguageSpan shadow artifact；
5. 根据 real blind error breakdown 决定 calibrated editor refinement / Forced Alignment / ASR v2。

## 验证纪律

任何 P1.1 合并结论必须绑定 latest head + latest CI。工具未看到真实 private data 时，不能宣称真实准确率已提升。
