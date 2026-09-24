"""Checking a research brief against the evidence (Phase 3). No AI involved.

A brief can come from you, from Claude (pasted back from your own Claude
access), or from the app's automatic data-only brief. It uses this format:

    CANDIDATE: DEMOX          (or CANDIDATE: NONE)
    RUN: demo-screen-001
    - [CONFIRMED] Quarterly revenue was $48.2M. [m: m-123] [ev: run:sec:0001-26-000011]
    - [THIRD_PARTY] A news story reported the contract. [ev: run:news:900000100]
    - [AI_ESTIMATE] Revenue grew about 17.6% (48.2 / 41.0 - 1 = 0.176). [m: m-123] [m: m-456]

Each numbered claim is checked against the CITED metric (right company, metric,
period, and source) or the CITED evidence text. Result:
    passed   - nothing wrong found
    flagged  - usable, but read the warnings
    rejected - unsupported numbers, bad citations, wrong labels, or an ineligible candidate
"""
from __future__ import annotations

import ast
import hashlib
import json
import operator
import re
from dataclasses import asdict, dataclass, field

from . import journal, store
from .timeutil import parse_utc_iso

LABELS = ("CONFIRMED", "THIRD_PARTY", "AI_ESTIMATE")
CLAIM_RE = re.compile(r"^\s*[-*]\s*\[(CONFIRMED|THIRD_PARTY|AI_ESTIMATE)\]\s*(.+)$")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s")
APP_LINE_RE = re.compile(r"^\s*[-*]?\s*(SCORE|RISK|MISSING|GATE):\s*(.+)$")
EV_RE = re.compile(r"\[ev:\s*([^\]\s]+)\s*\]")
M_RE = re.compile(r"\[m:\s*([^\]\s]+)\s*\]")
URL_RE = re.compile(r"https?://[^\s)\]>]+")
FORBIDDEN = [
    (re.compile(r"price\s+target|target\s+price", re.I), "price targets are not allowed"),
    (re.compile(r"probabilit|\bodds\b|%\s*chance|chance\s+of\s+(success|winning|gain)", re.I),
     "probabilities of success are not allowed"),
    (re.compile(r"guarantee|can't lose|cannot lose|sure thing|will (double|triple|soar|skyrocket)", re.I),
     "promises of returns are not allowed"),
]
INJECTION = re.compile(r"ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(rules|instructions)|"
                       r"system prompt|you are now", re.I)
# Text that contains digits but is not a quantity claim: dates, IDs, form names, 8-K items, quarters.
NOT_QUANTITIES = [
    re.compile(r"\[(ev|m):[^\]]*\]"),
    URL_RE,
    re.compile(r"\d{4}-\d{2}-\d{2}(\.\.\d{4}-\d{2}-\d{2})?"),
    re.compile(r"\bitems?\s+\d+\.\d+(\s*,\s*\d+\.\d+)*", re.I),
    re.compile(r"\b(NT\s+)?\d{1,3}-[A-Z]{1,2}(/A)?\b"),          # 8-K, 10-Q, 10-K/A
    re.compile(r"\b[A-Z]{1,2}-\d{1,2}(/A)?\b"),                   # S-1, S-3, F-3
    re.compile(r"\b424B\d?\b|\b13[DG]\b", re.I),
    re.compile(r"\b\d{1,2}:\d{2}(\s?[AP]M)?\b", re.I),               # clock times
    re.compile(r"\b(FY|Q)\s?\d{1,4}\b", re.I),
    re.compile(r"\b(19|20)\d{2}\b"),                              # years
]
NUMBER_RE = re.compile(r"(?P<sign>[-−+])?\s?\$?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
                       r"(?P<unit>%|percent\b|[kKmMbB]\b|thousand\b|million\b|billion\b|x\b)?")
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
# A claim mentioning the word should cite a metric whose name (or calculation) contains one of these.
METRIC_WORDS = {"revenue": ("revenue",), "cash": ("cash",), "market cap": ("market_cap",),
                "shares": ("shares", "share_count"), "net income": ("net_income",), "net loss": ("net_income",),
                "price": ("price",), "volume": ("volume",)}


