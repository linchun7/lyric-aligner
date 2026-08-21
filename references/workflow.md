# 多语言混剪歌词字幕工作流

## 默认版本与结果语义

- 当前完整生产算法为 `v3.9`；V4 production-first 的权威运行说明见 `SKILL.md` / `references/v4-runtime-guide.md`。
- 对“只修文字、明确冻结剪映时间轴”的任务，使用 `scripts/v4_text_repair.py` 独立快速入口；它不运行完整音频对齐链。
- `passed=true` / `structurally_valid=true`：结构检查没有发现确定性错误。
- `fully_reviewed=true`：高、中、低风险候选均为零。
- `publish_ready=true`：同时满足结构通过和候选清零。
- `release_status=ready`：允许把结果称为正式终稿；否则必须是 `blocked`。

这些字段表示“没有已知未解决问题”，不等于未经测量的绝对 100% 正确率。

## 任务目录与完整指纹

建议目录：

```text
private/<任务名>/
├─ input/
│  ├─ source.srt
│  ├─ mix.wav
│  ├─ songs.txt
│  ├─ bpm.txt                  # 可选
│  ├─ lyrics/                 # 普通 LRC、Enhanced LRC 或受支持的 QRC 词级时间
│  └─ source-audio/            # 完整波形流程需要
└─ qa/
   ├─ task_manifest.json
   ├─ <任务名>_manual_overrides.json
   └─ <任务名>_regression_cases.json

output/<任务名>/
```

`task_manifest.json` 使用 schema 2.0，记录以下输入的路径、大小、哈希或目录递归哈希：

- `source_srt`
- `audio`
- `song_list`
- `lyrics_dir`
- `bpm_changes`（可空）
- `source_audio_dir`（可空）

任务指纹由项目名和这些内容记录共同计算。任一输入内容变化后，旧 manifest、中间产物、人工覆盖和回归文件都必须拒绝复用。

## 阶段 0：环境检查

基础流程：

```powershell
python -m pip install -r requirements.txt
python scripts/check_environment.py
```

ASR：

```powershell
python -m pip install -r requirements-asr.txt
python scripts/check_environment.py --asr
```

语言读音层：

```powershell
python -m pip install -r requirements-language.txt
```

开发与完整 Skill YAML 校验：

```powershell
python -m pip install -r requirements-dev.txt
python scripts/validate_skill.py .
```

不同 Python 解释器拥有不同依赖环境。必须使用准备运行生产命令的同一个解释器执行环境检查。

## 阶段 1：初始化或迁移任务

新任务：

```powershell
python scripts/init_task.py `
  --root "." `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --source-audio-dir "private/任务名/input/source-audio"
```

可选输入不存在时不要传对应参数。完整生产波形对齐仍需要原曲目录；没有原曲时只能先运行文本基线或使用已经验证并迁移的对齐证据，不得伪造波形结果。

旧 QA 文件必须显式迁移：

```powershell
python scripts/migrate_task.py `
  --root "." `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --source-audio-dir "private/任务名/input/source-audio" `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --regression-cases "private/任务名/qa/任务名_regression_cases.json" `
  --manifest "private/任务名/qa/task_manifest.json"
```

可重复传入 `--regression-cases`。迁移会先完整读取所有文件，再为每个被改写的旧文件创建 `.schema1.bak`，最后写入 manifest；生产命令不接受 legacy `_source_srt_sha256`。

## 快速路径：只修文字、冻结时间轴

当任务已经明确：规范歌词是最终文字真源，现有剪映 SRT 的 cue 数量、编号、开始时间、结束时间全部保持不变，只修正错字/词句时，不应为了文字纠错运行完整音频链。直接按歌曲顺序重复传入 canonical lyric：

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/任务名/input/source.srt" `
  --canonical-lrc "private/任务名/input/lyrics/01.lrc" `
  --canonical-lrc "private/任务名/input/lyrics/02.lrc" `
  --out "output/任务名/任务名_TEXT_REPAIRED.srt" `
  --report "output/任务名/任务名_TEXT_REPAIR.json"
```

这个入口：

