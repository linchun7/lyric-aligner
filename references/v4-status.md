# Lyric Aligner v4 当前实施状态

更新：2026-09-12
主线算法版本：`4.0.0a20`
Best-Safe：`1.1.0 / best-safe-smart-timing-floor-1.1`

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
Best-Safe           : 1.1.0
Best-Safe policy    : best-safe-smart-timing-floor-1.1
```

当前证据路径：`Standard -> Smart -> Pro -> Max`；当前产品路径：`Smart baseline -> Best-Safe -> Max Release`。Best-Safe 是证据模式之后的产品 selector，不改写 Smart/Max authority。

`smart_current.py` 是唯一 current-production Smart facade。v1.2.10 仅保留为历史 regression baseline；v1.2.11 沿用 v1.2.10 timing authority，并增加 final canonical ownership / connected lexical-floor hardening。

仓库当前代码版本不等于任意具体字幕任务自动 release-ready。任务仍必须使用自身输入、manifest、配置和证据通过 lexical / structural / semantic / release gate。

## 2. 当前工程状态

2026-09-12 Best-Safe 1.1 工程封板验证：

- Best-Safe focused suite：`20/20 / OK`；
- `python scripts/validate_skill.py .`：PASS；
- `python -m unittest discover -s scripts -p "test_*.py"`：`1882 tests / OK`；
- `git diff --check`：PASS（仅工作区 LF/CRLF 提示）；
- SHE25 真实 A/B 验证：v1.2.11 相对同参数 v1.2.10 的 610 cue timing signature、完整 timing decisions 与 rendered SRT 均不变；
- v1.2.11 的新增影响是收紧 final canonical ownership / lexical-floor authority，不重新建立 timing model。

测试数字只描述该次快照；后续修改必须以新的实际重跑为准。

### Source release freeze

Best-Safe 1.1 的源码冻结身份为 production tag `prod-v4.0.0a20-best-safe-v1.1.0-20260912`；tag target 是该源码 release 的 Git 身份，冻结契约见 [release manifest](releases/prod-v4.0.0a20-best-safe-v1.1.0-20260912.json)。历史 `prod-smart-v1.2.5-pro-v1.1.4-20260821` 仅表示旧 Smart/Pro baseline，不代表当前 Best-Safe。任务级私有输入/字幕/Gold/Review 不进入源码仓库；欧美140 Best-Safe artifact SHA 单独记录在 release manifest，不把任务 artifact 与 source tag 混成同一 authority。

### 本地音频模型部署状态

2026-09-12 完成本地实验模型清理：删除 Qwen3-ASR 0.6B/1.7B 权重、Qwen boundary/forced-aligner 权重与 probe venv、SOFA 模型/runtime、HuBERTFA ONNX/runtime/压缩包及临时探针缓存、STARS 权重/runtime，以及 faster-whisper medium/small 缓存，共释放约 `17.115 GB`。保留源码、adapter、模型身份/下载元数据与历史实验结果；这些 Qwen/SOFA/HuBERTFA/STARS 路径仍可作为可选实验代码，但再次实跑前必须重新部署本地模型，不能假设权重仍存在。当前本机保留的主要 ASR 权重为 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`（约 `1.51 GB`），仍作为现有默认/主要 faster-whisper backend 使用。清理后完整工程回归 `1882 tests / OK`，未触发模型自动下载。

## 3. 当前任务级阻断

### 欧美经典140 / a20

2026-09-12 fresh production 的 Max semantic release gate 仍为 `passed=false`。当前逐曲 `final_sync` 通过 `1 / 3 / 5 / 6 / 7 / 8 / 9 / 12`，BLOCK：

```text
2 / 4 / 10 / 11 / 13 / 14 / 15
```

因此 **Max Release 继续 BLOCK**，不得把 `PRODUCT/FINAL.srt` 当作完整 release-ready 成品。

同一冻结任务已升级为 Best-Safe 1.1：直接从原始 Smart v1.2.11 构建，Smart 的 923 cues、顺序及全部 start/end 作为 topology/timing floor；8 首 Max semantic-pass 曲仅记录为 candidate，不再整首吸收 Max timing/topology，7 首 semantic-BLOCK 同样不影响 floor。当前 `BEST_SAFE/FINAL.srt` 为 923 cues，逐 cue 对 Smart 的 timing diff=0，`topology_exact=true`、`unsupported_timing_change_count=0`。用户人工真值 Toxic cue28 start=`116833ms` 已写入 task-bound timing truth，最终实际值同为 `116833ms`、误差0。Smart 全部70个文字 review 区域现在均有显式处置：Smart report 的155个 review cues 已由 ledger 155/155 完整且唯一记账（unaccounted=0）；16个区域进入 canonical-gap proposal、54个模型直接 keep Smart；完整 deterministic + cue-ownership verifier 最终只接受4个区域 / 6 cues、拒绝12个，最终66个区域 / 149 cues 保守留在 Smart。文字层不允许无 timing authority 的跨 cue 插词/删词/搬词；显式 `*` mask、`strong_profanity_v1` 与 normalized-equivalent display 可安全吸收。最终相对 Smart 仅24条文字/display变化，timing 变化0；独立审计 PASS，残留可 mask 强敏感词0、英文粘词0，SRT SHA=`adc5f26b10ffd95da3d039482b9a942312075b12cf5c586823f04d41768212bb`。该 `publish_ready=true` 只表示 Best-Safe 1.1 Smart-floor 合同通过，**不改变 Max Release 的 BLOCK 状态**。

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
