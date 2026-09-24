"""Spending guard.

Owner decision: $0 additional spending. No paid data subscriptions, no paid
AI API calls. This module is the single gate every provider passes through:
providers marked `paid = true` in settings are refused, and any request for
a paid call raises PaidCallsBlocked.
"""
from __future__ import annotations

from .config import Settings


class PaidCallsBlocked(RuntimeError):
    pass


def budget_summary(settings: Settings) -> dict:
    b = settings.budget
    return {
        "ceiling_usd": float(b["total_ceiling_usd"]),
        "period": b["period"],
        "covers": list(b["covers"]),
        "safety_margin_usd": float(b["safety_margin_usd"]),
        "spent_usd": 0.0,  # no paid call can be made, so nothing can be spent
        "paid_calls_enabled_in_settings": bool(b["paid_calls_enabled"]),
    }


def require_paid_calls_allowed(settings: Settings, estimated_max_cost_usd: float) -> None:
    """Raise unless a paid call is allowed. With the $0 plan it never is."""
    b = budget_summary(settings)
    raise PaidCallsBlocked(
        f"Research paused: budget limit. Ceiling is ${b['ceiling_usd']:.2f} (owner chose $0 additional "
        f"spending). Requested up to ${estimated_max_cost_usd:.2f}; no paid call was made."
    )


def check_provider_is_free(settings: Settings, provider: str) -> None:
    cfg = settings.raw.get("providers", {}).get(provider, {})
    if cfg.get("paid", True):  # unknown providers are treated as paid
        require_paid_calls_allowed(settings, float(cfg.get("cost_per_call_usd", 0.01)))
