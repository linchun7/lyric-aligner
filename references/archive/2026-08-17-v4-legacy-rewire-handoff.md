# 本地 Codex 任务：冻结 v3.9，仅重接 v4 Integration Seam

基线分支：`agent/v4-integration-hardening`  
目标：**只修改 v3.9 monolith 与 v4 package 的接线，不新增算法、不调阈值。**

## 1. 不可违反的原则

1. `scripts/redo_karaoke_pipeline.py` 的算法版本保持 `3.9`。
2. 无 `--v4-track-assets/--v4-asset-artifact` 时，legacy v3.9 行为必须完全兼容。
3. 开启 v4 mode 后，legacy 只能通过 `lyric_aligner.legacy.bridge` 获取：
   - v4 package version；
   - TrackOccurrence；
   - source audio；
   - canonical LRC；
   - same-timestamp canonical original；
   - calibration profile identity；
   - asset artifact ID。
4. 禁止 legacy 再做第二次 source/LRC/canonical fuzzy resolution。
5. 禁止把 TimeWarp/transition/Forced Alignment 新算法写入 monolith。
6. 不修改 `main`；在当前 hardening 分支后继续提交或新开子分支。

## 2. 当前已确认的 P0 漏洞

### 2.1 source audio 双真源

当前 v4 resolver 已选定 `TrackAsset.source_audio_path/hash`，但 `command_audio_align()` 仍：

```python
source_path = find_source_audio(track, args.source_dir)
```

v4 mode 下必须删除这条生产路径，改为：

```python
binding = binding_for_ordinal(v4_context, track.index)
source_path = Path(binding.source_audio_path)
```

`find_source_audio()` 只允许保留在纯 legacy v3.9 mode。

### 2.2 canonical lyric 双真源

当前 legacy `parse_lrc()` 对同时间戳直接取 `alternatives[0]`。

v4 mode 下必须改为：

```python
lines = canonical_lines_for_ordinal(v4_context, track.index)
```

不能再调用 legacy first-alternative 选择。需要 adapter 将 `CanonicalLine/CanonicalToken` 转为 legacy `LyricLine/LyricToken`，但**不得重新选择文本**。

## 3. Bridge 初始化

删除/停止使用 monolith 内硬编码：

```python
V4_ALGORITHM_VERSION = "4.0.0a1"
```

改为导入：

```python
from lyric_aligner import __version__ as V4_ALGORITHM_VERSION
from lyric_aligner.legacy.bridge import (
    LEGACY_ALGORITHM_VERSION,
    binding_for_ordinal,
    canonical_lines_for_ordinal,
    legacy_bridge_metadata,
    load_bridge_context,
)
```

要求断言：

```python
ALGORITHM_VERSION == LEGACY_ALGORITHM_VERSION == "3.9"
```

新增统一 helper：

```python
def load_optional_v4_context(manifest, assets_path, artifact_path):
    ...
```

- 两个 path 都未给 -> `None`；
- 只给一个 -> fail closed；
- 两个都给 -> `load_bridge_context(..., verify_asset_files=True)`；
- 不再返回 raw dict。

删除或弃用 `load_v4_assets()` / `v4_lyric_path_by_ordinal()` 的 raw-payload 接线。

## 4. Track adapter

`parse_song_list()` 保留 legacy mode，但增加/改造 v4 path：

- artist/title/start 来自原 song-list，仅用于校验显示；
- LRC path 必须等于 binding canonical path；
- occurrence ordinal 必须一一对应；
- song-list artist/title 与 binding 不一致 -> BLOCK；
- `final_end_ms` 用 `max(cue.end_ms)`，不能用 `cues[-1].end_ms`。

不要让 v4 mode fallback 到 fuzzy LRC。

## 5. 所有 canonical lyric 读取点

搜索：

```text
parse_lrc(
```

逐个分类：

- pure legacy path：可继续旧 parser；
- v4 mode：必须改为 `canonical_lines_for_ordinal()`。

至少检查：

- `prepare`
- `audio-align`
- `build`
- `refine-asr`
- `finalize`
- `qa` 中重新投影 canonical event 的路径

如果某命令需要 `Track`，必须让它收到同一个 v4 context，而不是自己重新 parse song-list 后失去 binding。

## 6. 所有 source audio 读取点

搜索：

```text
find_source_audio(
```

v4 mode 下全部替换为 binding source path。

如果有 source hash 比较，使用 binding 中已验证的 `source_audio_sha256`；可以再次 hash 实体文件验证，但不能重新选择文件。

