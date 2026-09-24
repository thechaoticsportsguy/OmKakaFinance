"""Spending guard (Phase 1 version).

Phase 1 makes NO paid calls. This module is the single gate any future paid
call must pass through. Right now it always refuses. The real spending
ledger with per-call reservations is built in Phase 3, and paid calls stay
blocked until then AND until the owner explicitly authorizes them.
"""
from __future__ import annotations

from .config import Settings

PHASE_WITH_LEDGER = 3


class PaidCallsBlocked(RuntimeError):
    pass


def budget_summary(settings: Settings) -> dict:
    b = settings.budget
    return {
        "ceiling_usd": float(b["total_ceiling_usd"]),
        "period": b["period"],
        "covers": list(b["covers"]),
        "safety_margin_usd": float(b["safety_margin_usd"]),
        "spent_usd": 0.0,  # nothing can be spent before the Phase 3 ledger exists
        "paid_calls_enabled_in_settings": bool(b["paid_calls_enabled"]),
    }


def require_paid_calls_allowed(settings: Settings, estimated_max_cost_usd: float) -> None:
    """Raise unless a paid call is allowed. In Phase 1 it is never allowed."""
    raise PaidCallsBlocked(
        f"Research paused: budget controls not built yet (arrive in Phase {PHASE_WITH_LEDGER}). "
        f"Requested up to ${estimated_max_cost_usd:.2f}; no paid call was made."
    )
