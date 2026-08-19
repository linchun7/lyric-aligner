# Lyric Aligner v4 当前实施状态

更新日期：2026-08-19  
当前生产代码基线：P9 + Production Readiness Tooling + Windows validation hardening + bounded mix decode + source feature cache + PR25 execution optimizer + PR26 run-lock hardening + Text Repair V2 + optional forced-alignment batch protocol 1.1  
P8 merge：`00585a07b658ffea93509c4ed1a4b129deafd0a3`  
P9 merge：`efbdbb926b03efdf1d91622d5c23cabef1f9850c`  
PR21 merge：`04e0802156f62006c6b6af5b4ef59b1acc81ce86`  
PR22 merge：`2b4a13132e95a551392811407f48573b36edab95`  
PR23 merge：`4d7e086aedd2b56210368302d9a17df29fef6a0c`  
PR24 merge：`0b1f38c98542eed9ec80034677cef4bf8e7f9791`  
PR25 merge：`3bd5e388d8d68bf88233eee60e0d85dcd7816a3e`  
PR26 merge：`004d2558f646993a44f51d855305f6ba285d04cd`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入生产代码基线

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
PR21  production doctor + family evaluator + runtime snapshot
PR22  Windows command/bootstrap/privacy validation hardening
PR23  bounded mix decode for coarse/fine production alignment
PR24  source harmonic feature cache for repeated coarse jobs
PR25  text-only repair + verified-input session + safe resume + bounded workers
PR26  canonical-gap/timed-order/format-preserving repair + same-out-dir lock
Text V2 deterministic typo/missing/extra-char + anchored bounded segmentation-span repair, immutable timing
Batch  external forced-alignment protocol 1.1 (optional; single remains default)
```

P3 validate #493、P4 #517、P5 #530、P6 #545、P7 #560 均在各自 merge 前全绿。P8 latest result tree 的 fast-core #1 完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿后合入。P9 result tree 的 fast-core #2 同样全绿，日志显示 **Ran 324 tests / OK**；随后 P9 branch 与已经合入的 P8 main 同步 ancestry，再以 PR #19 合入。PR21 以 merge `04e0802156f62006c6b6af5b4ef59b1acc81ce86` 进入 main，补齐 Doctor、runtime snapshot 与 family calibration tooling。PR22 exact-head fast-core 与 Python 3.10/3.12/3.14 + ASR validate 全绿后，以 merge `2b4a13132e95a551392811407f48573b36edab95` 进入 main。PR23 exact-head fast-core #43 与 validate #675 全绿后，以 merge `4d7e086aedd2b56210368302d9a17df29fef6a0c` 进入 main。PR24 将同一 source track 的 harmonic features 做 SHA/config/runtime-bound disposable cache，合入 merge `0b1f38c98542eed9ec80034677cef4bf8e7f9791`。PR25 exact-head fast-core #77 与 validate #711 在 Python 3.10/3.12/3.14 + ASR 环境全绿后，以 merge `3bd5e388d8d68bf88233eee60e0d85dcd7816a3e` 进入 main；它保留原 authoritative `v4_run` core 不变，只在外围增加 text-only 路径与 execution optimizer。PR26 exact head `8539c1cd913918ea42468296a0a6d2005e822190` 的 fast-core #87 与 validate #722 全绿，392 项 unit/E2E 在 Python 3.10/3.12/3.14 均通过，随后以 merge `004d2558f646993a44f51d855305f6ba285d04cd` 进入 main；merge tree 与已验证 head tree 同为 `d87484280f80a22a7d7229233e68a93b39c49de9`。

CI 同时增加：

```text
pull_request validation
push only on main
concurrency + cancel-in-progress
timeout-minutes
bounded ffprobe/apt setup
fast-core ubuntu-slim lane
```

这样可避免 feature-branch push + PR 双重排队，以及失控的 ffmpeg apt job 长时间占用 runner。

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

当前固定：

```text
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此 `HIGH` 不是“可以自动改字幕”的生产置信度；它只表示当前未校准规则下多个 auxiliary families 相互支持。

