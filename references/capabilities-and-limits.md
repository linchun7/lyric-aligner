# 当前能力与使用边界

更新：2026-09-11

当前仓库主线为 `main`，算法版本 `4.0.0a20`；生产路径为 `Standard -> Smart v1.2.11 -> Pro v1.2.7 -> Max 4.0.0a20`。本页只描述当前可依赖的能力与限制；历史实验数字见 [实验台账](accuracy-experiment-register-2026-09-08.md) 和 [v4 change record](v4-change-record.md)。

## 1. 项目定位

本项目适合处理“已有 editor/剪映 SRT + 规范歌词 + 可选原曲/最终混音”的歌词字幕修复、对齐、审核和发布门禁。

它不是“任意歌曲、任意语种、无需审核即可保证正确”的全自动字幕器。当前没有代表所有歌曲/语种/制作方式的 untouched final-mix truth，因此**不能负责任地给出总体准确率百分比**。

核心原则：

```text
Content correctness
> Structure / ownership correctness
> Timing non-regression
> Timing improvement
```

证据不足时保持 editor/既有安全结果并进入 review/BLOCK，而不是放宽阈值制造通过。

## 2. 当前已具备能力

| 能力 | 当前可做 | 主要边界 |
| --- | --- | --- |
| Standard / Text Repair V2.1 | 按 canonical 修正文案，冻结 cue count/number/start/end；处理安全的 1↔N/N↔1 ownership 差异 | 不修原时间轴错误；LRC line break 不是 cue boundary authority |
| Smart v1.2.11 | no-audio 使用 timed canonical、editor majority anchors、sequence 与 exact/soft BPM prior 做文字恢复和少量受控 timing 判断 | v1.2.11 不扩大 v1.2.10 timing authority；review 仍需 Pro/人工 |
| Pro v1.2.7 | 仅对 Smart unresolved 的 bounded region 规划 local acoustic、ASR/forced evidence 和 adjudication | 默认 `timing_mutation_performed=false`；局部 evidence 不自动写成最终字幕 |
| Max 4.0.0a20 | 完整 Source-to-Mix、cut/overlap/recomposition、editor preservation、canonical evaluation、semantic/release lineage | 更重不等于更准；结构/语义 gate 未过仍 BLOCK |
| Canonical lexical floor | 对 resolved canonical occurrence 做字符 ownership、coverage、Latin word-boundary 等审计 | 证明生产链不静默丢字/改字，不证明歌词源本身绝对正确 |
| Canonical rebuttal | 允许独立 hash-bound evidence 对 normalized canonical lexical truth 提出反证 | 语义“看起来更顺”本身没有改字 authority |
| Editor preservation / hybrid | 在 canonical 完整性与 editor timing/topology 可同时证明时保留可信 editor cue，并补结构遗漏 | 只接受显式 exact/lineage-bound 区域；不把 editor 整体升级成真值 |
| Human Gold / Review | 复用已经确认的边界、review decision、blind/calibration split 与 selection lock | 旧 gold 可回归，但不能反复包装成新的 blind 泛化证据 |
| QA / release | 验证 input/config/artifact/version/hash lineage、结构、lexical floor、semantic evidence、release authority | QA 通过只证明对应 gate；不能替代未运行的其它 gate |

## 3. Smart / Pro 当前边界

当前 Smart schema/policy：

```text
schema_version = smart-1.1
policy_id      = smart-validation-policy-2026-09-10-v1.2.11
```

`smart_current.py` 是唯一 current-production Smart facade。v1.2.10 保留为 regression baseline。

SHE25 真实回归已证明：在同一真实 610-cue 输入与同参数下，v1.2.11 相比 v1.2.10 的 cue timing signature、完整 timing decisions 和 rendered SRT 均不变；v1.2.11 的新增影响是把最终 canonical ownership / lexical-floor 无法证明的区域继续留在 review，而不是重新建立 timing model。

Pro 只接受当前 Smart schema/policy + exact Smart SRT/canonical hash。旧 Smart artifact 即使 schema 名相同，只要 policy/hash 不匹配，也必须先重跑 Smart。

## 4. Max / timing 当前边界

Max 可处理复杂 cut、overlap、重复 occurrence、reference retime、editor reconciliation 等结构问题，但普通音频相关性、ASR 一致、两个模型接近、或 shadow observer 得分高都不等于 production timing truth。

2026-09-11 已停止继续调两条现有 timing 研究 family：

1. source-ASR -> harmonic/local acoustic retrieval；
2. English exact-final-mix HuBERTFA forced-alignment shadow。

原因是冻结实验未获得足以授予 production authority 的覆盖/尾部质量，并出现非边缘多秒 collapse。没有新的独立 final-mix truth、预先冻结的新评测集或明确 production failure 时，不重新对旧样本调 window、dictionary、margin、segment geometry、observer 或 selector threshold。

这些实现可继续作为显式诊断/研究模块，但不能从 shadow 身份直接升级成 release authority。

## 5. 当前任务实例的已知状态

### 欧美经典140 a20

当前 semantic release artifact 仍 `passed=false`。source-clock authority 已有 7 首合法晋级，但 ordinal：

```text
4 / 5 / 6 / 10 / 11 / 14 / 15
```

继续 BLOCK。因此“代码已经在 main”“static lexical/structural QA 通过”都不能解释为该成品已经 release-ready。

### SHE25

Smart v1.2.11 的 BPM soft-prior 配置相对无 prior 可减少一部分 text review，并保持 timing 不变；但当前真实结果仍为 `review_required` / `pro_escalation_required=true`，不能写成无人审核 final-ready。

## 6. 尚未具备或尚未证明

- 任意新歌/新语种在无人审核下稳定端到端交付；
- 在 editor 严重漏识、重复副歌、拖长音、多人叠唱、复杂 cut/crossfade 下自动确定全部真实边界；
- 从任意错误 canonical 自动恢复 authoritative lyrics；
- 为每条字幕给出经独立校准、跨项目有效的“正确概率”；
- 仅靠更多模型/候选/改动数量证明最终 SRT 更准确；
- 用旧 development-visible gold 反复调参后仍称其为 blind 泛化证据。

## 7. 什么时候可以重新做 accuracy 升级

必须同时具备：

1. 可复现的真实 production failure / 明确错误类别；
2. 与旧调参样本隔离的独立 truth；
3. 修改前冻结的 candidate/selector/threshold/runtime identity；
4. 对 final SRT 报告完整分母、coverage、median/P90/max、catastrophic harm 和回退率；
5. 相比 editor/旧 final 有净收益，并且原本正确区域不退化。

否则优先维护 current editor-first / fail-closed 架构，而不是增加 heuristic 复杂度。

## 8. 工程与可复现性边界

当前源码仓库只提供通用代码、合成测试、接口/契约和脱敏摘要。私有音视频、歌词/SRT、模型、Gold/Review 和完整运行 evidence 保持本地。

本地清理遵循 [local-artifact-retention.md](local-artifact-retention.md)：production final、manifest/run config、Gold/Review、blind/truth、calibration、regression baseline、被文档/release 直接引用的 evidence 默认保留；`__pycache__`、tmp/debug、明确 disposable cache 才是优先清理对象。

当前运行和开发入口见 [README](../README.md) 与 [workflow](workflow.md)。
