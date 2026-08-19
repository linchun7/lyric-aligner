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
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

P4 的 `policy_calibrated=true` 仅证明某个 cue-trust candidate 已经过 strict calibration + independent blind；它不改变 P9 authority，也不授权自动改 SRT。P5 的 `proposal_inputs_ready` 也只表示 formal inputs 已就绪。

## 2. Text Repair V2

已成立并进入主线。适用于“规范歌词可信、剪映时间轴冻结、只修文字”：不读取 audio，不改变 cue count/number/start/end；支持确定性错字、漏字、多字与 bounded segmentation-span repair；不确定时 `review_required`。

## 3. Partial Timeline Repair P1–P4

### P1 `partial_repair.py`

显式 cue trust = `trusted / untrusted / unknown`；trusted timing hard lock；只有 untrusted cue 可接收 Source-to-Mix candidate；支持 `AFFINE / PIECEWISE_RATE / CUT_AWARE`；`rate change != cut`；candidate 不得穿越 trusted neighbor 或与其他 candidate 重叠；proposal-only。

### P2 `partial_repair_evidence.py`

P9 `LOW/MEDIUM/HIGH/CONFLICT` 只做 diagnostics；HIGH 不自动 trusted；CONFLICT 不自动断言 editor 错；editor cue 必须唯一绑定 canonical line；candidate 只使用 authoritative `source_timeline_boundary_ms`；open-end sentinel 不可修。

### P3 `partial_repair_context.py` + `partial_repair_production.py`

生产输入必须是 effective run/artifact + P9 fusion/artifact；mapping kind 从 exact coarse/Fine `TimeWarp.mapping.mode` 派生，不从 BPM 猜；Fine identity 必须与 coarse 一致；blocked mapping 不可用；CUT_AWARE 只认 materialized `cut_timewarp_rebuild` lineage；tampered fusion JSON 通过 formal output hash fail closed。

### P4 `partial_repair_trust.py` + `partial_repair_trust_production.py`

复用 strict calibration/blind workflow，不定义 public auto threshold；selected candidate ID/revision/runtime/file SHA/gates 全部重新验证；只有 blind policy 显式通过的 `language:*` scope 才可生成 calibrated trust proposal；overall-only lock 可审计但不可自动产生 cue trust；CONFLICT 不可 auto-trusted；human review 可覆盖 calibrated decision；decision JSON 还必须有 formal artifact，绑定 exact lock/candidate/runtime/fusion upstream；仍不自动写 timing。

## 4. P5 Doctor/readiness

P5 代码已实现，合并只允许发生在最终 exact head 的 fast-core + full validate gate 全绿之后。

新增：

```text
lyric_aligner/timeline/partial_repair_readiness.py
lyric_aligner/doctor_partial.py
scripts/v4_doctor.py  # additive CLI options/requirements
scripts/test_v4_partial_timeline_repair_readiness.py
scripts/test_v4_doctor_partial_repair.py
scripts/test_v4_doctor_partial_cli.py
```

P5 是纯只读诊断层，复用 P3/P4 正式 validator，不创建 cue、candidate、trust lock、decision artifact、SRT mutation 或 release state。

Doctor `partial_timeline_repair` 子报告回答：

1. P3 lineage：effective run + P9 fusion formal artifact 是否 current/一致；各 occurrence 的 mapping-kind / unavailable 数量；confirmed cut；P9 conflict 与 `language:*` scopes。
2. P4 trust lock：是否有效、是否 actionable、blind 实际覆盖哪些 `language:*` scopes。没有 lock 时明确进入 human review/private calibration，而不是把 P9 HIGH 当 ready。
3. P4 decision artifact：payload/artifact 是否成对、formal output/upstream 是否有效、scope/binding/conflict 语义是否通过，并仅输出聚合 counts。
4. 下一步建议：补 lineage、构建真实 private trust lock、扩充 blind language gate、生成 formal calibrated decisions，或进入 proposal-only partial repair。

新增 requirements：

```text
partial_repair:lineage
partial_repair:trust_lock
partial_repair:actionable_scope
partial_repair:decisions
partial_repair:proposal_inputs
```

原 `build_doctor_report()` API、legacy requirements 和主流程推荐逻辑保持不变；P5 通过 `build_doctor_report_with_partial_repair()` 向后兼容扩展。

Privacy：不输出 raw lyric/subtitle、artifact output path 或 local absolute path；异常 detail 对 POSIX/Windows absolute paths 脱敏。

## 5. P5 readiness 状态

```text
not_requested
blocked
human_review_or_calibration_required
human_review_required
calibrated_decisions_required
proposal_inputs_ready
```

其中 `proposal_inputs_ready` 只表示：

```text
P3 lineage valid
P4 trust lock valid/actionable
formal calibrated decision artifact valid
```

它仍然不是 publish/release/timing-write-back 授权。

## 6. 代码层完成后的真实下一步

P5 通过最终 CI 并合入后，当前代码层安全/readiness 骨架完成。下一阶段不是继续编 synthetic threshold，而是在 private real-song 数据上：

```text
strict calibration
-> independent blind
-> real trust lock
-> formal calibrated decisions
-> P5 Doctor coverage/readiness
-> proposal-only partial repair evaluation
```

没有真实 blind false-positive / false-auto-repair 数据前，不进入 automatic timing write-back promotion。
