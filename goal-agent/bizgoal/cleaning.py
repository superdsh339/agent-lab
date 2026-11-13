"""数据清洗、契约校验与三类异常识别。

原则：
- 结构性错误（缺列、重复合同、勾稽不成立）直接报错，绝不“猜”数据；
- 业务异常（跨期、退款、长账龄）只做可解释的处置，并保留原始值；
- 输出「报表口径」与「管理口径」两套汇总，便于老板看懂差异。
"""

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import date

from .config import AS_OF, PERIOD_LABEL
from .errors import DataQualityError
from .models import CleanResult, Finding, RawRow, Totals

ANOMALY_LABELS = {
    "REVENUE_CROSS_PERIOD": "收入跨期确认",
    "REFUND_OR_REBATE": "退款/折让",
    "AR_AGING_GT_180": "账龄超 180 天",
}

MAX_ISSUES_RETURNED = 20


def _period_bounds(as_of: date) -> tuple[date, date]:
    last_day = calendar.monthrange(as_of.year, as_of.month)[1]
    return date(as_of.year, as_of.month, 1), date(as_of.year, as_of.month, last_day)


def _totals(rows: list[RawRow]) -> Totals:
    return Totals(
        revenue=round(sum(r.revenue for r in rows), 2),
        cost=round(sum(r.cost for r in rows), 2),
        cash=round(sum(r.cash for r in rows), 2),
        ar=round(sum(r.ar_balance for r in rows), 2),
        rows=len(rows),
        customers=len({r.customer for r in rows}),
    )


def _validate(rows: list[RawRow], as_of: date) -> None:
    issues: list[str] = []
    if len(rows) < 15:
        issues.append(f"数据行数 {len(rows)} 少于契约要求的 15 行")
    customers = {r.customer for r in rows}
    if len(customers) < 8:
        issues.append(f"客户数 {len(customers)} 少于契约要求的 8 个")
    seen: set[str] = set()
    period_start, period_end = _period_bounds(as_of)
    for row in rows:
        prefix = f"{row.contract_id or '（空合同号）'}"
        if row.contract_id in seen:
            issues.append(f"{prefix}：合同号重复")
        seen.add(row.contract_id)
        if not row.customer or not row.contract_id:
            issues.append(f"第 {row.source_line} 行：客户或合同号为空")
        if row.contract_amount < 0 or row.cost < 0 or row.ar_balance < 0:
            issues.append(f"{prefix}：金额出现负数")
        if row.terms_days < 0 or row.terms_days > 365:
            issues.append(f"{prefix}：约定账期 {row.terms_days} 天不在合理范围 0-365")
        if row.revenue < 0:
            # 退款/折让红字行
            if row.cost != 0 or row.cash > 0 or row.ar_balance != 0:
                issues.append(f"{prefix}：退款行应满足成本=0、回款≤0、应收=0")
        else:
            if row.cash < 0:
                issues.append(f"{prefix}：非退款行回款为负")
            if row.revenue > row.contract_amount + 0.01:
                issues.append(f"{prefix}：本月确认收入大于合同金额")
            if period_start <= row.invoice_date <= period_end:
                expected_ar = row.revenue - row.cash
                if abs(expected_ar - row.ar_balance) > 0.01:
                    issues.append(
                        f"{prefix}：应收勾稽不成立（应收 {row.ar_balance:,.2f}，"
                        f"收入-回款 {expected_ar:,.2f}）"
                    )
            elif row.revenue != 0:
                # 允许的跨期异常，但不应有应收
                if row.ar_balance != 0:
                    issues.append(f"{prefix}：跨期收入行应收应为 0")
    if issues:
        shown = issues[:MAX_ISSUES_RETURNED]
        raise DataQualityError(f"数据校验未通过（{len(issues)} 处问题）", shown)


def clean(rows: list[RawRow], as_of: date = AS_OF) -> CleanResult:
    _validate(rows, as_of)
    period_start, period_end = _period_bounds(as_of)
    findings: list[Finding] = []
    adjusted_rows: list[RawRow] = []
    removed_revenue = 0.0
    removed_cost = 0.0

    for row in rows:
        adjusted = replace(row)
        aging_days = (as_of - row.invoice_date).days
        in_period = period_start <= row.invoice_date <= period_end

        if row.revenue > 0 and not in_period:
            if row.invoice_date > period_end:
                direction = "提前确认（开票在下期，收入记在本期）"
            else:
                direction = "延迟确认（开票在上期，收入记在本期）"
            removed_revenue += row.revenue
            removed_cost += row.cost
            findings.append(
                Finding(
                    code="REVENUE_CROSS_PERIOD",
                    severity="warning",
                    message=f"收入跨期确认：{direction}，按管理口径冲回本期",
                    contract_id=row.contract_id,
                    customer=row.customer,
                    adjustment={"revenue": -row.revenue, "cost": -row.cost},
                    evidence={
                        "开票日": row.invoice_date.isoformat(),
                        "本期": f"{period_start.isoformat()}~{period_end.isoformat()}",
                        "本月确认收入": row.revenue,
                        "本月成本": row.cost,
                    },
                )
            )
            adjusted.revenue = 0.0
            adjusted.cost = 0.0
            adjusted.adjusted = True
            adjusted.adjustment_note = "跨期收入与匹配成本已冲回，待开票期间确认"

        if row.revenue < 0:
            findings.append(
                Finding(
                    code="REFUND_OR_REBATE",
                    severity="info",
                    message="退款/折让：红字发票冲减收入并按实际退款冲减回款，保留净额口径",
                    contract_id=row.contract_id,
                    customer=row.customer,
                    adjustment={"revenue": row.revenue, "cash": row.cash},
                    evidence={
                        "开票日": row.invoice_date.isoformat(),
                        "本月确认收入": row.revenue,
                        "本月回款": row.cash,
                    },
                )
            )

        if row.ar_balance > 0 and aging_days > 180:
            findings.append(
                Finding(
                    code="AR_AGING_GT_180",
                    severity="warning",
                    message=f"账龄超 180 天：应收 {row.ar_balance:,.0f} 元，账龄 {aging_days} 天，纳入专项清收",
                    contract_id=row.contract_id,
                    customer=row.customer,
                    evidence={
                        "开票日": row.invoice_date.isoformat(),
                        "账龄天数": aging_days,
                        "逾期天数": max(0, aging_days - row.terms_days),
                        "应收余额": row.ar_balance,
                    },
                )
            )

        adjusted_rows.append(adjusted)

    reported = _totals(rows)
    adjusted_totals = _totals(adjusted_rows)
    stats = {
        "period": PERIOD_LABEL,
        "as_of": as_of.isoformat(),
        "rows": reported.rows,
        "customers": reported.customers,
        "anomaly_counts": {
            code: sum(1 for f in findings if f.code == code) for code in ANOMALY_LABELS
        },
        "removed_revenue": round(removed_revenue, 2),
        "removed_cost": round(removed_cost, 2),
        "removed_gross_profit": round(removed_revenue - removed_cost, 2),
        "reported_gross_margin": reported.gross_margin,
        "adjusted_gross_margin": adjusted_totals.gross_margin,
        "margin_delta_pp": round((adjusted_totals.gross_margin - reported.gross_margin) * 100, 2),
    }
    return CleanResult(
        rows=adjusted_rows,
        raw_rows=rows,
        findings=findings,
        reported=reported,
        adjusted=adjusted_totals,
        stats=stats,
    )