- 不读取 `mix.wav` 或 source audio，不运行 coarse/fine/transition/ASR/forced alignment；
- 支持普通 LRC、Enhanced LRC、QRC 常见时间标记和纯文本歌词；完全 timed 的单个歌词文件会按实际 timestamp stable-sort occurrence，多文件仍保持 `--canonical-lrc` 调用顺序；
- 使用 Unicode NFKC + 忽略标点/空白的文本归一化与单调序列匹配；只有高置信、长度结构安全的 1:1 pair 才进入自动修复；
- 自动写回只替换 lexical/content 字符，保留原 SRT 的标点、空格和换行布局；如果 canonical 内容无法一一映射到现有布局，该 cue 保持原文并进入 `review_required`；
- 单调 alignment 跳过任何 canonical lyric occurrence 时，不新增 cue、不猜 timing，而是记录 `unmatched_canonical` 并进入 `review_required`；
- 输出前强制比较每个 cue 的原编号与完整 timing line，任何变化直接失败；
- 拒绝 `--out` 覆盖 `--source-srt`，因此原件始终保留；
- `status=ready` 时退出 0；存在低置信、分段不一致、未匹配 cue、canonical gap 或不安全的长度/布局变化时报告 `review_required` 并退出 2。

如果任务实际需要新增/删除 cue、拆分/合并字幕、修正时间边界、处理 cut/overlap，或时间轴本身不可信，**不要使用 text-only fast path**，进入下面完整工作流/V4 production path。

## V4 production 入口的同输出目录保护

完整 V4 public entrypoint `scripts/v4_run.py` 会在指定 `--out-dir` 内创建 exclusive `.v4-run.lock`。同一个 output tree 同时只允许一个完整 orchestrator：第二个进程直接 fail closed，避免 assets、primary、transitions、resume state 和 final materializations 相互覆盖。

正常退出或正常异常传播时 owner 会释放自己的 lock；lock 使用随机 owner token，因此一个进程不会删除后来被替换的其他 lock。若进程被强制终止导致 stale lock 保留，**先确认没有真实 V4 run 仍在执行，再人工删除 lock**；不要自动把“PID 看起来不存在”当成可安全夺锁的依据。

该 lock 只保护 orchestrator output ownership，不参与 Source-to-Mix、artifact identity、resume identity、stage scheduling 或任何 timing decision。

## 阶段 2：保守文本基线

```powershell
python scripts/redo_karaoke_pipeline.py prepare `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --audio "private/任务名/input/mix.wav" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --out-dir "output/任务名/01_prepare"
```

只自动采用高相似、单一 LRC 行和单调顺序一致的候选。准备阶段不改变输入，输出文本草稿、审计 CSV 和带任务指纹的 manifest。

## 阶段 3：原曲与混剪波形对齐

```powershell
python scripts/redo_karaoke_pipeline.py audio-align `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --audio "private/任务名/input/mix.wav" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --source-dir "private/任务名/input/source-audio" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --out "output/任务名/02_audio_alignment.json"
```

BPM 是速度比例先验，不是时间戳。实际波形路径和高可信文本锚点决定映射。混剪曲段不得默认对应原曲第一句：先由波形/文本锚点求出混剪入口在原曲中的实际偏移，再投影该入口之后的歌词。映射到曲段入口之前的 LRC 事件记录为 `trimmed_before_mix_entry`，不作为漏字幕，也不得被强塞到第一格。检测到歌曲中段源时间向前跳跃时只生成剪切候选；状态为 `review` 的剪切会阻止构建和发布。

将精确坐标与 `confirmed`/`rejected` 决定写入 `_audio_edit_reviews`，再运行：

```powershell
python scripts/redo_karaoke_pipeline.py review-audio-edits `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --out "output/任务名/02_audio_alignment_reviewed.json"
```

后续 `build`、`refine-asr`、`finalize` 和 `qa` 必须使用 reviewed JSON。固定分段变速和连续加减速只有在波形锚点单调、平滑、残差受控且明显优于单一仿射映射时自动启用；源时间不连续前跳仍按剪切处理，锚点冲突则退回保守映射或阻止发布。

逐字/逐词歌词仅作辅助证据。当前支持 Enhanced LRC 行内 `<mm:ss.xx>` 和常见 QRC `(start,duration)` 词级时间；普通 LRC 行为不变。无论输入含多少词级时间点，最终 SRT 仍输出完整逐行歌词，只用首末可信词时间校正整行起止与审计证据，不输出逐字 cue。

## 阶段 4：投影规范歌词

```powershell
python scripts/redo_karaoke_pipeline.py build `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment_reviewed.json" `
  --out-srt "output/任务名/03_build.srt" `
  --out-report "output/任务名/03_build.csv" `
  --out-mapping "output/任务名/03_mapping.json"
