# Lyric Aligner v4 生产运行手册

更新：2026-08-19  
主线算法版本：`4.0.0a8`

> P4 前的完整长版运行手册保存在 `references/archive/2026-08-19-pre-p4-v4-runtime-guide.md`。本文件保留当前生产入口、authority 与 Partial Timeline Repair P1–P4 的真实运行顺序。

## 1. 不变的 authority

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor / ASR / forced -> auxiliary evidence
P9 fusion      -> uncalibrated shadow diagnostics
P4 trust lock  -> calibrated cue-trust proposal eligibility only
```

全局固定：

```text
release_gate_eligible = false
automatic_timing_change_allowed = false
```

`P9 HIGH` 不是自动修改时间轴的授权；P4 trust lock 即使通过 private calibration + blind，也只允许生成 cue trust proposal，不允许自动 timing write-back。

## 2. 任务分流

### 2.1 只修文字、冻结剪映时间轴

使用 Text Repair V2：

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lrc "private/<任务>/input/lyrics/01.lrc" `
  --out "output/<任务>/<任务>_TEXT_REPAIRED.srt" `
  --report "output/<任务>/<任务>_TEXT_REPAIR.json"
```

它不读取 audio，不修改 cue count / number / start / end。需要修 timing、cut、overlap、缺失 cue 时不要走这个入口。

### 2.2 时间轴需要判断或局部修复

先完成完整 V4 Source-to-Mix 主链：

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py ...
python scripts/v4_recompose_overlap.py ...       # 需要时
python scripts/v4_rebuild_cut.py ...              # confirmed cut 后
python scripts/v4_compose_materializations.py ... # cut+overlap 同时存在时
```

必须得到 authoritative effective run + exact artifact。BPM/rate change 是连续映射问题，默认遵守：

```text
AFFINE / PIECEWISE_RATE
rate change != cut
```

CUT_AWARE 只在 confirmed cut 已通过正式 `cut_timewarp_rebuild` materialize 后成立。

## 3. P7 / P8 / P9 evidence

需要声学辅助证据时按顺序运行：

```text
editor evidence
ASR first-pass / bounded second-pass
external forced alignment（需要时）
forced source -> mix projection
P9 fusion
```

P7 source-ms forced evidence 禁止直接与 editor/ASR mix-ms 比较；forced evidence 必须先经 P8 投影到 mix time。

P9 fusion：

```text
LOW / MEDIUM / HIGH / CONFLICT
```

全部仍是 shadow diagnostics。任何 payload 与 artifact 必须成对使用，禁止手改 JSON 绕过 output SHA / artifact lineage。

## 4. Partial Timeline Repair P1–P3

P1：显式 `trusted / untrusted / unknown` cue trust；trusted cue timing hard lock；只有 untrusted cue 可接收 Source-to-Mix candidate；candidate 不能穿越 trusted neighbor，也不能互相重叠。

P2：P9 shadow level 不自动生成 trust；editor cue 必须唯一绑定 canonical line；candidate 只读 authoritative `source_timeline_boundary_ms`；open-end 1ms sentinel 不可作为 repair interval。

P3 正式生产输入必须是：

```text
effective run + exact run artifact
P9 fusion + exact fusion artifact
explicit cue trust
```

P3 会从 exact coarse/Fine/cut lineage 派生 `AFFINE / PIECEWISE_RATE / CUT_AWARE`，不接受生产调用方手填 mapping/cut 标签。Fine 必须与 coarse 的 occurrence/track/canonical-selection identity 一致；tampered fusion JSON 会因 formal output hash 不一致 fail closed。

## 5. P4：真实 calibration/blind 后构建 trust lock

P4 不在代码里发明 threshold。先使用既有 strict workflow 完成真实 private calibration + independent blind：

```powershell
python scripts/v4_calibration_workflow.py evaluate ...
python scripts/v4_calibration_workflow.py select ...
python scripts/v4_calibration_workflow.py blind ...
```

要求：

- calibration / blind source_group 隔离；
- selected candidate ID / revision / runtime identity 锁定；
- calibration 与 blind ground-truth/case identities 不同；
- blind gate 通过；
- 需要自动产生某语言 cue trust 时，blind policy 必须显式包含并通过对应 `language:*` gate。

只通过 overall gate 不会外推为所有语言可用。

然后构建 trust lock：

```powershell
python scripts/v4_build_partial_trust_lock.py `
  --selection "private/calibration/selection.json" `
  --calibration-baseline "private/calibration/baseline.eval.json" `
  --calibration-candidate "private/calibration/selected.eval.json" `
  --calibration-policy "private/calibration/calibration.policy.json" `
  --blind-gate "private/blind/blind.gate.json" `
  --blind-baseline "private/blind/baseline.eval.json" `
  --blind-candidate "private/blind/selected.eval.json" `
  --blind-policy "private/blind/blind.policy.json" `
  --out "private/calibration/partial_timeline_trust.lock.json"
```

