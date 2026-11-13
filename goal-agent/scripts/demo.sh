#!/usr/bin/env bash
# 3 分钟录屏用的一键演示脚本：按顺序跑正常闭环和三类兜底。
# 说明：录屏仍需本人开麦讲解；脚本只负责把证据依次摆出来。
set -u
cd "$(dirname "$0")/.."

OUT="runs/recording"
rm -rf "$OUT"

section() {
  printf '\n\n========== %s ==========\n' "$1"
  sleep 1
}

section "1/6 造数：18 行 / 9 客户 / 三类异常各一笔"
python3 scripts/generate_data.py

section "2/6 正常闭环：目标 +3pp / DSO<=45"
python3 -m bizgoal run --data data/business_2026_10.csv --out "$OUT/ok"

section "3/6 模型不可用：规则模板降级"
python3 -m bizgoal run --out "$OUT/llm-down" --chaos llm-down

section "4/6 动作失败：剔除失败动作后重规划"
python3 -m bizgoal run --out "$OUT/recover" --chaos action-fail

section "5/6 坏数据：契约校验失败，退出码 2"
python3 -m bizgoal run --out "$OUT/corrupt" --chaos data-corrupt
printf 'corrupt exit=%s\n' "$?"

section "6/6 目标不可达：输出缺口，退出码 3"
python3 -m bizgoal run --out "$OUT/hard" --goal "本月毛利提 12 个点，回款周期压到 20 天以内"
printf 'hard exit=%s\n' "$?"

printf '\n报告：%s/ok/report.md\n' "$OUT"
printf '日志：%s/ok/run.log\n' "$OUT"
