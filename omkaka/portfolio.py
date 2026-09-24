"""Paper portfolio (Phase 5). Paper money only: no broker, no real orders.

Rules (also shown on the Portfolio page):
  * Orders exist only when YOU place them. The daily candidate is never bought automatically.
  * Fill price: the CLOSE of the first trading day that ends after you placed the order
    (a market-on-close assumption), made slightly worse by slippage. A price that
    was not yet known when you decided is never back-dated onto an earlier decision.
  * No price within [portfolio].unfilled_after_trading_days trading days -> order rejected.
  * At most [portfolio].max_holdings holdings; no margin (cash cannot go negative); no short selling.
  * Dividends and splits come from YOUR entries (with a source note). Without them,
    values are price-only, and big one-day moves are flagged as possible splits.
  * SPY comparison: every deposit/withdrawal is mirrored into SPY at the same fill rule,
    so both sides get identical money at identical times. Both are compared price-only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from . import journal
from .db import transaction
from .market_calendar import trading_days_between
from .timeutil import NEW_YORK, format_new_york, parse_utc_iso, to_utc_iso, utc_now

PROVIDER = "massive"


def _cfg(settings) -> dict:
    return settings.raw["portfolio"]


def _benchmark(settings) -> str:
    return settings.raw.get("benchmark", {}).get("ticker", "SPY")


# ------------------------------------------------------------------ prices

def first_close_after(conn, ticker: str, moment_iso: str, now_iso: str):
    """The first close that happened after `moment` and that we actually had by `now`."""
    return conn.execute(
        """SELECT * FROM market_bars WHERE ticker = ? AND provider = ? AND effective_at > ?
           AND effective_at <= ? AND fetched_at <= ? ORDER BY trade_date LIMIT 1""",
        (ticker.upper(), PROVIDER, moment_iso, now_iso, now_iso)).fetchone()


def close_on_or_before(conn, ticker: str, day: str):
    return conn.execute("SELECT * FROM market_bars WHERE ticker=? AND provider=? AND trade_date <= ? "
                        "ORDER BY trade_date DESC LIMIT 1", (ticker.upper(), PROVIDER, day)).fetchone()


def close_on(conn, ticker: str, day: str):
    return conn.execute("SELECT * FROM market_bars WHERE ticker=? AND provider=? AND trade_date = ?",
                        (ticker.upper(), PROVIDER, day)).fetchone()


# ------------------------------------------------------------------ actions (explicit, by you)

def cash_movement(conn, kind: str, amount: float, note: str | None = None, now: datetime | None = None) -> None:
    if kind not in ("deposit", "withdrawal"):
        raise ValueError("kind must be deposit or withdrawal")
    if amount <= 0:
        raise ValueError("amount must be positive")
    now = now or utc_now()
    if kind == "withdrawal" and amount > state(conn, now=now).cash + 1e-9:
        raise ValueError("Cannot withdraw more paper cash than you have.")
    with transaction(conn):
        conn.execute("INSERT INTO paper_cash(kind, amount, decided_at, note, fetched_at) VALUES (?, ?, ?, ?, ?)",
                     (kind, float(amount), to_utc_iso(now), note, to_utc_iso(now)))
    journal.append_entry(conn, "paper_trade", f"Paper {kind}: ${amount:,.2f}", note or f"Paper {kind}.",
                         payload={"kind": kind, "amount": amount}, now=now)


def place_order(conn, settings, side: str, ticker: str, quantity: float, note: str | None = None,
                linked_entry_id: str | None = None, now: datetime | None = None) -> str:
    now = now or utc_now()
    ticker = ticker.strip().upper()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    if not ticker.replace(".", "").replace("-", "").isalnum() or len(ticker) > 10:
        raise ValueError(f"{ticker!r} does not look like a ticker")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if linked_entry_id and not conn.execute("SELECT 1 FROM journal_entries WHERE entry_id=?",
                                            (linked_entry_id,)).fetchone():
        raise ValueError(f"No journal entry {linked_entry_id}")
    st = state(conn, now=now)
    pending = pending_orders(conn)
    if side == "sell":
        pending_sells = sum(o["quantity"] for o in pending if o["side"] == "sell" and o["ticker"] == ticker)
        if quantity > st.holdings.get(ticker, 0) - pending_sells + 1e-9:
            raise ValueError("Cannot sell more shares than you hold (no short selling).")
    else:
        names = set(st.holdings) | {o["ticker"] for o in pending if o["side"] == "buy"}
        if ticker not in names and len(names) >= int(_cfg(settings)["max_holdings"]):
            raise ValueError(f"Portfolio is limited to {_cfg(settings)['max_holdings']} holdings.")
    order_id = f"po-{uuid.uuid4().hex[:12]}"
    with transaction(conn):
        conn.execute("""INSERT INTO paper_orders(order_id, side, ticker, quantity, decided_at, note, linked_entry_id,
                        fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (order_id, side, ticker, float(quantity), to_utc_iso(now), note, linked_entry_id, to_utc_iso(now)))
    journal.append_entry(conn, "paper_trade", f"Paper order placed: {side} {quantity:g} {ticker}",
                         f"Will fill at the close of the first trading day ending after "
                         f"{format_new_york(now)}. {note or ''}".strip(),
                         payload={"order_id": order_id, "side": side, "ticker": ticker, "quantity": quantity,
                                  "linked_entry_id": linked_entry_id}, ticker=ticker, now=now)
    return order_id


