# 样例运行产物（2026-10）

由以下命令生成，可作为验收证据直接阅读：

```bash
cd goal-agent
python scripts/generate_data.py
python -m bizgoal run --data data/business_2026_10.csv --out examples/2026-10
```

核心结果：

- 基线：毛利率 25.00%，DSO 48.50 天，应收 1,333 万
- 目标：毛利率 28.00%（+3pp），DSO ≤45 天
- 预测：毛利率 28.25%，DSO 44.24 天，回款 117 万
- 动作：4 个（毛利 2 / 回款 2），12 项自检全部通过

文件说明：

| 文件 | 内容 |
| --- | --- |
| `report.md` | 完整报告：口径、清洗、诊断、动作、闭环校验、兜底 |
| `summary.json` | 机读摘要与校验统计 |
| `cleaning_report.json` | 三类异常与管理口径逐行数据 |
| `metrics_before.json` | 报表/管理两套基线指标与公式 |
| `plan.json` | 选中动作、基线/保守情景预测、备选动作 |
| `actions_library.json` | 全部候选动作及假设、金额、负责人 |
| `action_receipts.json` | 沙盘执行回执 |
| `metrics_after.json` | 复核后的最终指标与缺口 |
| `events.jsonl` / `run.log` | 结构化事件与人读日志 |
| `llm_trace.json` | 模型配置、调用与降级记录（本样例未配置密钥） |
