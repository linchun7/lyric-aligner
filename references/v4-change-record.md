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
- P2 editor shadow evidence：`2e96569189ac6eb16d987fb2f304403696bc809b`。

P2 的 exact commit `197c9f01ef7b054837d22e9248ba0780372ffaa8` 先在 validate #479 全绿；由于 Draft->Ready GitHub control-plane mutation 连续 502，创建非 Draft replacement PR #11，validate #484 再次全绿后 squash merge。没有绕过 Draft 规则。

---

## 2026-08-18 — P3 Local Acoustic Evidence Planner / Backend / Faster-Whisper Executor

### 1. 目标

只对已经被 v4 标记为不确定的**局部区间**补充 ASR/Forced Alignment evidence，不把整条 40 分钟 mix 重新交给一个模型，也不改变 canonical lyric / Source-to-Mix 的权威关系。

### 2. 新架构

新增：

```text
lyric_aligner/alignment/
  backends.py
  planner.py
  asr_executor.py
```

CLI：

```text
scripts/v4_alignment_backends.py
scripts/v4_plan_alignment.py
scripts/v4_execute_asr_evidence.py
```

专门文档：

```text
references/local-acoustic-evidence.md
```

### 3. Truthful backend registry

Registry 只做 package/command discovery 和显式运行前提检查：

```text
faster_whisper -> mix_asr, word_timestamps
whisperx       -> mix_asr, word_timestamps, ctc_alignment
external forced-aligner command -> source_forced_alignment
```

严格区分：

```text
available != execution_ready != validated_on_singing
```

`--require-capability` 缺失返回非零，不做假 fallback。

### 4. Local job planner

Artifact：

```text
alignment_job_planning / alignment_plan
```

来源：

- run/review issue；
- P2 editor boundary disagreement；
- P2 editor candidate ambiguity；
- editor no-candidate 可选，默认关闭。

每个 job 只保存 occurrence/line、canonical text SHA、mix/source local window、reason、capabilities、priority 和 deterministic ID，不复制歌词正文。

默认 context 为 mix ±1.5s / source ±1.0s；job 超过 `max_jobs` 时明确 `plan_truncated=true`。

Planner 固定：

```text
mode=plan_only
backend_execution_performed=false
```

### 5. Faster-whisper bounded executor

Executor 只处理 planner 中请求 `mix_asr` 的 jobs。每个 job 用 exact local window 调用 faster-whisper `clip_timestamps`，开启 word timestamps，关闭 previous-text conditioning，且 clip 模式不依赖 VAD。

语言 hint：en/zh/ko/ja 显式；yue/unknown/auto 留空让 backend detection，避免把粤语强制为普通话。

默认 output 不含 raw ASR text，只保存：

- text SHA；
- segment/word timestamps；
- word probability；
- avg_logprob / no_speech / compression；
- detected language/probability；
- canonical local text support score。

只有显式 private opt-in 才保留 raw ASR text。

### 6. Lineage

Planner 绑定 task、source run、effective canonical timeline、可选 editor evidence。

ASR executor 再绑定 task/mix audio、plan artifact、source run、canonical timeline，并验证 plan canonical text SHA 与当前 timeline 一致。

### 7. Forced Alignment 当前边界

当前只完成：

- `source_forced_alignment` capability；
- source local-window job planning；
- external command readiness 检查。

**尚未接入具体 WhisperX/SOFA/MMS production backend，也未假装模型已经下载/可执行/对歌声有效。**

### 8. Tests

新增：

- run issue/editor disagreement/ambiguity local planning；
- editor missing opt-in；
- max job truncation；
- canonical text identity mismatch fail；
- backend availability/readiness；
- missing required capability exit 2；
- planner artifact E2E；
- fake-model faster-whisper clip/word-timestamp contract；
- raw text default privacy；
- yue language detection open；
- no ASR job => no model loading。

### 9. GitHub Actions 真实边界

CI 可以验证 package contract、planner、fake executor、ASR dependency environment，但当前**不下载/运行真实 Whisper model**。因此不能声称 GitHub Actions 已在真实歌曲上跑出 word timing/准确率，也不能声称 WhisperX/forced aligner production ready。

### 10. Algorithm/profile

P3 evidence tooling不改 Source-to-Mix、timeline、renderer 或 calibrated acoustic thresholds：

```text
algorithm_version = 4.0.0a8
calibration_profile = production-bootstrap-2026-08-17-a7
```

Planner threshold 只决定“哪里值得补证据”，不是发布阈值。

## 验证纪律

P3 必须用 latest-head ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 验收。Fake-model 测试不能被描述成真实模型测试。
