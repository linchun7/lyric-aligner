# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-dataset-readiness`  
当前 main：`1c6babe37067c217d14a7404aa0ed6a1c4779a00`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`  
TrackAsset schema：`1.1`  
Review Decision schema：`1.2`

## 1. main 已完成能力

main 已具备完整 production-first 重建主链：

- fail-closed TrackAsset / canonical lyric single truth；
- harmonic HPSS + Chroma CENS/MFCC Source-to-Mix mapping；
- AFFINE-first / evidence-driven PIECEWISE_RATE；
- Selective Fine；
- candidate-level transition / TimeWarp review；
- confirmed-overlap 双路 canonical timeline recomposition；
- confirmed middle-cut local boundary localization；
- CUT_AWARE retained segments + explicit source gaps；
- line-LRC partial-cut fail-closed 与 Enhanced/QRC canonical fragments；
- cut + overlap 可证明互不冲突时的 `combined_recomposition`；
- final SRT / audit / QA / release strict artifact lineage。

## 2. P1 Calibration / Blind Test 已合入 main

PR #8 latest head `c3ad2e6b2e57655fd69f2edd935ea3f01386a318` 经 validate #447 的 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后，squash merge 为：

```text
1c6babe37067c217d14a7404aa0ed6a1c4779a00
```

正式入口：

```text
scripts/v4_calibration_workflow.py
```

P1 已机器化约束：

- dataset schema `1.1`；
- opaque `dataset_revision` / `source_group`；
- source_group 不得跨 train/calibration/blind_test；
- calibration 阶段不读取 blind prediction/QA；
- selected split ground-truth SHA-256；
- baseline + selected candidate id/revision/runtime identity 冻结；
- explicit gate policy + deterministic calibration ranking；
- blind_test 只接受 calibration 锁定 identity；
- sequence/cue/boundary/cut/overlap/track/review/runtime 指标；
- matched-cut maximum-cardinality DP + boundary coverage/MAE/P50/P90/P95。

## 3. 当前 P1.1：Dataset Readiness / Scaffold

本分支新增：

```text
lyric_aligner/evaluation/readiness.py
scripts/v4_dataset_readiness.py
```

目的：减少真实 private dataset 手工配置错误，同时保持“没有数据就明确不 ready”。

### scaffold

自动生成：

- strict schema 1.1 manifest；
- calibration / blind_test 匿名 case IDs；
- 每 case 独立 opaque source_group；
- `reference/` 与 `predictions/<candidate>/` 目录；
- calibration/blind policy 模板；
- initial `READINESS.json`。

**不会**创建假 reference SRT、prediction SRT、QA 或 accuracy result。

### clone-candidate

从 baseline/candidate manifest 复制同一 ground-truth metadata，只改：

```text
predicted_srt
qa_json
predicted_cuts / predicted_overlaps 清空
```

不会修改 reference、source_group、split、expected cut/overlap/occurrence truth。

### check

可分别检查：

```text
metadata
references
predictions
evaluation
```

报告：

- split/case/language counts；
- cut/overlap/occurrence/plain scenario coverage；
- missing reference/prediction/QA 的 opaque case IDs；
- QA runtime identity 是否有效且 split 内一致；
- selected split 是否真正可进入 P1 evaluate。

输出不包含歌词正文或文件系统路径。

## 4. GitHub Actions 可以验证什么

公开 CI 可以真实验证：

- scaffold metadata 是否满足 split isolation；
- scaffold 不会生成假 SRT/QA；
- candidate clone 不改 ground truth；
- readiness 对真实存在/缺失文件判断是否正确；
- calibration 可 ready 而 blind 文件尚不存在；
- mixed/invalid QA runtime identity 是否阻断；
- CLI overwrite/path traversal 防护；
- 既有 P1/P0 全部 regression。

## 5. 当前明确做不到的真实数据工作

仓库/GitHub Actions 当前没有用户授权的真实歌曲音频 + 人工 reference truth。因此不能伪造或宣称：

- 中文/英文/韩文/日文/粤语真实准确率；
- 真实 cue boundary MAE/P95；
- 真实 cut/overlap precision/recall；
- 真实 production runtime/review density；
- baseline vs candidate 的 real blind improvement。

P1.1 只能让真实数据准备和缺口检查变得可执行，不能替代真实数据本身。

## 6. 下一阶段

### P2 — Editor Evidence + LanguageSpan shadow integration

已有基础：

- `lyric_aligner/text/language_spans.py`；
- `scripts/editor_evidence.py` reliability policy scaffold；
- task `source_srt` 已被 fingerprint 绑定；
- v4 canonical timeline 仍由 Source-to-Mix + canonical lyric 决定。

下一步应新增独立 editor evidence artifact，默认 **shadow-only**：

- zh/en direct-text evidence；
- ko/ja phonetic-hint only；
- yue/unknown text 低权或禁用；
- mixed per-span routing；
- 不改 canonical text；
- 未经过 real calibration 前不自动重写 final cue timing。

### P3 — Forced Alignment / ASR v2

仍由真实 P1 error breakdown 决定是否进入 production，不先用更大 Whisper 替代 Source-to-Mix。

### Explicit BLOCK

same-region cut+overlap joint acoustic model 仍不自动化，除非真实 blind 数据证明其频率与收益值得投入。

## 7. 当前正确表述

> **P0/a8 重建链和 P1 calibration/blind framework 已进入 main；P1.1 正在把 private dataset scaffold/readiness 做成可直接运行的工具。没有真实授权数据时，只能验证工具和 synthetic contracts，不能生成真实准确率。**