@dataclass
class Report:
    run_id: str | None
    candidate: str | None
    status: str = "passed"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)

    def finish(self) -> "Report":
        self.status = "rejected" if self.errors else ("flagged" if self.warnings else "passed")
        return self


@dataclass
class Quantity:
    text: str
    values: list[tuple[float, float]]  # (interpreted value, rounding tolerance)


def extract_quantities(text: str) -> list[Quantity]:
    cleaned = text
    for pattern in NOT_QUANTITIES:
        cleaned = pattern.sub(" ", cleaned)
    out = []
    for m in NUMBER_RE.finditer(cleaned):
        raw = m.group("num").replace(",", "")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        base = float(raw) * (-1 if m.group("sign") in ("-", "−") else 1)
        unit = (m.group("unit") or "").lower()
        tol = 0.5 * 10 ** -decimals
        if unit in ("%", "percent"):
            vals = [(base / 100, tol / 100), (base, tol)]
        elif unit in SCALE:
            vals = [(base * SCALE[unit], tol * SCALE[unit])]
        else:
            vals = [(base, tol)]
        out.append(Quantity(m.group(0).strip(), vals))
    return out


def _matches(q: Quantity, value: float | None) -> bool:
    if value is None:
        return False
    for v, tol in q.values:
        for candidate in (v, -v):  # "a loss of $8M" vs a stored -8,000,000
            if abs(candidate - value) <= tol + 1e-9 * max(1.0, abs(value)):
                return True
    return False


def _in_text(q: Quantity, texts: list[str]) -> bool:
    raw = q.text.lstrip("+-−$ ").replace(",", "")
    num = re.match(r"\d+(\.\d+)?", raw)
    if not num:
        return False
    needle = num.group(0)
    for t in texts:
        if re.search(rf"(?<![\d.]){re.escape(needle)}(?![\d])", (t or "").replace(",", "")):
            return True
    return False


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg}


def _safe_eval(expr: str) -> float | None:
    """Evaluate plain arithmetic only (numbers, + - * / and brackets). Anything else -> None."""
    try:
        tree = ast.parse(expr.replace(",", ""), mode="eval")
    except SyntaxError:
        return None

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("not plain arithmetic")

    try:
        return ev(tree)
    except (ValueError, ZeroDivisionError):
        return None


CALC_RE = re.compile(r"\(?\s*([\d.,\s+\-*/()]+?)\s*=\s*(-?[\d.,]+)\s*\)?")


NUM_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _checked_calculations(text: str, cited_values: list[float]) -> tuple[set[str], list[float], list[str]]:
    """Find 'a / b - 1 = c' style calculations and verify them.

    Inputs must be cited metric values (possibly written in thousands/millions/billions,
    e.g. 48.2 for $48,200,000) or the constants 1 and 100. The result must equal the
    arithmetic. Returns (number texts used and proven, proven results, problems).
    """
    used, results, problems = set(), [], []
    for m in CALC_RE.finditer(text):
        expr, result = m.group(1).strip(), m.group(2).replace(",", "").rstrip(".")
        while expr.count("(") > expr.count(")"):  # drop an unmatched opening bracket from the prose
            expr = expr.replace("(", "", 1).strip()
        if not re.search(r"[+\-*/]", expr.lstrip("-")):
            continue
        value = _safe_eval(expr)
        if value is None:
            continue
        decimals = len(result.split(".")[1]) if "." in result else 0
        if abs(value - float(result)) > 0.5 * 10 ** -decimals + 1e-12:
            problems.append(f"calculation '{expr.strip()} = {result}' is wrong (it equals {value:.6g})")
            continue
        for tok in NUM_TOKEN.findall(expr):
            raw = tok.replace(",", "")
            dec = len(raw.split(".")[1]) if "." in raw else 0
            x = float(raw)
            ok = x in (1.0, 100.0) or any(
                abs(x * scale - v) <= 0.5 * 10 ** -dec * scale + 1e-9 * abs(v)
                for v in cited_values for scale in (1, 1e3, 1e6, 1e9) for v in (v, -v))
            if ok:
                used.add(raw)
            else:
                problems.append(f"calculation input {tok} is not a cited metric value")
        used.add(result)
        results.append(value)
    return used, results, problems


