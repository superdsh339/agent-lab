"""业务异常类型。"""

from __future__ import annotations


class BizGoalError(Exception):
    """所有可预期错误的基类。"""


class DataQualityError(BizGoalError):
    """数据不满足契约，无法安全计算。"""

    def __init__(self, message: str, issues: list[str] | None = None):
        super().__init__(message)
        self.issues = issues or []


class GoalParseError(BizGoalError):
    """目标口述无法解析为结构化指标。"""


class LLMError(BizGoalError):
    """模型服务不可用或输出不合法。"""


class PlanningError(BizGoalError):
    """规划过程出现不可恢复的问题。"""