```

默认不保留任何固定 cue 白名单。若任务确有旁白等必须冻结的格，可显式传 `--preserve-cues`，并将理由记录到任务 QA，而不是修改全局默认值。

构建阶段执行全局单调序列对齐、重复段约束、跨格文本 span、缺格插入和长格拆分。所有 CSV 行和映射 JSON 都携带任务指纹。

## 阶段 5：多语言 ASR 证据

新 ASR 作业文件：

```json
{
  "schema_version": "1.0",
  "jobs": [
    {
      "id": "opaque-window-id",
      "track": "与歌曲清单一致的标题",
      "start": 100.0,
      "end": 130.0,
      "language": "mixed",
      "language_mode": "detect"
    }
  ]
}
```

运行：

```powershell
python scripts/validate_multilingual_asr.py `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --audio "private/任务名/input/mix.wav" `
  --jobs "private/任务名/qa/asr_jobs.json" `
  --out "output/任务名/04_asr.json"

python scripts/redo_karaoke_pipeline.py refine-asr `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment_reviewed.json" `
  --asr-json "output/任务名/04_asr.json" `
  --in-report "output/任务名/03_build.csv" `
  --out-srt "output/任务名/04_refined.srt" `
  --out-report "output/任务名/04_refined.csv"
```

`refine-korean` 是同一命令的兼容别名。默认按语言 profile 选择阈值；只有做过校准实验时才手工传 `--min-asr-score` 或 `--min-asr-coverage`。

日文含汉字且缺少 `pykakasi` 读音层时只能标为复核，不能自动标高置信。中文拼音和日文读音只作为辅助证据，终稿仍使用规范 LRC 原文。

## 阶段 6：合成终稿

```powershell
$report = "output/任务名/04_refined.csv" # 未运行 ASR 时改为 03_build.csv

python scripts/redo_karaoke_pipeline.py finalize `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment_reviewed.json" `
  --in-report $report `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --out-srt "output/任务名/任务名_FINAL.srt" `
  --out-report "output/任务名/任务名_FINAL_审计.csv"
```

人工覆盖只允许写入 schema 2.0 文件。覆盖文本、插入、拆分、时序、LRC 索引、确认遗漏、确认边界和复核备注都必须有 `evidence` 或 `reason`。

两首歌首尾叠唱时，算法只在一个过渡 cue 同时具有两首规范歌词的文本与时间证据时生成 `cross_track_vocal_overlap` 候选；不自动把两首歌词拼成一行。先在 `_cross_track_overlap_reviews` 按精确 `cue`、两个 `tracks`、`confirmed`/`rejected` 与 `evidence` 记录结论。若确认确有双曲人声，再在 `_insertions`/`_interval_overrides` 中分别写入两条逐行字幕，并在 `_confirmed_overlap_intervals` 记录同一 `cue`、精确 `start_ms`、`end_ms`、两个 `tracks` 和 `evidence`。普通顺序交接应记为 `rejected`；未复核候选、只有 confirmed 决定但没有精确允许区间，或范围外新增重叠都会阻止发布。

## 阶段 7：最终 QA

```powershell
python scripts/redo_karaoke_pipeline.py qa `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --source-srt "private/任务名/input/source.srt" `
  --final-srt "output/任务名/任务名_FINAL.srt" `
  --report "output/任务名/任务名_FINAL_审计.csv" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment_reviewed.json" `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --regression-cases "private/任务名/qa/任务名_regression_cases.json" `
  --out "output/任务名/任务名_FINAL_QA.json" `
  --out-review "output/任务名/任务名_REVIEW.csv"
