# OmKakaFinance

A **local stock-research assistant** that runs on your own computer and opens in your browser.

> **Research tool only.** No broker connection, no real-money orders, no automatic trading.
> It never promises winning stocks. Scores (later phases) are for prioritizing, not win odds.

**Current status: Phase 1 (foundation) complete.** Storage, the append-only journal,
data-status rules, and the dashboard shell work. An **offline demo with fictional data** shows how it
will look. Live data sources (Phase 2), Claude research (Phase 3), the 6 a.m. schedule (Phase 4), and the
paper portfolio (Phase 5) are not built yet.

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

## Budget

`config/settings.toml` sets a **$100 total ceiling, no automatic reset**, covering Claude API calls **and**
paid data services. In Phase 1 **all paid calls are blocked in code**. Changing a setting does not
authorize spending.

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
  budget.py            spending gate (blocks all paid calls in Phase 1)
  demo.py              builds the offline demo database
  ui/pages.py          dashboard pages
config/settings.toml   thresholds, budget, staleness (no secrets)
fixtures/demo/         FICTIONAL demo data
tests/                 automated checks
```

## Troubleshooting

- **`py` is not recognized**: reinstall Python and tick "Add python.exe to PATH", or try `python` instead of `py -3`.
- **Port already in use**: another dashboard window is still open. Close it (Ctrl+C) and retry.
- **"Demo database not found"**: run `.venv\Scripts\python.exe -m omkaka demo-reset`.
- **"is a 'demo' database; expected 'live'"**: a safety stop. The demo and live files were mixed up; don't rename database files.
