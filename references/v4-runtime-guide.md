# Lyric Aligner v4 生产运行手册

更新：2026-08-19  
主线算法版本：`4.0.0a8`

> P4 前的完整长版手册保存在 `references/archive/2026-08-19-pre-p4-v4-runtime-guide.md`。本文件描述 Text Repair V2.1、Partial Timeline Repair P1–P5 与当前生产边界。

## 1. 不变的 authority

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor / ASR / forced -> auxiliary evidence
P9 fusion      -> uncalibrated shadow diagnostics
P4 trust lock  -> calibrated cue-trust proposal eligibility only
P5 Doctor      -> read-only readiness diagnostics only
```

始终：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

## 2. 冻结剪映时间轴，只修文字

使用 Text Repair V2.1：

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lrc "private/<任务>/input/lyrics/01.lrc" `
  --out "output/<任务>/<任务>_TEXT_REPAIRED.srt" `
  --report "output/<任务>/<任务>_TEXT_REPAIR.json"
```

它不读取 audio，不改变 cue count / number / start / end。输出写回后会重新解析 SRT，并再次比较 cue count、number 与 timing signature；任何变化都会直接失败。

V2.1 的生产规则：

- timestamped metadata（作词、作曲、制作等）会在移除时间标签后再次过滤；
- 同一 canonical 文件不能混合 timed lyric occurrence 与未标时正文；出现这种情况直接 fail closed。纯 timed LRC/QRC 与纯 untimed TXT 仍可使用；
- 缺字如果恰落 cue 边界、空格边界或换行边界，不猜字符应该属于哪一侧，保持原字幕并 `review_required`；
- canonical 中有歌词而当前 SRT 没有对应 cue 时，记录 `coverage_status=warning` / `coverage_warning_count`，但它本身不再把文字修复任务判成失败；可疑 cue 本身仍按 gap guard / ambiguity guard 进入人工复核；
- report schema 为 `2.1`。

正式 CLI 默认 `--auto-threshold 0.72`，允许提高但**不允许降低到 0.72 以下**。core Python API 仍保留实验阈值能力；正式单任务和 batch runner 都会拒绝生产阈值降级。

批处理：

```powershell
python scripts/v4_text_repair_batch.py `
  --manifest "private/<任务>/text-repair-batch.json" `
  --summary "output/<任务>/text-repair-summary.json"
```

batch 会在任何输出写入前统一预检 path ownership 和所有 job-level `auto_threshold`。`coverage_warning_job_count` 与 `review_required_count` 分开统计；仅有 coverage warning 的任务仍返回 ready/exit 0。

