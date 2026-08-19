# Lyric Aligner v4 生产运行手册

更新：2026-08-19  
主线算法版本：`4.0.0a9`

> P4 前完整长版手册见 `references/archive/2026-08-19-pre-p4-v4-runtime-guide.md`。真实生产 workload 与产品设计基线见 `references/production-requirements.md`。

## 1. 四档模式怎么选

```text
Standard -> Smart -> Pro -> Max
```

### Standard

只修文字，绝对不改剪映 timing；不读取 audio。

适合：你已经完全信任剪映时间轴。

### Smart

默认主力模式。先做 Standard 文字配对，再用 timed LRC、可用的逐字 timing、Jianying 大量正确 cue、以及 BPM/实际 stretch ratio 建立 no-audio anchor model，只推翻少量有多重独立证据的 timing outlier。

适合：剪映时间轴大部分正确，只有少数 cue 可疑。

### Pro

只对 Smart 无法解决的局部窗口读取 audio。当前已实现 Smart→Pro bounded plan、局部 source↔mix 声学 evidence 与 bounded faster-whisper；Pro v1 暂不自动把声学结果写回 SRT。

适合：英文 rap、古风/特殊唱法、歌曲交界、局部 identity/timing 冲突等少量困难位置。

### Max

完整 Full V4 Source-to-Mix/acoustic path。

适合：大部分 timing 本身就不可信、canonical mapping coverage 太弱、复杂 cut/overlap/reorder，或 Pro 局部处理仍无法解决。

**韩文/日文不是自动 Max 条件。** 如果有规范 timed lyrics 且 editor cue 能稳定与 canonical 配对，Smart 的文本 identity + timing math 仍然成立；只有证据不足时才升级 Pro/Max。

## 2. Standard：冻结剪映时间轴，只修文字

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lrc "private/<任务>/input/lyrics/01.lrc" `
  --out "output/<任务>/<任务>_TEXT_REPAIRED.srt" `
  --report "output/<任务>/<任务>_TEXT_REPAIR.json"
```

Text Repair V2.1：

- 不读取 audio；
- cue count / number / start / end 完全冻结；
- canonical 是最终文字/顺序 truth；
- timestamped metadata、mixed timed/untimed body、layout-boundary insertion、ambiguous match 等继续 fail closed；
- coverage warning 与 cue review 分开；
- production `--auto-threshold` 不得低于 0.72。

批量任务继续使用 `scripts/v4_text_repair_batch.py`。

## 3. Smart：无音频智能修文字 + 少量 timing

### 3.1 最简单调用

```powershell
python scripts/v4_smart_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lyrics `
    "private/<任务>/input/lyrics/01.lrc" `
    "private/<任务>/input/lyrics/02.lrc" `
  --output-srt "output/<任务>/<任务>_SMART.srt" `
  --report "output/<任务>/<任务>_SMART.json"
```

canonical 文件必须按 mix/song 顺序给出。Smart v1 要求 timestamped canonical；若只有 untimed TXT，使用 Standard，或升级 Pro/Max 获取 acoustic timing evidence。

Enhanced LRC / QRC 中已有逐字/逐词 timestamp 时会自动保留并利用，无需额外开关。

### 3.2 已知 BPM

如果成品统一到 140 BPM：

```powershell
python scripts/v4_smart_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --target-bpm 140 `
  --source-bpm "01.lrc=128" `
  --source-bpm "02.lrc=132" `
  --output-srt "output/<任务>/<任务>_SMART.srt" `
  --report "output/<任务>/<任务>_SMART.json"
```

内部使用：

```text
rate_prior = target_bpm / source_bpm
```

例如 `128 -> 140`，rate prior = `1.09375`。

### 3.3 已知实际 stretch ratio

若 Cubase/DAW 能给出真实 time-stretch ratio，优先直接传：

```powershell
--rate-prior "01.lrc=1.09375"
```

exact `--rate-prior` 对同一 source 优先于 BPM-derived value。

### 3.4 Smart 会自动改什么

v1 只允许 A-grade exact/unique/1:1 cue 自动 timing repair，并要求 leave-one-out independent model。

普通中间 cue 需要左右各至少 2 个独立 A anchors；歌曲第一句/最后一句缺一侧时，必须有 rate prior 且另一侧至少 3 个 A anchors。

当前首版安全线：

```text
<= 350ms deviation -> preserve
350..900ms         -> preserve
>= 900ms            -> candidate
> 8000ms shift      -> review
```

模型还必须稳定，candidate 不能破坏邻居基本时间结构。

B/C identity、重复副歌、模型不稳定、rate prior conflict、支持不足都会保留原 timing 并进入 review，不猜。

### 3.5 Smart report

JSON report 会记录：

- `audio_read=false`；
- text replacement/review count；
- word-timed canonical coverage；
- 每首歌 affine model；
- 每条 cue 的 preserve/repair/review；
- residual 与 evidence reasons。

原 source SRT 永远不覆盖；Smart 必须写独立 output path。

## 4. 少量同歌多速度怎么办

生产中大多数歌曲是整个 occurrence 单一 stretch ratio，因此 Smart v1 采用：

```text
Affine first
```

少量歌曲若出现：

```text
120 -> 130
125 -> 130
```

并且发生在同一首歌不同区段，当前 Smart v1 不会为了覆盖这个 rare case 自动拟合 piecewise；它更可能表现为 model unstable / rate prior conflict，然后进入 review/Pro/Max。

后续只有真实 private 样本证明有必要，才增加 evidence-triggered piecewise。速度变化本身永远不等于 cut。

## 5. Pro：Smart unresolved 才读局部 audio

### 5.1 先只生成计划

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --source-language "01.lrc=zh" `
  --source-language "02.lrc=zh" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json"
