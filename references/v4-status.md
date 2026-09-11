# Lyric Aligner v4 当前实施状态

更新：2026-09-11
主线算法版本：`4.0.0a20`

本文件只描述当前事实。历史实验、旧候选、阶段性测试数字和被否决路线见 [v4-change-record.md](v4-change-record.md)、[accuracy-experiment-register-2026-09-08.md](accuracy-experiment-register-2026-09-08.md) 与 Git history，不再在本页重复。

## 1. 当前代码与生产入口

```text
branch / code line : main
algorithm_version  : 4.0.0a20
Standard           : Text Repair V2.1
Smart              : v1.2.11
Smart policy       : smart-validation-policy-2026-09-10-v1.2.11
Pro                 : v1.2.7
Pro acoustic schema: 1.5
Max                 : 4.0.0a20
```

当前生产路径：`Standard -> Smart -> Pro -> Max`。

`smart_current.py` 是唯一 current-production Smart facade。v1.2.10 仅保留为历史 regression baseline；v1.2.11 沿用 v1.2.10 timing authority，并增加 final canonical ownership / connected lexical-floor hardening。

仓库当前代码版本不等于任意具体字幕任务自动 release-ready。任务仍必须使用自身输入、manifest、配置和证据通过 lexical / structural / semantic / release gate。

## 2. 当前工程状态

2026-09-11 工程封板验证：

- `python scripts/validate_skill.py .`：PASS；
- `python -m unittest discover -s scripts -p "test_*.py"`：`1862 tests / OK`；
- `git diff --check`：PASS；
- SHE25 真实 A/B 验证：v1.2.11 相对同参数 v1.2.10 的 610 cue timing signature、完整 timing decisions 与 rendered SRT 均不变；
- v1.2.11 的新增影响是收紧 final canonical ownership / lexical-floor authority，不重新建立 timing model。

测试数字只描述该次快照；后续修改必须以新的实际重跑为准。

## 3. 当前任务级阻断

### 欧美经典140 / a20

source-clock authority 1.1 已有 7 首通过冻结 holdout 晋级；当前 semantic release gate 仍为 `passed=false`，ordinal：

```text
4 / 5 / 6 / 10 / 11 / 14 / 15
```

继续 BLOCK。不得因为代码已在 main、静态 lexical/structural QA 通过或某个 shadow observer 有局部结果，就宣布 a20 成品 release-ready。

详细 lineage 与阶段结论见 [oumei140-a20-source-clock-upgrade-handoff-2026-09-10.md](oumei140-a20-source-clock-upgrade-handoff-2026-09-10.md)。

### SHE25

Smart v1.2.11 的 BPM soft-prior 配置优于无 prior 的 text review 数，但仍为 `review_required` 且 `pro_escalation_required=true`；不能写成无人审核 final-ready。

## 4. Timing R&D 当前决定

当前两条 timing family 已停止继续调参：

1. source-ASR -> harmonic/local acoustic retrieval；
2. English exact-final-mix HuBERTFA shadow。

停止原因不是“理论永远无效”，而是现有冻结样本上未获得足以授予 production timing authority 的独立证据，并出现非边缘多秒 collapse。没有新的独立 final-mix truth、预先冻结的新评测集或明确 production failure 时，不重新围绕旧样本调 window、dictionary、margin、segment geometry、observer 或 selector threshold。

这些模块可保留作诊断/未来研究，但不得从 shadow 身份直接升级为 production authority。

## 5. 当前已验证能力

- Standard：canonical text/order 修复，timeline signature 冻结；
- Smart：timed canonical、sequence/BPM text recovery、editor ownership 与 lexical-floor hardening；
- Pro：对 Smart unresolved region 做 bounded audio/evidence planning，默认不自动写 timing；
- Max：source-to-mix、cut/overlap/recomposition、editor-preservation、canonical evaluation 与严格 release lineage；
- Human Gold/Review、calibration/blind、selection lock、regression baseline 与 release artifact 有独立身份；
- evidence/source-clock/observer/runtime/config 均要求 hash/version lineage，证据不足时 fail closed。

总体无人审核准确率仍未知，不能从局部已见样本或单个 observer 的条件误差外推任意新歌。

## 6. 当前维护原则

质量优先级：

```text
Content correctness
> Structure / ownership correctness
> Timing non-regression
> Timing improvement
```

优先修可复现 correctness、lineage、path-safety、运行可靠性和文档事实漂移。新的 accuracy heuristic 必须先有独立 truth 与事前冻结的验收协议，不能通过降低阈值或反复调同一 holdout 获得“进步”。

## 7. 本地资产保护

清理必须遵守 [local-artifact-retention.md](local-artifact-retention.md)。以下默认 KEEP：

- 当前生产输入与 manifest/run config；
- production final、QA/release/audit；
- Human Gold/Review、blind/truth、selection lock；
- calibration/regression baseline 与评估结果；
- 人工确认、editor reconciliation、结构 evidence；
- 被当前文档、测试或 release lineage 明确引用的 output/private 资产。

`__pycache__`、`.pytest_cache`、tmp/debug、Playwright、明确 disposable cache 可清理。不要按日期、版本号或体积批量删除 output/private。

## 8. 文档入口

- 项目入口：[../README.md](../README.md)
- 生产流程：[workflow.md](workflow.md)
- 能力边界：[capabilities-and-limits.md](capabilities-and-limits.md)
- Smart / Pro：[smart-pro-v1-1.md](smart-pro-v1-1.md)
- Runtime：[v4-runtime-guide.md](v4-runtime-guide.md)
- CLI：[v4-cli-contract.md](v4-cli-contract.md)
- 历史变更：[v4-change-record.md](v4-change-record.md)
- 实验台账：[accuracy-experiment-register-2026-09-08.md](accuracy-experiment-register-2026-09-08.md)
