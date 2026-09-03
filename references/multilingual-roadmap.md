# 多语言字幕算法升级路线

更新：2026-09-03

## 当前状态

当前产品路径：

```text
Standard -> Text Repair V2.1
Smart    -> Sequence Reconciliation + Anchor Timeline Repair v1.2.10
Pro      -> Selective Audio Repair v1.2.6
Max      -> Full V4 Alignment 4.0.0a14
```

普通 canonical text/order 与 Source-to-Mix timing 已经不是主要瓶颈。正式 private calibration 当前达到：

```text
unit_f1                   = 0.999221
line_exact_f1             = 0.999167
cue_text_exact_match_rate = 1.0
boundary_mae_ms           = 17.982
boundary_p95_ms           = 6.0
```

因此后续优化重点从“继续压普通 timing 毫秒误差”转向：

1. 运行语义可复现；
2. cut / overlap / same-track splice / reorder 等结构事件；
3. review 到 production release 的自动编排；
4. 只有在上述稳定后再做性能 A/B。

## P0：生产运行可复现性

`4.0.0a14` 已引入 task-local `v4_run_config.json`，绑定 Full V4 的：

```text
profile
language_map
middle_cut_map
lyric_role_map
```

三个 public Max run entrypoint 自动发现并验证该配置。配置内容变化、task fingerprint 不匹配、手工 CLI 参数漂移都 fail closed。

P0 后续验收目标：

- 现有代表性 private task 完成 run-config 迁移；
- “只给 task manifest + out-dir”即可重放与既有生产语义一致的 raw Max；
- semantic config 不再只存在于聊天、临时命令或人工记忆；
- raw task fingerprint 与 semantic run-config identity 分层但都可审计；
- 不把 workers/cache/resume 等执行策略误当成 semantic identity。

## P1：结构事件 benchmark 与新证据

当前 raw Max 普通歌词已接近 production truth，但 calibration 中结构事件仍可能保留 review，历史自动候选的 blind 泛化也未达到门槛。

下一轮 benchmark 必须把结构类型单独建模：

```text
hard_cut
same_track_splice
cross_track_crossfade
true_overlap
sequential_transition
piecewise_rate
source_reorder
silence_island / detached_tail
```

原则：

- 真实项目仅用于 private calibration / blind；
- public regression 使用 generic synthetic fixture；
- case/group 在 candidate selection 前锁定；
- fresh blind 失败后不得针对结果继续调同一 candidate threshold；
- precision / false-auto 优先于减少 review。

优先研究**正交证据**，而不是继续堆同源 retrieval score：

1. 可选 editor/NLE retained-segment / edit-decision evidence；
2. independent change-point / source-offset discontinuity evidence；
3. word/token timing 对结构边界的独立支持；
4. prepared stem 仅作为辅助诊断，除非新的 fresh blind 重新证明泛化。

历史 prepared-stem candidate 已在 fresh blind 失败并撤回，不能因为 calibration 个例表现好就重新进入 production。

## P2：生产 orchestrator

当前 authority 分层是正确的，但人工需要串联：

```text
run
-> review
-> cut/overlap materialization
-> reference-retime（若有独立证据）
-> evaluation render
-> reconciliation
-> production materialization
-> display policy
-> audit
-> release validation
```

目标不是合并这些 authority，而是提供一个单一 orchestration state machine：

- 每一步仍消费/产生现有正式 artifact；
- 遇到 review 自动停在可操作任务单；
- review 完成后可从 exact artifact identity 继续；
- 不允许 orchestrator 通过跳过 stage 获得 authority；
- 最终输出唯一 production FINAL + release manifest。

## P3：性能与成本

功能与可复现入口稳定后，再做严格 A/B：

- same machine；
- same task / run config；
- same workers；
- clean cache 与 warm resume 分开；
- wall time、decoded seconds、peak RAM、stage execution count 分开测。

现有 a12 bounded mix decode 已减少长 mix 重复解码，但不能在不同 worker/cache 条件下宣称具体百分比提升。

优先级仍是“普通任务尽量停在 Smart/Pro”，而不是让每个任务都进入 Max 再微优化几秒。

## 多语言继续方向

多语言本身不是难度标签。继续保持：

- 中文、英文、韩文、日文、mixed 的 canonical normalization 与 language hint 分层；
- Enhanced LRC/QRC word timing 能保留就保留；
- 同 timestamp 多行无法唯一判断 original 时 fail closed；
- `lyric_role_map` 只指定 canonical original，不自动猜 translation/romanization；
- code-switch/mixed/unknown 的 ASR language 保持 local/auto 策略，避免错误整曲固定语言。

## 项目上限

只有最终 mix + source song + line-LRC + imperfect editor SRT 时，某些复杂结构在信息上可能没有唯一答案。成熟目标不是宣称“所有输入 100% 无人工”，而是：

```text
ordinary regions -> highly automatic
hard structural regions -> small, precise review queue
all automatic changes -> evidence + lineage
uncertain cases -> blocked rather than silent error
production FINAL -> exact release authority
```

如果未来可稳定获得 editor/NLE edit decisions 或可靠 word-level canonical timing，项目上限会明显提高，因为一部分当前必须反推的结构问题会变成直接事实输入。
