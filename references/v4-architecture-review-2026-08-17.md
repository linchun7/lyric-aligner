# Lyric Aligner v4 深度架构复盘与 a3 原则

日期：2026-08-17  
当前目标版本：`4.0.0a3`

## 1. 架构决策

### v4 采用 production-first

v4 不再长期依赖“shadow-only + v3.9 runtime fallback”。新真实任务尽早进入 v4；遇到不可解释的 mapping、cut、overlap 或证据冲突时 fail-closed 进入 review。

这样做的目的不是降低稳定性要求，而是让真实任务尽快产生：

- mapping residual；
- boundary error；
- cut / overlap false positive/false negative；
- review density；
- runtime；
- language-specific failure cases。

这些数据用于下一版 calibration profile 和算法改进。

### v3.9 的新定位

v3.9 不再维护为第二套长期生产路径。

其代码和提交仍可用于：

- 历史比较；
- regression 分析；
- 仓库级 rollback；
- 理解已验证过的 middle-cut / Enhanced LRC/QRC / QA 行为。

但新能力不得继续写进 5000+ 行 legacy monolith，也不能在 v4 出现 review 时静默切回 v3.9。

## 2. “彻底重构”的定义

4.0 的彻底重构不是一次性 rewrite，而是**生产事实真源迁移**。

最终必须做到：

```text
Asset identity        → lyric_aligner/assets/
Canonical lyric       → lyric_aligner/text/
Source-to-Mix         → lyric_aligner/audio/
Timeline              → lyric_aligner/timeline/
Calibration           → lyric_aligner/config / 后续 calibration/
Orchestration         → lyric_aligner/pipeline/
Artifact / Release    → lyric_aligner/contracts + qa/
Evidence fusion       → 后续 evidence/ + fusion/
```

当这些职责都迁出后，`scripts/redo_karaoke_pipeline.py` 应退役或只保留迁移工具性质，而不是继续成为架构中心。

## 3. 当前长期目录骨架

```text
lyric_aligner/
  assets/
    resolver.py
    bindings.py
    lyric_roles.py
  audio/
    features.py
    coarse_mapper.py
    timewarp.py
    fine_alignment.py
    transition.py
  contracts/
    artifacts.py
  io/
    text.py
  pipeline/
    context.py
    production.py
  qa/
    final_integrity.py
  text/
    normalization.py
    canonical_lyrics.py
    language_spans.py
  timeline/
    projector.py
  config.py
  domain.py
```

后续按真实需求再建立，而不是提前创建空抽象：

```text
lyric_aligner/evidence/
lyric_aligner/fusion/
lyric_aligner/alignment/
lyric_aligner/calibration/
lyric_aligner/cache/
```

## 4. 当前最重要的设计原则：一个事实只有一个真源

### Asset

`ResolvedAssetBinding` 一旦形成，下游不得再 fuzzy resolve source 或 LRC。

### Canonical lyric

同一个 raw lyric 文件可能因 same-timestamp role selection 得到不同 canonical interpretation，因此 canonical selection hash 必须属于 TrackAsset identity。

### Calibration

阈值属于完整、版本化 profile。改变 production 阈值必须改变 profile identity，不能靠隐藏 CLI/default 参数。

### TimeWarp

序列化模型的 canonical state：

```text
intercept
base_slope
breakpoints
slope_deltas
```

派生统计不能反过来成为第二个模型状态。

### Timeline

v4 timeline 必须来自 canonical lyric + effective TimeWarp，不从 Jianying 文本重建 canonical truth。

### Release

最终 release 必须由 materialized output hash + upstream artifact chain 重建验证，不能只相信先前 QA 的布尔字段。

## 5. Source-to-Mix 架构

### Common case first

大多数歌曲优先 AFFINE：

```text
source_time = intercept + slope * mix_time
```

只有当 residual / drift / coverage 证明 fixed-rate 解释不足时，才进入 continuous PIECEWISE_RATE。

### 动态 BPM 不要求用户声明

