"""Markdown 运行报告。

报告是给人看的最终产物，也是验收时的证据：
每个数字都能回到 CSV 或中间产物，每个动作都有金额、负责人和截止日，
目标、动作、预测、实际回执在同一个闭环里对齐。
"""

from __future__ import annotations

import json
from typing import Any

from .config import PERIOD_LABEL


def _wan(value: float) -> str:
    return f"{value/10_000:,.2f}"


def _pct(ratio: float) -> str:
    return f"{ratio*100:.2f}%"


def _pp(value: float) -> str:
    return f"{value:+.2f}pp"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        return str(value).replace("|", "／").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(cell(value) for value in row) + " |")
    return "\n".join(lines)


def _status_label(status: str) -> str:
    return {
        "met": "目标达成",
        "met_after_recovery": "目标达成（经兜底重规划）",
        "partial": "未完全达成（执行失败后仍有缺口）",
        "infeasible": "目标不可达",
        "failed": "运行失败",
    }.get(status, status)


def build_llm_facts(result: dict[str, Any]) -> dict[str, Any]:
    """给 LLM 的匿名事实包：不含客户名、合同原文。"""
    before = result["metrics_before"]["adjusted"]
    goal = result["goal"]
    after = result["metrics_after"]["forecast"]
    plan = result["plan"]
    cleaning = result["cleaning"]
    effective = result.get("execution", {}).get("effective_actions") or []
    actions = effective or plan["selected"] or plan["backups"]
    return {
        "基线期": result.get("period", PERIOD_LABEL),
        "计划期": result.get("plan_period", ""),
        "基线毛利率_pct": round(before["gross_margin"] * 100, 2),
        "目标毛利率_pct": round(goal["target_margin"] * 100, 2),
        "预测毛利率_pct": round(after["gross_margin"] * 100, 2),
        "基线DSO_天": round(before["dso_days"], 2),
        "目标DSO_天": goal["dso_target_days"],
        "预测DSO_天": round(after["dso_days"], 2),
        "动作数": len(actions),
        "毛利动作数": sum(1 for a in actions if a["kind"] == "margin"),
        "回款动作数": sum(1 for a in actions if a["kind"] == "collection"),
        "数据异常类数": len(cleaning["anomaly_counts"]),
        "预计回款_万元": round(after.get("collected", 0) / 10_000, 2),
        "目标是否达成": bool(plan["meets"]),
    }


def fallback_narrative(result: dict[str, Any], reason: str = "") -> str:
    before = result["metrics_before"]["adjusted"]
    goal = result["goal"]
    after = result["metrics_after"]["forecast"]
    plan = result["plan"]
    effective = result.get("execution", {}).get("effective_actions") or []
    actions = effective or plan["selected"] or plan["backups"]
    action_suffix = "" if plan["meets"] else "（测算，未执行）"
    lines = [
        f"- 基线毛利率 {_pct(before['gross_margin'])}，目标 {_pct(goal['target_margin'])}，"
        f"预测 {_pct(after['gross_margin'])}（{_pp((after['gross_margin']-before['gross_margin'])*100)}）。",
        f"- 回款周期 {before['dso_days']:.2f} 天，目标 ≤{goal['dso_target_days']:.0f} 天，"
        f"预测 {after['dso_days']:.2f} 天。",
        f"- 共 {len(actions)} 个动作：{sum(1 for a in actions if a['kind']=='margin')} 个毛利动作、"
        f"{sum(1 for a in actions if a['kind']=='collection')} 个回款动作；"
        f"清洗识别 {len(result['cleaning']['anomaly_counts'])} 类异常{action_suffix if not plan['meets'] else ''}。",
    ]
    if reason:
        lines.append(f"（模型未参与：{reason}；以上为规则模板生成。）")
    return "\n".join(lines)


