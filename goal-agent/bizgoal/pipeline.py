"""端到端编排：数据 → 清洗 → 基线 → 目标 → 诊断 → 动作 → 规划 → 执行 → 复核 → 报告。"""

from __future__ import annotations

import hashlib
import json
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .actions import build_action_library
from .chaos import ChaosConfig
from .cleaning import clean
from .config import AS_OF, DEFAULT_DATA_PATH, DEFAULT_GOAL, PERIOD_LABEL
from .data_gen import generate_dataset
from .data_io import read_csv_rows
from .diagnosis import diagnose
from .errors import DataQualityError, GoalParseError, LLMError
from .execution import execute_actions, successful_action_ids
from .goal import goal_from_numbers, parse_goal
from .llm import LLMClient, LLMConfig
from .logging_utils import RunLogger
from .metrics import compute_metrics, formula_sheet
from .models import Action, Goal, Metrics, Plan
from .planner import select_plan, verify_actions
from .report import (
    build_llm_facts,
    fallback_narrative,
    render_failure_report,
    render_report,
)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _plan_period(as_of: date) -> str:
    year, month = (as_of.year + 1, 1) if as_of.month == 12 else (as_of.year, as_of.month + 1)
    return f"{year}-{month:02d}"


def _resolve_input(
    data_path: Path | None,
    run_dir: Path,
    logger: RunLogger,
    chaos: ChaosConfig,
    allow_regenerate: bool,
) -> Path:
    source = Path(data_path) if data_path else DEFAULT_DATA_PATH
    if source.exists():
        base = source
    elif allow_regenerate and data_path is None:
        generated_dir = run_dir / "generated"
        csv_path = generated_dir / "business_2026_10.csv"
        manifest = generate_dataset(csv_path, generated_dir / "data_manifest.json")
        logger.fallback(
            "ingest",
            f"默认数据文件不存在：{source}",
            "按固定种子重新生成数据",
            path=str(csv_path),
            rows=manifest["totals"]["rows"],
        )
        base = csv_path
    else:
        raise DataQualityError(f"数据文件不存在：{source}")

    if chaos.data_corrupt:
        corrupted = run_dir / "corrupted_input.csv"
        text = base.read_text(encoding="utf-8")
        # 破坏表头：把“本月成本”改名，触发契约校验失败
        text = text.replace("本月成本", "成本", 1)
        corrupted.write_text(text, encoding="utf-8")
        logger.warn("ingest", "已生成坏数据副本用于演示", path=str(corrupted))
        return corrupted
    return base


def _resolve_goal(
    goal_text: str,
    baseline_margin: float,
    llm_config: LLMConfig,
    client: LLMClient,
    logger: RunLogger,
) -> Goal:
    try:
        goal = parse_goal(goal_text, baseline_margin)
        logger.info(
            "goal",
            "规则解析目标成功",
            margin_delta_pp=goal.margin_delta_pp,
            dso_target_days=goal.dso_target_days,
            target_margin=round(goal.target_margin, 4),
        )
        return goal
    except GoalParseError as rule_error:
        logger.warn("goal", f"规则解析失败：{rule_error}")
        if not llm_config.enabled:
            logger.fallback(
                "goal",
                f"规则解析失败且模型不可用（{llm_config.disabled_reason}）",
                "终止并提示补充目标表述",
            )
            raise GoalParseError(str(rule_error)) from rule_error
        try:
            numbers = client.parse_goal(goal_text)
            goal = goal_from_numbers(
                goal_text,
                numbers["margin_delta_pp"],
                numbers["dso_target_days"],
                baseline_margin,
                parser="llm",
                notes=["规则未命中，由 LLM 解析出两个数字，基线换算由代码完成"],
            )
            logger.info("goal", "LLM 解析目标成功", margin_delta_pp=goal.margin_delta_pp)
            return goal
        except LLMError as llm_error:
            logger.fallback(
                "goal",
                f"规则与模型解析均失败：{llm_error}",
                "终止并提示补充目标表述",
            )
            raise GoalParseError(f"{rule_error}；模型兜底失败：{llm_error}") from llm_error