def validate(conn, text: str, run_id: str | None = None) -> Report:
    lines = text.splitlines()
    cand_line = next((l for l in lines if l.strip().upper().startswith("CANDIDATE:")), None)
    run_line = next((l for l in lines if l.strip().upper().startswith("RUN:")), None)
    run_id = run_id or (run_line.split(":", 1)[1].strip() if run_line else None)
    candidate = cand_line.split(":", 1)[1].strip().upper() if cand_line else None
    r = Report(run_id, None if candidate in (None, "NONE") else candidate)

    run = store.run_row(conn, run_id) if run_id else None
    if run is None:
        r.errors.append(f"Unknown or missing run ({run_id!r}). Add a line 'RUN: <run id>' from the packet.")
        return r.finish()
    if cand_line is None:
        r.errors.append("Missing 'CANDIDATE: <TICKER>' or 'CANDIDATE: NONE' line.")
        return r.finish()

    cutoff = run["decision_cutoff_at"]
    usable = {e["evidence_id"]: e for e in store.evidence_for_run(conn, run_id)}
    all_ev = {e["evidence_id"]: e for e in conn.execute("SELECT * FROM evidence WHERE run_id = ?", (run_id,))}
    metrics = {m["metric_id"]: m for m in store.metrics_as_of(conn, parse_utc_iso(cutoff)) if m["run_id"] == run_id}
    known_urls = {e["url"] for e in usable.values() if e["url"]}

    # Candidate eligibility -------------------------------------------------
    if r.candidate:
        row = conn.execute("SELECT * FROM screening_results WHERE run_id=? AND stage='shortlist' AND ticker=?",
                           (run_id, r.candidate)).fetchone()
        if row is None:
            r.errors.append(f"{r.candidate} is not on this run's shortlist.")
        elif row["outcome"] != "passed":
            r.errors.append(f"{r.candidate} is not eligible: screening outcome '{row['outcome']}' "
                            f"({'; '.join(json.loads(row['reasons_json'])) or 'see packet'}).")
        else:
            from .selection import gate_failures
            for g in gate_failures(conn, run_id, row):
                r.errors.append(f"{r.candidate} fails selection gate: {g}")

    # Whole-text checks ------------------------------------------------------
    if INJECTION.search(text):
        r.warnings.append("The brief contains instruction-like text (e.g. 'ignore previous instructions'). "
                          "Check that it was not copied from untrusted evidence.")
    for url in URL_RE.findall(text):
        if url not in known_urls and not re.match(r"https://(finviz\.com|www\.sec\.gov)/", url):
            r.warnings.append(f"Link not from the packet's evidence: {url}")

    # Claims ---------------------------------------------------------------
    n_claims = 0
    cand_row = conn.execute("SELECT * FROM screening_results WHERE run_id=? AND stage='shortlist' AND ticker=?",
                            (run_id, r.candidate)).fetchone() if r.candidate else None
    for i, line in enumerate(lines, 1):
        app = APP_LINE_RE.match(line)
        if app:
            problem = _check_app_line(conn, run_id, r.candidate, cand_row, app.group(1), app.group(2).strip())
            if problem:
                r.errors.append(f"Line {i} [{app.group(1)}]: {problem}")
            continue
        m = CLAIM_RE.match(line)
        if not m:
            if (not CHECKBOX_RE.match(line) and not line.lstrip().startswith("#")
                    and not line.strip().upper().startswith(("CANDIDATE:", "RUN:")) and extract_quantities(line)):
                r.warnings.append(f"Line {i} has a number but no label, so it cannot be checked: {line.strip()[:90]}")
            continue
        n_claims += 1
        label, body = m.group(1), m.group(2)
        claim = {"line": i, "label": label, "text": body.strip(), "problems": []}
        r.claims.append(claim)
        problems = claim["problems"]
        for pattern, msg in FORBIDDEN:
            if pattern.search(body):
                problems.append(msg)

        ev_ids, m_ids = EV_RE.findall(body), M_RE.findall(body)
        cited_ev, cited_m = [], []
        for eid in ev_ids:
            if eid in usable:
                cited_ev.append(usable[eid])
            elif eid in all_ev:
                problems.append(f"evidence {eid} was fetched after the decision cutoff; it cannot be used")
            else:
                problems.append(f"evidence {eid} does not exist in this run")
        for mid in m_ids:
            if mid in metrics:
                cited_m.append(metrics[mid])
            else:
                problems.append(f"metric {mid} does not exist in this run (or is after the cutoff)")
        for item in cited_ev + cited_m:
            t = item["ticker"]
            if r.candidate and t and t != r.candidate and t not in body.upper():
                problems.append(f"cites {t} data in a claim about {r.candidate}; name {t} explicitly if intended")

        # Label rules.
        primary = any(e["is_primary_source"] for e in cited_ev) or any(
            mm["value_kind"] == "REPORTED" and mm["provider"] == "sec" for mm in cited_m)
        if label == "CONFIRMED" and not primary:
            problems.append("CONFIRMED needs a primary source (SEC filing or company release, or a reported SEC metric)")
        if label == "THIRD_PARTY" and not (cited_ev or cited_m):
            problems.append("THIRD_PARTY needs a cited evidence ID")
        if label == "THIRD_PARTY" and cited_ev and all(e["is_primary_source"] for e in cited_ev):
            r.warnings.append(f"Line {i}: THIRD_PARTY claim cites only primary sources (could be CONFIRMED).")

        # Metric-word consistency.
        lower = body.lower()
        for word, parts in METRIC_WORDS.items():
            if word in lower and cited_m and not any(
                    p in mm["metric"] or p in (mm["calculation_json"] or "") for mm in cited_m for p in parts):
                r.warnings.append(f"Line {i} mentions '{word}' but cites no {word} metric.")

        # Numbers.
        proven, results, calc_problems = _checked_calculations(
            body, [mm["value"] for mm in cited_m if mm["value"] is not None])
        problems.extend(calc_problems)
        # Numbers inside a cited metric's period label (e.g. '20 trading days') describe the period.
        ev_texts = [f"{e['title']} {e['excerpt'] or ''}" for e in cited_ev] + [mm["period_label"] for mm in cited_m]
        for q in extract_quantities(body):
            if q.text.lstrip("+-−$ ").replace(",", "") in proven or any(_matches(q, v) for v in results):
                continue
            if any(_matches(q, mm["value"]) for mm in cited_m):
                continue
            if _in_text(q, ev_texts):
                continue
            if _in_text(q, [" ".join(str(v) for mm in cited_m for v in _calc_inputs(mm))]):
                continue
            problems.append(_explain_unsupported(q, r.candidate, metrics, cited_m))
        if problems:
            r.errors.extend(f"Line {i} [{label}]: {p}" for p in problems)

    if r.candidate and n_claims == 0:
        r.errors.append("A candidate brief needs labeled claims ('- [CONFIRMED] ... [ev: ...]').")
    if not r.candidate and n_claims == 0 and len([l for l in lines if l.strip()]) < 3:
        r.warnings.append("'No qualifying candidate' should include a short reason.")
    if not r.candidate:
        from .selection import select_candidate
        chosen, _ = select_candidate(conn, run_id)
        if chosen is not None:
            r.warnings.append(f"{chosen['ticker']} passed every selection gate, but this brief chose no candidate. "
                              "That is allowed; say why in the brief.")
    return r.finish()


