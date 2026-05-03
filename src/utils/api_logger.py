"""API usage logger — appends to logs/api_usage.jsonl.

Called from all Gemini HTTP wrappers to track token counts and estimated cost.
Sync-safe: plain file I/O, no async required.
"""
import json
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parents[2] / "logs" / "api_usage.jsonl"

# USD per 1M tokens — verify at ai.google.dev/pricing (prices change)
_PRICE_TABLE: dict[str, dict] = {
    "gemini-2.5-flash": {
        "input_per_m":    0.30,
        "output_per_m":   2.50,
        "thinking_per_m": 3.50,
    },
    "gemini-2.5-flash-lite": {
        "input_per_m":    0.10,
        "output_per_m":   0.40,
        "thinking_per_m": 0.0,
    },
}
_DEFAULT_PRICE = {"input_per_m": 0.30, "output_per_m": 2.50, "thinking_per_m": 3.50}

# Alert thresholds (used by api_cost_check.py)
DAILY_COST_ALERT_USD   = 1.0
MONTHLY_COST_ALERT_USD = 20.0


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  thinking_tokens: int = 0) -> float:
    p = _PRICE_TABLE.get(model, _DEFAULT_PRICE)
    return (
        (input_tokens    / 1_000_000) * p["input_per_m"]
        + (output_tokens / 1_000_000) * p["output_per_m"]
        + (thinking_tokens / 1_000_000) * p["thinking_per_m"]
    )


def log_usage(
    api_provider: str,
    model: str,
    script: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
    success: bool = True,
) -> float:
    """Append one record to api_usage.jsonl. Returns estimated cost in USD."""
    cost = estimate_cost(model, input_tokens, output_tokens, thinking_tokens)
    record = {
        "logged_at":      datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "api_provider":   api_provider,
        "model":          model,
        "script":         script,
        "input_tokens":   input_tokens,
        "output_tokens":  output_tokens,
        "thinking_tokens": thinking_tokens,
        "cost_usd":       round(cost, 6),
        "success":        success,
    }
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [api_logger] write error: {e}")
    return cost