def _build_verification(
    *,
    rows_count: int,
    customers_count: int,
    anomaly_counts: dict[str, int],
    removed_revenue: float,
    goal: Goal,
    before: Metrics,
    after: dict[str, float],
    effective_actions: list[Action],
    plan: Plan,
    meets: bool,
    failures: int,
    artifacts_exist: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, expected: str, actual: str, passed: bool) -> None:
        checks.append({"check": name, "expected": expected, "actual": actual, "pass": bool(passed)})

    add("数据行数 ≥15", "≥15", str(rows_count), rows_count >= 15)
    add("客户数 ≥8", "≥8", str(customers_count), customers_count >= 8)
    for code, label in [
        ("REVENUE_CROSS_PERIOD", "跨期确认"),
        ("REFUND_OR_REBATE", "退款/折让"),
        ("AR_AGING_GT_180", "账龄>180天"),
    ]:
        count = anomaly_counts.get(code, 0)
        add(f"{label}异常", "1 笔", f"{count} 笔", count == 1)
    add("跨期收入冲回", "已冲回（>0）", f"{removed_revenue:,.2f}", removed_revenue > 0)
    derived_target = goal.baseline_margin + goal.margin_delta_pp / 100
    add(
        "目标解析",
        "目标 = 基线 + 提升幅度",
        f"{goal.margin_delta_pp:.2f}pp → {goal.target_margin:.2%}",
        abs(goal.target_margin - derived_target) < 1e-9 and goal.margin_delta_pp > 0 and goal.dso_target_days > 0,
    )
    delta_gp = sum(a.delta_gp for a in effective_actions)
    delta_rev = sum(a.delta_revenue for a in effective_actions)
    attribution_ok = (
        abs((before.gross_profit + delta_gp) - after["gross_profit"]) < 0.01
        and abs((before.revenue + delta_rev) - after["revenue"]) < 0.01
    )
    add(
        "动作归因勾稽",
        "Σ动作Δ = 预测 - 基线",
        f"Δ毛利 {delta_gp:,.2f} / Δ收入 {delta_rev:,.2f}",
        attribution_ok,
    )
    add(
        "毛利率达标",
        f"≥{goal.target_margin:.2%}",
        f"{after['gross_margin']:.2%}",
        after["gross_margin"] >= goal.target_margin - 1e-9,
    )
    add(
        "DSO 达标",
        f"≤{goal.dso_target_days:.0f}天",
        f"{after['dso_days']:.2f}天",
        after["dso_days"] <= goal.dso_target_days + 1e-9,
    )
    add(
        "执行回执",
        "成功或兜底后成功",
        f"失败 {failures} 笔；计划{'可达' if plan.meets else '不可达'}",
        meets,
    )
    add("日志与报告落盘", "存在", "存在" if artifacts_exist else "缺失", artifacts_exist)
    return checks


