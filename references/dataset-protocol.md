# 私有训练与回归数据协议

真实歌词、歌曲和音频可以在取得授权且仓库保持私有的前提下用于研究、校准与回归。源码仓库仍应保持轻量：原始媒体、逐字歌词、人工答案和模型缓存放在 `private/`、Git LFS 或独立私有数据仓库，不进入普通源码提交。

## 数据拆分

- `train`：允许用于训练或生成候选规则。
- `calibration`：只用于选择阈值、比较模型和校准置信度。
- `blind_test`：不得用于写规则、调阈值或挑模型，只用于最终评估。
- 按歌曲、艺人和版本隔离拆分；同曲 remix、现场版、伴奏版和不同字幕版本不得横跨训练与盲测集。
- 每次算法升级都保存固定盲测集结果，不能只报告本次表现最好的任务。

## 推荐的任务标注

每个样本至少包含：语言、授权来源记录、混剪音频、规范歌词、参考 SRT、歌曲区间、版本标识和人工确认状态。高价值附加标注包括：准确边界、重复段、合并/拆分 cue、源音频剪切点、BPM/变速、可听但不收录的衬词、不可辨片段和错误类型。

不要把“有大量歌词文件”等同于可训练数据。对本项目最有价值的是成对标注：同一版本音频、正确歌词、准确时间和明确错误类型。没有时间或版本对应关系的大量文本主要适合做词源候选，不能直接证明字幕边界正确。

## 数据集 manifest

建议放在 `private/datasets/<名称>/dataset.json`：

```json
{
  "schema_version": "1.0",
  "dataset": "authorized-lyric-alignment",
  "cases": [
    {
      "id": "opaque-case-id",
      "split": "blind_test",
      "language": "mixed",
      "reference_srt": "reference/case.srt",
      "predicted_srt": "predictions/case.srt",
      "qa_json": "predictions/case_QA.json",
      "audio_duration_seconds": 1800,
      "runtime_seconds": 240,
      "expected_cut_ids": ["cut-1"],
      "predicted_cut_ids": ["cut-1"]
    }
  ]
}
```

路径相对 dataset manifest 所在目录。`id` 使用不含歌词、曲名和艺人的稳定匿名标识。

## 评估

运行：

```powershell
python scripts/evaluate_dataset.py `
  --dataset "private/datasets/<名称>/dataset.json" `
  --out "output/evaluation/<版本>.json"
```

输出只包含聚合数字和匿名 case id，不包含歌词正文。核心指标包括：歌词单位 precision/recall/F1、cue 文本完全匹配率、边界 MAE/P95、每 10 分钟复核候选数、每音频分钟运行耗时、发布通过率和剪切检测 precision/recall。

算法升级应同时满足：固定盲测集总体不退化、主要语言不出现明显退化、发布候选密度不因放宽门槛而虚假下降、运行成本处于可接受范围。无法自动确认的物理不可辨片段必须拒绝发布，不能通过猜测追求表面上的 100%。