def render_report(result: dict[str, Any]) -> str:
    goal = result["goal"]
    before = result["metrics_before"]["adjusted"]
    reported = result["metrics_before"]["reported"]
    after = result["metrics_after"]["forecast"]
    cons = result["metrics_after"].get("conservative") or {}
    plan = result["plan"]
    cleaning = result["cleaning"]
    diagnosis = result["diagnosis"]
    status = result["status"]
    days = before["days_in_month"]

    margin_cushion = (after["gross_margin"] - goal["target_margin"]) * 100
    dso_cushion = goal["dso_target_days"] - after["dso_days"]

    if status in {"met", "met_after_recovery"}:
        headline = (
            f"目标达成：毛利率 {_pct(before['gross_margin'])} → {_pct(after['gross_margin'])}"
            f"（目标 {_pct(goal['target_margin'])}，安全垫 {margin_cushion:+.2f}pp）；"
            f"DSO {before['dso_days']:.2f} → {after['dso_days']:.2f} 天（目标 ≤{goal['dso_target_days']:.0f} 天，"
            f"安全垫 {dso_cushion:+.2f} 天）。"
        )
    elif status == "infeasible":
        headline = (
            "目标不可达：即使可用动作全部落地，预测毛利率 "
            f"{_pct(after['gross_margin'])}、DSO {after['dso_days']:.2f} 天，仍存在缺口。"
            + "；".join(result["metrics_after"].get("gaps", []))
        )
    elif status == "partial":
        headline = (
            "执行失败后未完全达成：预测毛利率 "
            f"{_pct(after['gross_margin'])}、DSO {after['dso_days']:.2f} 天。"
            + "；".join(result["metrics_after"].get("gaps", []))
        )
    else:
        headline = "运行失败，详见失败原因。"

    parts: list[str] = []
    parts.append(f"# 经营目标智能体运行报告")
    parts.append("")
    parts.append(
        f"- 运行 ID：`{result['run_id']}`\n"
        f"- 状态：**{_status_label(status)}**\n"
        f"- 数据期间：{result.get('period', PERIOD_LABEL)}；计划期：{result.get('plan_period', '')}\n"
        f"- 模型：{result.get('llm_label', '未调用')}\n"
        f"- 结论：{headline}"
    )
    parts.append("")

    parts.append("## 1. 目标解析")
    parts.append("")
    parts.append(
        _table(
            ["项目", "值", "说明"],
            [
                ["口述原文", f"“{goal['raw_text']}”", "老板原话"],
                ["毛利提升", f"{goal['margin_delta_pp']:.2f}pp", "按百分点解释"],
                ["基线毛利率", _pct(goal["baseline_margin"]), "10 月管理口径（已冲回跨期）"],
                ["目标毛利率", _pct(goal["target_margin"]), "基线 + 提升幅度"],
                ["回款周期目标", f"≤ {goal['dso_target_days']:.0f} 天", "DSO，期末应收/月收入×31"],
                ["解析器", goal["parser"], "rule=规则；llm=模型解析后由代码换算"],
            ],
        )
    )
    parts.append("")
    if goal.get("notes"):
        parts.append("解析备注：" + "；".join(goal["notes"]))
        parts.append("")
    target_gp = goal["target_margin"] * before["revenue"]
    target_ar = goal["dso_target_days"] * before["revenue"] / days
    parts.append("**目标金额化（按基线收入测算）**")
    parts.append("")
    parts.append(
        _table(
            ["目标", "计算", "金额"],
            [
                ["目标毛利额", f"{_pct(goal['target_margin'])} × {_wan(before['revenue'])} 万元", f"{_wan(target_gp)} 万元"],
                ["毛利缺口", f"{_wan(target_gp)} - {_wan(before['gross_profit'])} 万元", f"{_wan(target_gp - before['gross_profit'])} 万元"],
                ["应收上限", f"{goal['dso_target_days']:.0f} × {_wan(before['revenue'])} / {days}", f"{_wan(target_ar)} 万元"],
                ["回款缺口", f"{_wan(before['ar'])} - {_wan(target_ar)} 万元", f"{_wan(before['ar'] - target_ar)} 万元"],
            ],
        )
    )
    parts.append("")

    parts.append("## 2. 数据清洗与口径")
    parts.append("")
    rows = [
        ["行数 / 客户数", f"{cleaning['stats']['rows']} 行 / {cleaning['stats']['customers']} 个客户", "契约要求 ≥15 行、≥8 客户"],
        ["报表口径收入", f"{_wan(cleaning['reported']['revenue'])} 万元", "原始 CSV 直接汇总"],
        ["管理口径收入", f"{_wan(cleaning['adjusted']['revenue'])} 万元", "冲回跨期确认后"],
        ["跨期冲回", f"收入 {_wan(cleaning['stats']['removed_revenue'])} 万元 / 成本 {_wan(cleaning['stats']['removed_cost'])} 万元", "HT-2026-0107 开票在 11 月，收入记在 10 月"],
        ["报表毛利率", _pct(reported["gross_margin"]), "含跨期收入，虚高"],
        ["管理毛利率", _pct(before["gross_margin"]), "本原型作为目标的基线"],
        ["报表 DSO", f"{reported['dso_days']:.2f} 天", "受虚增收入影响，看似更低"],
        ["管理 DSO", f"{before['dso_days']:.2f} 天", "以管理口径收入为分母"],
    ]
    parts.append(_table(["项目", "值", "说明"], rows))
    parts.append("")
    if cleaning["findings"]:
        finding_rows = [
            [
                f["code"],
                f["contract_id"],
                f["message"],
                json.dumps(f.get("adjustment", {}), ensure_ascii=False),
            ]
            for f in cleaning["findings"]
        ]
        parts.append(_table(["异常代码", "合同号", "说明", "调整"], finding_rows))
        parts.append("")
    parts.append(
        "口径假设：\n"
        "1) 收入确认：权责发生制，以开票日所在期间为确认期间；开票日在期间外却确认本期收入，判为跨期并冲回。\n"
        "2) 回款：等于本月实际到账（银行流水），退款为负数，不等于开票或确认收入。\n"
        "3) 账龄：起算日为开票日，截至 2026-10-31；逾期 = max(0, 账龄 - 约定账期)。\n"
        f"4) DSO = 期末应收 / 本月确认收入 × {days}；单月数据无法还原期初应收，属简化近似。"
    )
    parts.append("")

    parts.append("## 3. 基线指标与诊断")
    parts.append("")
    parts.append(
        _table(
            ["指标", "报表口径", "管理口径", "公式"],
            [
                ["收入（万元）", _wan(reported["revenue"]), _wan(before["revenue"]), "Σ 本月确认收入"],
                ["成本（万元）", _wan(reported["cost"]), _wan(before["cost"]), "Σ 本月成本"],
                ["毛利（万元）", _wan(reported["gross_profit"]), _wan(before["gross_profit"]), "收入 - 成本"],
                ["毛利率", _pct(reported["gross_margin"]), _pct(before["gross_margin"]), "毛利 / 收入"],
                ["回款（万元）", _wan(reported["cash"]), _wan(before["cash"]), "Σ 本月回款（实际到账）"],
                ["应收（万元）", _wan(reported["ar"]), _wan(before["ar"]), "Σ 期末应收"],
                ["DSO（天）", f"{reported['dso_days']:.2f}", f"{before['dso_days']:.2f}", "应收 / 收入 × 31"],
                ["回款率", _pct(reported["collection_rate"]), _pct(before["collection_rate"]), "回款 / 收入"],
            ],
        )
    )
    parts.append("")
    parts.append("诊断结论：")
    for item in diagnosis["findings"]:
        parts.append(f"- {item}")
    parts.append("")
    aging_rows = [
        [label, f"{_wan(value)} 万元", f"{(value / before['ar'] * 100):.1f}%" if before["ar"] else "0.0%"]
        for label, value in before["aging_buckets"].items()
    ]
    aging_rows.append(["合计", f"{_wan(before['ar'])} 万元", "100.0%" if before["ar"] else "0.0%"])
    parts.append(_table(["账龄档", "应收", "占期末应收"], aging_rows))
    parts.append("")

    parts.append("## 4. 动作计划")
    parts.append("")
    execution = result.get("execution", {})
    effective_actions = execution.get("effective_actions") or []
    plan_ids = {a["id"] for a in plan["selected"]}
    effective_ids = {a["id"] for a in effective_actions}
    if not plan["selected"]:
        display_actions = plan["backups"]
        parts.append("> 说明：当前没有可满足目标的动作组合；下表是不重复计量的兼容动作测算，未实际执行。")
        parts.append("")
    elif effective_actions and effective_ids != plan_ids:
        display_actions = effective_actions
        parts.append("> 说明：执行阶段发生动作失败，下表为兜底重规划后**实际生效**的动作；失败动作见第 6 节。")
        parts.append("")
    else:
        display_actions = effective_actions or plan["selected"]
    rows = []
    for action in display_actions:
        rows.append(
            [
                action["id"],
                "毛利" if action["kind"] == "margin" else "回款",
                action["title"],
                action["owner"],
                action["due_date"],
                f"{action['confidence']:.0%}",
                _wan(action["delta_revenue"]),
                _wan(action["delta_cost"]),
                _wan(action["delta_gp"]),
                _wan(action["expected_cash"]),
            ]
        )
    if not rows:
        rows = [["—", "—", "无可执行动作", "—", "—", "—", "—", "—", "—", "—"]]
    parts.append(
        _table(
            ["ID", "类型", "动作", "负责人", "截止日", "置信度", "Δ收入(万)", "Δ成本(万)", "Δ毛利(万)", "预计回款(万)"],
            rows,
        )
    )
    parts.append("")
    parts.append("动作假设（节选）：")
    for action in display_actions:
        assumptions = "；".join(action.get("assumptions", []))
        parts.append(f"- `{action['id']}`：{assumptions}")
    parts.append("")
    executed_or_failed = plan_ids | effective_ids | {
        r["action_id"] for r in execution.get("receipts", []) if r.get("status") == "failed"
    }
    remaining_backups = [a for a in plan.get("backups", []) if a["id"] not in executed_or_failed]
    if remaining_backups:
        backup_titles = "；".join(f"{a['id']} {a['title']}" for a in remaining_backups[:4])
        parts.append(f"备选动作（未执行；重规划时按“同一合同不重复加杠杆”约束筛选）：{backup_titles}。")
        parts.append("")

    parts.append("## 5. 闭环校验：目标 → 动作 → 指标")
    parts.append("")
    parts.append(
        _table(
            ["指标", "基线", "目标", "预测（基线情景）", "预测（保守 95%）", "结论"],
            [
                [
                    "毛利率",
                    _pct(before["gross_margin"]),
                    _pct(goal["target_margin"]),
                    _pct(after["gross_margin"]),
                    _pct(cons["gross_margin"]) if cons else "—",
                    "达成" if after["gross_margin"] >= goal["target_margin"] else "未达成",
                ],
                [
                    "DSO（天）",
                    f"{before['dso_days']:.2f}",
                    f"≤{goal['dso_target_days']:.0f}",
                    f"{after['dso_days']:.2f}",
                    f"{cons['dso_days']:.2f}" if cons else "—",
                    "达成" if after["dso_days"] <= goal["dso_target_days"] else "未达成",
                ],
                [
                    "毛利（万元）",
                    _wan(before["gross_profit"]),
                    _wan(goal["target_margin"] * after["revenue"]),
                    _wan(after["gross_profit"]),
                    _wan(cons["gross_profit"]) if cons else "—",
                    "—",
                ],
                [
                    "应收（万元）",
                    _wan(before["ar"]),
                    _wan(goal["dso_target_days"] * after["revenue"] / days),
                    _wan(after["ar"]),
                    _wan(cons["ar"]) if cons else "—",
                    "—",
                ],
            ],
        )
    )
    parts.append("")
    margin_delta = sum(a["delta_gp"] for a in effective_actions)
    revenue_delta = sum(a["delta_revenue"] for a in effective_actions)
    cost_delta = sum(a["delta_cost"] for a in effective_actions)
    cash_delta = sum(a["expected_cash"] for a in effective_actions)
    parts.append("**勾稽复核**")
    parts.append("")
    parts.append(
        _table(
            ["复核项", "计算", "结果"],
            [
                [
                    "毛利变化",
                    f"Σ动作Δ毛利 = {_wan(margin_delta)} 万元",
                    f"基线 {_wan(before['gross_profit'])} + {_wan(margin_delta)} = {_wan(before['gross_profit'] + margin_delta)} 万元",
                ],
                [
                    "收入变化",
                    f"Σ动作Δ收入 = {_wan(revenue_delta)} 万元",
                    f"{_wan(before['revenue'])} + {_wan(revenue_delta)} = {_wan(before['revenue'] + revenue_delta)} 万元",
                ],
                [
                    "成本变化",
                    f"Σ动作Δ成本 = {_wan(cost_delta)} 万元",
                    f"{_wan(before['cost'])} + ({_wan(cost_delta)}) = {_wan(before['cost'] + cost_delta)} 万元",
                ],
                [
                    "应收变化",
                    f"Σ预计回款 = {_wan(cash_delta)} 万元",
                    f"{_wan(before['ar'])} - {_wan(cash_delta)} = {_wan(before['ar'] - cash_delta)} 万元",
                ],
                [
                    "预测毛利率",
                    f"({_wan(before['gross_profit'])} + {_wan(margin_delta)}) / ({_wan(before['revenue'])} + {_wan(revenue_delta)})",
                    _pct(after["gross_margin"]),
                ],
                [
                    "预测 DSO",
                    f"({_wan(before['ar'])} - {_wan(cash_delta)}) / ({_wan(before['revenue'])} + {_wan(revenue_delta)}) × {days}",
                    f"{after['dso_days']:.2f} 天",
                ],
            ],
        )
    )
    parts.append("")
    if result.get("verification"):
        parts.append("**自动校验点**")
        parts.append("")
        parts.append(
            _table(
                ["校验项", "期望", "实际", "结果"],
                [
                    [item["check"], item["expected"], item["actual"], "PASS" if item["pass"] else "FAIL"]
                    for item in result["verification"]
                ],
            )
        )
        parts.append("")

    parts.append("## 6. 执行与兜底")
    parts.append("")
    receipts = result.get("execution", {}).get("receipts", [])
    if receipts:
        receipt_rows = [
            [
                r["action_id"],
                r["title"],
                {"executed": "成功", "failed": "失败", "already_executed": "已执行（幂等）", "skipped": "跳过"}.get(r["status"], r["status"]),
                _wan(r["amount"]),
                r["note"],
            ]
            for r in receipts
        ]
        parts.append(_table(["动作", "标题", "状态", "金额(万)", "备注"], receipt_rows))
    else:
        parts.append("本次未执行动作（目标不可达或运行失败）。")
    parts.append("")
    recovery = result.get("execution", {}).get("recovery")
    if recovery:
        parts.append(
            f"**兜底重规划**：失败动作 {recovery['failed_ids']}；"
            f"重规划结果：{'达成' if recovery['meets'] else '仍未达成'}；"
            f"新增执行动作 {recovery.get('added_actions', [])}。"
        )
        parts.append("")
    fallbacks = result.get("fallbacks", [])
    if fallbacks:
        parts.append("**兜底事件**")
        parts.append("")
        parts.append(
            _table(
                ["阶段", "原因", "兜底动作"],
                [[f["stage"], f["reason"], f["action"]] for f in fallbacks],
            )
        )
        parts.append("")
    else:
        parts.append("**兜底事件**：无（所有阶段正常）。")
        parts.append("")
    llm = result.get("llm", {})
    parts.append(
        f"**模型状态**：{result.get('llm_label', '未调用')}；"
        f"配置：provider={llm.get('provider', '—')}，model={llm.get('model', '—')}，"
        f"enabled={llm.get('enabled', False)}"
        + (f"（{llm.get('disabled_reason')}）" if llm.get("disabled_reason") else "")
        + "。"
    )
    parts.append("")

    parts.append("## 7. LLM 摘要（数字已校验）")
    parts.append("")
    narrative = result.get("narrative") or {}
    if narrative.get("text"):
        source = "LLM 生成" if narrative.get("source") == "llm" else "规则模板（模型不可用/被关闭）"
        parts.append(f"来源：{source}。")
        parts.append("")
        parts.append(narrative["text"])
    else:
        parts.append("无摘要。")
    parts.append("")

    parts.append("## 8. 假设与局限")
    parts.append("")
    parts.extend(
        [
            "- 数据为演示合成数据，不含真实客户信息；金额单位为元，表内部分为万元。",
            "- 单月数据无法还原期初应收，DSO 采用「期末应收/本月收入×31」的近似口径；真实上线应改为滚动 12 个月或日均应收口径。",
            "- 动作效果为管理测算值：降本率、提价保留率、账龄分档回收率均为显式假设，执行后需回填实际值并复算。",
            "- 执行层为沙盘模拟，不写入 ERP/CRM；真实落地需接入审批、合同、回款系统，并保留幂等键。",
            "- 跨期收入按管理口径冲回，未在 11 月重新确认；上线后应按实际开票/交付期间回填。",
            "- 若使用 LLM，仅发送聚合指标与动作摘要，不发送客户名称和合同原文；模型不可用时自动降级，不阻断主流程。",
        ]
    )
    parts.append("")

    parts.append("## 9. 复现")
    parts.append("")
    parts.append("```bash")
    parts.append("cd goal-agent")
    parts.append("python scripts/generate_data.py")
    parts.append("python -m bizgoal run --out runs/2026-10")
    parts.append("# 故障注入演示：模型不可用 / 动作失败 / 坏数据")
    parts.append("python -m bizgoal run --out runs/demo-llm-down --chaos llm-down")
    parts.append("python -m bizgoal run --out runs/demo-recover --chaos action-fail")
    parts.append("python -m bizgoal run --out runs/demo-corrupt --chaos data-corrupt  # 预期退出码 2")
    parts.append("python -m bizgoal llm-check")
    parts.append("```")
    parts.append("")
    parts.append("## 10. 运行产物")
    parts.append("")
    for name in [
        "run.log / events.jsonl",
        "cleaning_report.json",
        "metrics_before.json",
        "goal.json",
        "diagnosis.json",
        "actions_library.json",
        "plan.json",
        "action_receipts.json",
        "metrics_after.json",
        "summary.json",
        "llm_trace.json",
    ]:
        parts.append(f"- `{name}`")
    parts.append("")
    parts.append("---")
    parts.append("本报告由确定性计算引擎生成；所有金额、比例、动作贡献均可按上述公式用原始 CSV 复算。")
    return "\n".join(parts) + "\n"


def render_failure_report(
    run_id: str,
    error: Exception,
    issues: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# 经营目标智能体运行报告（失败）",
        "",
        f"- 运行 ID：`{run_id}`",
        f"- 状态：**运行失败**",
        f"- 错误：`{type(error).__name__}: {error}`",
        "",
        "## 失败原因与处置",
        "",
    ]
    if issues:
        for item in issues:
            lines.append(f"- {item}")
    else:
        lines.append("- 详见 run.log / events.jsonl。")
    lines.extend(
        [
            "",
            "## 兜底原则",
            "",
            "- 数据契约不满足时直接停止，不猜测、不静默修复，避免用错数做决策。",
            "- 错误写入 run.log、events.jsonl 与本报告，保留现场。",
            "- 修正数据或目标后，用同一命令重跑即可。",
            "",
        ]
    )
    if extra:
        lines.extend(["## 附加上下文", "", "```json", json.dumps(extra, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines) + "\n"
