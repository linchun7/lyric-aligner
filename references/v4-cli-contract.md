# Lyric Aligner V4 CLI Safety Contract

状态：mandatory  
适用范围：所有会写入 production/evaluation artifact 的 `scripts/v4_*.py` CLI。

## 1. Artifact writer 的路径不变量

任何 v4 artifact writer 在第一次写文件之前都必须 fail closed 验证：

```text
outputs ∩ task-manifest-bound inputs = ∅
outputs ∩ direct CLI inputs = ∅
outputs ∩ discovered upstream/materialized inputs = ∅
all output paths are pairwise distinct
```

其中 task manifest 的 directory input 保护的是**整棵目录子树**，不是只保护目录名或当前已存在的成员：

- manifest 中已 fingerprint 的每个文件成员会显式进入 protected input 集合；
- 即使 `lyrics_dir/new-output.json` 之类目标文件事先不存在，只要位于受保护 input directory 下也必须拒绝；
- 这样 artifact writer 既不能覆盖已有输入，也不能通过新增文件静默改变目录递归哈希，使刚验证过的 task manifest 立即失效。

路径保护只负责 ownership/safety，不改变 artifact identity、timing、text、review 或 release authority。

## 2. 当前 Max 必经 CLI

### `v4_review.py`

`template` 输出不得覆盖：task manifest/任一 task input、production run、production run artifact。

`apply` 还必须保护 review decisions 输入；reviewed run 与 review artifact 两个输出也不得同路径。

人工 review 的 allowed actions、issue identity 和 replay semantics 不因路径保护改变。

### `v4_render.py`

四个输出：

```text
final SRT
audit CSV
QA JSON
final-render artifact
```

必须彼此不同，并不得覆盖：task manifest/任一 task input、run、run artifact、TrackAssets、asset artifact，以及 run 中实际读取的每个 canonical timeline / timeline artifact。

该检查必须发生在 `_write_srt()` 或任何其他 materialization 之前。当前 renderer 仍是 `canonical_line_evaluation_only`；路径保护不授予 `editor_reconciled`。

决定 render eligibility / materialization 完整性的以下计数必须是真正 JSON integer，不能依赖 `int(...)` coercion：

```text
review_resolution.remaining_issue_count
overlap_recomposition.remaining_issue_count
cut_rebuild.remaining_issue_count
cut_rebuild.canonical_fragment_issue_count
cut_rebuild.rebuilt_occurrence_count
combined_recomposition.remaining_issue_count
combined_recomposition.combined_occurrence_count
```

相关 run artifact 的 `normalized_config` 也必须确实为 JSON object。

### `v4_editor_cue_reconcile.py`

reconciliation output/artifact 不得覆盖 task input 或 canonical evaluation SRT/audit/QA/final-render artifact。它继续保持：

```text
segmentation_authority = editor_reconciliation_evaluation_only
production_authority_granted = false
```

### `v4_validate_release.py`

release manifest 不得覆盖 task manifest/任一 task input、final SRT、audit CSV、QA JSON 或任何 upstream artifact。

release authority gate 不因路径保护改变：V4 仍要求 exact final-render binding + `segmentation_authority=editor_reconciled` + release QA 完整通过。

## 3. JSON 类型必须 fail closed

Release/evaluation authority 不能依赖 Python 的宽松强制转换。

`review_candidate_count` 的零值必须是真正 JSON integer `0`：

- `0`：有效；
- `false`：无效；
- `0.0` / `0.5`：无效；
- `"0"`：无效；
- `null`：无效。

相同原则适用于上面列出的 render authority counts。同理，artifact 顶层和 `normalized_config` 在需要 object contract 的位置必须确实是 JSON object；畸形、自洽重哈希的 artifact 也只能得到受控 fail-closed 错误，不能靠 `AttributeError` 等未处理异常泄漏出契约。

## 4. 回归要求

公共 regression 只能使用 generic synthetic fixtures，并至少证明：

- task directory member 会进入 protected path 集合；
- output 指向 task input directory 下一个尚不存在的新文件时同样被拒绝；
- review template/apply 的碰撞不会改变被保护输入字节；
- release manifest 碰撞不会改变被保护输入字节；
- malformed review/rebuild/render authority count 被拒绝；
- 正常 render/review/release/evaluation 路径不因 guard 产生 false positive。

真实歌曲名、歌词、cue、timestamp、audio 或私有路径不得进入本文件或公开测试。