## 7. CLI 参数传播

目前只有 prepare/audio-align 部分接 v4 assets。必须把以下命令全部增加同一对可选参数：

```text
--v4-track-assets
--v4-asset-artifact
```

至少：

- prepare
- audio-align
- build
- refine-asr / refine-korean alias
- finalize
- qa

同一次 pipeline 不能某阶段 v4、某阶段 legacy。建议新增 report/artifact metadata guard：如果上游 artifact 带 `asset_artifact_id`，下游没有提供同一 v4 context -> BLOCK。

## 8. Artifact metadata 传播

v4 mode 下所有主要 JSON/CSV stage 至少记录：

```json
{
  "legacy_algorithm_version": "3.9",
  "v4_algorithm_version": "4.0.0a2",
  "calibration_profile_version": "...",
  "calibration_profile_id": "...",
  "asset_artifact_id": "..."
}
```

使用：

```python
legacy_bridge_metadata(context)
```

不要复制生成逻辑。

对 CSV，可以逐行增加对应字段或在 stage manifest 中统一记录；必须确保 QA 能校验同一 lineage。

## 9. Release lineage

QA ready 时当前自动生成 release manifest。改造：

- v4 mode 必须至少将 `asset_artifact_id` 作为 upstream；
- 如果后续传入 coarse/fine/transition artifact，则也进入 upstream IDs；
- release config 记录 calibration profile version/id；
- 如果上游 artifacts profile ID 不一致 -> BLOCK；
- pure v3.9 legacy mode 保持现有 release 行为。

可复用 `scripts/v4_validate_release.py` 当前 upstream-artifact 逻辑，不要在 monolith 再写一套复杂 validator。

## 10. Strict SRT / timeline end

v4 mode 输入边界：

- 用 `lyric_aligner.srt.parse_srt_strict` 或 adapter；
- non-empty malformed block 必须失败；
- timeline end = `max(cue.end_ms)`；
- 不要求一次性删除 legacy `parse_srt()`，pure v3.9 可以保持兼容。

## 11. 必须新增的回归测试

### T1 source single truth

准备：TrackAsset 明确 source A；目录中另有文件名更相似的 source B。

v4 mode `audio-align` 必须读取 A，绝不能调用/选择 B。

### T2 canonical second alternative

LRC：

```text
[00:01.00]translation
[00:01.00]canonical
```

role-map 选择 index=1。

prepare/build/finalize/qa 投影出来必须始终是 `canonical`，不能在任意阶段重新变成第一行。

### T3 role selection changes identity

同 raw LRC，index 0 与 index 1：

- track_id 不同；
- version_id 不同；
- old artifact 不能复用。

### T4 mixed v4/legacy stage blocked

v4 prepare/audio artifact 后，不带相同 asset artifact 的 build/finalize/qa 必须 BLOCK。

### T5 wrong v4 version blocked

手造 `4.0.0a1` track_assets artifact，当前 package `4.0.0a2` 必须拒绝。

### T6 strict SRT max end

乱序 overlay SRT，最后文件 block 不是最大结束时间；v4 path 必须使用 max end。

### T7 pure v3.9 regression

不传任何 v4 参数，现有全部 v3.9 tests 行为保持不变。

## 12. 禁止项

- 不得删除 v3.9 middle-cut / variable-speed / overlap / Enhanced LRC/QRC 实现；
- 不得把 v4 TimeWarp 直接替换 production mapping；
- 不得修改 bootstrap/calibration 阈值；
- 不得引入 Forced Alignment；
- 不得把项目真实歌词/音频/任务数据提交仓库；
- 不得为了测试通过恢复“first alternative”或“find nearest source”的 fallback。

## 13. 验收命令

```bash
python -m compileall -q lyric_aligner scripts
PYTHONPATH=.:scripts python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/privacy_scan.py
python scripts/check_environment.py
git diff --check
```

如果 ASR 环境可用，再：

```bash
python scripts/check_environment.py --asr
```

## 14. 提交策略

建议拆两个 commit：

1. `Wire frozen v3.9 to v4 asset/canonical truth`
2. `Propagate v4 lineage through legacy release flow`

不要把算法优化混入这两个提交。

完成后输出：

- commit SHA；
- 修改的 legacy functions；
- 新增 regression tests；
- 全测试数量；
- 是否仍存在任何 `v4 mode -> find_source_audio()` 或 `v4 mode -> alternatives[0]` 路径；
- 当前 TimeWarp 是否仍保持 shadow（答案必须是 yes）。