系统通过实际音频 anchor 判断是否升级。BPM 只是 soft prior。

### Cut 与 rate change 分离

- slope change 可以突然发生；
- source mapping 连续 → rate change；
- source position jump → discontinuity/cut candidate。

Middle cut 无论是否声明，都不能被自动 confirmed。

## 6. Transition 架构

之前最大缺陷之一是把下一首 nominal start 当上一首硬 end。

a3 明确分成：

```text
Primary occurrence interval
+
Shared transition evidence interval
```

对于 A→B：

```text
A primary: ... → boundary
B primary: boundary → ...

transition evidence:
A source ┐
         ├→ boundary ± margin
B source ┘
```

两首都能在同一区间取证，才有可能真实识别 2–10 秒跨曲混剪。

但搜索区间重叠只是 candidate discovery，不是 overlap truth。确认动作必须留在 review/decision 层。

## 7. Production Orchestrator

`scripts/v4_run.py` 是 a3 的默认真实任务入口。

它不是新的业务 monolith：

- 算法留在 package 模块；
- 各 stage 仍产生独立 fingerprinted artifact；
- orchestrator 负责计划、调用、lineage 和状态聚合；
- 未来替换 Coarse/Fine/Transition 实现时不需要重写入口。

当前链：

```text
Asset
 → Primary Coarse
 → Selective Fine
 → TimeWarp
 → Canonical Timeline
 → Shared-boundary evidence
 → Transition
 → production run summary
```

任何 unresolved issue 都生成 `review_required`；没有 legacy fallback。

## 8. 为什么现在仍是 alpha

production-first 不等于 stable。

a3 仍缺：

1. package-native final timeline composer；
2. final SRT renderer；
3. review decision artifact；
4. Editor Evidence / LanguageSpan 到最终 cue decision 的完整融合；
5. final renderer → release guard 一键链；
6. real private calibration / blind-test。

因此当前 `ready_for_render` 只能表示 mapping/timeline 前置条件通过，不能等价为 `publish_ready`。

## 9. 为什么暂不优先 Forced Alignment

在真实任务投入 Forced Alignment 前，必须先验证：

- Asset identity 没有错绑；
- TimeWarp 是主要时间真源；
- transition 不再被 nominal hard boundary 截断；
- real-task evaluator 能指出剩余边界误差。

只有数据证明“Source-to-Mix 已可靠，但词/句边界仍明显不够准”，Forced Alignment 才是下一步高价值投入。

否则会把昂贵模型叠在尚未稳定的 timeline 上。

## 10. 4.0 后续升级路径

### a3

production-first mapping / transition / canonical timeline。

### 下一 alpha

package-native composer + review decision artifact + final render bridge。

### beta

真实任务 calibration，形成新的 named profile；按语言/场景分层评估。

### rc

根据真实误差决定 Forced Alignment、ASR v2、vocal local alignment 的范围；完成 blind-test 与 release gate。

### 4.0 stable

必须满足：

- 新任务只有 v4 正式入口；
- final SRT/QA/release 都由 v4 package contract 决定；
- unresolved cut/overlap/evidence conflict 不产生 false-ready；
- real calibration + blind-test 有明确指标；
- 文档同步契约在 CI 中持续生效；
- rollback 依赖 Git/version/artifact，而不是第二套运行时算法。

## 11. 2026-08-18 Windows external-command compatibility addendum

后续 P7 external forced alignment、backend readiness 与 runtime identity 都依赖同一 configured command。Windows 本地验收证明，如果三处分别使用平台相关 `shlex`，带双引号和空格的 executable path 可能出现“readiness / executor / runtime snapshot 对同一 command 得到不同 argv”的第二真源问题。

因此跨平台 command parsing 现在遵守同一个架构规则：

```text
configured external command string
            ↓
lyric_aligner.command_line.split_external_command()
            ↓
backend discovery / forced executor / runtime snapshot identity
```

