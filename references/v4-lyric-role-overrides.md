# v4 Canonical LRC 角色 Override 规范

更新：2026-08-17

## 1. 为什么需要 Override

v4 默认采用 fail-closed 原则：同一 LRC 时间戳如果存在多条歌词，只有在系统能够**唯一确认 canonical original** 时才继续。

例如韩文原文 + 拉丁转写：

```text
[00:01.00]사랑해 너를
[00:01.00]saranghae neoreul
```

在 `language=ko` 下，韩文可唯一作为 `original`，拉丁行保持 `unknown`，不需要人工 override。

但下列情况不能安全猜：

```text
[00:01.00]我们一起走
[00:01.00]我們一起走
```

两行都属于 Han script。它们可能是简繁版本、翻译、替换版本或不同来源。v4 不允许简单采用“第一行就是原文”。

## 2. Override 文件格式

任务级文件建议：

```text
private/<任务>/qa/lyric_role_map.json
```

格式：

```json
{
  "Artist - Song": {
    "1000": 1,
    "45200": 0
  },
  "Another Song": {
    "183400": 0
  }
}
```

含义：

```text
track key
  ↓
timestamp_ms
  ↓
同一 timestamp group 中的 zero-based alternative index
```

例如：

```json
"1000": 1
```

表示 1000ms 这一组中：

```text
index 0 -> unknown
index 1 -> original
```

## 3. Track Key

优先使用完整：

```text
Artist - Song
```

也兼容仅 Song title，但完整身份更安全。

如果存在同名歌、不同艺人或不同版本，不应只用 title 做长期生产配置。

## 4. Override 的安全边界

Override 只解决：

> “这一组候选中哪一行是 canonical original？”

它**不会**：

- 把其他同行自动判成 translation/romanization/pronunciation；
- 改变 LRC 原始文件；
- 改变 source audio；
- 自动修歌词正文；
- 自动确认 middle cut；
- 绕过 TrackAsset 资产匹配。

被选中的行标记 `original`；其他非 metadata 行保持 `unknown`。

如果指定 index 超出实际 alternatives 范围，系统立即 BLOCK。

## 5. CLI 使用

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
  --artifact-out "output/<任务>/v4/track_assets.artifact.json"
```

## 6. 可追溯性

`v4_resolve_assets.py` 会将以下配置文件的 SHA-256 写入 `asset_resolution` ArtifactManifest：

```text
language_map_sha256
middle_cut_map_sha256
lyric_role_map_sha256
```

因此：

- override 被修改后，新的 asset artifact 与旧 artifact 配置不同；
- 后续 stage 可以追溯它使用了哪一版人工 canonical 选择；
- 关键人工结论不会只存在于对话里。

## 7. 推荐操作

优先级：

1. 能提供干净 canonical LRC -> 优先修 LRC；
2. 第三方 LRC 必须保留多行且无法自动判定 -> 使用 role override；
3. 不要为了“自动通过”降低角色判断门槛；
4. override 必须留在任务级 `private/<任务>/qa/`，不要把真实歌词或歌曲特例写入通用源码。
