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

会动态生成多个子文件的 materializer/orchestrator 还必须证明**整棵 output tree 与输入双向不相交**：output tree 不得位于 protected input directory 内，任何 protected input file/directory 也不得位于 output tree 内。输入 run payload 中声明的全部 `*_path` lineage 在首次 `mkdir`、子进程或 materialization 前都视为 protected input，即使该路径当前不存在。

路径保护只负责 ownership/safety，不改变 artifact identity、timing、text、review 或 release authority。

## 2. 当前 Max 必经 CLI

### `v4_run.py` / `v4_run_optimized.py` / `v4_run_legacy.py`

三条 public production orchestration entrypoint 都必须在第一次 filesystem mutation 前证明 `--out-dir` 的整棵 tree ownership：

- canonical `v4_run.py` 必须在 `OutputRunLock` 创建 output directory 或 `.v4-run.lock` 前检查；
- direct optimized entrypoint 必须在 `cache/`、verified-input session、stage directory 创建前检查；
- direct legacy entrypoint 必须在 stage directories 创建前检查；
- task manifest、所有 manifest-bound input roots/subtrees，以及显式 `--profile`、`--language-map`、`--middle-cut-map`、`--lyric-role-map` 都属于 protected inputs；
- output tree 位于任一 protected input 内，或 output tree 反向包含 protected input，都必须 fail closed。

Legacy/optimized 原 orchestration implementation 以 blob-identical `_v4_run_*_impl.txt` internal source resource 保存，由安全 public wrapper 在 preflight 后加载。internal resource 不是受支持 CLI，也不得作为绕过 preflight 的第二入口。该拆分不改变 orchestration algorithm、monkey-patch compatibility、readiness 或 release authority。

### `v4_resolve_assets.py` / `v4_coarse_align.py` / `v4_fine_align.py` / `v4_probe_transition.py`

四条 primary-stage CLI 即使脱离 orchestrator 被直接执行，也必须在原实现第一次 artifact/cache write 前完成 ownership preflight：

- 所有 `--out` / `--artifact-out` 必须 pairwise distinct，且不得覆盖 task manifest、manifest-bound input subtree 或直接 upstream/config input；
- TrackAssets/coarse 等 JSON 输入中递归声明的 `*_path` lineage 同样属于 protected inputs；
- `v4_coarse_align.py` 的 `--feature-cache-dir` 是动态 writable tree，必须与 protected inputs 双向不相交；未显式传参时，也必须对由 `--out` 推导出的 production cache tree执行相同检查；
- output 文件只是单文件 ownership，不因为其父目录包含别的合法 stage artifact 就误判整棵父目录为 writer-owned；
- public `v4_*.py` 是唯一受支持入口，原四份 stage implementation 以 blob-identical `_v4_*_impl.txt` internal source resource 保存，不能通过 resource 绕过 preflight。

当前冻结 implementation blob：

```text
resolve_assets    162b1d9dfc25b3ae2e5995d0e790c47dbcc931f8
coarse_align      735c9aa1a98607953206aedbe1264f7680b5c145
fine_align        005ba2744ba299ded2eed4c7ee7a8c9511448706
probe_transition  eabf2b2f10f67d1057adab992b395ee562a1f8c4
```

该 gate 不改变 asset resolution、Source-to-Mix、Fine、transition score/margin、TimeWarp 或 readiness authority。

### `v4_review.py`

`template` 输出不得覆盖：task manifest/任一 task input、production run、production run artifact。

`apply` 还必须保护 review decisions 输入；reviewed run 与 review artifact 两个输出也不得同路径。

人工 review 的 allowed actions、issue identity 和 replay semantics 不因路径保护改变。

### `v4_rebuild_cut.py` / `v4_recompose_overlap.py` / `v4_compose_materializations.py`

三条 review 后 materializer 在任何目录创建、Fine 子进程或 JSON materialization 前必须完成 output-tree preflight，保护 task inputs/subtrees、直接 run/artifact 与 TrackAssets 输入，以及输入 payload 中递归声明的全部 `*_path` provenance。

公开 `v4_*.py` 是唯一支持的 CLI entrypoint。原 materializer 算法 source 以 blob-identical 的 `_v4_*_impl.txt` internal resource 保存，由通过 preflight 的 wrapper 以非 `__main__` 名称加载；这些 resource 不是 CLI、不得直接执行。这个拆分只把安全 guard 放到原实现第一次写入之前，不改变 cut/overlap/combined 算法。

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

V4 release 只有在唯一 exact final-render artifact 的 production authority **三层一致**时才可继续：

```text
normalized_config.segmentation_authority = editor_reconciled
evidence.segmentation_authority          = editor_reconciled
evidence.publish_ready                   = true
exact QA.segmentation_authority           = editor_reconciled
exact QA.publish_ready                    = true
```

artifact evidence 或 exact QA 任何一处仍有非空 `release_blocked_reason` 时必须 fail closed。不能只把 `normalized_config` 改成 production authority，而让 evidence/QA 仍保持 evaluation-only；这种半升级状态不得生成 release manifest。

之后仍需通过既有 exact SRT/audit/QA hash binding、task fingerprint、algorithm version、calibration profile 与 release QA 完整检查。路径保护和三层一致性检查都不创造新的 segmentation authority；它们只验证真正的 production materializer 是否给出了完整一致的证据。

## 3. JSON 类型必须 fail closed

Release/evaluation authority 不能依赖 Python 的宽松强制转换。

`review_candidate_count` 的零值必须是真正 JSON integer `0`：

- `0`：有效；
- `false`：无效；
- `0.0` / `0.5`：无效；
- `"0"`：无效；
- `null`：无效。

相同原则适用于上面列出的 render authority counts。同理，artifact 顶层、`normalized_config` 与 production release 需要消费的 `evidence` 在 object contract 位置必须确实是 JSON object；畸形、自洽重哈希的 artifact 也只能得到受控 fail-closed 错误，不能靠 `AttributeError` 等未处理异常泄漏出契约。

## 4. 回归要求

公共 regression 只能使用 generic synthetic fixtures，并至少证明：

- task directory member 会进入 protected path 集合；
- output 指向 task input directory 下一个尚不存在的新文件时同样被拒绝；
- run/materializer output tree 不能包住 direct/lineage input，也不能位于 task input subtree；
- canonical / optimized / legacy 三个 run entrypoint 的 unsafe output collision 必须在任何 output directory、lock、cache/session 或 stage write 前失败；
- resolve/coarse/fine/transition direct CLI 的 unsafe output collision 必须在 stage artifact 写入前失败；
- coarse feature-cache tree 不能进入或反向包含 task/upstream inputs；
- primary-stage `--out` 与 `--artifact-out` 必须 pairwise distinct；
- materializer collision 在原实现首次写入前失败，被保护输入字节不变；
- `--help` 与正常 run/asset/coarse/fine/transition/cut/overlap/combined E2E 不因安全 wrapper 退化；
- review template/apply 的碰撞不会改变被保护输入字节；
- release manifest 碰撞不会改变被保护输入字节；
- malformed review/rebuild/render authority count 被拒绝；
- final-render config/evidence/QA authority 任一层不一致时 release 被拒绝；
- 正常 render/review/release/evaluation 路径不因 guard 产生 false positive。

真实歌曲名、歌词、cue、timestamp、audio 或私有路径不得进入本文件或公开测试。
