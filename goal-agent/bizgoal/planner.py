"""动作组合搜索与闭环校验。

- 基线情景：满足 毛利率 >= 目标 + 0.2pp，且 DSO <= 目标；
- 保守情景：所有动作效果按 95% 实现，仍满足 毛利率 >= 目标、DSO <= 目标；
- 在满足条件的组合中，优先动作数最少，其次保守情景安全垫最大。

搜索是穷举的：动作库规模很小（个位数到十几条），穷举可复现、可解释，
不做黑盒优化。动作数超过 16 时会退化为贪心，并在日志中提示。
"""

from __future__ import annotations

import itertools
from typing import Any

from .config import CONSERVATIVE_REALIZATION, MARGIN_BUFFER_PP, PLANNER_MAX_ACTIONS
from .models import Action, Goal, Metrics, Plan

_RISK_SCORE = {"低": 0, "中": 1, "高": 2}
_EFFORT_SCORE = {"低": 0, "中": 1, "高": 2}


def _margin_disjoint(actions: list[Action]) -> bool:
    """同一合同只允许一个毛利杠杆，避免降本/提价叠加重复计量。"""
    used: set[str] = set()
    for action in actions:
        if action.kind != "margin":
            continue
        ids = set(action.contract_ids)
        if used & ids:
            return False
        used |= ids
    return True


def _best_effort(actions: list[Action]) -> list[Action]:
    """不可达时的兼容性最优集合：毛利杠杆按增厚毛利从大到小贪心且合同不重叠。"""
    chosen: list[Action] = []
    used: set[str] = set()
    for action in sorted(
        (a for a in actions if a.kind == "margin"),
        key=lambda a: (a.delta_gp, _RISK_SCORE.get(a.risk, 1)),
        reverse=True,
    ):
        ids = set(action.contract_ids)
        if ids & used:
            continue
        chosen.append(action)
        used |= ids
    chosen.extend(sorted((a for a in actions if a.kind == "collection"), key=lambda a: a.expected_cash, reverse=True))
    return chosen


def forecast_subset(base: Metrics, actions: list[Action], realization: float = 1.0) -> dict[str, float]:
    """按动作测算下一期指标。回款动作只改善应收，不影响损益。"""
    revenue = base.revenue + sum(round(a.delta_revenue * realization, 2) for a in actions)
    cost = base.cost + sum(round(a.delta_cost * realization, 2) for a in actions)
    cash = sum(round(a.expected_cash * realization, 2) for a in actions)
    ar = max(0.0, round(base.ar - cash, 2))
    gross_profit = round(revenue - cost, 2)
    margin = gross_profit / revenue if revenue > 0 else 0.0
    dso = ar / revenue * base.days_in_month if revenue > 0 else 0.0
    return {
        "revenue": round(revenue, 2),
        "cost": round(cost, 2),
        "gross_profit": gross_profit,
        "gross_margin": round(margin, 6),
        "ar": ar,
        "collected": round(cash, 2),
        "dso_days": round(dso, 2),
    }


def _meets(
    forecast: dict[str, float],
    goal: Goal,
    require_buffer: bool,
) -> bool:
    margin_floor = goal.target_margin + (MARGIN_BUFFER_PP / 100 if require_buffer else 0.0)
    return forecast["gross_margin"] >= margin_floor - 1e-12 and forecast["dso_days"] <= goal.dso_target_days + 1e-9


def _cushion(forecast: dict[str, float], goal: Goal) -> tuple[float, float]:
    margin_cushion = (forecast["gross_margin"] - goal.target_margin) * 100
    dso_cushion = goal.dso_target_days - forecast["dso_days"]
    return margin_cushion, dso_cushion


def _gaps(forecast: dict[str, float], goal: Goal, base: Metrics) -> list[str]:
    gaps: list[str] = []
    margin_gap = (goal.target_margin - forecast["gross_margin"]) * 100
    if margin_gap > 1e-9:
        amount = max(0.0, goal.target_margin * forecast["revenue"] - forecast["gross_profit"])
        gaps.append(f"毛利率缺口 {margin_gap:.2f}pp（约 {amount/10_000:.1f} 万元毛利）")
    dso_gap = forecast["dso_days"] - goal.dso_target_days
    if dso_gap > 1e-9:
        amount = max(0.0, (forecast["dso_days"] - goal.dso_target_days) * forecast["revenue"] / base.days_in_month)
        gaps.append(f"DSO 缺口 {dso_gap:.2f} 天（约 {amount/10_000:.1f} 万元应收）")
    return gaps


