# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已经进入代码的行为、兼容/迁移与验证边界。

## 已合入 main

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed overlap：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed cut/CUT_AWARE：`096210fbdbb8a55ee908b592bba20b1244c2821f`；
- a8 cut+overlap composition：`5c458d8327d2641ba053423fff3066d7fdd8ba3b`；
- P1 strict calibration/blind：`1c6babe37067c217d14a7404aa0ed6a1c4779a00`；
- P1.1 private dataset readiness：`ad6c403a56209e945a9a61a1eeab1a4bc3c204b4`；
- P2 editor shadow evidence：`2e96569189ac6eb16d987fb2f304403696bc809b`；
- P3 local acoustic evidence：`cd3420750c06a55fa1af7d6314ec56971e728928`；
- P4 evidence fusion shadow：`bc4e10760ffee2e5990ca580d5edbadd7d561eaf`；
- P5 ASR second-pass routing：`1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d`；
- P6 ASR second-pass execution/composite：`6eacacc50e885684b0265e3abea729b19b1b7725`；
- P7 external source forced alignment：`9ad6df4f04b396871f757422bcb35f1fa7676678`；
- P8 forced alignment source-to-mix projection：PR #17，merge `00585a07b658ffea93509c4ed1a4b129deafd0a3`；
- P9 forced alignment multi-family shadow fusion：PR #19，merge `efbdbb926b03efdf1d91622d5c23cabef1f9850c`；
- Production Readiness Tooling：PR #21，merge `04e0802156f62006c6b6af5b4ef59b1acc81ce86`；
- Windows Local Validation Hardening：PR #22，merge `2b4a13132e95a551392811407f48573b36edab95`；
- Production Bounded Mix Decode：PR #23，merge `4d7e086aedd2b56210368302d9a17df29fef6a0c`；
- Source Harmonic Feature Cache：PR #24，merge `0b1f38c98542eed9ec80034677cef4bf8e7f9791`；
- Text-only repair + Safe Execution Optimizer：PR #25，merge `3bd5e388d8d68bf88233eee60e0d85dcd7816a3e`；
- PR25 hardening：PR #26，merge `004d2558f646993a44f51d855305f6ba285d04cd`。

P7 head `2ee9e1d2ced75c3d24b5a00353e9f275fc9dc9f9` 的 validate #560 全绿后合入。P8 latest result tree 在 fast-core #1 完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿后合入。P9 result tree 在 fast-core #2 完成同级验证并跑完 **324 tests** 全绿后，与 P8 main 同步 ancestry，再合入 main。PR22 exact-head fast-core 与 Python 3.10/3.12/3.14 + ASR validate 全绿后合入。PR23 exact-head fast-core #43 与 validate #675 全绿后合入。PR24 在 source-feature cache roundtrip、corruption/key isolation 与 direct/precomputed coarse equivalence 覆盖后合入。PR25 exact-head `6c5ac00e5755593136840c35794e480a45b9f237` 的 fast-core #77 与 validate #711 在 Python 3.10/3.12/3.14 + ASR 环境全绿后合入。PR26 exact-head `8539c1cd913918ea42468296a0a6d2005e822190` 的 fast-core #87 与 validate #722 全绿后合入，测试 head tree 与 merge tree 一致。

---

## 2026-08-18 — P8 Forced Alignment Source-to-Mix Projection

P8 将 P7 absolute source-ms forced evidence 通过当前 occurrence 的 exact Source-to-Mix 映射投影到 edited-mix time，避免跨时基比较。

新增：

```text
lyric_aligner/alignment/forced_projection.py
scripts/v4_project_forced_alignment.py
scripts/test_v4_forced_mix_projection.py
scripts/test_v4_forced_mix_projection_end_to_end.py
```

Authority 不变：canonical lyrics 是 final text/order truth，Source-to-Mix 是 primary timing truth，forced alignment 只是 auxiliary evidence。

`AFFINE` / `PIECEWISE_RATE` 复用现有 `mix_time_for_source()`；`CUT_AWARE` 对 confirmed source gap 或 cross-cut line 标记 `unprojectable`，绝不 bridge；spans 独立投影。只解析 forced-evidence 实际引用 occurrences，relevant mapping/provenance 缺失仍 fail closed。

Artifact：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

公共 CI 只能证明 projection/cut/lineage/privacy，不能证明真实 forced-aligner checkpoint 对歌声准确。

---

## 2026-08-18 — P9 Forced Alignment Multi-Family Shadow Fusion

### 1. 目标

P8 让 forced evidence 与 editor/ASR 都进入 mix time 后，P9 才允许它作为独立 auxiliary family 进入 evidence fusion。仍然是 shadow-only：不修改 canonical text、Source-to-Mix、render timeline 或 release eligibility。

更新：

