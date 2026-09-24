# OmKakaFinance

A **local stock-research assistant** that runs on your own computer and opens in your browser.

> **Research tool only.** No broker connection, no real-money orders, no automatic trading.
> It never promises winning stocks. Scores (later phases) are for prioritizing, not win odds.

**Spending plan: $0 additional.** Only free data sources; no paid subscriptions; no paid AI API calls.
The app gathers, screens, and organizes evidence into a **research packet** that you review yourself or with
your existing Claude access.

**Current status: Phase 2 (free data sources, screening, research packet) complete.**
Phases 3–6 are listed under [Roadmap](#roadmap).

---

## Windows setup (one time)

1. Install **Python 3.11 or newer** from <https://www.python.org/downloads/windows/>.
   During install, tick **"Add python.exe to PATH"**.
2. Install **Git** from <https://git-scm.com/download/win>.
3. Open **PowerShell** (Start menu → type "PowerShell") and run:

```powershell
cd $HOME\Documents
git clone -b claude/inspiring-dirac-bivcg6 https://github.com/thechaoticsportsguy/OmKakaFinance.git
cd OmKakaFinance
py -3 --version
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m omkaka init
```

(`py -3 --version` must print 3.11 or higher.) Instead of the last three lines you can double-click `setup_windows.bat`.

## Everyday commands (PowerShell, inside the OmKakaFinance folder)

| What | Command | Or double-click |
|---|---|---|
| Offline demo (fictional data) | `.venv\Scripts\python.exe -m omkaka demo` | `start_demo.bat` |
| Live dashboard | `.venv\Scripts\python.exe -m omkaka app` | `start_app.bat` |
| Rebuild the demo from scratch | `.venv\Scripts\python.exe -m omkaka demo-reset` | |
| Run automated checks | `.venv\Scripts\python.exe -m pytest` | `run_checks.bat` |
| Check the journal was not tampered with | `.venv\Scripts\python.exe -m omkaka verify` | |
| Show settings & which secrets are set | `.venv\Scripts\python.exe -m omkaka status` | |
| Test each free data source once | `.venv\Scripts\python.exe -m omkaka sources-check` | |
| Run a full screen (several minutes) | `.venv\Scripts\python.exe -m omkaka screen` | |
| Write the research packet | `.venv\Scripts\python.exe -m omkaka packet` | |
| Watchlist | `.venv\Scripts\python.exe -m omkaka watch add TICKER` / `watch remove TICKER` / `watch list` | |

The dashboard opens at <http://localhost:8501>. Stop it with **Ctrl+C** in the PowerShell window.
It listens only on your own computer (`localhost`).

## Secrets (API keys)

Never paste keys into chat, screenshots, or commits. When a later phase needs one:

```powershell
copy .env.example .env
notepad .env
```

Fill in the value after the `=`, save, and close. `.env` is ignored by git. The dashboard only ever shows
"set" / "not set".

## Free data sources (set up once)

| Source | What it gives | Cost / limit | Setting in `.env` |
|---|---|---|---|
| SEC EDGAR | Listed companies, share counts, filings (8-K announcements, 10-Q, S-3…), reported financials | Free, no key; max 10 requests/second | `SEC_USER_AGENT` = your name + email |
| Massive (formerly Polygon.io) "Stocks Basic" | End-of-day prices and volume for every US stock | Free; 5 calls/minute; personal use | `MARKET_DATA_API_KEY` |
| Finnhub | Company news headlines and summaries | Free; ~60 calls/minute; personal use | `NEWS_API_KEY` |
| Reddit (official API only) | Post links, counts, timestamps | Free, but Reddit must **approve** your access first | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |

The app waits between calls to stay under each limit. The first screen downloads about 25 days of prices at
12 seconds per call (about 5 minutes); later runs only fetch new days.

## How a screening run works

1. **Whole market (cheap):** every NYSE/Nasdaq company is checked for price ≥ $2, market cap $100M–$5B,
   average daily dollar volume ≥ $1M, and enough recent price history. Change these in `config/settings.toml`.
2. **Prioritization score (0–100):** volume spike, 20-day momentum, recent 8-K filings, smaller size.
   Every component is shown. It is **not** a probability. Reddit never adds points.
3. **Shortlist (top 15 + your watchlist):** filings, reported financials, news, and Reddit are collected, and
   **risk flags** are raised (dilution filings, share-count growth, negative cash flow, thin liquidity,
   unexplained price spikes, possible promotion).
4. A company whose SEC filings could not be retrieved is **withheld** ("insufficient data"), even with a high score.
5. **Research packet:** `python -m omkaka packet` (or the button on the Screening page) writes
   `data\packets\packet-<run>.md`. Read it, or attach it to your existing Claude (e.g. open this folder in
   Claude Code and ask "review the latest research packet"). The packet includes the review rules.

## Budget

`config/settings.toml` sets a **$0 ceiling** (no paid data, no paid AI API calls). Every provider is marked
`paid = false`; any provider marked paid, or unknown, is refused by the app before a request is sent.

## Roadmap

| Phase | What | Needs money? |
|---|---|---|
| 1 ✅ | Foundation, append-only journal, dashboard, offline demo | No |
| 2 ✅ | Free data sources, screening, risk flags, research packet | No |
| 3 | **Review loop:** paste a brief (yours or Claude's) back into the app; it checks every number against the right company, metric, period, and source, checks cited evidence IDs exist and were available by the cutoff, and saves it to the journal | No |
| 4 | **Daily 6 a.m. run** with Windows Task Scheduler: screen + packet ready by 6:00 New York time, deterministic "candidate / no qualifying candidate" gates, missed-run and holiday handling | No |
| 5 | Paper portfolio (15 holdings) vs SPY, explicit paper trades only | No |
| 6 | Reliability checks, backups, troubleshooting guide | No |

**Not planned under the $0 plan** (each would need a separate paid API or a local model):
automatic AI research roles (fundamentals / news / sentiment / skeptic / editor) running unattended,
an AI-written brief ready at 6 a.m. without you, and AI reading of full filing text.
Options if you ever want them: the Anthropic API (paid, separate from a Claude subscription) or a local
model (free to run, e.g. Ollama, but needs a capable PC and gives weaker results).

## How the data rules are enforced

| Rule | How |
|---|---|
| Missing data is never zero or neutral | Every result has a status (OK, no results, partial, failed, rate-limited, no coverage, no access, not configured). The database refuses to store a number for a failed item, or to store an OK item with no number. Unavailable items show **"Data unavailable — reason"**. |
| "Searched, found nothing" ≠ "search failed" | Separate statuses: `NO_RESULTS` vs `FAILED`/`RATE_LIMITED`/… |
| Stale vs fresh | Each number is judged by its market-data time or publication time against `[staleness_hours]` in settings. |
| Times | Every record has `fetched_at` (UTC). Where applicable also `published_at` (when the source published it) and `effective_at` (the market-data "as of" time). The database rejects non-UTC times. |
| Numbers keep their context | Each stored number records company (ticker/CIK), metric, period, unit, kind (reported / guidance / third-party forecast / market data / calculated), provider, source URL and ID. Calculated numbers store their inputs and formula. |
| What was known when | Each run has a **decision cutoff**; research reads only evidence fetched **and** published by then. |
| Append-only history | The database blocks edits and deletes of evidence, numbers, source checks and journal entries. Corrections are new entries pointing at the original. A hash chain detects tampering done outside the app. |
| Demo never mixes with live | Separate database files, each permanently stamped `demo` or `live`. Opening or seeding the wrong one is refused. Demo companies (DEMOX, DEMOY) are fictional, and links go to example.com. |

## Backups

Your research history lives in `data\omkaka.db`. To back it up, close the app and copy that file somewhere
safe (e.g. `copy data\omkaka.db D:\Backups\omkaka-2026-09-24.db`).

## Project layout

```
app.py                 dashboard entry point
omkaka/
  config.py            settings + secrets (values never printed)
  models.py            statuses, claim labels, value kinds
  timeutil.py          UTC storage, New York display
  db.py                database schema and protections
  store.py             saving runs, source checks, evidence, numbers; cutoff queries
  journal.py           append-only, hash-chained journal
  display.py           honest formatting ("Data unavailable — reason", STALE)
  budget.py            spending gate ($0 plan: refuses paid providers)
  sources/             free data sources: sec, massive, finnhub, reddit; http layer; offline fixtures
  screening.py         screening rules, scores, risk flags
  pipeline.py          the screening run
  packet.py            research packet for your own review
  demo.py              builds the offline demo database
  ui/pages.py          dashboard pages
config/settings.toml   thresholds, budget, staleness (no secrets)
fixtures/demo/         FICTIONAL demo data (Phase 1 brief example + Phase 2 fixture market)
tests/                 automated checks
```

## Troubleshooting

- **`py` is not recognized**: reinstall Python and tick "Add python.exe to PATH", or try `python` instead of `py -3`.
- **Port already in use**: another dashboard window is still open. Close it (Ctrl+C) and retry.
- **"Demo database not found"**: run `.venv\Scripts\python.exe -m omkaka demo-reset`.
- **"is a 'demo' database; expected 'live'"**: a safety stop. The demo and live files were mixed up; don't rename database files.
- **`sources-check` says NOT_CONFIGURED**: that key is missing from `.env`.
- **SEC shows NO_ACCESS (HTTP 403)**: `SEC_USER_AGENT` must contain your name and a real email.
- **Massive shows NO_ACCESS**: the free plan may not include that date yet (end-of-day data appears after the close).
- **RATE_LIMITED**: wait a few minutes and run again; cached data is reused.
