# Lyric Aligner v4 生产运行手册

更新：2026-08-19  
主线算法版本：`4.0.0a9`

> 真实生产 workload 与产品设计基线见 `references/production-requirements.md`；Smart / Pro v1.1 设计细节见 `references/smart-pro-v1-1.md`。

## 1. 四档模式

```text
Standard -> Smart -> Pro -> Max
```

- **Standard**：只修文字，不读 audio，不改 timing。
- **Smart**：默认主力。0 audio；利用 timed LRC/QRC、逐字 timing、剪映多数可信 cue、DAW/BPM rate，修少量有充分证据的 timing outlier。
- **Pro**：只处理 Smart unresolved 的局部 audio region；当前执行 local acoustic / bounded ASR / external forced alignment evidence，仍不自动写 timing。
- **Max**：整体时间轴不可信、复杂 cut/overlap/reorder 或 Pro 无法收敛时才进入完整 Full V4。

韩文/日文不是自动 Max 条件。有规范 timed lyrics 且 editor/canonical 能稳定配对时仍先 Smart。

## 2. Standard

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lrc "private/<任务>/input/lyrics/01.lrc" `
  --out "output/<任务>/<任务>_TEXT_REPAIRED.srt" `
  --report "output/<任务>/<任务>_TEXT_REPAIR.json"
```

Text Repair V2.1 冻结 cue count/number/start/end，canonical 是最终文字/顺序 truth，production `--auto-threshold >= 0.72`。

## 3. Smart v1.1

### 3.1 基本调用

```powershell
python scripts/v4_smart_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --output-srt "output/<任务>/<任务>_SMART.srt" `
  --report "output/<任务>/<任务>_SMART.json"
```

Smart 要求 timestamped canonical。Enhanced LRC / QRC 的逐字/逐词 timestamp 会自动利用。

### 3.2 BPM / exact stretch

已知目标 140 BPM：

```powershell
--target-bpm 140 `
--source-bpm "01.lrc=128" `
--source-bpm "02.lrc=132"
```

内部派生：

```text
rate_prior = target_bpm / source_bpm
```

若 DAW/Cubase 有真实 stretch ratio，优先：

```powershell
--rate-prior "01.lrc=1.09375"
```

v1.1 report 明确区分：

```text
exact_daw
bpm_derived
anchor_estimated
```

### 3.3 Smart v1.1 的 ready 语义

`ready` 现在表示 timing 已被 Smart 实际验证/安全修复；以下不再只是“原样保留然后装作 ready”，而会 `review` 并交给 Pro：

- timing model 不 ready；
- 无唯一 timed-canonical mapping；
- C-grade identity。

B-grade 不能建立 timing model，只能由 already-ready A-anchor model 二次确认。

原 v1 的 leave-one-out、左右 anchor、edge rate prior、最大 shift 等限制继续有效。v1.1 再加：**不得制造原 SRT 中不存在的新 overlap**。

Smart report schema = `smart-1.1`，关键字段：

```text
status
pro_escalation_required
timing_validated_preserve_count
timing_repair_count
timing_review_count
models[].rate_provenance
```

## 4. Pro v1.1

### 4.1 先只计划，不读 audio

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --source-language "01.lrc=zh" `
  --source-language "02.lrc=zh" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json"
```

v1.1 reason-aware routing：

```text
timing review -> local source<->mix acoustic first
text/identity review -> bounded ASR + word timestamps
no word timing + source-side identity needs help -> forced alignment
unmapped review -> bounded ASR only
```

已有逐字 Enhanced LRC/QRC 时不重复请求 source forced alignment。

### 4.2 Region 合并与自适应 source window

相邻 review cue 会被合并进同一 `region_id`。局部声学执行每个 region 只 decode / extract 一次 mix features；cue/source identity 仍逐条独立。

计划会记录：

```text
region_count
planned_mix_audio_ms_unmerged
planned_mix_audio_ms_merged
region_merge_saved_ms
```

source window：逐字 timing 优先；否则利用下一 canonical onset；最后一行使用 bounded fallback。

### 4.3 局部 source↔mix 声学验证

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json" `
  --mix-audio "private/<任务>/input/mix.wav" `
  --source-audio "01.lrc=private/<任务>/source/01.wav" `
  --source-audio "02.lrc=private/<任务>/source/02.wav" `
  --acoustic-out "output/<任务>/<任务>_PRO_ACOUSTIC.json"
```

可靠 Smart rate 存在时仍使用 narrow slope search。歌曲首/尾 timing review 可能增加前/后歌曲 shadow competitor：

```text
shadow_evidence_only = true
boundary_role = previous_source | next_source
```

这是 join/crossfade 双源判断，不是自动 timing authority。

### 4.4 局部 Whisper

```powershell
--mix-audio "private/<任务>/input/mix.wav" `
--asr-model-id "<faster-whisper-model>" `
--asr-out "output/<任务>/<任务>_PRO_ASR.json"
```

语言按当前 canonical line 路由：中文歌纯英文 rap -> `en`；code-switch -> auto；韩/日 pure line -> `ko/ja`。

### 4.5 External forced alignment

v1.1 已把既有 external forced-aligner protocol 接入 standalone Pro CLI。示例：

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json" `
  --source-audio "01.lrc=private/<任务>/source/01.wav" `
  --source-audio "02.lrc=private/<任务>/source/02.wav" `
  --forced-out "output/<任务>/<任务>_PRO_FORCED.json" `
  --forced-command "<external-aligner-command>" `
  --forced-backend-id "<backend>" `
  --forced-backend-version "<version>" `
  --forced-model-id "<model>" `
  --forced-model-revision "<revision>"
```

Forced alignment 仍是 auxiliary source-side evidence；canonical lyric 仍拥有最终文字/顺序 authority。

## 5. 少量同歌多速度

Smart 继续 `Affine first`。同一首歌出现少量多 rate 时，先表现为 unstable/conflict 并升级 Pro；只有真实 private 样本证明有必要才增加 evidence-triggered piecewise。`rate change != cut`。

## 6. Max

Smart/Pro 解决不了、或整体 timeline 本来就不可信时再运行完整 Source-to-Mix 主链。Max 不再是普通 timing 修复默认入口。

## 7. Legacy Partial Timeline Repair

旧 P1–P5 formal proposal/calibration chain 继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro 不借用 P9/P4 authority，也不会反向提升旧 chain。

## 8. 推荐日常生产顺序

```text
1. canonical lyrics + Jianying SRT
2. timing 完全可信 -> Standard
3. 大部分可信、少量可疑 -> Smart
4. Smart unresolved -> Pro plan
5. Pro 只处理 bounded regions，按原因选择 acoustic / ASR / forced
6. Pro evidence 当前仍不自动写 timing
7. broad untrusted / complex structure / Pro 无法收敛 -> Max
8. 永远保留原输入，写独立 outputs/artifacts
```

## 9. 验证边界

Public CI 能验证 deterministic policy、no-new-overlap、reason routing、region reuse、boundary shadow competitor、forced orchestration contract 与 Python/ASR compatibility。真实歌曲 false-auto / false acoustic match 仍必须通过 private calibration + independent blind；通过前不开放 Pro 自动 timing write-back。