## 3. 时间轴任务先完成 Source-to-Mix 主链

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py ...
python scripts/v4_recompose_overlap.py ...       # 需要时
python scripts/v4_rebuild_cut.py ...              # confirmed cut 后
python scripts/v4_compose_materializations.py ... # cut+overlap 同时存在时
```

必须得到 authoritative effective run + exact artifact。BPM/rate change 属于连续映射：

```text
AFFINE / PIECEWISE_RATE
rate change != cut
```

CUT_AWARE 只在正式 `cut_timewarp_rebuild` materialization 后成立。

## 4. Evidence 与 P9

editor / ASR / forced 都是辅助证据。forced source-ms 必须先经 Source-to-Mix projection 后才能与 mix-time evidence 比较。P9 `LOW / MEDIUM / HIGH / CONFLICT` 全部仍是 shadow diagnostics；禁止从 P9 HIGH 直接推导 trusted cue 或 timing mutation。

## 5. Partial Timeline Repair P1–P4

P1：显式 `trusted / untrusted / unknown`，trusted cue timing hard lock，只允许 untrusted cue 接收 Source-to-Mix candidate。

P2：editor cue 必须唯一绑定 canonical line；candidate 只读 authoritative `source_timeline_boundary_ms`；open-end sentinel 不可修。

P3：正式生产要求：

```text
run.json + run.artifact.json
fusion.json + fusion.artifact.json
```

并从 exact coarse/Fine/cut lineage 派生映射类型，拒绝调用方手填 mapping/cut 标签。

P4：真实 private strict calibration + independent blind 通过后，构建 trust lock；只有 blind policy 明确覆盖并通过的 `language:*` scope 才可生成 calibrated cue-trust decision。正式 decision 还必须有 formal artifact，并绑定 exact lock/candidate/runtime/fusion identity。

## 6. P5 Doctor：局部时间轴修复 readiness

P5 只做只读体检，不生成 proposal，也不修改 SRT。`scripts/v4_doctor.py` 新增：

```text
--partial-trust-lock
--partial-trust-decisions
--partial-trust-decisions-artifact
```

并支持：

```text
--require partial_repair:lineage
--require partial_repair:trust_lock
--require partial_repair:actionable_scope
--require partial_repair:decisions
--require partial_repair:proposal_inputs
```

典型完整检查：

```powershell
python scripts/v4_doctor.py `
  --run "private/<任务>/run.json" `
  --run-artifact "private/<任务>/run.artifact.json" `
  --fusion "private/<任务>/fusion.json" `
  --fusion-artifact "private/<任务>/fusion.artifact.json" `
  --partial-trust-lock "private/<任务>/partial_timeline_trust.lock.json" `
  --partial-trust-decisions "private/<任务>/partial_timeline_trust.decisions.json" `
  --partial-trust-decisions-artifact "private/<任务>/partial_timeline_trust.decisions.artifact.json" `
  --require partial_repair:proposal_inputs
```

P5 会报告：

- P3 effective-run / fusion formal lineage 是否有效；
- AFFINE / PIECEWISE_RATE / CUT_AWARE occurrence 数、unavailable occurrence、confirmed cut；
- P9 CONFLICT 数和 language scopes；
- P4 trust lock 是否有效及是否存在 actionable `language:*` scope；
- formal calibrated decision artifact 是否有效；
- trusted / untrusted / unknown 与安全降级计数；
- 下一步建议动作。

可能状态：

```text
not_requested
blocked
human_review_or_calibration_required
human_review_required
calibrated_decisions_required
proposal_inputs_ready
```

`proposal_inputs_ready` 只表示可以进入 proposal-only local repair；仍然：

```text
automatic_timing_change_allowed = false
release_gate_eligible = false
```

错误详情会清理 POSIX/Windows 本地绝对路径；报告不得包含 raw lyric/subtitle text 或 artifact output paths。

## 7. 推荐生产顺序

```text
1. 确认最新 main + clean worktree
2. runtime snapshot
3. 根据任务选择 Text Repair V2.1 或完整 V4
4. Text Repair V2.1：只修文字；coverage warning 与 cue review 分开处理
5. 完整 V4：run -> review -> cut/overlap materialization -> effective run
6. editor/ASR/forced -> P8 -> P9 fusion
7. 若需局部时间轴修复，先运行 P5 Doctor readiness
8. 没有真实 P4 private calibration/blind lock 时保持 human review
9. 有 valid/actionable lock 后生成 formal calibrated decisions
10. Doctor 达到 proposal_inputs_ready 后才进入 proposal-only repair
11. 不得因 Doctor/P9/CI 结果自动写回 SRT timing
```

## 8. 公共 CI 边界

公共 CI 能证明 Text Repair timeline immutability、parser fail-closed 行为、layout-boundary insertion guard、coverage/report semantics、anchor scalability、schema/hash/lineage、mapping contract、strict lock mechanics、scope enforcement、decision artifact validation、Doctor fail-closed 行为、privacy 与 Python compatibility。

公共 CI 不能证明真实歌曲边界准确率、某 candidate 已通过 private blind、真实 false-positive / false-auto rate，也不能授权 automatic timing write-back。