```

正式交付至少要求：

- `issues=[]`
- `passed=true`
- `structurally_valid=true`
- `fully_reviewed=true`
- `publish_ready=true`
- `review_candidate_count=0`
- `unexpected_overlap_count=0`
- `lyric_index_regression_count=0`
- `unresolved_lyric_gap_count=0`
- `unreviewed_audio_edit_candidate_count=0`
- `unresolved_cross_track_overlap_count=0`
- `noncanonical_vocalization_count=0`

中低风险候选不再被视为可发布。命令在 `publish_ready=false` 时返回非零状态。

歌词覆盖不能只依据审计 CSV 中的 `lrc_indices`。QA 同时核对：

- 索引绑定字幕是否包含对应规范歌词；
- 投影时间前后 3 秒内的同曲字幕是否跨格组成该句；
- 较长规范歌词必须有完整文本或同时满足足够的相似度与覆盖率。

`lyric_index_text_mismatch` 记录索引挂在相邻格、拆句格或旧文本上的审计项。只要投影附近实际存在完整歌词，它不阻断发布；若索引和附近字幕都找不到规范歌词，则进入 `lyric_coverage_missing` 并阻断发布。

## QA schema 2.0

`manual_overrides` 与 `regression_cases` 顶层全部必填：

```json
{
  "schema_version": "2.0",
  "artifact_type": "manual_overrides",
  "project": "任务名",
  "source_srt_sha256": "<64 位小写 SHA-256>",
  "task_fingerprint_sha256": "<64 位小写 SHA-256>",
  "scope": "Bound to this exact task manifest fingerprint; never reuse for another audio, SRT, song list, BPM list, lyric set, or source-audio set."
}
```

代码会逐字段验证 `schema_version`、`artifact_type`、`project`、`source_srt_sha256`、`task_fingerprint_sha256` 和 `scope`，不再只检查 SRT 哈希。

## 测试与数据评估

```powershell
python -m compileall -q scripts
python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/check_environment.py
git diff --check
```

测试包含合成音频的 `prepare → audio-align → build → finalize → qa` 完整链路，不使用真实歌词或歌曲。

授权私有数据集按 `references/dataset-protocol.md` 管理，并运行：

```powershell
python scripts/evaluate_dataset.py `
  --dataset "private/datasets/<名称>/dataset.json" `
  --out "output/evaluation/v3.9.json"
```

评估输出仅含聚合指标和匿名 case id，不输出歌词正文。

## 源码发布边界

可提交算法、测试、文档、依赖和脱敏合成 fixture。不得提交 `private/`、`output/`、真实音视频、任务歌词、任务 QA、账号、本机绝对路径或凭据。大型授权数据如需版本管理，使用 Git LFS 或独立私有 dataset 仓库，并仍保持 train/calibration/blind_test 隔离。

## Smart v1.2.5 no-audio 生产入口

日常 Smart 继续使用同一个公开 CLI；v1.2.5 没有新增命令行参数：

```powershell
python scripts/v4_smart_repair.py `
  --source-srt "private/任务名/input/source.srt" `
  --canonical-lyrics "private/任务名/input/lyrics/01.lrc" "private/任务名/input/lyrics/02.lrc" `
  --output-srt "output/任务名/任务名_SMART.srt" `
  --report "output/任务名/任务名_SMART.json"
```

CLI 现在进入 `smart_policy_v125.smart_repair_srt_text_v125()`。运行顺序是：先完整执行冻结的 v1.2.4 Smart policy，得到最终文字与最终 timing decisions；然后 A-bounded 仅使用这些已经完成的 same-source A-grade timing 证据，对极窄 mapped-review region 做 text-only recovery。A-bounded 之后**不重新建立 timing model、不重新计算 timing decision**。

v1.2.5 仍保持：

- 0 audio；
- canonical lyric = final text/order truth；
- cue count / number 不变；
- A-bounded 自身绝不改变 start/end；
- recovered score `< B-grade timing authority`；
- BPM-derived 仍是 soft prior；
- pure vocalization、unmapped/zero-width、cross-source、multi-cue Latin/mixed、boundary insertion 等 A-bounded case 继续 review；
- report schema 仍为 `smart-1.1`，policy id 更新为 `smart-validation-policy-2026-08-21-v1.2.5`。

生产发布仍以 CI + private acceptance 为 gate；任何真实歌曲、cue、时间戳或歌词只留在私有验收，不写入 public regression。
