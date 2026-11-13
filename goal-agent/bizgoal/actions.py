"""动作库：从数据中生成可量化、可执行、可归因的经营动作。

动作全部来自基线数据，不预写死具体客户：
- 毛利侧：重点供应商重谈、成本率偏高合同降本、低毛利合同提价、高毛利客户追加订单；
- 回款侧：按账龄分档对逾期应收生成专项清收动作。

每个动作都带「假设 + 置信度 + 负责人 + 截止日」，金额可被独立复算。
"""

from __future__ import annotations

from datetime import date, timedelta

from .models import Action, CleanResult, Metrics


def _next_month(as_of: date) -> date:
    if as_of.month == 12:
        return date(as_of.year + 1, 1, 1)
    return date(as_of.year, as_of.month + 1, 1)


def _contract_suffix(contract_id: str) -> str:
    return contract_id.replace("HT-2026-", "").replace("HT-", "")


def _recovery_rate(aging_days: int) -> float:
    if aging_days > 180:
        return 0.40
    if aging_days > 120:
        return 0.30
    if aging_days > 90:
        return 0.15
    if aging_days > 60:
        return 0.10
    return 0.0


def _collection_confidence(aging_days: int) -> float:
    if aging_days > 180:
        return 0.60
    if aging_days > 120:
        return 0.70
    if aging_days > 90:
        return 0.75
    return 0.80


def _money(value: float) -> str:
    return f"{value:,.0f}"