## 3. P8：Forced Alignment Source-to-Mix Projection

正式输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

规则：

- `AFFINE` / `PIECEWISE_RATE` 复用现有 `mix_time_for_source()`；
- `CUT_AWARE` cross-gap/cross-cut line -> `unprojectable`；
- 不跨 confirmed cut bridge 假连续 interval；
- spans 独立投影；
- relevant mapping/artifact provenance 缺失或不一致 fail closed；
- P7 source-ms evidence 禁止直接与 editor/ASR mix-ms 比较。

## 4. P9：Multi-Family Shadow Fusion

可用 auxiliary boundary families：

```text
editor
asr
forced_alignment
```

Forced family 只接受 P8 mix-time evidence，并再次检查 occurrence/canonical line/track/canonical_text_sha256 identity。

所有可用 auxiliary pair 都做 boundary disagreement：

```text
editor ↔ asr
editor ↔ forced_alignment
asr ↔ forced_alignment
```

任意 pair 超过 `conflict_boundary_ms` -> `CONFLICT`，不会以 2-of-3 多数票隐藏 outlier。

无冲突时：

```text
0 auxiliary family -> LOW
1 auxiliary family -> MEDIUM
>=2 auxiliary families -> HIGH
```

新增 diagnostics：

```text
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
forced_alignment_line_counts = projected / unprojectable / absent
```

Cross-run evidence、artifact hash 篡改、unknown canonical line、track/text identity 漂移、`unprojectable` forced payload 夹带 mix boundary 均 fail closed。

## 5. 本地 Codex 生产入口

本地 Codex 开始任务先读：

```text
SKILL.md
references/v4-runtime-guide.md
references/v4-status.md
references/v4-implementation.md
references/dataset-protocol.md
```

如果任务明确要求“规范歌词可信，剪映 SRT cue 数量/编号/全部起止时间冻结，只修文字”，优先使用 **Text Repair V2**；它不读取音频，也不运行 Source-to-Mix / coarse / fine / transition / ASR / forced alignment：

```text
scripts/v4_text_repair.py
scripts/v4_text_repair_batch.py   # 多任务批处理
```

Text Repair V2 允许剪映与规范歌词的分句方式不同，但不允许因此修改 cue/time。它先用唯一 exact normalized lyric 锚点把长字幕切成局部区间，再在每个区间用 banded monotonic span DP；常见 `1↔1 / 1↔2 / 2↔1 / 2↔2` 允许高置信修复，3–4 个 cue/lyric 的更极端多断句/少断句只在拼接后近乎完全一致时放行。span 内执行确定性字符 `replace / insert / delete`，因此普通错字、漏字、多字、断句数量/位置不同都可在证据充分时自动处理；真实缺失 canonical occurrence、额外 subtitle cue、低相似度、gap 邻域不稳定、近似重复歌词歧义或会清空现有 cue 的重分配继续 `review_required`。

Text Repair V2 完全忽略 LRC timestamp 的绝对间距来决定 SRT timing，因此歌曲 BPM 加速/减速不会改变本路径的文字纠错结果；任何需要修时间轴的任务都必须进入完整 Source-to-Mix 路径。

只有需要重新判断时间轴、cut、overlap、缺失 cue 或声学边界时才进入完整 V4：

```text
1. task + canonical LRC + source audio + edited mix/editor SRT
2. v4_run / review / cut-overlap materialization -> authoritative effective run
3. editor evidence
4. ASR first-pass + bounded second-pass
5. external source forced alignment（需要时；single 默认，batch 仅显式启用）
6. P8 forced source->mix projection
7. P9 shadow fusion：先看 CONFLICT / unprojectable / missing family
8. 人工核查高风险行
9. render
10. validate_release
11. 把真实人工 ground truth 写入 private calibration/blind 数据集
12. calibration 选型/阈值冻结后，再用独立 blind set 验证
```

遇到 provenance/blocking error 时重新生成或修正上游输入，**不要手改 artifact JSON 绕过 lineage**。

## 6. 从现在开始必须由真实私有数据完成的工作

代码阶段不再继续凭 synthetic 测试放宽 timing authority。下一阶段应直接在本地真实歌曲上：

