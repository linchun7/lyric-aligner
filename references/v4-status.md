# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
开发分支：`agent/v4-accuracy-foundation`  
Draft PR：#1  
v4 package 版本：`4.0.0a1`

> 本文件回答“目前真正写了什么、测试到了哪里、什么还没接生产”。详细公式/设计见 `v4-implementation.md`，逐项变更见 `v4-change-record.md`，实际命令见 `v4-runtime-guide.md`。

## 1. 当前结论

v4 已经不只是方案文档，以下核心已经有实际代码、单元/合成声学回归和 Artifact lineage：

1. sequence-aware evaluator；
2. 多语言 Editor Evidence 基础；
3. strict SRT / release integrity / artifact lineage；
4. TrackAsset + TrackOccurrence + fail-closed asset resolver；
5. conservative canonical LRC role preflight；
6. UTF-8 default fail-closed 文本读取；
7. Affine-first TimeWarp；
8. HPSS harmonic + Chroma CENS + MFCC coarse retrieval；
9. 多窗口全局 source path；
10. selective high-resolution fine alignment；
11. 相邻 TrackOccurrence transition/overlap review evidence；
12. 可直接运行的 v4 stage CLI。

**尚未完成的关键点：** GitHub 远端 legacy `redo_karaoke_pipeline.py` 仍是已提交 v3.8；之前真实使用的 v3.9 是未提交工作树。v4 package 尚未替换/接入 legacy `audio-align/build/finalize/qa` 主链，因此当前 PR 必须保持 Draft。

## 2. 已实现关键代码

### 2.1 可信发布链

- `lyric_aligner/srt.py`
- `lyric_aligner/contracts/artifacts.py`
- `lyric_aligner/qa/final_integrity.py`
- `scripts/v4_validate_release.py`

关键升级：

- SRT 坏 block 不再静默跳过；
- overlay SRT 的时间轴终点使用 `max(cue.end_ms)`；
- FINAL SRT / audit CSV / QA 必须严格逐行对应；
- 终稿正文、时间、行数、顺序任何变化都会 BLOCK；
- ArtifactManifest 绑定 task fingerprint、algorithm version、配置、模型/依赖占位、upstream artifact IDs、输出 SHA-256；
- 消费上游 artifact 时不仅校验 manifest 自己，还重新校验磁盘实体文件的 size/hash，防止 manifest 生成后文件被修改。

### 2.2 输入可信性

- `lyric_aligner/domain.py`
- `lyric_aligner/assets/resolver.py`
- `lyric_aligner/assets/lyric_roles.py`
- `lyric_aligner/io/text.py`
- `scripts/v4_resolve_assets.py`

关键升级：

- 一个录音版本 = `TrackAsset`；本次 mix 中一次出现 = `TrackOccurrence`；
- 同歌重复出现不会和另一次 occurrence 混淆；
- LRC/source 不再“最像的就用”：top1 threshold + top1/top2 margin + artist/title identity + 文件唯一占用；
- 同时间戳多行歌词必须能唯一确认 canonical original；不能确认就 BLOCK；
- 韩文旁边的拉丁行不会自动猜成“罗马音”或“英文翻译”；
- v4 文本默认 UTF-8；CP949/Shift-JIS 等必须显式声明/转换，禁止盲猜解码。

### 2.3 Source→Mix 音频映射

- `lyric_aligner/audio/features.py`
- `lyric_aligner/audio/coarse_mapper.py`
- `lyric_aligner/audio/timewarp.py`
- `lyric_aligner/audio/fine_alignment.py`
- `scripts/v4_coarse_align.py`
- `scripts/v4_fine_align.py`

核心链路：

```text
Mix / Source audio
    ↓
HPSS harmonic
    ↓
Chroma CENS + MFCC
    ↓
每窗口多 source-position / slope 候选
    ↓
top1/top2 margin + candidate NMS
    ↓
跨窗口单调全局路径
    ↓
AlignmentAnchor
    ↓
AFFINE first
    ↓ 仅固定模型解释失败时
PIECEWISE_RATE
    ↓ 仅低 margin / blocked / complex 时
Fine Alignment
```

关键语义：

- 大多数正常歌曲停在 AFFINE；
- BPM 只是软 prior，不锁 slope；
- 错 BPM 不能排除全局 slope 搜索；
- 同一歌局部 `1.08 -> 1.17 -> 1.43` 可以表示为连续 PIECEWISE_RATE；
- abrupt slope change 不是 cut；
- source position jump 才是 middle-cut/discontinuity 候选；
- `middle_cut=true` 只允许搜索，绝不自动 confirmed；
- fine alignment 默认跳过 clean AFFINE，只处理不确定/复杂路径或显式 `--force`。