Lock 会重新验证 selection/self-hash、evaluation/policy file SHA、candidate revision/runtime、calibration gate、blind gate 与 split identity；不会仅信任一个 `passed=true` 字段。

## 6. P4 trust decision 的正式生产要求

真实 selected candidate 的 cue-level decision 必须绑定：

```text
trust_policy_lock_sha256
candidate_id / candidate_revision / runtime_identity
exact P9 fusion artifact ID
language:* scope
```

同时必须生成 formal artifact：

```text
stage = partial_timeline_trust_decisions
role  = cue_trust_decisions
```

正式生产只接受：

```text
run.json + run.artifact.json
fusion.json + fusion.artifact.json
trust.lock.json
decisions.json + decisions.artifact.json
```

Decision artifact 必须校验 output SHA/size、自签名、exact fusion upstream、trust-lock/candidate/runtime/fusion binding。只有 self-hash、没有 formal artifact 的 decision JSON 不能进入生产。

安全降级：

- blind 未覆盖该 language scope -> `unknown`；
- editor→canonical binding 缺失或多义 -> `unknown`；
- P9 `CONFLICT` + candidate decision=`trusted` -> 强制 `unknown`；
- P9 `HIGH` 但没有 calibrated decision -> 不生成 trust；
- explicit human review 对相同 cue 优先于 calibrated decision。

即使得到 calibrated `untrusted` cue，后续也只是进入 P1/P3 生成 Source-to-Mix repair proposal；仍不会自动写 SRT timing。

## 7. Production Doctor / runtime identity

每个真实候选开始前先固化 runtime：

```powershell
python scripts/v4_runtime_snapshot.py ...
```

已有 payload/artifact 时运行：

```powershell
python scripts/v4_doctor.py ... --require lineage
```

遇到 task/artifact/output SHA/runtime/provenance 漂移时重新生成上游，不要修改 artifact JSON。

## 8. CI 能证明 / 不能证明

公共 CI 可以证明：

```text
schema / artifact hash / lineage
AFFINE / PIECEWISE_RATE / CUT_AWARE contract
Fine/coarse identity
fusion/decision tamper rejection
strict selection/blind lock mechanics
language-scope coverage enforcement
trusted timing structural guards
privacy / docs / Python compatibility
```

公共 CI 不能证明：

```text
真实歌曲边界准确率
某个真实 trust candidate 已经通过 private blind
某语言自动 trust 的 false-positive rate
自动 timing repair 的 false-auto rate
自动 timing write-back 安全
```

仓库因此不提交 synthetic “production-ready trust lock”，也不因为单测通过就开启 automatic timing mutation。

## 9. 给本地 Codex 的当前生产顺序

```text
1. 读取 SKILL.md / v4-runtime-guide / v4-status / v4-implementation
2. 确认最新 main + clean worktree
3. runtime snapshot + doctor --require lineage
4. 根据任务选择 Text Repair V2 或完整 V4
5. 完整 V4：run -> review -> cut/overlap materialization -> effective run
6. editor/ASR/forced -> P8 -> P9 fusion
7. Partial Timeline Repair 只在 explicit human trust 或真实 P4 trust lock/decision 存在时生成 proposal
8. 没有 private calibration/blind 证据时保持人工 review
9. render / validate_release 仍按现有 V4 release contract执行
```

遇到证据不足时 fail closed；不要从 P9 HIGH、BPM、LRC timestamp 或 synthetic test 猜生产 timing authority。