```

这一步不读取 audio。只把 Smart 中的 `timing review/text review` cue 变成 bounded jobs。已经 preserve/repair 的 cue 不进入 Pro。

计划默认：

```text
mix: cue 前后约 2.5s
source: canonical line/token 附近约 3.5s before + 5s after
```

plan 内不保存 raw canonical text，只保存 SHA 与 identity。

### 5.2 做局部 source↔mix 声学验证

若原曲音频齐全：

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --source-language "01.lrc=zh" `
  --source-language "02.lrc=zh" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json" `
  --mix-audio "private/<任务>/input/mix.wav" `
  --source-audio "01.lrc=private/<任务>/source/01.wav" `
  --source-audio "02.lrc=private/<任务>/source/02.wav" `
  --acoustic-out "output/<任务>/<任务>_PRO_ACOUSTIC.json"
```

Pro 不跑 Full V4 broad coarse search，而是在 Smart 已知位置附近使用 bounded HPSS/Chroma/MFCC retrieval。

如果 Smart 有可靠 rate，Pro 默认只搜：

```text
rate ± 0.06
```

从最佳 local source match 会得到：

```text
predicted_mix_start_ms
editor_start_residual_ms
estimated_slope
fused/chroma/mfcc score
margin / ambiguity
```

当前 `timing_mutation_performed=false`：声学证据先用于校准和后续 fusion，不直接改 SRT。

### 5.3 只对局部窗口跑 Whisper

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --source-language "01.lrc=zh" `
  --source-language "02.lrc=zh" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json" `
  --mix-audio "private/<任务>/input/mix.wav" `
  --asr-model-id "<faster-whisper-model>" `
  --asr-out "output/<任务>/<任务>_PRO_ASR.json"
```

没有 `--include-private-asr-text` 时，输出不保存 raw ASR text，只保留 hash、置信度、word timing 和 canonical support。

### 5.4 中英/韩日语言路由

Pro 先看当前 canonical line，而不是只看整首 track：

```text
中文 track + 纯英文 rap line -> en
中文 + English 同一行        -> auto
韩文纯行                      -> ko
日文纯行                      -> ja
```

如果 canonical 文本本身无法确定语言，则保持 ASR auto，不为了“省识别”强行猜语言。

因此 40 分钟大量韩/日歌曲也不等于一定 Max：先 Smart；只有 Smart unresolved 才 Pro；只有局部证据仍无法建立、或者整体 timeline 广泛不可信才 Max。

### 5.5 Pro 当前边界

mapped Pro job 已请求 `source_forced_alignment` capability，但 `v4_pro_selective.py` v1 尚未直接编排 external forced-aligner。当前直接可执行的是 local source↔mix acoustic + faster-whisper 两条 bounded evidence。

Pro v1 不自动 write-back timing。真实歌曲 blind 校准通过后，再加入 evidence fusion/write-back gate。

## 6. Max：完整 V4

Smart/Pro 解决不了、或时间轴整体本身不可信时，再运行完整 Source-to-Mix 主链：

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py ...
python scripts/v4_recompose_overlap.py ...       # 需要时
python scripts/v4_rebuild_cut.py ...              # confirmed cut 后
python scripts/v4_compose_materializations.py ... # cut+overlap 同时存在时
```

连续 mapping：

```text
AFFINE / PIECEWISE_RATE
rate change != cut
```

CUT_AWARE 仍只在正式 materialized cut lineage 后成立。

## 7. Legacy Partial Timeline Repair P1–P5

旧 Partial chain 继续用于 formal P3/P4/P5 proposal/calibration workflow。其 authority 不因 Smart/Pro 上线而改变：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

P9 HIGH 仍不能直接生成 trusted/timing mutation；P5 `proposal_inputs_ready` 仍只表示 formal proposal inputs 已就绪。

Smart/Pro 不依赖该 chain，也不能反向提升它的 authority。

## 8. 推荐生产顺序

```text
1. canonical lyrics + Jianying SRT 到位
2. timing 完全可信 -> Standard
3. timing 大部分可信、少量可疑 -> Smart（默认）
4. Smart 能安全修的直接修；review cue -> Pro plan
5. Pro 只处理这些 bounded windows
6. Pro acoustic/ASR evidence 当前先 review/calibration
7. broad untrusted / complex structure / Pro 无法收敛 -> Max
8. 始终保留原始输入，只写独立 outputs/artifacts
```

对于大量韩文/日文的 40 分钟节目：先看 canonical 配对与 Smart model coverage，而不是直接按语言选 Max。

## 9. 验证边界

公共 CI 可以验证 Standard/Smart deterministic contracts、Smart→Pro selective planning、局部语言 hint、bounded acoustic no-mutation contract、strict JSON、旧 P3/P4/P5 formal contract 与 Python compatibility。

公共 CI 不能证明真实歌曲 false-auto rate/false acoustic match。当前 Smart 350/900/8000ms、Pro local retrieval score/margin 等边界需要在 private real-song calibration + independent blind 上验证；通过前不开放 Pro 自动 timing write-back。