def record_corporate_action(conn, kind: str, ticker: str, ex_date: str, value: float, source_note: str,
                            now: datetime | None = None) -> None:
    date.fromisoformat(ex_date)
    now = now or utc_now()
    with transaction(conn):
        conn.execute("""INSERT INTO paper_corporate_actions(kind, ticker, ex_date, amount_per_share, split_ratio,
                        source_note, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (kind, ticker.upper(), ex_date, value if kind == "dividend" else None,
                      value if kind == "split" else None, source_note, to_utc_iso(now)))
    journal.append_entry(conn, "paper_trade", f"Paper {kind} recorded: {ticker.upper()} ex-date {ex_date}",
                         f"{kind} {value:g} ({source_note})", ticker=ticker, now=now,
                         payload={"kind": kind, "ticker": ticker.upper(), "ex_date": ex_date, "value": value})


# ------------------------------------------------------------------ fills

def pending_orders(conn) -> list:
    return conn.execute("""SELECT o.* FROM paper_orders o LEFT JOIN paper_fills f ON f.order_id = o.order_id
                           WHERE f.order_id IS NULL ORDER BY o.decided_at""").fetchall()


def fill_pending(conn, settings, now: datetime | None = None) -> list[dict]:
    """Fill (or reject) waiting orders using only prices available by `now`."""
    now = now or utc_now()
    now_iso = to_utc_iso(now)
    cfg = _cfg(settings)
    slip = float(cfg["slippage_bps"]) / 10_000
    fee = float(cfg["fee_per_trade_usd"])
    results = []
    for o in pending_orders(conn):
        bar = first_close_after(conn, o["ticker"], o["decided_at"], now_iso)
        if bar is None:
            decided = parse_utc_iso(o["decided_at"]).astimezone(NEW_YORK).date()
            waited = len(trading_days_between(decided, now.astimezone(NEW_YORK).date()))
            if waited >= int(cfg["unfilled_after_trading_days"]):
                results.append(_reject(conn, o, f"No price for {o['ticker']} within {waited} trading days "
                                                f"(ticker may be unsupported or halted).", now))
            continue
        price = bar["close"] * (1 + slip if o["side"] == "buy" else 1 - slip)
        st = state(conn, as_of_date=bar["trade_date"], now=now)
        if o["side"] == "buy":
            cost = price * o["quantity"] + fee
            if cost > st.cash + 1e-9:
                results.append(_reject(conn, o, f"Not enough paper cash (${st.cash:,.2f} available, "
                                                f"${cost:,.2f} needed at the fill price).", now))
                continue
            if o["ticker"] not in st.holdings and len(st.holdings) >= int(cfg["max_holdings"]):
                results.append(_reject(conn, o, f"Would exceed {cfg['max_holdings']} holdings.", now))
                continue
            cash_change = -cost
        else:
            if o["quantity"] > st.holdings.get(o["ticker"], 0) + 1e-9:
                results.append(_reject(conn, o, "Not enough shares to sell (no short selling).", now))
                continue
            cash_change = price * o["quantity"] - fee
        with transaction(conn):
            conn.execute("""INSERT INTO paper_fills(order_id, status, price, close_price, price_date, price_effective_at,
                            slippage_bps, fee, cash_change, fetched_at) VALUES (?, 'filled', ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (o["order_id"], round(price, 6), bar["close"], bar["trade_date"], bar["effective_at"],
                          float(cfg["slippage_bps"]), fee, round(cash_change, 6), now_iso))
        journal.append_entry(conn, "paper_trade", f"Paper order filled: {o['side']} {o['quantity']:g} {o['ticker']}",
                             f"Filled at ${price:,.4f} (close ${bar['close']:,.4f} on {bar['trade_date']}, "
                             f"{cfg['slippage_bps']} bps slippage, fee ${fee:.2f}).",
                             payload={"order_id": o["order_id"], "price": price, "price_date": bar["trade_date"]},
                             ticker=o["ticker"], now=now)
        results.append({"order_id": o["order_id"], "status": "filled", "price": price, "price_date": bar["trade_date"]})
    return results


def _reject(conn, o, reason: str, now: datetime) -> dict:
    with transaction(conn):
        conn.execute("INSERT INTO paper_fills(order_id, status, reason, fetched_at) VALUES (?, 'rejected', ?, ?)",
                     (o["order_id"], reason, to_utc_iso(now)))
    journal.append_entry(conn, "paper_trade", f"Paper order rejected: {o['side']} {o['quantity']:g} {o['ticker']}",
                         reason, payload={"order_id": o["order_id"]}, ticker=o["ticker"], now=now)
    return {"order_id": o["order_id"], "status": "rejected", "reason": reason}


# ------------------------------------------------------------------ state and valuation

@dataclass
class State:
    cash: float = 0.0
    holdings: dict = field(default_factory=dict)      # ticker -> shares
    cost_basis: dict = field(default_factory=dict)    # ticker -> total cost of current shares
    contributions: float = 0.0
    dividends: float = 0.0


def _events(conn, as_of_date: str | None, now_iso: str) -> list[tuple]:
    """Everything that changes cash or shares, in time order: (sort_key, kind, row)."""
    ev = []
    for r in conn.execute("SELECT * FROM paper_cash WHERE fetched_at <= ?", (now_iso,)):
        ev.append((r["decided_at"], "cash", r))
    for r in conn.execute("""SELECT f.*, o.side, o.ticker, o.quantity FROM paper_fills f JOIN paper_orders o
                             ON o.order_id = f.order_id WHERE f.status = 'filled' AND f.fetched_at <= ?""", (now_iso,)):
        ev.append((r["price_effective_at"], "fill", r))
    for r in conn.execute("SELECT * FROM paper_corporate_actions WHERE fetched_at <= ?", (now_iso,)):
        ev.append((r["ex_date"] + "T00:00:00.000000+00:00", "action", r))
    ev.sort(key=lambda e: e[0])
    if as_of_date:
        ev = [e for e in ev if e[0][:10] <= as_of_date]
    return ev


def state(conn, as_of_date: str | None = None, now: datetime | None = None) -> State:
    s = State()
    for _, kind, r in _events(conn, as_of_date, to_utc_iso(now or utc_now())):
        if kind == "cash":
            sign = 1 if r["kind"] == "deposit" else -1
            s.cash += sign * r["amount"]
            s.contributions += sign * r["amount"]
        elif kind == "fill":
            t, q = r["ticker"], r["quantity"]
            s.cash += r["cash_change"]
            if r["side"] == "buy":
                s.holdings[t] = s.holdings.get(t, 0) + q
                s.cost_basis[t] = s.cost_basis.get(t, 0) - r["cash_change"]
            else:
                held = s.holdings.get(t, 0)
                s.cost_basis[t] = s.cost_basis.get(t, 0) * (1 - q / held) if held else 0
                s.holdings[t] = held - q
                if s.holdings[t] <= 1e-9:
                    s.holdings.pop(t, None)
                    s.cost_basis.pop(t, None)
        elif kind == "action" and r["ticker"] in s.holdings:
            if r["kind"] == "split":
                s.holdings[r["ticker"]] *= r["split_ratio"]
            else:
                amount = s.holdings[r["ticker"]] * r["amount_per_share"]
                s.cash += amount
                s.dividends += amount
    return s


def valuation(conn, settings, as_of_date: str | None = None, now: datetime | None = None) -> dict:
    """Current value. A holding without a price is 'Data unavailable', never zero or cost."""
    now = now or utc_now()
    st = state(conn, as_of_date, now)
    day = as_of_date or now.date().isoformat()
    rows, missing, total = [], [], st.cash
    split_pct = float(_cfg(settings)["split_warning_move_pct"])
    for t, q in sorted(st.holdings.items()):
        bar = close_on_or_before(conn, t, day)
        flags = []
        if bar is None:
            missing.append(t)
        else:
            prev = conn.execute("SELECT close FROM market_bars WHERE ticker=? AND provider=? AND trade_date < ? "
                                "ORDER BY trade_date DESC LIMIT 1", (t, PROVIDER, bar["trade_date"])).fetchone()
            if prev and abs(bar["close"] / prev["close"] - 1) * 100 >= split_pct and not conn.execute(
                    "SELECT 1 FROM paper_corporate_actions WHERE ticker=? AND kind='split'", (t,)).fetchone():
                flags.append("possible split or corporate action: verify and record it")
        value = None if bar is None else q * bar["close"]
        if value is not None:
            total += value
        rows.append({"ticker": t, "shares": q, "close": None if bar is None else bar["close"],
                     "close_date": None if bar is None else bar["trade_date"], "value": value,
                     "cost_basis": st.cost_basis.get(t), "flags": flags})
    return {"cash": st.cash, "holdings": rows, "total": None if missing else total, "partial_total": total,
            "missing_prices": missing, "contributions": st.contributions, "dividends": st.dividends}


# ------------------------------------------------------------------ SPY comparison

def history(conn, settings, now: datetime | None = None) -> list[dict]:
    """Daily values for the portfolio and the SPY mirror, price-only for both.

    SPY mirror: each deposit buys SPY (withdrawal sells) at the close of the first trading
    day ending after the cash movement, with the same slippage. Until then it is cash.
    """
    now = now or utc_now()
    now_iso = to_utc_iso(now)
    bm = _benchmark(settings)
    slip = float(_cfg(settings)["slippage_bps"]) / 10_000
    cash_rows = conn.execute("SELECT * FROM paper_cash WHERE fetched_at <= ? ORDER BY decided_at", (now_iso,)).fetchall()
    if not cash_rows:
        return []
    start = cash_rows[0]["decided_at"][:10]
    days = [r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM market_bars WHERE ticker=? AND provider=? "
                                       "AND trade_date >= ? AND effective_at <= ? ORDER BY trade_date",
                                       (bm, PROVIDER, start, now_iso))]
    # SPY mirror: each cash movement converts to SPY units at its first close; until then it is cash.
    mirror = []  # (fill trade_date or None, signed amount, signed SPY units)
    for r in cash_rows:
        sign = 1 if r["kind"] == "deposit" else -1
        bar = first_close_after(conn, bm, r["decided_at"], now_iso)
        units = 0.0 if bar is None else sign * r["amount"] / (bar["close"] * (1 + slip if sign > 0 else 1 - slip))
        mirror.append((r["decided_at"][:10], None if bar is None else bar["trade_date"], sign * r["amount"], units))
    out = []
    for d in days:
        st = state(conn, d, now)
        value, complete = st.cash, True
        for t, q in st.holdings.items():
            bar = close_on_or_before(conn, t, d)
            if bar is None:
                complete = False
            else:
                value += q * bar["close"]
        spy_close = close_on(conn, bm, d)["close"]
        contributed = sum(amt for (dec, _, amt, _) in mirror if dec <= d)
        spy_value = sum(u * spy_close if (fd and fd <= d) else amt
                        for (dec, fd, amt, u) in mirror if dec <= d)
        out.append({"date": d, "portfolio": value if complete else None, "spy_mirror": spy_value,
                    "contributions": contributed,
                    "portfolio_return": (value / contributed - 1) if complete and contributed else None,
                    "spy_return": (spy_value / contributed - 1) if contributed else None})
    return out


