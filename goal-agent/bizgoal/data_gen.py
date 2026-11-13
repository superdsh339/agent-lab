"""2026 年 10 月经营数据造数脚本。

数据为演示用合成数据，不含任何真实客户信息。生成过程完全确定：
不依赖随机数、外部网络或第三方库，重复执行得到逐字节一致的结果。

契约字段（9 列）：
客户、合同号、合同金额、本月确认收入、本月成本、本月回款、开票日、
约定账期、应收余额。

内置三类异常（由 clean 阶段识别并给出处置）：
1. 收入跨期确认：HT-2026-0107，开票日在 2026-11-08，却在 10 月确认收入；
2. 退款/折让：HT-2026-0103-R，红字冲减 180,000 元并实际退款；
3. 账龄超 180 天：HT-2026-0101，2026-02-15 开票、应收 1,500,000 元、账龄 258 天。
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from .config import AS_OF, COLUMNS, DEFAULT_DATA_PATH, DEFAULT_MANIFEST_PATH, PERIOD_LABEL
from .models import RawRow

# 合同级种子表。金额单位：元。开票日为收入确认凭证日（简化口径）。
# 金额经过设计，使 10 月管理口径毛利率为 25.00%、DSO 约 48.5 天，
# 从而让「+3pp / ≤45 天」的目标既非唾手可得、也可以通过动作闭环达成。
CONTRACT_SPECS: list[dict[str, Any]] = [
    # 客户, 合同号, 合同金额, 本月确认收入, 本月成本, 本月回款, 开票日, 账期, 应收余额
    dict(customer="华信建设", contract_id="HT-2026-0101", amount=1_500_000, revenue=0, cost=0, cash=0, invoice="2026-02-15", terms=60, ar=1_500_000),
    dict(customer="华信建设", contract_id="HT-2026-0112", amount=900_000, revenue=900_000, cost=660_000, cash=500_000, invoice="2026-10-08", terms=60, ar=400_000),
    dict(customer="恒远物流", contract_id="HT-2026-0102", amount=1_200_000, revenue=1_200_000, cost=900_000, cash=720_000, invoice="2026-10-03", terms=45, ar=480_000),
    dict(customer="瑞康医药", contract_id="HT-2026-0103", amount=1_400_000, revenue=1_400_000, cost=1_050_000, cash=1_000_000, invoice="2026-10-06", terms=30, ar=400_000),
    dict(customer="瑞康医药", contract_id="HT-2026-0103-R", amount=0, revenue=-180_000, cost=0, cash=-180_000, invoice="2026-10-18", terms=30, ar=0),
    dict(customer="蓝海科技", contract_id="HT-2026-0105", amount=2_000_000, revenue=2_000_000, cost=1_300_000, cash=1_200_000, invoice="2026-10-12", terms=60, ar=800_000),
    dict(customer="启明电子", contract_id="HT-2026-0106", amount=800_000, revenue=800_000, cost=680_000, cash=300_000, invoice="2026-10-15", terms=90, ar=500_000),
    dict(customer="华辰制造", contract_id="HT-2026-0107", amount=960_000, revenue=960_000, cost=560_000, cash=0, invoice="2026-11-08", terms=60, ar=0),
    dict(customer="远达能源", contract_id="HT-2026-0108", amount=1_100_000, revenue=1_100_000, cost=820_000, cash=600_000, invoice="2026-10-20", terms=30, ar=500_000),
    dict(customer="盛达零售", contract_id="HT-2026-0109", amount=700_000, revenue=700_000, cost=560_000, cash=350_000, invoice="2026-10-05", terms=45, ar=350_000),
    dict(customer="中科软件", contract_id="HT-2026-0110", amount=600_000, revenue=600_000, cost=420_000, cash=200_000, invoice="2026-10-22", terms=30, ar=400_000),
    dict(customer="恒远物流", contract_id="HT-2026-0111", amount=1_300_000, revenue=0, cost=0, cash=0, invoice="2026-06-30", terms=60, ar=1_300_000),
    dict(customer="蓝海科技", contract_id="HT-2026-0114", amount=900_000, revenue=0, cost=0, cash=0, invoice="2026-08-20", terms=60, ar=900_000),
    dict(customer="启明电子", contract_id="HT-2026-0115", amount=1_500_000, revenue=0, cost=0, cash=0, invoice="2026-07-15", terms=45, ar=1_500_000),
    dict(customer="远达能源", contract_id="HT-2026-0116", amount=1_900_000, revenue=0, cost=0, cash=0, invoice="2026-05-15", terms=90, ar=1_900_000),
    dict(customer="盛达零售", contract_id="HT-2026-0117", amount=1_000_000, revenue=0, cost=0, cash=0, invoice="2026-09-15", terms=30, ar=1_000_000),
    dict(customer="中科软件", contract_id="HT-2026-0118", amount=800_000, revenue=0, cost=0, cash=0, invoice="2026-08-31", terms=45, ar=800_000),
    dict(customer="华辰制造", contract_id="HT-2026-0119", amount=600_000, revenue=0, cost=0, cash=0, invoice="2026-09-05", terms=30, ar=600_000),
]

EXPECTED_ANOMALIES = {
    "REVENUE_CROSS_PERIOD": ["HT-2026-0107"],
    "REFUND_OR_REBATE": ["HT-2026-0103-R"],
    "AR_AGING_GT_180": ["HT-2026-0101"],
}

ASSUMPTIONS = {
    "收入确认": "按权责发生制，以开票日所在期间作为收入确认期间（简化内控口径：开票即取得收款权利）。开票日晚于 2026-10-31 却在 10 月确认收入，判为提前确认（跨期）。",
    "回款": "等于本月实际到账（银行流水口径），退款为负数；不等于开票金额或确认收入。",
    "账龄": "账龄起算日为开票日，截至 2026-10-31；账龄 = 截止日 - 开票日，逾期天数 = max(0, 账龄 - 约定账期)。",
    "应收余额": "为截至 2026-10-31 尚未收回的余额，可能包含以前月份开票；单月数据无法还原期初应收，因此 DSO 为单月近似值。",
    "退款行": "退款/折让红字行的合同金额记为 0，避免与主合同重复；以红字发票冲减收入和实际退款为准。",
}


def build_rows() -> list[RawRow]:
    rows: list[RawRow] = []
    for index, spec in enumerate(CONTRACT_SPECS, start=1):
        rows.append(
            RawRow(
                customer=spec["customer"],
                contract_id=spec["contract_id"],
                contract_amount=float(spec["amount"]),
                revenue=float(spec["revenue"]),
                cost=float(spec["cost"]),
                cash=float(spec["cash"]),
                invoice_date=date.fromisoformat(spec["invoice"]),
                terms_days=int(spec["terms"]),
                ar_balance=float(spec["ar"]),
                source_line=index,
            )
        )
    validate_rows(rows)
    return rows


def validate_rows(rows: list[RawRow]) -> None:
    """造数自检：契约数量、异常三类、金额勾稽关系。"""
    if len(rows) < 15:
        raise ValueError(f"数据行数不足 15 行：{len(rows)}")
    customers = {row.customer for row in rows}
    if len(customers) < 8:
        raise ValueError(f"客户数不足 8 个：{len(customers)}")
    if len({row.contract_id for row in rows}) != len(rows):
        raise ValueError("合同号存在重复")
    period_rows = [r for r in rows if r.invoice_date.year == AS_OF.year and r.invoice_date.month == AS_OF.month]
    cross = [r for r in rows if r.revenue > 0 and r not in period_rows]
    if len(cross) != 1:
        raise ValueError(f"跨期确认异常应恰好 1 笔，实际 {len(cross)}")
    refunds = [r for r in rows if r.revenue < 0]
    if len(refunds) != 1:
        raise ValueError(f"退款/折让异常应恰好 1 笔，实际 {len(refunds)}")
    aged = [r for r in rows if r.ar_balance > 0 and (AS_OF - r.invoice_date).days > 180]
    if len(aged) != 1:
        raise ValueError(f"账龄超 180 天异常应恰好 1 笔，实际 {len(aged)}")

    for row in rows:
        if row.cost < 0 or row.contract_amount < 0 or row.ar_balance < 0:
            raise ValueError(f"{row.contract_id} 金额出现负数")
        if row.revenue < 0:
            if row.cost != 0 or row.cash > 0 or row.ar_balance != 0:
                raise ValueError(f"{row.contract_id} 退款行勾稽不成立")
            if abs(row.cash - row.revenue) > 0.01:
                raise ValueError(f"{row.contract_id} 退款行回款与冲减收入不一致")
        elif row.invoice_date.year == AS_OF.year and row.invoice_date.month == AS_OF.month:
            # 当月开票：应收 = 确认收入 - 实际回款
            if abs(row.ar_balance - (row.revenue - row.cash)) > 0.01:
                raise ValueError(
                    f"{row.contract_id} 应收勾稽不成立：{row.ar_balance} != {row.revenue} - {row.cash}"
                )
        elif row.revenue != 0:
            # 期间外开票却确认了期间内收入：这是跨期异常，不应有应收
            if row.ar_balance != 0:
                raise ValueError(f"{row.contract_id} 跨期异常行不应有应收余额")


def totals(rows: list[RawRow]) -> dict[str, Any]:
    revenue = sum(r.revenue for r in rows)
    cost = sum(r.cost for r in rows)
    return {
        "rows": len(rows),
        "customers": len({r.customer for r in rows}),
        "revenue": round(revenue, 2),
        "cost": round(cost, 2),
        "gross_profit": round(revenue - cost, 2),
        "gross_margin": round((revenue - cost) / revenue, 6) if revenue else 0.0,
        "cash": round(sum(r.cash for r in rows), 2),
        "ar": round(sum(r.ar_balance for r in rows), 2),
    }


def write_csv(rows: list[RawRow], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict())


def build_manifest(rows: list[RawRow], csv_path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    anomalies = []
    for code, contract_ids in EXPECTED_ANOMALIES.items():
        for contract_id in contract_ids:
            row = next(r for r in rows if r.contract_id == contract_id)
            anomalies.append(
                {
                    "code": code,
                    "contract_id": contract_id,
                    "customer": row.customer,
                    "description": {
                        "REVENUE_CROSS_PERIOD": "开票日晚于期间截止日，却在本期确认了收入和成本",
                        "REFUND_OR_REBATE": "红字发票冲减收入并实际退款",
                        "AR_AGING_GT_180": "应收余额账龄超过 180 天",
                    }[code],
                }
            )
    return {
        "period": PERIOD_LABEL,
        "as_of": AS_OF.isoformat(),
        "generated_by": "bizgoal.data_gen",
        "seed_note": "固定种子表，无随机数；重复执行结果一致",
        "sha256": digest,
        "columns": COLUMNS,
        "totals": totals(rows),
        "anomalies": anomalies,
        "assumptions": ASSUMPTIONS,
    }


def generate_dataset(
    csv_path: Path = DEFAULT_DATA_PATH,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    rows = build_rows()
    write_csv(rows, csv_path)
    manifest = build_manifest(rows, csv_path)
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:  # pragma: no cover - 由脚本入口调用
    manifest = generate_dataset()
    print(f"已生成 {DEFAULT_DATA_PATH}（{manifest['totals']['rows']} 行，{manifest['totals']['customers']} 个客户）")
    print(f"已生成 {DEFAULT_MANIFEST_PATH}")
    print(json.dumps(manifest["totals"], ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