def _check_app_line(conn, run_id, candidate, row, kind, text) -> str | None:
    """SCORE / RISK / MISSING lines must match exactly what the app recorded, so they
    cannot be used to slip unsupported numbers into a brief."""
    if kind == "GATE":
        from .selection import gate_failures
        ticker, _, reason = text.partition(":")
        srow = conn.execute("SELECT * FROM screening_results WHERE run_id=? AND stage='shortlist' AND ticker=?",
                            (run_id, ticker.strip())).fetchone()
        if srow is None:
            return f"{ticker.strip()} is not on this run's shortlist"
        recorded = gate_failures(conn, run_id, srow)
        parts = [p.strip() for p in reason.split(";") if p.strip()]
        return None if parts and all(p in recorded for p in parts) else "does not match the recorded gate results"
    if row is None:
        return f"{kind} lines are only allowed in a candidate brief for a shortlisted company"
    if kind == "RISK":
        return None if text in json.loads(row["flags_json"]) else "not one of the app's recorded risk flags"
    if kind == "MISSING":
        reasons = [r[0] for r in conn.execute(
            "SELECT status_reason FROM metric_values WHERE run_id=? AND ticker=? AND status != 'OK' "
            "UNION SELECT status_reason FROM source_checks WHERE run_id=? AND ticker=? AND status_reason IS NOT NULL",
            (run_id, candidate, run_id, candidate))]
        return None if any(r and r in text for r in reasons) else "not a recorded missing-data reason"
    allowed = [row["score"], row["rank"], 100]
    for c in json.loads(row["components_json"]).values():
        allowed += [c["points"], c["max"]]
    for q in extract_quantities(text):
        if not any(_matches(q, a) for a in allowed if a is not None):
            return f"'{q.text}' is not in the recorded score components"
    return None


