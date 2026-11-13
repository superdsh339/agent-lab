"""把老板口述转成结构化目标。

规则解析覆盖常见中文说法；解析失败时明确报错，或由 LLM 解析器兜底
（LLM 只输出两个数字，最终目标金额仍由代码根据基线计算）。
"""

from __future__ import annotations

import re

from .errors import GoalParseError
from .models import Goal

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_NUM = r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)"

_MARGIN_PATTERNS = [
    re.compile(
        rf"毛利(?:率)?[^，。；;]{{0,16}}?(?:提升|提高|提|增加|增长|\+)\s*{_NUM}\s*(?:个)?(?:百分点|点|pp)",
    ),
    re.compile(
        rf"毛利(?:率)?[^，。；;]{{0,16}}?{_NUM}\s*(?:个)?(?:百分点|点|pp)",
    ),
    re.compile(
        rf"毛利(?:率)?[^，。；;]{{0,16}}?(?:提升|提高|提|增加|增长)\s*{_NUM}\s*%",
    ),
]

_DSO_PATTERNS = [
    re.compile(
        rf"(?:回款周期|回款天数|DSO)[^，。；;]{{0,18}}?(?:压到|压至|降到|降至|控制在?|不超过|缩短到?|≤|小于)\s*{_NUM}\s*天",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:回款周期|回款天数|DSO)[^，。；;]{{0,18}}?{_NUM}\s*天(?:以内|内)?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"回款[^，。；;]{{0,14}}?(?:压到|压至|降到|降至|控制在?|不超过|≤|小于)\s*{_NUM}\s*天",
    ),
    re.compile(
        rf"回款[^，。；;]{{0,14}}?{_NUM}\s*天(?:以内|内)",
    ),
    re.compile(
        rf"{_NUM}\s*天(?:以内|内)?[^，。；;]{{0,8}}?回款",
    ),
]


def _to_number(token: str) -> float:
    token = token.strip()
    try:
        return float(token)
    except ValueError:
        pass
    if "十" in token:
        tens_part, _, ones_part = token.partition("十")
        tens = _CN_DIGITS.get(tens_part, 1) if tens_part else 1
        ones = _CN_DIGITS.get(ones_part, 0) if ones_part else 0
        return float(tens * 10 + ones)
    if token in _CN_DIGITS:
        return float(_CN_DIGITS[token])
    raise GoalParseError(f"无法识别数字：{token}")


def _first_match(patterns: list[re.Pattern], text: str) -> tuple[float, str] | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _to_number(match.group(1)), match.group(0)
    return None


def parse_goal(text: str, baseline_margin: float) -> Goal:
    """解析口述目标；baseline_margin 为 0-1 的小数。"""
    if not text or not text.strip():
        raise GoalParseError("目标口述为空，无法解析")
    if not (0 < baseline_margin < 1):
        raise GoalParseError(f"基线毛利率不合法：{baseline_margin}")

    margin_match = _first_match(_MARGIN_PATTERNS, text)
    if margin_match is None:
        raise GoalParseError(
            "未识别到毛利提升幅度。支持示例："
            "「本月毛利提 3 个点」「毛利率提升 3 个百分点」"
        )
    dso_match = _first_match(_DSO_PATTERNS, text)
    if dso_match is None:
        raise GoalParseError(
            "未识别到回款周期目标。支持示例："
            "「回款周期压到 45 天以内」「DSO 不超过 45 天」"
        )

    delta_pp, margin_phrase = margin_match
    dso_target, dso_phrase = dso_match
    if not (0 < delta_pp <= 30):
        raise GoalParseError(f"毛利提升幅度 {delta_pp}pp 超出合理范围 0-30pp")
    if not (1 <= dso_target <= 180):
        raise GoalParseError(f"回款周期目标 {dso_target} 天超出合理范围 1-180 天")

    target_margin = baseline_margin + delta_pp / 100.0
    if target_margin >= 1:
        raise GoalParseError(f"目标毛利率 {target_margin:.2%} 超过 100%，请检查口述")

    notes = [f"命中毛利表述：「{margin_phrase}」", f"命中回款表述：「{dso_phrase}」"]
    if "%" in margin_phrase:
        notes.append("口述用 % 表达提升幅度，按「百分点」口径解释")
    return Goal(
        raw_text=text.strip(),
        margin_delta_pp=delta_pp,
        dso_target_days=dso_target,
        baseline_margin=baseline_margin,
        target_margin=target_margin,
        parser="rule",
        notes=notes,
    )


def goal_from_numbers(
    text: str,
    delta_pp: float,
    dso_target_days: float,
    baseline_margin: float,
    parser: str,
    notes: list[str] | None = None,
) -> Goal:
    """由 LLM 解析出的两个数字构造目标，基线换算与校验仍在这里完成。"""
    if not (0 < delta_pp <= 30):
        raise GoalParseError(f"毛利提升幅度 {delta_pp}pp 超出合理范围 0-30pp")
    if not (1 <= dso_target_days <= 180):
        raise GoalParseError(f"回款周期目标 {dso_target_days} 天超出合理范围 1-180 天")
    target_margin = baseline_margin + delta_pp / 100.0
    if target_margin >= 1:
        raise GoalParseError(f"目标毛利率 {target_margin:.2%} 超过 100%")
    return Goal(
        raw_text=text.strip(),
        margin_delta_pp=float(delta_pp),
        dso_target_days=float(dso_target_days),
        baseline_margin=baseline_margin,
        target_margin=target_margin,
        parser=parser,
        notes=notes or [],
    )
