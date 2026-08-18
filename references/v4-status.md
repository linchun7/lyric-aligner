# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前 main：P9 已合入；P10 batch forced-alignment 正在验收  
P8 merge：`00585a07b658ffea93509c4ed1a4b129deafd0a3`  
P9 merge：`efbdbb926b03efdf1d91622d5c23cabef1f9850c`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产主链与 evidence 链当前包含：

```text
a3-a8 production reconstruction / render / review / overlap / cut / composition
P1    strict calibration/blind framework
P1.1  private dataset scaffold/readiness
P2    editor/Jianying multilingual shadow evidence
P3    local acoustic planner/backend/faster-whisper executor
P4    shadow multi-family evidence fusion
P5    bounded ASR second-pass routing
P6    ASR second-pass execution + composite evidence
P7    external source forced alignment
P8    forced alignment source-to-mix projection
P9    editor/ASR/forced multi-family shadow fusion
```

P3 validate #493、P4 #517、P5 #530、P6 #545、P7 #560 均在各自 merge 前全绿。P8 latest result tree 的 fast-core #1 完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿后合入。P9 result tree 的 fast-core #2 同样全绿，日志显示 **Ran 324 tests / OK**；随后 P9 branch 与已经合入的 P8 main 同步 ancestry，再以 PR #19 合入。

CI 已包含：

```text
pull_request validation
push only on main
concurrency + cancel-in-progress
timeout-minutes
bounded ffprobe/apt setup
fast-core ubuntu-slim lane
```

## 2. Authority graph（生产时不要改写）

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor SRT     -> auxiliary mix-time shadow evidence
ASR            -> auxiliary mix-time acoustic evidence
P7 forced      -> auxiliary source-time acoustic evidence
P8 forced      -> same forced evidence projected to mix time
P9 fusion      -> diagnostic/shadow support state only
```

固定：

```text
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

`HIGH` 不是“可以自动改字幕”的生产置信度。

## 3. P8 / P9 已完成的 forced evidence 路径

P8 正式输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

规则：

- `AFFINE` / `PIECEWISE_RATE` 复用 `mix_time_for_source()`；
- `CUT_AWARE` cross-gap/cross-cut line -> `unprojectable`；
- 不跨 confirmed cut bridge 假连续 interval；
- spans 独立投影；
- relevant mapping/artifact provenance 缺失或不一致 fail closed。

P9 只接受 P8 mix-time forced evidence，并与 editor/ASR 做全 pairwise disagreement：

```text
editor ↔ asr
editor ↔ forced_alignment
asr ↔ forced_alignment
```

任意 pair 超过 `conflict_boundary_ms` -> `CONFLICT`；不会用多数票隐藏 outlier。`unprojectable` forced line 只保留 diagnostic，不算 available family。

## 4. 当前 P10：Forced Alignment Batch Protocol 1.1

P7 protocol `1.0` 每个 bounded forced job 启动一个 subprocess。真实 CTC/singing backend 可能每次进程启动都重新加载大模型，因此在几十/数百歌词 jobs 时会产生重复模型加载。

P10 新增：

```text
lyric_aligner/alignment/forced_batch.py
references/forced-alignment-batch-protocol.md
scripts/test_v4_forced_alignment_batch.py
scripts/test_v4_forced_alignment_batch_end_to_end.py
```

并扩展：

```text
scripts/v4_execute_forced_alignment.py
lyric_aligner/alignment/__init__.py
```

CLI 默认行为不变：

```text
--execution-mode single   # protocol 1.0，一 job 一进程
--execution-mode batch    # protocol 1.1，所有 selected jobs 一次 subprocess
```

Batch request/response：

```text
<command> --batch-request <temp.json> --batch-response <temp.json>
```

一个成功 batch 必须返回与 request **完全相同的 job ID 集合**；missing/extra/duplicate job 均 fail closed。

## 5. P10 safety / compatibility

Batch 只改变执行效率，不改变 authority 或 P7 formal evidence 类型：

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
canonical_text_authority = canonical_lyrics_only
timing_authority = auxiliary_source_forced_alignment_evidence
```

因此 batch output 可以不改协议地继续进入：

```text
P10 batch forced evidence
-> P8 exact Source-to-Mix projection
-> P9 multi-family shadow fusion
```

Batch 仍逐 job 校验 source audio SHA、canonical SHA、source window、backend/model identity、line/span monotonicity。正式 evidence 不复制 raw canonical text/local source path。

显式 `selected_job_ids=[]` = 0 work，不能解析/启动 configured command。

Artifact 同时区分：

```text
requested_execution_mode = single | batch
execution_mode = single_job_subprocess | batch_subprocess
protocol_version = 1.0 | 1.1
command_invocation_count
```

## 6. P10 tests 已写入

Unit：

- 两个 jobs -> runner 只调用 1 次；
- selected subset -> 仍只调用 1 次；
- explicit empty selection -> 0 调用；
- response 缺 job -> fail；
- duplicate response job ID -> fail；
- ephemeral request 可含 canonical text，但 formal result 不泄漏。

真实 subprocess E2E：

- 临时 Python batch adapter；
- 两个 source forced jobs；
- 磁盘计数器证明只启动一次外部进程；
- formal artifact 记录 protocol `1.1`、`batch_subprocess`、`command_invocation_count=1`；
- plan/assets/run/timeline lineage 保持完整；
- formal payload/artifact 不保存歌词或完整 external command。

P10 尚未经过当前 clean branch latest-head Actions，当前不能宣称可合入。

## 7. Multilingual model lineage 边界

Batch protocol 假设同一次 invocation 的 backend/model identity 可审计。未来 WhisperX reference adapter 不得在同一 batch 内静默加载多种语言 checkpoint、却只记录一个误导性的 `model_id`。

第一版真实 adapter 应优先：

```text
same language + same align model -> one batch
```

后续若需要跨语言 batch，应定义 versioned model-bundle manifest，完整记录 per-language checkpoint/revision/hash。

## 8. 本地 Codex 生产入口

标准顺序：

```text
1. task + canonical LRC + source audio + edited mix/editor SRT
2. v4_run / review / cut-overlap materialization
3. editor evidence
4. ASR first-pass + bounded second-pass
5. external source forced alignment（可 single；P10 合入后推荐同模型 jobs 使用 batch）
6. P8 forced source->mix projection
7. P9 shadow fusion
8. 人工核查高风险行
9. render + validate_release
10. private calibration/blind
```

遇到 provenance/blocking error 时重新生成或修正上游输入，不手改 artifact JSON 绕过 lineage。

## 9. 代码收口后仍必须由真实私有数据完成

P10 解决真实 forced backend 的进程/模型加载可行性，但**不证明准确率**。下一阶段仍必须：

1. 选择/安装实际 forced-aligner adapter/runtime；
2. 锁定 backend/package version、model/checkpoint revision、language/G2P resources、runtime/device identity；
3. 用 private real-song calibration set 测 Source-to-Mix/editor/ASR/forced boundary error、coverage/conflict；
4. 冻结后在独立 blind set 验证；
5. blind 数据证明收益前，不启用 auxiliary timing mutation/release authority。

> **当前目标：先把 P10 batch protocol 用 synthetic/real-subprocess CI 收口并合入；随后停止继续扩抽象层，直接做真实 WhisperX/SOFA reference adapter 与 private calibration/blind。**