```text
lyric_aligner/evidence/fusion.py
scripts/v4_fuse_evidence.py
scripts/test_v4_evidence_fusion_end_to_end.py
```

新增：

```text
scripts/test_v4_forced_evidence_fusion.py
```

### 2. Forced family contract

只接受：

```text
mode = forced_alignment_mix_projection
source_evidence_backend = external_forced_aligner
primary_timing_authority = source_to_mix_only
forced_alignment_authority = auxiliary_acoustic_evidence_only
```

每个 forced job 必须绑定已知 occurrence/canonical line/track/canonical_text_sha256；job IDs 与 canonical line identity 必须唯一。`projected` job 才提供 mix boundary；`unprojectable` 会显式进入 diagnostics，但不计为可用 auxiliary boundary family。`unprojectable` payload 若携带 mix boundary 会 fail closed。

### 3. Conflict policy

P4 旧版只计算 editor↔ASR disagreement。P9 改为三 family pairwise fail-closed：

```text
editor ↔ asr
editor ↔ forced_alignment
asr ↔ forced_alignment
```

只要任意可用 auxiliary pair 的 onset/offset 最大分歧超过 `conflict_boundary_ms`，整行 shadow state 为 `CONFLICT`。不会用多数票掩盖 outlier。

无冲突时仍按未校准 shadow 规则：0 family=`LOW`，1=`MEDIUM`，>=2=`HIGH`。这里的 `HIGH` 仍不是 release confidence。

新增 diagnostics：

```text
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
summary.forced_alignment_line_counts
```

### 4. CLI / lineage

`v4_fuse_evidence.py` 新增：

```text
--forced-mix-evidence
--forced-mix-evidence-artifact
```

P8 artifact 必须与当前 source run 同 task、同 algorithm version、同 source_run_artifact_id，并把 source run 放在 upstream。fusion artifact 保存所有实际输入 artifacts 的 lineage，并记录 forced artifact ID 与 `any_auxiliary_pair_over_threshold_blocks` conflict policy。

### 5. Safety boundary

P9 固定：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

这就是代码阶段的刻意收口点。下一阶段必须使用 private real-song calibration/blind 来决定 family 的实际独立性、不同语言/风险类型阈值，以及是否允许任何自动 timing refinement。公共 synthetic CI 不得用于宣称真实 accuracy。

---

## 2026-08-18 — Production Readiness Tooling

本轮不改变 Source-to-Mix、P9 shadow policy 或 release authority，只补齐真实数据生产前的可操作性、可测量性和可复现性。

新增：

```text
lyric_aligner/doctor.py
scripts/v4_doctor.py
lyric_aligner/evaluation/family_calibration.py
scripts/v4_evaluate_evidence_families.py
lyric_aligner/runtime_snapshot.py
scripts/v4_runtime_snapshot.py
```

### Production Doctor

`v4_doctor.py` 使用当前真实 schema-2.0 task 与 effective-run shape 做只读检查，并可接收 run/editor/plan/ASR/forced source/forced mix/fusion 的 payload + artifact manifest 配对。除 stage readiness 外，它验证 artifact 自签名、task fingerprint、algorithm identity、exact payload output SHA/size、current run upstream，以及 payload 的 `source_run_artifact_id`（存在时）。`--require lineage` / `artifact:*` 可将这些条件变成机器可判定非零退出。

报告只保留必要的 basename、artifact ID prefix 与无敏感 diagnostics；不输出歌词、absolute local/artifact output paths、backend resolved paths 或完整 external command。Backend `available/execution_ready` 仍只表示运行前提可发现，不代表 singing accuracy。

### Evidence Family Calibration Evaluator

`v4_evaluate_evidence_families.py` 直接消费 P9 fusion 的统一 mix-time family boundaries 与 private hash-bound line truth；按 overall / language / risk bucket 输出 Source-to-Mix、editor、ASR、forced 的 coverage、onset/offset/boundary MAE、P50/P90/P95、250/500ms 命中率，以及 `CONFLICT` / forced `unprojectable` rate。输出不包含 raw lyric 或本地路径。

每份 family dataset 必须绑定一个 self-validating runtime snapshot；runtime 内容被修改但继续使用旧 `runtime_identity_sha256` 时 fail closed。跨 case 的 fusion policy identity 包含 algorithm、policy ID 与 semantic config，但排除每首歌必然不同的 `*_artifact_id` lineage 字段；因此同一 policy 的不同歌曲可以聚合，而不同 `conflict_boundary_ms` / conflict policy 等语义设置不能混算。

该 evaluator 要求 `canonical_text_sha256` 与 truth 精确匹配，并固定：

