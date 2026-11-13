# 经营目标智能体原型（私有化环境）

把老板的一句话「本月毛利提 3 个点，回款周期压到 45 天以内」，拆成**可量化动作**，并跑通「目标 → 动作 → 指标 → 复核」闭环。

- 数字全部由确定性代码计算；LLM（DeepSeek / 私有化 Qwen）只做目标解析兜底与摘要，模型不可用也能跑完。
- 数据是合成的 2026-10 经营数据：18 行、9 个客户、三类异常各 1 笔。
- 执行层是沙盘模拟，不写真实 ERP/CRM；失败有日志、有重规划、有退出码。

## 快速开始

要求：Python 3.10+，无第三方依赖。

```bash
cd goal-agent
python scripts/generate_data.py          # 生成 data/business_2026_10.csv
python -m bizgoal demo                   # 端到端运行，输出 runs/demo/
python -m unittest discover -s tests -v  # 36 项测试
bash scripts/demo.sh                     # 录屏用：正常 + 三类兜底依次跑
```

打开 `runs/demo/report.md` 查看完整报告；`runs/demo/summary.json` 是机读摘要。

## 一个可复现的结果

| 指标 | 基线（10 月管理口径） | 目标 | 预测 |
| --- | --- | --- | --- |
| 毛利率 | 25.00% | 28.00%（+3pp） | **28.25%**（保守 95%：28.09%） |
| DSO | 48.50 天 | ≤45 天 | **44.24 天**（保守：44.46） |
| 期末应收 | 1,333 万 | ≤1,236.77 万 | 1,216 万 |

动作 4 个：重点供应商降本 7%（+22.75 万毛利）、高成本率合同降本 4%（+4.96 万）、华信 258 天清收 60 万、远达 169 天清收 57 万。12 项自动校验全部通过。

## 数据与口径（摘要）

- 字段：客户、合同号、合同金额、本月确认收入、本月成本、本月回款、开票日、约定账期、应收余额。
- 三类异常：HT-2026-0107 收入跨期（开票 11 月、确认在 10 月）；HT-2026-0103-R 退款/折让 18 万；HT-2026-0101 账龄 258 天、应收 150 万。
- 收入确认：权责发生制，以开票日所在期间确认；跨期按管理口径冲回。
- 回款：等于实际到账（银行流水），退款为负数。
- 账龄：从开票日起算，截至 2026-10-31；DSO = 期末应收 / 本月收入 × 31。
- 场景：10 月为基线，目标落在 11 月计划；详见 [`docs/01_口径说明.md`](docs/01_口径说明.md)。

## 故障与兜底演示

```bash
# 模型不可用：规则模板摘要，主流程继续，退出码 0
python -m bizgoal run --out runs/demo-llm --chaos llm-down

# 动作失败：剔除失败动作，重新规划，退出码 0
python -m bizgoal run --out runs/demo-recover --chaos action-fail

# 坏数据：不猜数，退出码 2，失败报告 + 日志
python -m bizgoal run --out runs/demo-bad --chaos data-corrupt; echo "exit=$?"

# 目标不可达：输出缺口，不执行动作，退出码 3
python -m bizgoal run --out runs/demo-hard --goal "本月毛利提 12 个点，回款周期压到 20 天以内"; echo "exit=$?"
```

## 模型选型

首选 **DeepSeek-V3（国产 API，`deepseek-chat`）**；私有化备选 **Ollama/vLLM + Qwen2.5-7B-Instruct**。选型理由、环境变量、隐私边界与「本环境没有密钥时走规则降级」的真实状态，见 [`docs/03_模型选型.md`](docs/03_模型选型.md)。

```bash
export DEEPSEEK_API_KEY=sk-...
python -m bizgoal llm-check
python -m bizgoal run --out runs/with-llm
```

## 目录

```
goal-agent/
├── bizgoal/                 # 原型代码（标准库）
│   ├── data_gen.py          # 固定种子数据 + 清单
│   ├── cleaning.py          # 契约校验、三类异常、两套口径
│   ├── metrics.py           # 毛利率、回款率、DSO、账龄
│   ├── goal.py              # 口述 → 结构化目标
│   ├── diagnosis.py         # 缺口拆到合同/账龄
│   ├── actions.py           # 毛利/回款动作库
│   ├── planner.py           # 穷举组合 + 双情景校验
│   ├── execution.py         # 沙盘执行、回执、幂等账本
│   ├── llm.py               # DeepSeek / Ollama 兼容客户端 + 数字白名单
│   ├── report.py            # Markdown 报告
│   └── pipeline.py / cli.py # 编排与命令行
├── scripts/generate_data.py # 造数脚本
├── data/                    # 数据与 data_manifest.json
├── docs/                    # 口径、设计、模型、自评、录屏脚本
├── tests/                   # 36 项单测/端到端测试
└── examples/2026-10/        # 一次完整运行的产物（报告、计划、回执）
```

## 验收对照

| 要求 | 证据 |
| --- | --- |
| 按契约造数，≥15 行、8 客户 | `data/business_2026_10.csv`：18 行 / 9 客户；`scripts/generate_data.py` |
| 三类异常各一笔 | `cleaning_report.json` 与报告第 2 节；`data/data_manifest.json` |
| 口径写清 | [`docs/01_口径说明.md`](docs/01_口径说明.md)；报告第 1-2 节 |
| 目标 → 动作 → 指标闭环 | 报告第 4-5 节；`plan.json`、`action_receipts.json`、`metrics_after.json` |
| 数据清洗 / 任务拆分 / 指标测量 | [`docs/02_设计说明.md`](docs/02_设计说明.md)；报告第 2-5 节 |
| 失败有日志、有兜底 | `--chaos` 三种演示；`run.log`、`events.jsonl`、失败报告；设计说明第 6 节 |
| 模型选型与理由 | [`docs/03_模型选型.md`](docs/03_模型选型.md) |
| 自评表 | [`docs/04_自评表.md`](docs/04_自评表.md)（如实标注 AI 参与） |
| 3 分钟录屏 | 脚本 [`docs/05_录屏脚本.md`](docs/05_录屏脚本.md)，需本人开麦录制 |

## 已知边界

- 单月数据无法还原期初应收，DSO 用「期末应收/月收入×31」近似；真实上线应改滚动 12 个月口径。
- 动作效果（降本率、提价保留率、回收率）是显式管理假设，需在执行后回填实际。
- 沙盘执行不写真实系统；生产化需接审批、合同、回款系统及权限/审计。
- 演示数据为合成数据，不含真实客户信息；模型只接收聚合事实，不接收客户名。
- 同一输出目录重跑会复用 `execution_ledger.json` 的执行账本（幂等）；换数据/目标请用新目录，或删除该账本后再跑。
