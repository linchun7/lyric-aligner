# 模块生命周期与生产资格

更新：2026-09-18

## 唯一默认人工复核 UI

`scripts/v4_human_boundary_anchor_audit_ui.py` 的已确认 **3.2** 核心为唯一默认；统一 `scripts/v4_listen.py` / `试听复核.cmd`。`ui32_review.js` 只扩展同页任务字段，`review/listening.py` 只保存原始作答和描述统计，无生产 authority。P1 默认 renderer 复用该核心，历史协议不变。旧 gap UI 需显式 `--legacy-ui`；其他版本和 task-local 页面一律 legacy，不为新任务生成。完整冻结/废弃清单见 [listening-ui](listening-ui.md)。

本文件只回答“代码还在不等于当前生产会用”。生产资格由当前 facade / policy / release gate 决定，不由目录是否存在、模型是否曾经下载、历史实验是否跑通过决定。

## Production / current

正常用户流程仅 Safe Final；Max Recovery 只用于 editor timeline 事故。内部实现名称不构成强制升级链，Balanced/Fluent 不恢复。

`review/safe_final_review.py`：current **read-only lexical candidate / gap-review tooling**，复用已有 parser、发音提示与严格简繁比较；没有自动 lexical selector 或 gap insertion authority。`review/semantic_sensitive.py` 与同一 `scripts/v4_review_safe_final.py` 的 `sensitive-pack/sensitive-finalize` 子命令是 current **mandatory final display gate**：pack 只提示候选，GPT 必须全量语义审查；finalizer 只允许 task-bound high-confidence mask，不授予 canonical/timing/segmentation authority。歌词文字物化仍只由 `review/canonical_splices.py` 完成。契约集中见 [Safe Final review](safe-final-review.md)。

| 模块 | 当前身份 | 说明 |
| --- | --- | --- |
| Standard / Text Repair V2.1 | production | canonical text repair；冻结 timing |
| `smart_current.py` / Smart v1.2.11 | production default | 普通生产稳定 floor；旧版本不能作为独立默认入口，但 v1.2.11 仍实际调用 v1.2.10 等 wrapper/base，不能当作无 consumer 死代码删除 |
| Pro v1.2.7 core planning/evidence | production-capable evidence layer | Smart unresolved 的 bounded evidence；默认不自动写 timing |
| Max 4.0.0a20 core Source-to-Mix | production-capable heavy evidence path | 每个任务仍需完整 release gate；更重不代表更准 |
| Best-Safe 1.1 | production product selector | Smart topology/timing floor；未来正常任务默认不机械 mask；当前机器 timing promotion 未开放 |
| Semantic sensitive final gate | production mandatory display gate | GPT 全量语义 KEEP/MASK/REVIEW；程序仅物化 exact mask；REVIEW fail closed |
| faster-whisper large-v3-turbo | retained optional ASR backend | 当前本机保留的主要 ASR 权重；ASR evidence 本身不等于 timing authority |

当前 production source freeze：

```text
prod-v4.0.0a20-best-safe-v1.1.0-20260912
```

## Shadow / P1 evaluation modules

以下模块可以运行评估，但**不能**写生产字幕：

- `evaluation/timing_decision_pack.py`
- `evaluation/timing_decision_review.py`
- `evaluation/decision_validation.py`
- `evaluation/product_boundary_quality.py`
- `evaluation/strict_workflow.py`
- `evaluation/boundary_promotion_shadow.py`
- `scripts/v4_boundary_promotion_shadow.py`

P1 shadow gate 即使 PASS，也固定 `production_authority_granted=false`；P1 review 必须在人工标注前绑定 pre-gold frozen selection hash + intended partition，并保留 raw candidate-blind response 供 response→Gold 重算核对；development/calibration response 不得事后重标 blind/holdout。需要单独的 authority contract/code review 才能进入 Best-Safe timing promotion。

## Dormant experimental / 未部署权重

这些实现保留用于历史复现或未来明确实验，但当前本机已删除重型权重/runtime，且没有 production timing authority：

- Qwen3-ASR / `qwen_asr_executor.py`；
- Qwen forced-aligner/boundary probe；
- SOFA Mandarin singing alignment；
- HuBERTFA source-context / English final-mix shadow；
- STARS / RMVPE bottleneck experiments。

规则：

1. 后续模型看到这些源码时，**不得自动下载或重装权重**；
2. 不得因为旧实验报告中某些局部点表现好就恢复 production；
3. 重新启用必须先有新的真实 production failure、预注册实验与 untouched truth；
4. 重新部署模型时记录 revision/SHA/runtime identity；
5. 仍先进入 shadow，不直接授予 Best-Safe/Max release authority。

2026-09-12 清理约 `17.115 GB` 后，当前主要保留的本地音频识别权重是 faster-whisper large-v3-turbo（约 1.51 GB）。

## Regression / legacy

以下代码可能仍被测试、兼容或历史复现引用，但不应作为新任务默认入口：

- 旧 Smart policy 版本（v1.2.5–v1.2.10 等）；
- `legacy/` bridge；
- `redo_karaoke_pipeline.py` 等旧 pipeline；
- 历史 freeze tag 对应的旧 Smart/Pro 行为。

不得为了“清爽”直接删除这些兼容代码；先确认 current facade、测试和历史 artifact replay 都不依赖，再单独做 deprecation/removal。

## 判断顺序

最低保底文字增量可使用`review/canonical_splices.py`和`scripts/v4_apply_reviewed_text_splices.py`，资格仅为**执行已绑定的任务审定**及显示当前floor上下文/空档。不是自动lexical selector、ownership证明器或新的证据模式；外部观察和逐条措辞/归属审定缺失则拒绝修改。旧任务采用只作兼容回归，不授予未来歌曲词义authority。见 [契约](reviewed-canonical-splices.md)。

后续 AI/维护者判断某个模块能否用于真实任务时，按以下顺序：

```text
当前 README / SKILL / v4-status
        ↓
current facade / policy identity
        ↓
任务自身 manifest + evidence + release gate
        ↓
module lifecycle
        ↓
历史 change record / experiment register
```

历史文档只能解释“为什么做过”，不能覆盖当前生产资格。