def select_plan(
    base: Metrics,
    goal: Goal,
    actions: list[Action],
    excluded_ids: list[str] | None = None,
) -> Plan:
    excluded_ids = excluded_ids or []
    candidates = [a for a in actions if a.id not in excluded_ids]
    checks: dict[str, Any] = {
        "margin_buffer_pp": MARGIN_BUFFER_PP,
        "conservative_realization": CONSERVATIVE_REALIZATION,
        "excluded_ids": excluded_ids,
        "candidate_count": len(candidates),
    }

    if not candidates:
        forecast = forecast_subset(base, [])
        return Plan(
            selected=[],
            backups=[],
            target_margin=goal.target_margin,
            target_dso_days=goal.dso_target_days,
            forecast=forecast,
            conservative=forecast_subset(base, [], CONSERVATIVE_REALIZATION),
            meets=False,
            gaps=["没有可用动作：请检查数据或动作库配置"],
            checks=checks,
            search_stats={"evaluated": 0, "feasible": 0},
            excluded_ids=excluded_ids,
        )

    if len(candidates) > PLANNER_MAX_ACTIONS:
        candidates = candidates[:PLANNER_MAX_ACTIONS]
        checks["truncated_to"] = PLANNER_MAX_ACTIONS

    best: tuple[tuple, list[Action], dict[str, float], dict[str, float]] | None = None
    evaluated = 0
    feasible = 0
    for size in range(1, len(candidates) + 1):
        for subset_tuple in itertools.combinations(candidates, size):
            evaluated += 1
            subset = list(subset_tuple)
            if not _margin_disjoint(subset):
                continue
            base_forecast = forecast_subset(base, subset, 1.0)
            if not _meets(base_forecast, goal, require_buffer=True):
                continue
            cons_forecast = forecast_subset(base, subset, CONSERVATIVE_REALIZATION)
            if not _meets(cons_forecast, goal, require_buffer=False):
                continue
            feasible += 1
            margin_cushion, dso_cushion = _cushion(cons_forecast, goal)
            risk_sum = sum(_RISK_SCORE.get(a.risk, 1) for a in subset)
            effort_sum = sum(_EFFORT_SCORE.get(a.effort, 1) for a in subset)
            key = (
                len(subset),
                -round(margin_cushion, 6),
                risk_sum,
                effort_sum,
                -round(dso_cushion, 6),
            )
            if best is None or key < best[0]:
                best = (key, subset, base_forecast, cons_forecast)

    stats = {"evaluated": evaluated, "feasible": feasible}

    if best is not None:
        _, subset, base_forecast, cons_forecast = best
        selected = sorted(subset, key=lambda a: (a.kind != "margin", a.id))
        selected_ids = {a.id for a in selected}
        backups = [
            a
            for a in actions
            if a.id not in selected_ids and a.id not in excluded_ids
        ]
        backups.sort(key=lambda a: (a.kind != "margin", -a.delta_gp, -a.expected_cash))
        return Plan(
            selected=selected,
            backups=backups,
            target_margin=goal.target_margin,
            target_dso_days=goal.dso_target_days,
            forecast=base_forecast,
            conservative=cons_forecast,
            meets=True,
            gaps=[],
            checks={**checks, "base_meets": True, "conservative_meets": True},
            search_stats=stats,
            excluded_ids=excluded_ids,
        )

    # 兼容性最优动作仍不达标：返回“尽力而为”测算，供报告说明缺口，不执行动作。
    best_actions = _best_effort(candidates)
    best_effort = forecast_subset(base, best_actions, 1.0)
    best_cons = forecast_subset(base, best_actions, CONSERVATIVE_REALIZATION)
    gaps = _gaps(best_effort, goal, base)
    if excluded_ids:
        gaps.append(f"已排除动作：{', '.join(excluded_ids)}")
    return Plan(
        selected=[],
        backups=sorted(best_actions, key=lambda a: (a.kind != "margin", -a.delta_gp, -a.expected_cash)),
        target_margin=goal.target_margin,
        target_dso_days=goal.dso_target_days,
        forecast=best_effort,
        conservative=best_cons,
        meets=False,
        gaps=gaps or ["未找到可同时满足毛利与回款目标的动作组合"],
        checks={
            **checks,
            "base_meets": False,
            "conservative_meets": False,
            "best_effort_compatible_actions": [a.id for a in best_actions],
        },
        search_stats=stats,
        excluded_ids=excluded_ids,
    )


def verify_actions(base: Metrics, actions: list[Action], goal: Goal) -> tuple[dict[str, float], dict[str, float], bool, list[str]]:
    """执行后复核：只按真正执行成功的动作计算最终指标。"""
    forecast = forecast_subset(base, actions, 1.0)
    conservative = forecast_subset(base, actions, CONSERVATIVE_REALIZATION)
    meets = _meets(forecast, goal, require_buffer=False)
    gaps = _gaps(forecast, goal, base)
    return forecast, conservative, meets, gaps