def _calc_inputs(metric_row) -> list:
    if not metric_row["calculation_json"]:
        return []
    calc = json.loads(metric_row["calculation_json"])
    return list((calc.get("inputs") or {}).values())


def _family(metric: str) -> str:
    """revenue_quarter_prior_year -> revenue_quarter (same measure, different period)."""
    return re.sub(r"_(prior_year|year_ago)$", "", metric)


def _explain_unsupported(q: Quantity, candidate, metrics: dict, cited: list) -> str:
    same_name = {_family(mm["metric"]) for mm in cited}
    for mm in metrics.values():
        if not _matches(q, mm["value"]):
            continue
        if candidate and mm["ticker"] != candidate:
            return f"'{q.text}' matches {mm['ticker']}'s {mm['metric']}, not {candidate}'s (wrong company)"
        if _family(mm["metric"]) in same_name:
            return (f"'{q.text}' matches {mm['metric']} for period {mm['period_label']} "
                    f"({mm['metric_id']}), not the cited period (wrong period)")
        return f"'{q.text}' matches {mm['metric']} ({mm['metric_id']}) but that metric is not cited"
    return f"'{q.text}' is not supported by any cited metric or evidence text"


def save_brief(conn, text: str, report: Report, origin: str, extra: dict | None = None, dedupe_key=None) -> str:
    """Record the brief and its check result in the journal (never overwrites anything)."""
    sha = hashlib.sha256(text.encode()).hexdigest()
    payload = {"brief_markdown": text, "validation": asdict(report), "origin": origin, "sha256": sha,
               "candidate": report.candidate, **(extra or {})}
    if report.status == "rejected":
        return journal.append_entry(conn, "note", f"Rejected brief for run {report.run_id}",
                                    f"Brief ({origin}) failed checks: {len(report.errors)} error(s).",
                                    payload=payload, run_id=report.run_id, ticker=report.candidate,
                                    dedupe_key=f"rejected-{sha}")
    entry_type = "research_brief" if report.candidate else "no_candidate"
    title = (f"Research candidate: {report.candidate}" if report.candidate else "No qualifying candidate")
    if (extra or {}).get("late"):
        title = "LATE — " + title
    return journal.append_entry(conn, entry_type, title,
                                f"{origin}. Check result: {report.status} "
                                f"({len(report.warnings)} warning(s)).",
                                payload=payload, run_id=report.run_id, ticker=report.candidate,
                                dedupe_key=dedupe_key or f"brief-{sha}")