```text
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此 calibration 报告本身不能提升 auxiliary timing authority；P1 strict workflow 仍负责 source-group split isolation 与 calibration→blind lock，冻结候选 backend/model/profile/threshold/runtime identity 后必须在独立 blind set 验证。

### Runtime Snapshot

`v4_runtime_snapshot.py` 记录 Git commit/dirty state、Python、OS/arch、ffmpeg/ffprobe、关键 package versions、logical model IDs、device request 与 external forced-aligner command hash/basename，并生成稳定 `runtime_identity_sha256`。读取时会重新计算 canonical identity hash；clean worktree 明确记录 `dirty=false`。Hostname/username/absolute repo path/local model path/full external command 不进入 formal snapshot。

### Compatibility / rollback

这些工具都是 additive diagnostics/evaluation metadata，不改变现有 artifacts、renderer 或 release contracts；如需回滚可删除上述新增模块/CLI，而不会改变 P9 之前的 authoritative production output。真实数据出现前仍禁止凭 synthetic CI 调整 family threshold 或自动写回 timeline。

---

## 2026-08-18 — Windows Local Validation Hardening

本轮由真实 Windows 本地等价 CI 暴露三个问题：quoted executable path 在 external forced command 中解析不一致、`env={}` bootstrap tests 在 Windows/Python 3.10 下可能触发 CreateProcess 错误、以及测试 fixture 为了验证隐私而把本地用户路径 literal 写进 tracked source。

修复：

```text
lyric_aligner/command_line.py
lyric_aligner/alignment/backends.py
lyric_aligner/alignment/forced_executor.py
lyric_aligner/runtime_snapshot.py
scripts/test_v4_command_line.py
scripts/test_v4_cli_bootstrap.py
scripts/test_v4_calibration_cli_bootstrap.py
scripts/test_v4_doctor.py
scripts/test_v4_runtime_snapshot.py
scripts/privacy_scan.py
```

- backend readiness、P7 executor 与 runtime snapshot 共享同一 `split_external_command()`，Windows 双引号 executable/argument 可在 `shell=False` 下解析成一致 argv；malformed quote fail closed；
- Windows 仅规范化双引号，不把单引号错误当成 native Windows shell grouping；
- bootstrap tests 保留 OS 创建进程所需环境，同时移除 `PYTHONPATH/PYTHONHOME` 并设置 `PYTHONNOUSERSITE=1`，继续验证 CLI 不依赖外部 Python path；
- privacy scanner 恢复严格的本地用户目录根路径扫描（覆盖常见 Unix/macOS/Windows 形式），敏感示例由测试在 runtime 拼接，不引入 allowlist/排除规则。

Authority 与 release boundary 完全不变：canonical lyric、Source-to-Mix、P7/P8/P9 shadow semantics、threshold、release gate、automatic timing behavior 均未调整。该变更属于跨平台执行/验证可靠性修复，不是 accuracy promotion。

---

## 2026-08-18 — Production Bounded Mix Decode Fast Path

第一轮真实私有生产暴露了吞吐问题：一个长 mix 含多首歌时，`v4_run.py` 会逐 occurrence 调用 coarse/fine CLI，而旧 CLI 每次都重新完整解码长 mix。核心 coarse/fine feature 实际只使用当前 occurrence 的局部窗口，因此完整解码属于重复计算。

本轮先做不改变 authoritative 算法决策的执行优化：

```text
lyric_aligner/audio/coarse_mapper.py
lyric_aligner/audio/fine_alignment.py
scripts/v4_coarse_align.py
scripts/v4_fine_align.py
scripts/test_v4_coarse_mapper.py
scripts/test_v4_fine_alignment.py
```

### Bounded decode

- coarse 先通过音频 metadata 取得完整 mix duration，再只解码 `mix_start..mix_end` 外加两侧最多 2 秒 padding；
- fine 从 coarse retrieval windows 推导实际需要的 global mix interval，只解码该区间外加最多 2 秒 padding；
- core mapper/refiner 新增 `mix_audio_start` 与 `full_mix_duration`，允许输入一个局部 waveform buffer，同时继续使用 absolute mix coordinates；
- `feature_scope.full_mix_duration` 仍表示原始完整 mix 时长，不因 cropped buffer 改义；
- 正式 payload 继续计算并保存完整 mix file SHA，因此 asset/task identity 没有被局部 decode 弱化。

### Equivalence / safety

新增内存等价测试：同一 waveform 使用完整 buffer 与 cropped buffer + absolute origin 运行时，coarse windows/path/timewarp 与 fine path/timewarp 必须相同。默认 core API 仍以 `mix_audio_start=0` 工作，旧调用方保持兼容。

该变更不调整 coarse/fine threshold、slope grid、cut detection、review policy、canonical/Source-to-Mix authority、P9 shadow、release gate。它也不是尚未校准的“低精度 fast mode”；任何复杂 cut/overlap 仍按原规则 fail closed/review。

### 后续性能路线

真实 benchmark 后再依次考虑：

1. 对有效 artifact 做 resume/cache，避免中断或下游变化时重算 unchanged occurrence；
2. 对独立 occurrence 加 bounded worker 并发，保持输出顺序与 artifact lineage；
3. 对普通 global-rate 歌曲研究 sparse fast probe，但必须在不确定时自动 fallback 当前 full mapping，并在 real calibration 前保持非默认/非 authority promotion。

回滚本轮只需恢复 full-mix decode 调用方式；artifact contract 和 release contract 无需迁移。

---

## 2026-08-18 — Source Harmonic Feature Cache

第一轮多歌 production 还暴露第二个重复计算：同一 source track 会在 primary coarse 及相邻 transition coarse 中多次使用，旧实现每次都重新 decode 原曲并运行 HPSS、Chroma CENS 与 MFCC。

本轮增加 disposable source feature cache：

```text
lyric_aligner/audio/feature_cache.py
lyric_aligner/audio/coarse_mapper.py
scripts/v4_coarse_align.py
scripts/test_v4_feature_cache.py
scripts/test_v4_coarse_mapper.py
```

### Cache identity / reuse

- `FeatureCacheSpec` key 绑定 source audio SHA-256、`sr`、`hop_length`、MFCC dimensionality、feature implementation ID 与 librosa version；
- `v4_run.py` 生成在 `primary/` 与 `transitions/` 下的 coarse outputs 会自动解析到同一 V4-local `cache/features` 目录，因此同一 source 的后续 transition coarse 可以直接复用 numeric feature bundle；
- cache hit 后不再 decode source audio，也不再运行该 source 的 HPSS/chroma/MFCC；
- cache miss 正常从 SHA-bound source audio 计算并原子写入；
- corrupt / incompatible / missing cache 一律当 miss，不 BLOCK 生产；
- formal payload/artifact 仍绑定完整 source/mix SHA、task fingerprint、profile、algorithm version 和 upstream asset artifact；
- cache 本身不是 upstream artifact。

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

---

## 2026-08-19 — Partial Timeline Repair V1 Preview（PR #31 draft）

Text Repair V2 解决的是“时间轴全部冻结，只修文字”；真实生产还存在另一类常见任务：大部分剪映 cue 时间可直接保留，仅少量 cue 明显需要重新按原曲→混剪映射计算。PR #31 第一版只建立安全的局部 **preview**，没有提升 production timing authority。

新增：

```text
lyric_aligner/partial_timeline_repair.py
scripts/v4_partial_timeline_repair.py
scripts/test_v4_partial_timeline_repair.py
```

### V1 contract

- 用户必须显式重复 `--cue` 指定 numeric SRT cue；未选 cue 的完整 timing line 冻结，所有 cue 的文字、编号、数量始终不变；
- CLI 每次只消费一个 timed canonical lyric occurrence 与一个 V4 mapping payload；`--occurrence-id` 必须与 mapping payload 的 occurrence identity 精确相同。Blocked coarse/fine mapping、未 applied fine result 或不支持的 mapping kind fail closed；
- 为避免用文本模糊猜 timing identity，候选必须是唯一的 1 cue ↔ 1 canonical line；1↔N / N↔1、重复 subtitle/canonical occurrence、短句/低相似度、无法推断 end 的最后一行都只报告 review；
- source interval 经既有 P8 投影规则进入 mix time：`AFFINE / PIECEWISE_RATE` 使用同一 inverse；`CUT_AWARE` 不 bridge confirmed source gap/cut，跨 cut interval 或 boundary 落 gap 直接 `unprojectable`；
- 候选生成后再与所有相邻 cue 做单调/overlap guard。无论相邻 cue 是锁定还是同时选中，只要建议时间会造成交叠，就不写该建议；
- `--preview-out` 是可选的非发布文件；report 固定 `releaseable=false` 与 `automatic_timing_change_allowed=false`。CLI `preview_ready` 只表示“所选 cue 都有安全可人工查看的 Source-to-Mix 建议”，不等于可直接进入 renderer/release。

### Safety / promotion boundary

公共 synthetic CI 只用于证明：全局变速与分段变速逆映射、CUT_AWARE 不跨 cut、selected-only 写入、邻居锁定、mapping identity/blocked fail-closed。它不能证明真实歌曲上 canonical timestamp/source mapping/声学边界误差已经满足自动发布要求。

下一步必须用私有真实样本对 preview 建议和人工终稿比较，至少统计 onset/offset MAE/P95、错误建议率、review rate，并覆盖 global-rate、piecewise stretch、cut、重复副歌、弱人声/说唱/多语言。只有独立 blind-test 证明收益后，才允许另行讨论 authoritative partial timing write-back；PR #31 本身不改变 `automatic_timing_change_allowed=false`。