1. 选择/安装实际 forced-aligner adapter/runtime；
2. 锁定 backend/package version、model/checkpoint revision、language/G2P resources、runtime/device identity；
3. 每种主要语言先取 3–5 个 30–90 秒片段，并覆盖 normal global-rate、dynamic local stretch、cut、overlap、弱人声/强伴奏、editor 弱语言；
4. 人工 ground truth 记录 Source-to-Mix/editor/ASR/forced boundary error、family coverage、CONFLICT、unprojectable 与 language/risk bucket；
5. calibration set 选择真实 backend/threshold/profile；
6. 冻结后只在 blind set 验证；
7. **只有 blind 数据证明收益后**，才设计/启用 calibrated automatic boundary refinement 或 release-gate integration。

公共 Actions、fake external subprocess 和 synthetic fixtures 能证明 contract/math/lineage/privacy，但不能证明 WhisperX/SOFA/MFA 或任何真实 checkpoint 对歌声的准确率。

## 7. Production Readiness Tooling

已进入 main 的三个只读/评估型能力，目的是让本地 Codex 能可靠续跑真实任务并积累 calibration 数据，而不是继续凭 synthetic 数据改算法：

```text
scripts/v4_doctor.py
scripts/v4_evaluate_evidence_families.py
scripts/v4_runtime_snapshot.py
```

`v4_doctor.py`：检查 task/run/evidence/dataset/backend readiness，支持 `--require` 返回非零，并给出 recommended next action。报告不泄露歌词、绝对路径或完整 external command。

`v4_evaluate_evidence_families.py`：对 P9 fusion 中统一到 mix-time 的 Source-to-Mix/editor/ASR/forced boundaries 与 private hash-bound truth 做逐 family 误差统计；输出 overall、language、risk bucket 的 coverage、MAE/P95、250/500ms 命中率、CONFLICT 与 unprojectable rate。它仍是 calibration evidence，不改变 authority。

`v4_runtime_snapshot.py`：生成稳定 `runtime_identity_sha256`，记录 Git/Python/OS/ffmpeg/package/model/device/forced-command hash 等可复现信息，并对本地路径/完整命令做 redaction。

推荐本地真实任务在开始时先运行 runtime snapshot；已有 artifact 时运行 doctor；完成 P9 fusion 并有人工作 truth 后运行 family evaluator。这样下一轮决定 backend/checkpoint/threshold 时有机器可比的数据，而不是凭肉眼印象。

## 8. Windows validation hardening（已合入 PR22）

PR22 只处理真实 Windows 本地验收暴露的跨平台问题，不扩大 timing scope：

- external forced-aligner command parsing 由 backend readiness、P7 executor、runtime snapshot 共用同一 helper；
- Windows 双引号 executable path / quoted arguments 在 `shell=False` 下保持一致 argv 语义，malformed quoting fail closed；
- bootstrap tests 保留 OS 创建进程所需环境，同时移除 `PYTHONPATH/PYTHONHOME` 并设置 `PYTHONNOUSERSITE=1`，继续验证 repository-root bootstrap；
- privacy scanner 恢复严格的本地用户目录根路径扫描（覆盖常见 Unix/macOS/Windows 形式），敏感示例由测试在 runtime 拼接，不引入 allowlist/排除规则。

Authority 与 release boundary 完全不变：canonical lyric、Source-to-Mix、P7/P8/P9 shadow semantics、threshold、release gate、automatic timing behavior 均未调整。该变更属于跨平台执行/验证可靠性修复，不是 accuracy promotion。

## 9. Bounded mix decode（已合入 PR23）

第一轮真实 12 首私有生产任务证明旧串行 V4 存在明显重复 I/O：每个 occurrence/transition coarse 或 fine CLI 都可能重新解码最终长 mix，虽然核心 feature 实际只需要局部时间窗。

PR23 已把这部分变成同算法语义的 bounded decode：

