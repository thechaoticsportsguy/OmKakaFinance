"""Choosing ONE candidate, or "No qualifying candidate" (Phase 4). No AI.

A shortlisted company must pass every gate in [selection] (settings.toml).
Among companies that pass, the highest prioritization score is chosen.
If none pass, the result is "No qualifying candidate" with each company's reasons.
"""
from __future__ import annotations

import json

from .config import load_settings
from .db import db_mode


def _settings_for(conn, settings):
    return settings or load_settings(db_mode(conn))


def gate_failures(conn, run_id: str, row, settings=None) -> list[str]:
    cfg = _settings_for(conn, settings).raw["selection"]
    fails = []
    if row["outcome"] != "passed":
        fails.append(f"screening outcome is '{row['outcome']}'")
    if row["score"] is None or row["score"] < cfg["min_priority_score"]:
        fails.append(f"priority score {row['score']} below minimum {cfg['min_priority_score']}")
    checks = {c["source"]: c["status"] for c in conn.execute(
        "SELECT source, status FROM source_checks WHERE run_id=? AND ticker=? ORDER BY check_id", (run_id, row["ticker"]))}
    for src in cfg["required_sources"]:
        if checks.get(src) != "OK":
            fails.append(f"required source '{src}' status is {checks.get(src, 'not checked')}")
    for flag in json.loads(row["flags_json"]):
        if any(flag.startswith(b) or b in flag for b in cfg["blocking_flags"]):
            fails.append(f"blocking risk flag: {flag}")
    inputs = json.loads(row["inputs_json"])
    if cfg["require_recent_catalyst"] and not (inputs.get("catalyst_8k") or inputs.get("independent_news_stories")):
        fails.append("no identifiable recent catalyst (no recent 8-K and no news story)")
    return fails


def select_candidate(conn, run_id: str, settings=None) -> tuple[object | None, list[dict]]:
    """Returns (chosen shortlist row or None, eligibility table for every shortlisted company)."""
    rows = conn.execute("SELECT * FROM screening_results WHERE run_id=? AND stage='shortlist' "
                        "ORDER BY score IS NULL, score DESC, ticker", (run_id,)).fetchall()
    table, chosen = [], None
    for row in rows:
        fails = gate_failures(conn, run_id, row, settings)
        table.append({"ticker": row["ticker"], "score": row["score"], "eligible": not fails, "reasons": fails})
        if not fails and chosen is None:
            chosen = row
    return chosen, table