def run_pipeline(
    *,
    out_dir: str | Path,
    data_path: str | Path | None = None,
    goal_text: str | None = None,
    llm_mode: str = "auto",
    chaos: ChaosConfig | None = None,
    as_of: date = AS_OF,
    allow_regenerate: bool = True,
) -> dict[str, Any]:
    chaos = chaos or ChaosConfig()
    run_dir = Path(out_dir).expanduser().resolve()
    logger = RunLogger(run_dir)
    for stale in (
        "report.md",
        "summary.json",
        "input_info.json",
        "cleaning_report.json",
        "metrics_before.json",
        "goal.json",
        "diagnosis.json",
        "actions_library.json",
        "plan.json",
        "action_receipts.json",
        "metrics_after.json",
        "llm_trace.json",
    ):
        stale_path = run_dir / stale
        if stale_path.exists():
            stale_path.unlink()
    run_id = run_dir.name
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    llm_config = LLMConfig.from_env(mode=llm_mode, force_down=chaos.llm_down)
    client = LLMClient(llm_config)
    goal_text = goal_text or DEFAULT_GOAL

    result: dict[str, Any] = {
        "run_id": run_id,
        "out_dir": str(run_dir),
        "started_at": started_at,
        "period": PERIOD_LABEL,
        "plan_period": _plan_period(as_of),
        "chaos": chaos.describe(),
        "llm": llm_config.describe(),
        "fallbacks": logger.fallbacks,
        "goal_text": goal_text,
    }
    logger.info("start", "运行开始", run_id=run_id, out_dir=str(out_dir), chaos=chaos.describe() or "none")

    try:
        source = _resolve_input(Path(data_path) if data_path else None, run_dir, logger, chaos, allow_regenerate)
        raw_bytes = source.read_bytes()
        rows = read_csv_rows(source)
        input_info = {
            "source": str(source),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "rows": len(rows),
            "customers": len({row.customer for row in rows}),
        }
        _write_json(run_dir / "input_info.json", input_info)
        logger.info("ingest", "数据读取完成", rows=len(rows), customers=input_info["customers"])

        clean_result = clean(rows, as_of)
        cleaning_payload = clean_result.to_dict()
        cleaning_payload["rows"] = [row.to_dict() for row in clean_result.rows]
        _write_json(run_dir / "cleaning_report.json", cleaning_payload)
        logger.info(
            "clean",
            "清洗完成",
            anomaly_counts=clean_result.anomaly_counts,
            reported_margin=round(clean_result.reported.gross_margin, 4),
            adjusted_margin=round(clean_result.adjusted.gross_margin, 4),
        )

        reported_metrics = compute_metrics(clean_result.raw_rows, as_of)
        adjusted_metrics = compute_metrics(clean_result.rows, as_of)
        _write_json(
            run_dir / "metrics_before.json",
            {
                "reported": reported_metrics.to_dict(),
                "adjusted": adjusted_metrics.to_dict(),
                "formulas": formula_sheet(as_of),
            },
        )
        logger.info(
            "metrics",
            "基线指标完成",
            adjusted_margin=round(adjusted_metrics.gross_margin, 4),
            adjusted_dso=adjusted_metrics.dso_days,
        )

        goal = _resolve_goal(goal_text, adjusted_metrics.gross_margin, llm_config, client, logger)
        _write_json(run_dir / "goal.json", goal.to_dict())

        aging_ids = {f.contract_id for f in clean_result.findings if f.code == "AR_AGING_GT_180"}
        diagnosis = diagnose(adjusted_metrics, goal, aging_ids)
        _write_json(run_dir / "diagnosis.json", diagnosis.to_dict())

        actions = build_action_library(clean_result, adjusted_metrics, as_of)
        _write_json(run_dir / "actions_library.json", [a.to_dict() for a in actions])
        logger.info("actions", "动作库生成", count=len(actions))

        plan = select_plan(adjusted_metrics, goal, actions)
        _write_json(run_dir / "plan.json", plan.to_dict())
        logger.info(
            "plan",
            "规划完成",
            meets=plan.meets,
            actions=len(plan.selected),
            forecast_margin=round(plan.forecast["gross_margin"], 4),
            forecast_dso=plan.forecast["dso_days"],
        )

        result.update(
            {
                "cleaning": cleaning_payload,
                "metrics_before": {
                    "reported": reported_metrics.to_dict(),
                    "adjusted": adjusted_metrics.to_dict(),
                },
                "goal": goal.to_dict(),
                "diagnosis": diagnosis.to_dict(),
                "plan": plan.to_dict(),
                "execution": {
                    "receipts": [],
                    "recovery": None,
                    "effective_action_ids": [],
                    "effective_actions": [],
                },
                "metrics_after": {},
                "narrative": {},
                "verification": [],
            }
        )

        if not plan.meets:
            logger.error("plan", "目标不可达", gaps="；".join(plan.gaps))
            effective_actions = list(plan.backups) if plan.forecast else []
            after = plan.forecast
            result["execution"]["effective_actions"] = [a.to_dict() for a in effective_actions]
            result["execution"]["effective_action_ids"] = [a.id for a in effective_actions]
            result["metrics_after"] = {
                "forecast": after,
                "conservative": plan.conservative,
                "meets": False,
                "gaps": plan.gaps,
                "target": {"margin": goal.target_margin, "dso": goal.dso_target_days},
                "effective_action_ids": [a.id for a in effective_actions],
            }
            result["status"] = "infeasible"
            result["exit_code"] = 3
            narrative, llm_label = _make_narrative(result, llm_config, client, logger)
            result["narrative"] = narrative
            result["llm_label"] = llm_label
            _write_json(run_dir / "metrics_after.json", result["metrics_after"])
            _write_json(run_dir / "llm_trace.json", _llm_trace(llm_config, client, narrative))
            result["verification"] = _build_verification(
                rows_count=len(rows),
                customers_count=input_info["customers"],
                anomaly_counts=clean_result.anomaly_counts,
                removed_revenue=clean_result.stats["removed_revenue"],
                goal=goal,
                before=adjusted_metrics,
                after=after,
                effective_actions=effective_actions,
                plan=plan,
                meets=False,
                failures=0,
                artifacts_exist=True,
            )
            _finalize(result, run_dir, logger)
            return result

        receipts, _ledger = execute_actions(plan.selected, run_dir, logger, chaos)
        all_receipts = list(receipts)
        failed_ids = [r.action_id for r in receipts if r.status == "failed"]
        recovery: dict[str, Any] | None = None
        if failed_ids:
            recovery_plan = select_plan(adjusted_metrics, goal, actions, excluded_ids=failed_ids)
            recovery = {
                "failed_ids": failed_ids,
                "attempted": True,
                "meets": recovery_plan.meets,
                "gaps": recovery_plan.gaps,
                "added_actions": [],
                "recovery_plan": recovery_plan.to_dict(),
            }
            if recovery_plan.meets:
                executed_ids = successful_action_ids(all_receipts)
                extra_actions = [a for a in recovery_plan.selected if a.id not in executed_ids]
                extra_receipts, _ledger = execute_actions(extra_actions, run_dir, logger, chaos)
                all_receipts.extend(extra_receipts)
                recovery["added_actions"] = [a.id for a in extra_actions]
                logger.info(
                    "execute",
                    "兜底重规划成功",
                    failed=failed_ids,
                    added=[a.id for a in extra_actions],
                )
            else:
                logger.fallback(
                    "execute",
                    "剔除失败动作后重规划仍不可达",
                    "保留成功动作，输出剩余缺口",
                )
        final_receipts = all_receipts
        _write_json(run_dir / "action_receipts.json", [r.to_dict() for r in final_receipts])

        effective_ids = successful_action_ids(final_receipts)
        effective_actions = [a for a in actions if a.id in effective_ids]
        after, conservative, meets, gaps = verify_actions(adjusted_metrics, effective_actions, goal)
        failures = sum(1 for r in final_receipts if r.status == "failed")
        result["execution"] = {
            "receipts": [r.to_dict() for r in final_receipts],
            "recovery": recovery,
            "effective_action_ids": [a.id for a in effective_actions],
            "effective_actions": [a.to_dict() for a in effective_actions],
        }
        result["metrics_after"] = {
            "forecast": after,
            "conservative": conservative,
            "meets": meets,
            "gaps": gaps,
            "target": {"margin": goal.target_margin, "dso": goal.dso_target_days},
            "effective_action_ids": [a.id for a in effective_actions],
        }
        if meets:
            result["status"] = "met_after_recovery" if failed_ids else "met"
            result["exit_code"] = 0
        else:
            result["status"] = "partial"
            result["exit_code"] = 4
        logger.info(
            "verify",
            "最终复核完成",
            meets=meets,
            margin=round(after["gross_margin"], 4),
            dso=after["dso_days"],
            failures=failures,
        )
        narrative, llm_label = _make_narrative(result, llm_config, client, logger)
        result["narrative"] = narrative
        result["llm_label"] = llm_label
        _write_json(run_dir / "metrics_after.json", result["metrics_after"])
        _write_json(run_dir / "llm_trace.json", _llm_trace(llm_config, client, narrative))
        result["verification"] = _build_verification(
            rows_count=len(rows),
            customers_count=input_info["customers"],
            anomaly_counts=clean_result.anomaly_counts,
            removed_revenue=clean_result.stats["removed_revenue"],
            goal=goal,
            before=adjusted_metrics,
            after=after,
            effective_actions=effective_actions,
            plan=plan,
            meets=meets,
            failures=failures,
            artifacts_exist=True,
        )
        _finalize(result, run_dir, logger)
        return result

    except (DataQualityError, GoalParseError, LLMError) as exc:
        issues = getattr(exc, "issues", []) or []
        logger.error("error", f"{type(exc).__name__}: {exc}", issues=issues)
        result["status"] = "failed"
        result["exit_code"] = 2
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["issues"] = issues
        _write_json(
            run_dir / "summary.json",
            {
                "status": "failed",
                "exit_code": 2,
                "run_id": run_id,
                "error": result["error"],
                "issues": issues,
                "chaos": chaos.describe(),
            },
        )
        (run_dir / "report.md").write_text(
            render_failure_report(run_id, exc, issues, {"chaos": chaos.describe()}),
            encoding="utf-8",
        )
        logger.error("error", "已写出失败报告", path=str(run_dir / "report.md"))
        return result
    except Exception as exc:  # noqa: BLE001 - 兜底保护，确保现场落盘
        logger.error(
            "error",
            f"未预期异常：{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc()[:3000],
        )
        result["status"] = "failed"
        result["exit_code"] = 5
        result["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(
            run_dir / "summary.json",
            {"status": "failed", "exit_code": 5, "run_id": run_id, "error": result["error"], "chaos": chaos.describe()},
        )
        (run_dir / "report.md").write_text(
            render_failure_report(run_id, exc, None, {"chaos": chaos.describe()}),
            encoding="utf-8",
        )
        return result


