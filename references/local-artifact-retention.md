# 本地 artifact 保留与清理规则

状态：本地维护规则
适用：`private/`、`output/`、运行缓存与实验产物

目标是在不破坏可复现性、blind 隔离和 production authority 的前提下控制本地体积。任何自动清理都必须 fail closed：不能仅凭目录名、版本号或“看起来较旧”判断可删除。

## KEEP：默认长期保留

以下内容不得自动删除：

- 当前任务输入及其 hash-bound task manifest、run config、language/lyric-role/middle-cut map；
- authoritative production final 的 SRT、QA、release、artifact、audit 和必要日志；
- calibration/blind split 定义、truth lock、baseline/candidate lock、gate 结果及匿名 reference；
- 已被文档、最终 QA/release 或回归基线明确引用的运行产物；
- 无法从仍保留输入与当前代码确定性重建的人工确认、editor reconciliation、review decision、structural truth/evidence。

## ARCHIVE：可移出活跃工作目录，但不应直接销毁

满足全部条件后，旧 raw run / superseded candidate 可以归档：

1. 已有更新的 authoritative final 或明确的 superseding run；
2. task fingerprint / 输入身份已确认一致；
3. 关键 timeline、issue、transition 或最终评估已完成等价/非回归验证；
4. 没有文档、lock、release、QA 或测试直接依赖该目录；
5. 归档动作不会把 blind truth 暴露到非 blind 区域。

归档只改变存放位置，不改变其历史结论。

## REGENERABLE：可优先清理

在没有运行进程使用且输入仍存在时，可清理：

- `__pycache__/`、`*.pyc`、`.pytest_cache/`；
- feature cache、resume sidecar、临时 decode/cache 文件；
- 已完成实验的重复 stdout/stderr 临时副本；
- 明确标记为 disposable execution optimization、且不承担最终审计证据的缓存。

如果缓存本身被 benchmark、性能回归或 artifact lineage 明确引用，则升级为 KEEP/ARCHIVE，不按本条删除。

## 实验产物

消融、shadow、diagnostic 结果必须与 production final 分目录保存，并明确标记 `calibration_only` / `blind_data_read` 等边界。实验未通过正式 gate 前不得覆盖 production final，也不得因为实验失败而修改既有 release authority。

## 禁止的清理方式

- 不按“版本号较旧”批量删 `output/`；
- 不删除 blind/reference/truth 以换取目录整洁；
- 不把 `private/` 上传到普通源码仓库；
- 不在存在活跃 runner/handoff 时删除其 cache/output；
- 不把删除历史证据作为降低项目复杂度的手段。复杂度应优先从 active code path、重复入口和文档歧义中消除。