- coarse CLI 只解码当前 occurrence / transition 区间及 2 秒保护 padding；
- fine CLI 只解码 coarse retrieval windows 覆盖区间及保护 padding；
- core mapper/refiner 接受带 absolute start 的 bounded mix buffer，仍输出全局 mix 坐标；
- 正式 payload 继续绑定完整 `mix_audio_sha256`；
- full-buffer ↔ bounded-buffer 等价测试要求 path/timewarp 保持一致。

这不是低精度模式，不改变 threshold、cut/review、Source-to-Mix authority 或 release semantics。

## 10. Source harmonic feature cache（已合入 PR24）

`v4_run.py` 对 12 首 primary occurrence 以及 11 个 transition boundary 会多次调用同一原曲的 coarse alignment。即使 mix 已 bounded decode，旧实现仍会对同一 source audio 重复执行 HPSS、Chroma CENS、MFCC feature extraction；一首歌通常会在 primary 及相邻 transition 中重复使用。

PR24 增加可删除的本地 source feature cache：

- cache 仅保存数值 harmonic `FeatureBundle`，不保存歌词、source path 或 timing decision；
- key 绑定 source audio SHA-256、sample rate、hop length、feature implementation ID 与 librosa version；
- 默认 V4 输出树的 primary/transition coarse 共享同一 `cache/features`；
- cache hit 时不再重新 decode/source HPSS/chroma/MFCC；
- cache miss 正常从 SHA-bound source audio 计算并原子写入；
- corrupt / incompatible / missing cache 一律当 miss，不 BLOCK 生产；
- formal coarse payload/artifact 仍绑定完整 source/mix SHA、task fingerprint、profile、algorithm version 和原有 lineage，cache 本身不是 upstream artifact。

该性能层不修改 slope grid、score/margin threshold、cut/review 或 Source-to-Mix 选择逻辑。

## 11. Text Repair V2：高频冻结时间轴文字修复

针对“规范歌词可信、剪映时间轴总体可信并明确要求冻结”的高频生产场景，Text Repair V2 将旧 1:1 等长 fast path 升级为独立、确定性的文字修复能力：

```text
lyric_aligner/text_repair.py
scripts/v4_text_repair.py
scripts/v4_text_repair_batch.py
scripts/test_v4_text_repair.py
scripts/test_v4_text_repair_hardening.py
scripts/test_v4_text_repair_v2.py
scripts/test_v4_text_repair_batch.py
```

硬边界：

- **永不读取 audio，永不修改 cue 数量、编号、开始时间、结束时间**；输出后重新解析并逐 cue 比较 timeline signature，任何变化 fail closed；
- canonical LRC/TXT/QRC 仍是唯一 final text/order truth；多个 canonical 文件按调用顺序保持歌曲顺序，单个 fully-timed 文件内部按 timestamp stable-sort；LRC timestamp 间距/BPM 先验不参与 SRT timing 写回；
- matching 使用 Unicode NFKC + casefold，并忽略标点/空白/control/format 与常见音乐装饰符进行比较，但输出保留源 SRT 的标点、空白、换行和装饰符；
- 长字幕先寻找全局唯一、长度足够的 exact normalized 1:1 anchor，取最长单调 anchor chain；锚点之间才运行局部 banded DP，防止局部漏行把后续整段拖偏，同时避免全矩阵 span DP 的高频性能回退；
- 局部 DP 支持 bounded span，最大 4 cue/4 canonical line；常见 2 段以内 span 需较高相似度与长度一致性，3–4 段仅在近乎完全一致时允许，因此覆盖更极端的多断句/少断句，但不把大跨度 fuzzy guess 当自动修复；canonical span 禁止跨不同歌词文件；
- span 内以最小字符 edit script 执行 `replace / insert / delete`，因此可自动处理普通错字、漏字、多字；对于断句不同，canonical 内容按已有 cue 字符归属重新投影，**不会为了匹配 LRC 行界而移动/合并/拆分 cue 时间边界**；
- span 低相似度不允许吞掉缺失歌词；真正的 canonical gap 仍进入 `unmatched_canonical`；subtitle gap、gap 邻域弱匹配、近似重复歌词竞争、结构差异过大、或重分配后会让已有 cue lexical content 为空继续 `review_required`；
- report schema 2.0 增加 cue/canonical span、source/canonical/output text、字符 edit operations/count、`segmentation_span_count`、`cue_count_unchanged`，用于人工快速复核；
- batch runner 接收多个彼此独立 job，单个 job error/review 不会把其他 job 输出误标为 ready；执行前对整个 manifest 做 path ownership preflight，禁止 duplicate job ID、重复 output/report/summary、以及任一输出覆盖任何 job 的 SRT/canonical 输入，避免顺序执行污染后续任务。

