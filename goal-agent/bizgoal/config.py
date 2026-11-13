"""全局常量与默认配置。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "business_2026_10.csv"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "data_manifest.json"

# 数据所属期间（2026 年 10 月），作为账龄和 DSO 的统一截止日。
AS_OF = date(2026, 10, 31)
PERIOD_LABEL = "2026-10"
DEFAULT_GOAL = "本月毛利提 3 个点，回款周期压到 45 天以内"

COLUMNS = [
    "客户",
    "合同号",
    "合同金额",
    "本月确认收入",
    "本月成本",
    "本月回款",
    "开票日",
    "约定账期",
    "应收余额",
]

MONEY_COLUMNS = ["合同金额", "本月确认收入", "本月成本", "本月回款", "应收余额"]
INT_COLUMNS = ["约定账期"]
CUSTOMER_COLUMN = "客户"
CONTRACT_COLUMN = "合同号"
DATE_COLUMN = "开票日"
AR_COLUMN = "应收余额"

# 规划器要求：基线情景至少留 0.2 个百分点的安全垫；
# 保守情景（所有动作效果按 95% 实现）仍需达标。
MARGIN_BUFFER_PP = 0.2
CONSERVATIVE_REALIZATION = 0.95
PLANNER_MAX_ACTIONS = 12

# 账龄分桶（天）。
AGING_BUCKETS = [
    ("0-30", 0, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("91-180", 91, 180),
    (">180", 181, 10**6),
]

# 模型服务：默认国产 API（DeepSeek），可通过环境变量切换到私有化部署。
LLM_DEFAULT_PROVIDER = "deepseek"
LLM_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
LLM_DEFAULT_MODEL = "deepseek-chat"
LLM_DEFAULT_TIMEOUT = 25.0
LLM_MAX_RETRIES = 1