def _make_narrative(
    result: dict[str, Any],
    llm_config: LLMConfig,
    client: LLMClient,
    logger: RunLogger,
) -> tuple[dict[str, str], str]:
    if not llm_config.enabled:
        reason = llm_config.disabled_reason or "模型未启用"
        logger.fallback("narrative", f"模型未启用：{reason}", "使用规则模板生成摘要")
        return (
            {"source": "fallback", "reason": reason, "text": fallback_narrative(result, reason)},
            f"未调用（{reason}）",
        )
    try:
        facts = build_llm_facts(result)
        text = client.narrate(facts)
        customer_names = {
            row["customer"] for row in result.get("cleaning", {}).get("rows", []) if row.get("customer")
        }
        hit = sorted(name for name in customer_names if name in text)
        if hit:
            raise LLMError(f"摘要包含客户名称：{hit[0]}")
        logger.info("narrative", "LLM 摘要生成成功", chars=len(text))
        return (
            {"source": "llm", "text": text},
            f"{llm_config.provider}/{llm_config.model}（摘要由模型生成）",
        )
    except LLMError as exc:
        logger.fallback("narrative", f"LLM 摘要失败：{exc}", "使用规则模板生成摘要")
        return (
            {"source": "fallback", "reason": str(exc), "text": fallback_narrative(result, str(exc))},
            f"{llm_config.provider}/{llm_config.model}（调用失败，已降级）",
        )