执行始终 `shell=False`；Windows 只规范化 native 双引号 token，malformed quote fail closed，不引入 shell interpolation。Runtime snapshot 仍只记录 executable basename、command SHA 与 argument count，不保存完整 command。

CLI bootstrap tests 也不再用 `env={}` 伪造“完全空环境”：它们保留 OS/CreateProcess 必需环境，只删除 `PYTHONPATH` / `PYTHONHOME` 并禁用 user-site，从而验证真正的 repository-root import isolation，而不是把 Windows 进程创建差异误判为产品 bug。

该兼容性层不参与 Source-to-Mix、canonical timeline、P8 projection、P9 fusion 或 release decision，因此不会产生新的 timing authority。真实 Windows production 可以继续使用 main；external forced family 在 backend 未准备好时仍是 optional auxiliary evidence。

## 12. 2026-08-21 Smart baseline correctness ownership addendum

Standard 与 Smart 的 canonical 输入能力并不完全相同，但“什么是 metadata/title、什么才是 lexical lyric”不能存在两套互相漂移的判断。共享的 `lyric_aligner.text.normalization` 负责 metadata/title classification；Standard 的 `text_repair.parse_canonical_files()` 继续保留 TXT/untimed 支持，Smart 的 timed parser 继续负责 same-timestamp alternative selection。这里共享的是 canonical classification contract，而不是强行合并两个 parser。

Pro v1.1.3 的完整 candidate-pool 扩池同样是内部实现细节：对外 `config.max_jobs` 必须保持调用者请求的 primary unresolved-cue budget；shadow boundary competitors 仍只附着于已选 primary 并作为 additive evidence。该维护不改变任何 Smart/Pro authority 或阈值。

## 13. 2026-08-21 Smart v1.2.5 A-bounded authority addendum

A-bounded 被设计成**冻结 timing 之后的最后一层文字证据**，而不是新的 timing model。职责边界固定为：

```text
v1.2.4 full Smart policy
        ↓
final text decisions + final timing decisions
        ↓
A-bounded reads mapped review + frozen A-grade timing evidence
        ↓
text-only materialization
        ↓
timing decisions copied unchanged
```

`a_bounded_reconcile.py` 只允许 same-source、bilateral、ready A/A bracket 授权已映射 review region；它不接受 unmapped/zero-width cue，不做 frontier chase，不跨 source，不处理 multi-cue Latin/mixed repartition，也不把 pure vocalization 当 lexical lyric。region 还必须通过独立 similarity、length-ratio 与 minimum-information gate。

`smart_policy_v125.py` 作为薄 wrapper 保留 `smart_policy.py` 的 v1.2.4 timing 行为不动。A-bounded recovered score cap `<=0.89`，低于 B timing authority；恢复后不重跑 `build_anchor_timing_plan()`。这个“post-timing + no-rebuild”结构是 anti-circularity 的架构约束，不只是当前实现细节。

因此 v1.2.5 增加的是**更窄的 canonical text recovery capability**，不是新的 timing authority：四-A gate、A/B/C grade 语义、BPM soft-prior 语义、overlap guards、cue count/number/start/end、Pro timing-write 权限全部继承 v1.2.4。

## 14. 2026-08-21 Smart→Pro current-policy ownership addendum

“当前 Smart policy”必须只有一个生产真源。Smart v1.2.5 采用薄 wrapper 冻结 v1.2.4 timing，因此旧 `timeline/smart_policy.py` 仍合法保留 v1.2.4 常量用于 base implementation；它不能再同时承担“current production policy”真源。

Pro v1.1.4 因此把兼容职责拆开：

```text
smart_policy.py      -> stable schema/base implementation contract
smart_policy_v125.py -> current production Smart policy id
selective_policy.py  -> exact current-policy gate before Pro planning
```

这不是新的 Pro authority，而是消除“base version constant 被误当 current version constant”的双真源。回归测试必须使用一个**字面量旧 policy id**验证 stale rejection，不能让生产代码和测试代码都从同一个可能过期的模块导入 policy id，否则 CI 会再次形成自洽但错误的兼容状态。
