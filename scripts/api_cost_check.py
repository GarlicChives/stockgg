#!/usr/bin/env python3
"""API usage cost report — reads logs/api_usage.jsonl and prints summary.

Checks if today's spend or 30-day total exceeds alert thresholds.
Safe to run standalone or called from daily_briefing.py.
"""
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.api_logger import DAILY_COST_ALERT_USD, MONTHLY_COST_ALERT_USD, LOG_FILE


def _load_records(days: int = 30) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = []
    if not LOG_FILE.exists():
        return records
    with LOG_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("logged_at", "")[:10] >= cutoff:
                    records.append(r)
            except Exception:
                pass
    return records


def _summarize(records: list[dict]) -> dict:
    by_day    = defaultdict(lambda: {"calls": 0, "cost": 0.0, "failures": 0})
    by_model  = defaultdict(lambda: {"calls": 0, "cost": 0.0})
    by_script = defaultdict(lambda: {"calls": 0, "cost": 0.0})

    for r in records:
        day    = r.get("logged_at", "")[:10]
        cost   = r.get("cost_usd", 0.0)
        model  = r.get("model", "unknown")
        script = r.get("script", "unknown")

        by_day[day]["calls"] += 1
        by_day[day]["cost"]  += cost
        if not r.get("success", True):
            by_day[day]["failures"] += 1

        by_model[model]["calls"] += 1
        by_model[model]["cost"]  += cost
        by_script[script]["calls"] += 1
        by_script[script]["cost"]  += cost

    return {
        "by_day":    dict(by_day),
        "by_model":  dict(by_model),
        "by_script": dict(by_script),
    }


def _check_alerts(summary: dict) -> list[str]:
    alerts = []
    today      = date.today().strftime("%Y-%m-%d")
    today_cost = summary["by_day"].get(today, {}).get("cost", 0.0)
    if today_cost > DAILY_COST_ALERT_USD:
        alerts.append(
            f"⚠ 今日花費 ${today_cost:.4f} 超過警戒線 ${DAILY_COST_ALERT_USD:.2f}"
        )

    monthly_cost = sum(v["cost"] for v in summary["by_day"].values())
    if monthly_cost > MONTHLY_COST_ALERT_USD:
        alerts.append(
            f"⚠ 近30天花費 ${monthly_cost:.4f} 超過警戒線 ${MONTHLY_COST_ALERT_USD:.2f}"
        )

    return alerts


def print_report(days: int = 30) -> list[str]:
    """Print cost report and return alert strings (empty = OK)."""
    records = _load_records(days)
    if not records:
        print("  (無 API 使用紀錄)")
        return []

    summary = _summarize(records)
    today   = date.today().strftime("%Y-%m-%d")
    today_d = summary["by_day"].get(today, {"calls": 0, "cost": 0.0, "failures": 0})
    monthly_calls = sum(v["calls"] for v in summary["by_day"].values())
    monthly_cost  = sum(v["cost"]  for v in summary["by_day"].values())

    print(f"  今日:   {today_d['calls']:3d} 次呼叫  ${today_d['cost']:.4f}"
          + (f"  ({today_d['failures']} 次失敗)" if today_d["failures"] else ""))
    print(f"  近30天: {monthly_calls:3d} 次呼叫  ${monthly_cost:.4f}")
    print()

    print("  按模型:")
    for model, d in sorted(summary["by_model"].items()):
        print(f"    {model}: {d['calls']} 次, ${d['cost']:.4f}")

    print("  按腳本:")
    for script, d in sorted(summary["by_script"].items()):
        print(f"    {script}: {d['calls']} 次, ${d['cost']:.4f}")

    print()
    print("  近7天每日明細:")
    recent = sorted(summary["by_day"].keys())[-7:]
    for day in recent:
        d = summary["by_day"][day]
        marker = " ← 今日" if day == today else ""
        fail   = f"  ({d['failures']}F)" if d.get("failures") else ""
        print(f"    {day}: {d['calls']:3d} 次  ${d['cost']:.4f}{fail}{marker}")

    alerts = _check_alerts(summary)
    print()
    if alerts:
        for a in alerts:
            print(f"  {a}")
    else:
        print("  ✅ 花費在合理範圍內")

    return alerts


if __name__ == "__main__":
    print_report()
