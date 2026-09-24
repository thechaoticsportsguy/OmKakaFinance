"""Screening rules, written as plain calculations so they are easy to check.

Stage 1 (prefilter) looks at every listed company using cheap data:
    exchange, ticker type, price, market cap, liquidity, recent filings.
Stage 2 (shortlist) adds per-company filings, fundamentals, news, Reddit,
and turns them into RISK FLAGS.

Outcomes are never fudged:
    passed             met every rule
    failed_threshold   had the data, failed a rule
    insufficient_data  a required input was missing/stale -> cannot pass

The score is a PRIORITIZATION tool (0-100), NOT a probability of success.
Reddit/social activity never adds points; it can only raise flags.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

DILUTION_FORMS = ("S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-3", "424B1", "424B2", "424B3", "424B4", "424B5")
LATE_FILING_FORMS = ("NT 10-K", "NT 10-Q")
CATALYST_8K_ITEMS = {"1.01": "material agreement", "2.02": "results of operations", "7.01": "Reg FD disclosure",
                     "8.01": "other events", "5.02": "officer/director change", "2.01": "acquisition/disposition"}


@dataclass
class Evaluation:
    ticker: str
    outcome: str
    reasons: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    score: float | None = None


def price_volume_stats(bars: list[dict]) -> dict:
    """bars: oldest -> newest, each with trade_date/close/volume."""
    if not bars:
        return {"days": 0}
    last = bars[-1]
    prior = bars[:-1]
    avg_dollar = sum(b["close"] * b["volume"] for b in bars) / len(bars)
    avg_prior_vol = (sum(b["volume"] for b in prior) / len(prior)) if prior else None
    first = bars[0]["close"]
    prev_close = prior[-1]["close"] if prior else None
    return {
        "days": len(bars),
        "last_date": last["trade_date"],
        "last_close": last["close"],
        "last_volume": last["volume"],
        "avg_dollar_volume": avg_dollar,
        "volume_ratio": (last["volume"] / avg_prior_vol) if avg_prior_vol else None,
        "return_period": (last["close"] / first - 1) if first else None,
        "one_day_change": (last["close"] / prev_close - 1) if prev_close else None,
    }


def prefilter(member: dict, bars: list[dict], shares: dict | None, filings_8k: int | None,
              cfg: dict, latest_trade_date: str | None) -> Evaluation:
    t = member["ticker"]
    ev = Evaluation(t, "passed")
    for pattern in cfg["exclude_ticker_patterns"]:
        if re.search(pattern, t):
            ev.outcome, ev.reasons = "failed_threshold", [f"ticker type excluded (pattern {pattern})"]
            return ev
    if member.get("exchange") not in cfg["allowed_exchanges"]:
        ev.outcome, ev.reasons = "failed_threshold", [f"exchange {member.get('exchange') or 'unknown'} not allowed"]
        return ev

    pv = price_volume_stats(bars)
    missing = []
    if pv["days"] == 0:
        missing.append("price data unavailable")
    elif pv["days"] < cfg["min_days_of_data"]:
        missing.append(f"only {pv['days']} days of prices (need {cfg['min_days_of_data']})")
    elif latest_trade_date and pv["last_date"] != latest_trade_date:
        missing.append(f"price stale: last trade date {pv['last_date']}, market's latest {latest_trade_date}")
    if not shares:
        missing.append("shares outstanding unavailable (not in SEC frames)")
    if missing:
        ev.outcome, ev.reasons = "insufficient_data", missing
        return ev

    mcap = pv["last_close"] * shares["val"]
    ev.inputs = {
        "last_close": pv["last_close"], "last_date": pv["last_date"],
        "shares_outstanding": shares["val"], "shares_as_of": shares["end"], "shares_accn": shares.get("accn"),
        "market_cap": mcap, "market_cap_formula": "last_close * shares_outstanding",
        "avg_dollar_volume": pv["avg_dollar_volume"], "days": pv["days"],
        "volume_ratio": pv["volume_ratio"], "return_period": pv["return_period"],
        "one_day_change": pv["one_day_change"], "recent_8k_filings": filings_8k,
    }
    fails = []
    if pv["last_close"] < cfg["min_price_usd"]:
        fails.append(f"price ${pv['last_close']:.2f} below ${cfg['min_price_usd']:.2f}")
    if mcap < cfg["min_market_cap_usd"]:
        fails.append(f"market cap ${mcap / 1e6:,.0f}M below minimum")
    if mcap > cfg["max_market_cap_usd"]:
        fails.append(f"market cap ${mcap / 1e6:,.0f}M above maximum")
    if pv["avg_dollar_volume"] < cfg["min_avg_dollar_volume_usd"]:
        fails.append(f"avg daily $ volume ${pv['avg_dollar_volume'] / 1e6:,.2f}M below minimum")
    if fails:
        ev.outcome, ev.reasons = "failed_threshold", fails
        return ev

    ev.components = score_components(ev.inputs, cfg)
    ev.score = round(sum(c["points"] for c in ev.components.values()), 1)
    return ev


def score_components(inputs: dict, cfg: dict) -> dict:
    """Each component shows its input, its points, and the maximum possible.
    A missing input earns 0 points and is labeled 'unavailable', never guessed."""
    w = cfg["weights"]
    comps = {}

    def add(name, raw, fraction, note):
        comps[name] = {"input": raw, "points": round(w[name] * fraction, 1) if fraction is not None else 0.0,
                       "max": w[name], "note": note if fraction is not None else "unavailable (0 points)"}

    vr = inputs.get("volume_ratio")
    add("volume_spike", vr, None if vr is None else min(max(vr - 1, 0) / 2, 1), "latest volume / prior average; 3x = full points")
    r = inputs.get("return_period")
    add("momentum", r, None if r is None else min(max(r, 0) / 0.3, 1), "20-day price change; +30% = full points")
    n = inputs.get("recent_8k_filings")
    add("recent_filings", n, None if n is None else min(n, 2) / 2, "8-K filings in catalyst window; 2+ = full points")
    lo, hi, m = cfg["min_market_cap_usd"], cfg["max_market_cap_usd"], inputs["market_cap"]
    add("smaller_size", m, (math.log(hi) - math.log(m)) / (math.log(hi) - math.log(lo)),
        "position in market-cap range; smallest = full points")
    return comps


def shortlist_flags(inputs: dict, filings: list[dict] | None, fundamentals: dict, reddit_summary: dict | None,
                    cfg: dict) -> list[str]:
    """Risk flags (never bonus points). filings=None means filings were unavailable."""
    f_cfg = cfg["flags"]
    flags = []
    if filings is None:
        flags.append("FILINGS UNAVAILABLE: cannot check dilution or disclosures")
    else:
        dil = sorted({f["form"] for f in filings if f["form"] in DILUTION_FORMS})
        if dil:
            flags.append(f"possible financing/dilution: recent {', '.join(dil)} filing(s)")
        late = sorted({f["form"] for f in filings if f["form"] in LATE_FILING_FORMS})
        if late:
            flags.append(f"late-filing notice: {', '.join(late)}")
    now_sh, old_sh = fundamentals.get("shares_outstanding"), fundamentals.get("shares_outstanding_year_ago")
    if now_sh and old_sh:
        growth = now_sh / old_sh - 1
        if growth * 100 > f_cfg["dilution_share_growth_pct"]:
            flags.append(f"share count up {growth:.0%} in about a year (dilution)")
    ocf = fundamentals.get("operating_cash_flow")
    if ocf is not None and ocf < 0:
        flags.append("negative operating cash flow (may need financing)")
    if inputs.get("avg_dollar_volume") is not None and \
            inputs["avg_dollar_volume"] < cfg["min_avg_dollar_volume_usd"] * f_cfg["thin_liquidity_multiple"]:
        flags.append("thin liquidity (hard to buy/sell without moving the price)")
    move = inputs.get("one_day_change")
    if move is not None and abs(move) * 100 >= f_cfg["price_spike_pct"] and not (filings and any(
            f["form"].startswith("8-K") for f in filings[:5])):
        flags.append(f"{move:+.0%} one-day move with no recent 8-K: check for promotion")
    if reddit_summary and reddit_summary["posts"] >= 10 and reddit_summary["distinct_authors"] <= 3:
        flags.append("many Reddit posts from few authors: possible promotion")
    return flags


def catalyst_filings(filings: list[dict] | None, since_iso: str) -> list[dict]:
    """8-K filings in the catalyst window, with plain-English item names."""
    out = []
    for f in filings or []:
        if f["form"].startswith("8-K") and f["filing_date"] >= since_iso:
            items = [i.strip() for i in (f.get("items") or "").split(",") if i.strip()]
            out.append({**f, "item_names": [CATALYST_8K_ITEMS.get(i, f"item {i}") for i in items]})
    return out