### 2.4 歌曲交界与叠唱

- `lyric_aligner/audio/transition.py`
- `scripts/v4_probe_transition.py`

关键升级：

- `nominal_start_ms` 是搜索先验，不是上一首硬 end；
- transition 建议在边界前后有限 margin 同时对两首 source 做 coarse alignment；
- 两首都在同一 mix 区间得到强、非歧义 source evidence 时，只生成：

```text
cross_track_overlap_candidate
status = review
```

- 不自动确认叠唱；
- 重复副歌导致的 low-margin 高分不会冒充 overlap，只进入 `uncertain_intervals` 并 BLOCK；
- 明显顺序交接不制造 review candidate。

## 3. Bootstrap 参数声明

当前所有阈值均是保守初始值，不是生产 calibration 结果，包括但不限于：

```text
asset min_score = 0.76
asset min_margin = 0.08
coarse slope range = 0.65..1.80
coarse slope step = 0.10
coarse fusion = 0.78 * chroma + 0.22 * mfcc
coarse min_score = 0.72
coarse min_margin = 0.035
fine source radius = 1.25s
fine slope radius = 0.08
fine slope step = 0.02
fine candidate step = 0.05s
transition min_score = 0.72
transition min_margin = 0.02
transition min overlap = 0.75s
TimeWarp max_continuous_rate = 2.0
TimeWarp min_excess_source_jump = 1.5s
```

这些值必须通过私有 calibration/blind-test 调整，不能因为合成测试通过就长期固定。

## 4. 当前验证

在恢复的 v3.9 工作树 + 当前 v4 package 本地组合执行：

```text
python -m compileall -q lyric_aligner scripts
PYTHONPATH=.:scripts python -m unittest <v4 tests + legacy core/end-to-end>
```

最新结果：

```text
94 tests passed
26.65s
```

覆盖：

- legacy v3.9 middle-cut / variable-speed / overlap / end-to-end；
- sequence-aware evaluator；
- release false-ready；
- artifact 磁盘 hash drift；
- asset 错绑/歧义；
- LRC 同时间戳原文歧义；
- CP949 非显式编码；
- 固定倍率 AFFINE；
- 错 BPM prior；
- `1.08 -> 1.17 -> 1.43` 连续局部倍率；
- rate change vs cut；
- 强 140 BPM click；
- source 重复 motif top1/top2 ambiguity；
- 多窗口单调 path；
- selective fine alignment；
- adjacent transition overlap review evidence；
- 5 个 v4 CLI 在空环境变量下从 repo root 可正常启动。

### 合成 fine alignment 示例

一个合成 fixture：source 片段按 1.20 倍 time-stretch，并叠加 140 BPM click。

- coarse source-center median error：约 38ms；
- fine source-center median error：约 20ms；
- refined AFFINE slope：约 1.204。

**该数字只证明合成 fixture 中精修路径有效，不代表真实歌曲总体准确率。**

## 5. 尚未完成

### P0/P1 必须完成

1. 用本地 Git/Codex 恢复真正 v3.9 legacy 工作树并形成可追溯 commit/tag；
2. 把 strict SRT / artifact lineage / TrackAsset / TimeWarp 真正接入 legacy 生产 CLI；
3. 禁止 legacy 3.8/3.9/v4 artifact 静默混用；
4. 将 Editor Evidence profile 接入生产 sequence/boundary score；
5. 为同脚本 translation/original 增加显式 role override，而不是靠猜。

### 音频准确率下一步

1. 把 legacy waveform NCC 作为独立 evidence channel 融入 v4，不直接删除；
2. transition margin 自动调度两首 occurrence 的 source search；
3. fine alignment 只在不确定窗口运行，增加局部边界输出；
4. 建立私有 real-song calibration/blind-test；
5. calibration 后再决定是否引入 source-side Forced Alignment；
6. ASR v2、双模型、人声分离继续后置。

## 6. 发布状态

当前 PR #1：**Draft / 不应合并到 main 作为生产替代。**

原因不是当前新模块测试失败，而是 legacy v3.9 的正式 Git 基线与生产接线尚未完成。当前 v4 新模块可以在本地私有任务中并行试跑和收集指标，但不能把它描述成已经替换 v3.x 的正式生产管线。
