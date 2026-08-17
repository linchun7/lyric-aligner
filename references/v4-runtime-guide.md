# Lyric Aligner v4 实验 Stage 运行手册

更新：2026-08-17

> 当前 v4 package 尚未替换 legacy 主流水线。本手册用于在**私有任务**上并行运行 v4 stage、生成可追溯 artifact、收集 calibration/blind-test 数据。真实歌词、媒体和任务级人工结论必须保留在 `private/` / `output/`。

## 1. 当前可运行 Stage

```text
schema 2.0 Task Manifest
        ↓
v4_resolve_assets.py
        ↓
TrackAsset / TrackOccurrence + asset artifact
        ↓
v4_coarse_align.py（每个 occurrence）
        ↓
coarse audio alignment + TimeWarp artifact
        ↓
[按需] v4_fine_align.py
        ↓
[歌曲交界] v4_probe_transition.py
        ↓
legacy build/finalize/qa（当前尚未接线 v4 mapping）
        ↓
v4_validate_release.py
```

## 2. 任务级辅助配置

### 2.1 language_map.json

```json
{
  "Artist A - Song A": "ko",
  "Artist B - Song B": "yue",
  "Artist C - Song C": "en"
}
```

未知语言可以 `auto`。非中英文不会把剪映文字直接当 canonical truth。

### 2.2 middle_cut_map.json

key 是 occurrence ordinal：

```json
{
  "3": "true",
  "7": "unknown"
}
```

未列出默认为 `false`。

语义：

- `false`：默认没有中段剪切；出现强 source discontinuity -> BLOCK；
- `true`：允许重点搜索 cut，但**仍必须 review，绝不自动 confirmed**；
- `unknown`：可生成 cut candidate，必须 review；
- trim-start/trim-end 不属于 middle cut。

### 2.3 lyric_role_map.json

仅在同一 LRC 时间戳有多条候选、系统无法唯一确认 canonical original 时使用：

```json
{
  "Artist - Song": {
    "1000": 1,
    "45200": 0
  }
}
```

含义：`timestamp_ms -> zero-based alternative index`。

例如 `"1000": 1` 表示 1000ms 这一组中第二行是 canonical original。其他非 metadata 行保持 `unknown`，不会被擅自猜成 translation/romanization。

完整规范见 `references/v4-lyric-role-overrides.md`。

## 3. 解析 TrackAsset / TrackOccurrence

```powershell
python scripts/v4_resolve_assets.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --song-list "private/<任务>/输入/歌曲清单.txt" `
  --lyrics-dir "private/<任务>/歌词" `
  --source-dir "private/<任务>/原曲音频" `
  --language-map "private/<任务>/qa/language_map.json" `
  --middle-cut-map "private/<任务>/qa/middle_cut_map.json" `
  --lyric-role-map "private/<任务>/qa/lyric_role_map.json" `
  --out "output/<任务>/v4/track_assets.json" `
  --artifact-out "output/<任务>/v4/track_assets.artifact.json" `
  --git-commit "<当前commit>"
```

三个 map 参数均可省略。提供时，其 SHA-256 会写入 asset artifact 配置。

会 BLOCK 的典型情况：

- LRC/source top1 太弱；
- top1/top2 太接近；
- 同标题不同艺人争用同一 generic 文件；
- 同一个源文件被两个不同 TrackAsset 静默复用；
- 同时间戳存在多个可能 original 且没有显式 override；
- role override index 越界；
- task manifest 与磁盘 song list/lyrics/source 不一致。

后续 stage 使用稳定 `occurrence_id`，不要继续用曲名作为唯一身份。

## 4. 普通歌曲：Coarse Alignment

```powershell
python scripts/v4_coarse_align.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --mix-audio "private/<任务>/输入/最终混音.wav" `
  --track-assets "output/<任务>/v4/track_assets.json" `
  --asset-artifact "output/<任务>/v4/track_assets.artifact.json" `
  --occurrence-id "occ_xxxxxxxxx" `
  --bpm-prior 1.1667 `
  --out "output/<任务>/v4/occ_x_coarse.json" `
  --artifact-out "output/<任务>/v4/occ_x_coarse.artifact.json" `
  --git-commit "<当前commit>"