def _llm_trace(llm_config: LLMConfig, client: LLMClient, narrative: dict[str, str]) -> dict[str, Any]:
    return {
        "config": llm_config.describe(),
        "calls": client.trace,
        "narrative_source": narrative.get("source"),
        "narrative_reason": narrative.get("reason", ""),
    }


def _finalize(result: dict[str, Any], run_dir: Path, logger: RunLogger) -> None:
    report_path = run_dir / "report.md"
    report_path.write_text(render_report(result), encoding="utf-8")
    verification = result.get("verification", [])
    passed = sum(1 for item in verification if item["pass"])
    result["report_path"] = str(report_path)
    summary = {
        "status": result["status"],
        "exit_code": result["exit_code"],
        "run_id": result["run_id"],
        "started_at": result["started_at"],
        "period": result["period"],
        "plan_period": result["plan_period"],
        "goal": result["goal"],
        "baseline": {
            "revenue": result["metrics_before"]["adjusted"]["revenue"],
            "gross_profit": result["metrics_before"]["adjusted"]["gross_profit"],
            "gross_margin": result["metrics_before"]["adjusted"]["gross_margin"],
            "ar": result["metrics_before"]["adjusted"]["ar"],
            "dso_days": result["metrics_before"]["adjusted"]["dso_days"],
        },
        "forecast": result["metrics_after"]["forecast"],
        "conservative": result["metrics_after"].get("conservative"),
        "gaps": result["metrics_after"].get("gaps", []),
        "plan_action_ids": [a["id"] for a in result["plan"]["selected"]],
        "effective_action_ids": result["execution"].get("effective_action_ids", []),
        "receipts": {
            "executed": sum(1 for r in result["execution"].get("receipts", []) if r["status"] in {"executed", "already_executed"}),
            "failed": sum(1 for r in result["execution"].get("receipts", []) if r["status"] == "failed"),
        },
        "fallbacks": result.get("fallbacks", []),
        "llm": result.get("llm"),
        "llm_label": result.get("llm_label"),
        "verification": {"passed": passed, "total": len(verification)},
        "artifacts": {
            "report": "report.md",
            "run_log": "run.log",
            "events": "events.jsonl",
            "cleaning": "cleaning_report.json",
            "plan": "plan.json",
            "receipts": "action_receipts.json",
            "metrics_after": "metrics_after.json",
        },
    }
    _write_json(run_dir / "summary.json", summary)
    logger.info(
        "done",
        "运行结束",
        status=result["status"],
        exit_code=result["exit_code"],
        verification=f"{passed}/{len(verification)}",
    )
    # 最后再确认报告存在，用于校验点
    for item in result.get("verification", []):
        if item["check"] == "日志与报告落盘":
            item["actual"] = "存在" if (run_dir / "run.log").exists() and report_path.exists() else "缺失"
            item["pass"] = (run_dir / "run.log").exists() and report_path.exists()
    # 重新计算通过数并更新 summary 中的校验统计
    passed = sum(1 for item in result.get("verification", []) if item["pass"])
    summary["verification"] = {"passed": passed, "total": len(result.get("verification", []))}
    _write_json(run_dir / "summary.json", summary)
    result["summary"] = summary