`--auto-threshold` 仍是 1:1 自动匹配基础门槛，不应为了提高 coverage 随意降低；multi-span 另有更严格结构门槛。下一步应使用真实“剪映原始 SRT + canonical + 人工终稿”私有 benchmark 重点统计 false auto correction、auto coverage、review rate 与长文件耗时。首要 promotion gate 仍是 **timeline change = 0 且真实 benchmark false auto correction = 0**。

Text Repair V2 **不负责“部分时间轴不可信”**。下一轮 Partial Timeline Repair 会把可信 cue 作为锁定锚点，只在不可信局部做声学重对齐；由于用户实际素材中 BPM 加减速是常态，该能力必须默认复用 Source-to-Mix 的 `AFFINE/PIECEWISE_RATE/CUT_AWARE` 语义，`rate change != cut`，绝不能用原曲绝对时间直接覆盖 mix-time cue。

## 12. PR25 execution optimizer 与已合入 PR26 同 out-dir 保护

PR25 在保持原 `scripts/v4_run.py` authoritative orchestration 字节级实现为 `scripts/v4_run_legacy.py` 的前提下，增加外围 execution optimizer：

- parent 首次完整 SHA 验证 task manifest 后，使用 same-invocation verified-input session 减少 child 重复 SHA 读取；
- coarse/fine/transition artifact 仅在 task、algorithm、producer clean HEAD、upstream artifact IDs、正式 output SHA 与 runtime sidecar 全部匹配时允许跨 run resume；
- independent stages 使用 `--workers 1..4`，默认 2；
- timeline/final issue/lineage 仍由原 authoritative core 重建。

PR26 merge `004d2558f646993a44f51d855305f6ba285d04cd` 增加 `--out-dir/.v4-run.lock`：同一个 output tree 同时只允许一个 public `v4_run.py` orchestrator。第二个进程 fail closed；lock 带 owner token，退出时只删除自己的 lock。异常终止留下 stale lock 时，必须先确认没有真实 V4 run 仍在运行，再人工删除，禁止自动猜测 stale。

这些 execution hardening 均不改变 Source-to-Mix slope grid、score/margin threshold、timewarp selection、cut/overlap decision、review policy 或 release semantics。

## 13. External forced-alignment batch protocol 1.1

P7 protocol 1.0 仍是默认兼容路径，每个 selected job 一个 external subprocess。Optional protocol 1.1 通过：

```text
scripts/v4_execute_forced_alignment.py --execution-mode batch
```

把全部 selected source-forced jobs 放入一个 ephemeral batch request，并只启动一次 external process。Batch response 必须 echo exact backend/version/model/revision，top-level status 必须为 `aligned_batch`，response job IDs 必须与 request 精确相等；每个 job 再复用 P7 protocol 1.0 的 boundary/span/canonical identity validator。

Formal evidence/artifact 新增/明确：

```text
protocol_version
requested_execution_mode
execution_mode
command_invocation_count
```

`single` 默认行为不变；`batch` 只改变 external model process lifecycle，不改变 P7 evidence family、P8 projection、P9 shadow fusion 或 authority。显式 empty selected jobs 是 zero-work，不解析/启动 external command。`--timeout-seconds` 在 batch 模式覆盖整个 batch subprocess，大任务需要显式给足 timeout。

旧 `agent/v4-whisperx-reference-adapter` 没有进入生产代码基线：收口 review 时确认其外部 WhisperX/NLTK runtime 假设已经与当前 upstream 发生漂移。保留 stale branch 不再被视为安全策略；未来实际 adapter 必须从当时的最新 main/最新 upstream 重新实现或校验，并经过真实 private calibration/blind。