```

`--bpm-prior` 可省略。它只作为 soft prior / slope-search 局部加密，不锁最终 slope，也不删除全局 slope 候选。

若不提供 `--mix-start/--mix-end`，当前 CLI 用：

```text
当前 nominal_start -> 下一 occurrence nominal_start
```

作为**普通曲段 coarse seed interval**。这不是最终 active end；歌曲交界必须另行做 transition margin 搜索。

## 5. 非匀速 / 不确定歌曲：Fine Alignment

```powershell
python scripts/v4_fine_align.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --mix-audio "private/<任务>/输入/最终混音.wav" `
  --track-assets "output/<任务>/v4/track_assets.json" `
  --asset-artifact "output/<任务>/v4/track_assets.artifact.json" `
  --coarse "output/<任务>/v4/occ_x_coarse.json" `
  --coarse-artifact "output/<任务>/v4/occ_x_coarse.artifact.json" `
  --out "output/<任务>/v4/occ_x_fine.json" `
  --artifact-out "output/<任务>/v4/occ_x_fine.artifact.json" `
  --git-commit "<当前commit>"
```

默认行为：

- clean `AFFINE_ACCEPTED` + 无 ambiguous window -> `skipped_clean_affine`；
- coarse blocked / piecewise / ambiguous -> 自动局部高分辨率精修；
- 难例诊断可加 `--force`；
- fine 只在 coarse source/slope 附近搜索，不重新做全曲昂贵检索。

## 6. 歌曲交界：nominal_start 不是硬边界

假设 B nominal start=600s，建议对 A、B 都重新搜索同一 transition window，例如 590–610s：

```powershell
python scripts/v4_coarse_align.py ... `
  --occurrence-id "occ_A" --mix-start 590 --mix-end 610 `
  --out ".../A_transition_coarse.json" `
  --artifact-out ".../A_transition_coarse.artifact.json"

python scripts/v4_coarse_align.py ... `
  --occurrence-id "occ_B" --mix-start 590 --mix-end 610 `
  --out ".../B_transition_coarse.json" `
  --artifact-out ".../B_transition_coarse.artifact.json"
```

然后：

```powershell
python scripts/v4_probe_transition.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --track-assets "output/<任务>/v4/track_assets.json" `
  --asset-artifact "output/<任务>/v4/track_assets.artifact.json" `
  --left-coarse ".../A_transition_coarse.json" `
  --left-artifact ".../A_transition_coarse.artifact.json" `
  --right-coarse ".../B_transition_coarse.json" `
  --right-artifact ".../B_transition_coarse.artifact.json" `
  --out "output/<任务>/v4/A_B_transition.json" `
  --artifact-out "output/<任务>/v4/A_B_transition.artifact.json"
```

结果：

- `clear_sequential_or_no_overlap`：没有足够双曲并行证据；
- `cross_track_overlap_candidate / review`：两首 source 在同一 mix 区间都有强、非歧义证据；必须确认/拒绝；
- `uncertain_intervals`：两边得分高但 source occurrence 有歧义，例如重复副歌；BLOCK，但不冒充 overlap。

当前 stage **不自动确认叠唱，也不自动生成正式同期双字幕**。

## 7. Release Integrity Guard

legacy 最终 SRT/审计/QA 生成后：

```powershell
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/<任务>_FINAL.srt" `
  --report "output/<任务>/<任务>_FINAL_审计.csv" `
  --qa-json "output/<任务>/<任务>_FINAL_QA.json" `
  --algorithm-version "<该QA实际算法版本>" `
  --git-commit "<当前commit>" `
  --out-manifest "output/<任务>/<任务>_RELEASE_ARTIFACT.json"
```

该 guard 会重新检查 task inputs，并严格绑定 FINAL SRT/CSV/QA 的正文、时间、数量、顺序和 SHA-256。

## 8. Artifact Chain

```text
Task Manifest
  ↓
asset_resolution artifact
  ↓
coarse_audio_alignment artifact(s)
  ↓
[fine_audio_alignment artifact]
  ↓
[transition_probe artifact]
```

每个下游 stage 都应：

1. 校验 task fingerprint；
2. 校验 algorithm version；
3. 校验 artifact_id；
4. 重新校验磁盘上游输出 size/SHA-256；
5. 记录 upstream artifact IDs。

因此不能把上一任务、上一算法版本或后来被修改过的 JSON 静默拼进当前结果。

## 9. `blocked=true` 的含义

它通常意味着“有产物可审查，但不能自动发布”，例如：

- 需要 review 的 source discontinuity；
- piecewise 模型证据不足；
- transition 可能叠唱；
- 重复 source 导致 occurrence ambiguity；
- fine refinement 有 unresolved window。

## 10. 当前不能做的声明

完成真实私有 calibration/blind-test 和 legacy 接线前，禁止宣称：

- v4 已替代生产 v3.9；
- 真实歌词准确率提高某个百分比；
- bootstrap 阈值已最优；
- transition candidate 等于已确认叠唱；
- `middle_cut=true` 等于系统可自动删除歌词。
