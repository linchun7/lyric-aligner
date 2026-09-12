# 模块生命周期与生产资格

更新：2026-09-12

本文件只回答“代码还在不等于当前生产会用”。生产资格由当前 facade / policy / release gate 决定，不由目录是否存在、模型是否曾经下载、历史实验是否跑通过决定。

## Production / current

| 模块 | 当前身份 | 说明 |
| --- | --- | --- |
| Standard / Text Repair V2.1 | production | canonical text repair；冻结 timing |
| `smart_current.py` / Smart v1.2.11 | production default | 普通生产主力；v1.2.10 以下只作 regression/history |
| Pro v1.2.7 core planning/evidence | production-capable evidence layer | Smart unresolved 的 bounded evidence；默认不自动写 timing |
| Max 4.0.0a20 core Source-to-Mix | production-capable heavy evidence path | 每个任务仍需完整 release gate；更重不代表更准 |
| Best-Safe 1.1 | production product selector | Smart topology/timing floor；当前机器 timing promotion 未开放 |
| faster-whisper large-v3-turbo | retained optional ASR backend | 当前本机保留的主要 ASR 权重；ASR evidence 本身不等于 timing authority |

当前 production source freeze：

```text
prod-v4.0.0a20-best-safe-v1.1.0-20260912
```

## Shadow / P1 evaluation

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