## 14. Partial Timeline Repair P1（开发中，shadow-only）

当前开发分支 `agent/partial-timeline-repair-p1` 开始实现 Text Repair V2 之后的下一阶段。P1 只建立局部时间轴修复的安全 planner，不直接写回生产 SRT，也不提升 timing/release authority。

新增核心：

```text
lyric_aligner/timeline/partial_repair.py
scripts/test_v4_partial_timeline_repair.py
```

当前契约：

- cue trust 必须显式为 `trusted / untrusted / unknown`；全 trusted 路由 `preserve`，明确混合 trusted+untrusted 路由 `hybrid`，存在 unknown 或缺少可信锚点时路由 `rebuild`；
- `trusted` cue 的原始 start/end 被硬锁，不读取候选 timing 覆盖它；
- 不可信 cue 的候选 timing 只接受 `source_to_mix` 来源，mapping kind 只接受 `AFFINE / PIECEWISE_RATE / CUT_AWARE`；editor/ASR timing 不能直接成为候选 authority；
- `PIECEWISE_RATE` 被视为连续变速映射，继续固定 `rate change != cut`；BPM 不能产生 `BPM_LOCKED` 一类硬 slope mapping；
- `CUT_AWARE` 只有在上游已独立确认 cut 后才可使用；cross-cut / confirmed-gap `unprojectable` 候选直接 block；
- 候选不能越过左右任一已锁 trusted cue，也不能与其他待修 cue 的候选区间互相重叠；否则 fail closed；
- `propose_repair` 仅表示结构上可供后续 calibrated policy 使用，当前仍 `proposal_only=true`、`publish_ready=false`，不会自动修改 authoritative timeline。

P1 promotion gate 是：旧测试全绿 + 新结构回归全绿 + trusted timing 零变化 + rate-change/cut/overlap guard 全部 fail-closed。真正自动写回局部时间轴仍必须等待真实 private calibration/blind 证明阈值安全。

## 15. Partial Timeline Repair P2（开发中，P9 evidence bridge）

P2 把 P1 planner 接到真实 P9 fusion line identity，但仍不从未校准的 shadow level 自动推导 cue trust。

新增：

```text
lyric_aligner/timeline/partial_repair_evidence.py
scripts/test_v4_partial_timeline_repair_evidence.py
```

规则：

- P9 payload 必须仍是 `shadow_only`、`policy_calibrated=false`、`release_gate_eligible=false`、`automatic_timing_change_allowed=false`，且 authority 明确保持 canonical lyrics / Source-to-Mix；任何 authority 漂移 fail closed；
- `LOW / MEDIUM / HIGH / CONFLICT` 全部只作为 diagnostics。`HIGH` 不会自动把 cue 提升为 trusted；`CONFLICT` 也不会自动断言某个 editor cue 一定错误；
- cue trust 只接受显式 `human_review`，或未来已经通过 calibration + independent blind-test 锁定的 `calibrated_policy`；
- 对显式 untrusted cue，只有当 P9 editor family 能唯一绑定到一个 canonical line 时才生成候选；多个 canonical line 指向同一 editor cue 时标记 ambiguous，不猜断句；
- 候选边界只读取该 P9 line 的 authoritative `source_timeline_boundary_ms`，绝不把 editor/ASR/forced auxiliary boundary 当写回 authority；
- occurrence mapping kind 必须显式提供并且是 `AFFINE / PIECEWISE_RATE / CUT_AWARE`，缺失时不从 BPM 猜 AFFINE；
- P9 为 open-ended canonical line 使用的 `start+1ms` 比较 sentinel 不得成为真实 repair candidate；1ms sentinel 直接 unavailable；
- bridge 输出仍固定 `proposal_only=true`、`publish_ready=false`、`automatic_timing_change_allowed=false`，随后继续交给 P1 的 trusted-neighbor / candidate-overlap guards。

P2 的目标只是把真实 evidence/identity 安全接线。自动 trust 分类、自动时间写回仍必须等真实 private calibration/blind promotion gate。