# Lyric Aligner v4 当前实施状态

更新日期：2026-08-19  
主线算法版本：`4.0.0a8`

> P3 前完整历史状态：`references/archive/2026-08-19-pre-p3-v4-status.md`。本文件只保留当前生产事实。

## 1. Authority 与生产边界

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor / ASR / forced -> auxiliary evidence
P9 fusion      -> uncalibrated shadow diagnostics
P4 trust lock  -> calibrated cue-trust proposal eligibility only
P5 Doctor      -> read-only readiness diagnostics only
```

始终固定：

```text
release_gate_eligible = false
automatic_timing_change_allowed = false
```

P4 的 `policy_calibrated=true` 仅证明某个 cue-trust candidate 已经过 strict calibration + independent blind；它不改变 P9 authority，也不授权自动改 SRT。

## 2. Text Repair V2

适用于“规范歌词可信、剪映时间轴冻结、只修文字”。不读取 audio，不改变 cue count/number/start/end；支持确定性错字、漏字、多字与 bounded segmentation-span repair；不确定时 `review_required`。

## 3. Partial Timeline Repair P1–P4

### P1 `partial_repair.py`

- cue trust = `trusted / untrusted / unknown`；
- trusted cue timing hard lock；
- untrusted cue 才可接收 Source-to-Mix candidate；
- `AFFINE / PIECEWISE_RATE / CUT_AWARE`；
- `rate change != cut`；
- candidate 不得穿越 trusted neighbor 或与其他 candidate 重叠；
- proposal-only。

### P2 `partial_repair_evidence.py`

- P9 `LOW/MEDIUM/HIGH/CONFLICT` 只做 diagnostics；
- HIGH 不自动 trusted；CONFLICT 不自动断言 editor 错；
- editor cue 必须唯一绑定 canonical line；
- candidate 只使用 authoritative `source_timeline_boundary_ms`；
- open-end `start+1ms` sentinel 不可修。

### P3 `partial_repair_context.py` + `partial_repair_production.py`

- 生产输入必须是 effective run/artifact + P9 fusion/artifact；
- mapping kind 从 exact coarse/Fine TimeWarp `mapping.mode` 派生，不从 BPM 猜；
- Fine occurrence/track/canonical-selection identity 与 coarse 完全一致；
- `mapping_blocked=true` -> unavailable；
- CUT_AWARE 只认 materialized `cut_timewarp_rebuild` lineage；
- tampered fusion JSON 通过 formal output SHA fail closed；
- 正式生产只接受当前 algorithm version。

### P4 `partial_repair_trust.py` + `partial_repair_trust_production.py`

- 复用 strict calibration/blind workflow，不定义 public auto threshold；
- calibration 与 blind source-group/ground-truth/case identity 分离；
- selected candidate ID/revision/runtime/file SHA/gate 重新验证；
- 只有 blind policy 显式通过的 `language:*` scope 才可生成 calibrated trust proposal；
- overall-only blind lock 可审计但不可自动产生 cue trust；
- CONFLICT 不可 auto-trusted；human review 可覆盖 calibrated decision；
- decision JSON 还必须有 formal `partial_timeline_trust_decisions` artifact，绑定 exact lock/candidate/runtime/fusion upstream；
- 仍不自动写 timing。

## 4. P5 Doctor/readiness（开发中）

新增：

```text
lyric_aligner/timeline/partial_repair_readiness.py
lyric_aligner/doctor_partial.py
scripts/v4_doctor.py  # additive CLI options/requirements
scripts/test_v4_partial_timeline_repair_readiness.py
scripts/test_v4_doctor_partial_repair.py
scripts/test_v4_doctor_partial_cli.py
```

P5 是纯只读诊断层。它复用 P3/P4 的正式 validator，不复制弱化的 lineage 规则，也不创建 cue、candidate、trust lock 或 decision artifact。

Doctor 新增 `partial_timeline_repair` 子报告，回答：

1. **P3 lineage**：effective run + P9 fusion formal artifact 是否 current/一致；各 occurrence 的 `AFFINE / PIECEWISE_RATE / CUT_AWARE / unavailable` 数量；confirmed-cut occurrence 数；P9 conflict 数与出现的 `language:*` scopes。
2. **P4 trust lock**：是否有效、是否 actionable、blind 实际覆盖哪些 `language:*` scopes。没有 lock 时明确提示 human review 或继续 private calibration，绝不把 P9 HIGH 当 ready。
3. **P4 decision artifact**：payload/artifact 是否成对、formal output/upstream 是否有效、decision scope 是否通过 P9/lock 语义校验，并只输出聚合 counts。
4. **下一步**：只给 readiness 建议，例如补 lineage、构建 private trust lock、补 language blind gate、生成 calibrated decision artifact、或进入 proposal-only partial repair。

新增 requirements：

```text
partial_repair:lineage
partial_repair:trust_lock
partial_repair:actionable_scope
partial_repair:decisions
partial_repair:proposal_inputs
```

这些 requirement 与原 Doctor requirements 合并判定；原 `build_doctor_report()` API 与既有主流程推荐逻辑保持不变，P5 通过 `build_doctor_report_with_partial_repair()` 扩展。

Privacy：P5 不输出 raw lyric/subtitle、artifact output path 或 local absolute path；异常 detail 还会额外对 POSIX/Windows absolute paths 做脱敏。

## 5. 当前状态含义

P5 最成熟状态为：

```text
partial_timeline_repair.status = proposal_inputs_ready
```

它只表示：

```text
P3 lineage valid
P4 trust lock valid/actionable
formal calibrated decision artifact valid
```

仍然：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

## 6. 下一阶段边界

P5 合入后，代码层安全/readiness 骨架已经足够。下一步应优先在 private real-song 数据上实际运行 strict calibration/blind、构建真实 trust lock，并用 Doctor 检查 coverage/readiness。没有真实 blind false-positive / false-auto-repair 数据前，不进入自动 timing write-back promotion。