# ------------------------------------------------------------------ research -> outcome

def candidate_outcomes(conn, settings, now: datetime | None = None) -> list[dict]:
    """How each published candidate moved afterwards vs SPY (hypothetical; not trades; price-only).

    Start: the close on the brief's trading date (the first close after a pre-market brief).
    """
    import json

    now_iso = to_utc_iso(now or utc_now())
    bm = _benchmark(settings)
    out = []
    for e in conn.execute("SELECT * FROM journal_entries WHERE entry_type='research_brief' ORDER BY entry_seq"):
        p = json.loads(e["payload_json"])
        t, day = p.get("candidate"), p.get("trading_date")
        if not t or not day:
            continue
        start = conn.execute("SELECT * FROM market_bars WHERE ticker=? AND provider=? AND trade_date >= ? "
                             "AND effective_at <= ? ORDER BY trade_date LIMIT 1", (t, PROVIDER, day, now_iso)).fetchone()
        end = conn.execute("SELECT * FROM market_bars WHERE ticker=? AND provider=? AND effective_at <= ? "
                           "ORDER BY trade_date DESC LIMIT 1", (t, PROVIDER, now_iso)).fetchone()
        row = {"entry_id": e["entry_id"], "ticker": t, "brief_date": day, "check": p.get("validation", {}).get("status")}
        if start is None or end is None or end["trade_date"] <= start["trade_date"]:
            row.update({"status": "Data unavailable (no later closing price yet)"})
        else:
            s_bm, e_bm = close_on(conn, bm, start["trade_date"]), close_on(conn, bm, end["trade_date"])
            row.update({"from": start["trade_date"], "to": end["trade_date"],
                        "return": end["close"] / start["close"] - 1,
                        "spy_return": (e_bm["close"] / s_bm["close"] - 1) if s_bm and e_bm else None,
                        "status": "ok"})
        out.append(row)
    return out