def build_action_library(clean: CleanResult, metrics: Metrics, as_of: date) -> list[Action]:
    if metrics.revenue <= 0:
        return []
    plan_month = _next_month(as_of)
    due_early = (plan_month + timedelta(days=14)).isoformat()
    due_mid = (plan_month + timedelta(days=19)).isoformat()
    due_late = date(plan_month.year, plan_month.month, 28).isoformat()
    actions: list[Action] = []

    active = [row for row in clean.rows if row.revenue > 0 and row.cost > 0]

    # 1) 重点供应商成本重谈：取成本最高的 3 个合同，目标降本 7%。
    top_cost = sorted(active, key=lambda r: r.cost, reverse=True)[:3]
    supplier_ids = {row.contract_id for row in top_cost}
    if top_cost:
        total_cost = sum(row.cost for row in top_cost)
        saving = round(total_cost * 0.07, 2)
        actions.append(
            Action(
                id="M-SUP-01",
                kind="margin",
                title="重点供应商成本重谈（降本 7%）",
                rationale=(
                    f"对成本最高的 {len(top_cost)} 个合同（合计成本 {_money(total_cost)} 元）"
                    f"启动供应商年框重谈，按 7% 降本测算，可增厚毛利 {_money(saving)} 元。"
                ),
                contract_ids=[row.contract_id for row in top_cost],
                customer_names=[row.customer for row in top_cost],
                owner="采购部",
                due_date=due_early,
                confidence=0.85,
                effort="高",
                risk="中",
                delta_revenue=0.0,
                delta_cost=-saving,
                assumptions=[
                    "按合同级采购成本 7% 测算，供应商接受并自计划月生效",
                    "不改变交付范围与售价，节省额全部进入毛利",
                ],
                evidence={
                    "合同成本": {row.contract_id: row.cost for row in top_cost},
                    "合计成本": total_cost,
                    "降本率": 0.07,
                },
            )
        )

    # 2) 高成本率合同降本专项：成本率 >= 80% 且毛利偏低，目标降本 4%。
    inefficient = [
        row
        for row in active
        if row.contract_id not in supplier_ids
        and row.cost / row.revenue >= 0.80
        and (row.revenue - row.cost) / row.revenue < 0.25
    ]
    if inefficient:
        base_cost = sum(row.cost for row in inefficient)
        saving = round(base_cost * 0.04, 2)
        actions.append(
            Action(
                id="M-WASTE-01",
                kind="margin",
                title="高成本率合同降本专项（降本 4%）",
                rationale=(
                    f"{len(inefficient)} 个合同成本率超过 80%，通过排产优化、外包比价降低 4% 成本，"
                    f"预计增厚毛利 {_money(saving)} 元。"
                ),
                contract_ids=[row.contract_id for row in inefficient],
                customer_names=[row.customer for row in inefficient],
                owner="运营部",
                due_date=due_mid,
                confidence=0.80,
                effort="中",
                risk="低",
                delta_revenue=0.0,
                delta_cost=-saving,
                assumptions=[
                    "按合同成本 4% 测算，排产优化当月生效",
                    "不降低交付质量，不产生一次性裁员支出",
                ],
                evidence={
                    "成本率": {row.contract_id: round(row.cost / row.revenue, 4) for row in inefficient},
                    "合计成本": base_cost,
                    "降本率": 0.04,
                },
            )
        )

    # 3) 低毛利合同提价：最低毛利合同，提价 5%，预计 10% 客户流失。
    low_margin = sorted(
        (row for row in active if (row.revenue - row.cost) / row.revenue < 0.20),
        key=lambda row: (row.revenue - row.cost) / row.revenue,
    )
    if low_margin:
        row = low_margin[0]
        delta_revenue = round(row.revenue * 0.05 * 0.90, 2)
        actions.append(
            Action(
                id=f"M-PRICE-{_contract_suffix(row.contract_id)}",
                kind="margin",
                title=f"低毛利合同调价：{row.customer}（+5%）",
                rationale=(
                    f"{row.contract_id} 毛利率仅 {(row.revenue-row.cost)/row.revenue:.2%}，"
                    f"按提价 5%、保留 90% 收入测算，可增加收入 {_money(delta_revenue)} 元且不增加成本。"
                ),
                contract_ids=[row.contract_id],
                customer_names=[row.customer],
                owner="销售部",
                due_date=due_late,
                confidence=0.55,
                effort="中",
                risk="高",
                delta_revenue=delta_revenue,
                delta_cost=0.0,
                assumptions=[
                    "提价 5% 自计划月执行，预计 10% 收入流失",
                    "交付成本不随提价变化",
                ],
                evidence={"原毛利率": round((row.revenue - row.cost) / row.revenue, 4), "提价幅度": 0.05},
            )
        )

    # 4) 高毛利客户追加订单：最高毛利合同，追加 5% 收入与同比例成本。
    high_margin = sorted(
        (row for row in active if (row.revenue - row.cost) / row.revenue >= 0.30),
        key=lambda row: (row.revenue - row.cost) / row.revenue,
        reverse=True,
    )
    if high_margin:
        row = high_margin[0]
        delta_revenue = round(row.revenue * 0.05, 2)
        delta_cost = round(row.cost * 0.05, 2)
        actions.append(
            Action(
                id=f"M-DEMAND-{_contract_suffix(row.contract_id)}",
                kind="margin",
                title=f"高毛利客户追加订单：{row.customer}（+5%）",
                rationale=(
                    f"{row.contract_id} 毛利率 {(row.revenue-row.cost)/row.revenue:.2%}，"
                    f"争取追加 5% 订单，按同毛利率测算增厚毛利 {_money(delta_revenue-delta_cost)} 元。"
                ),
                contract_ids=[row.contract_id],
                customer_names=[row.customer],
                owner="销售部",
                due_date=due_late,
                confidence=0.60,
                effort="中",
                risk="高",
                delta_revenue=delta_revenue,
                delta_cost=delta_cost,
                assumptions=[
                    "客户确认追加 5% 订单，交付成本同比例增加",
                    "新增订单在计划月内确认收入",
                ],
                evidence={"毛利率": round((row.revenue - row.cost) / row.revenue, 4), "追加比例": 0.05},
            )
        )

    # 5) 逾期应收专项清收：账龄 >= 60 天，按分档回收率测算，取金额最大 5 笔。
    candidates: list[Action] = []
    for row in clean.rows:
        if row.ar_balance <= 0:
            continue
        aging_days = (as_of - row.invoice_date).days
        rate = _recovery_rate(aging_days)
        if rate <= 0:
            continue
        expected_cash = round(row.ar_balance * rate, 2)
        overdue_days = max(0, aging_days - row.terms_days)
        if aging_days > 180:
            owner = "法务部+销售部"
            risk = "高"
            due = due_early
            bucket_note = ">180 天，需法务介入并评估分期/诉讼"
        elif aging_days > 120:
            owner = "销售部+财务部"
            risk = "中"
            due = due_mid
            bucket_note = "120-180 天，财务与销售联合上门催收"
        elif aging_days > 90:
            owner = "销售部"
            risk = "中"
            due = due_mid
            bucket_note = "90-120 天，销售负责人限期回收"
        else:
            owner = "销售部"
            risk = "低"
            due = due_late
            bucket_note = "60-90 天，纳入月度催收清单"
        candidates.append(
            Action(
                id=f"C-AR-{_contract_suffix(row.contract_id)}",
                kind="collection",
                title=f"{row.customer} {row.contract_id} 逾期{overdue_days}天专项清收",
                rationale=(
                    f"应收 {_money(row.ar_balance)} 元，账龄 {aging_days} 天、逾期 {overdue_days} 天；"
                    f"按所在账龄档回收率 {rate:.0%} 测算可回款 {_money(expected_cash)} 元。"
                ),
                contract_ids=[row.contract_id],
                customer_names=[row.customer],
                owner=owner,
                due_date=due,
                confidence=_collection_confidence(aging_days),
                effort="高" if aging_days > 180 else "中",
                risk=risk,
                delta_revenue=0.0,
                delta_cost=0.0,
                expected_cash=expected_cash,
                assumptions=[
                    f"{bucket_note}；按该档回收率 {rate:.0%} 测算，实际以到账为准",
                    "回款仅为现金流改善，不重复计入收入",
                ],
                evidence={
                    "开票日": row.invoice_date.isoformat(),
                    "约定账期": row.terms_days,
                    "账龄天数": aging_days,
                    "逾期天数": overdue_days,
                    "应收余额": row.ar_balance,
                    "回收率": rate,
                },
            )
        )
    candidates.sort(key=lambda action: action.expected_cash, reverse=True)
    actions.extend(candidates[:5])
    return actions
