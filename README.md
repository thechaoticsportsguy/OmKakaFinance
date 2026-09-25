# OmKakaFinance

A **local stock-research assistant** that runs on your own Windows computer and opens in your browser.
Every trading morning it screens US stocks (focused on overlooked small and mid caps), gathers filings, news
and prices from **free** sources, and gives you **one research candidate, or "No qualifying candidate"**,
with every number sourced, checked, and recorded in a permanent journal.

> **Research tool only.** No broker connection, no real-money orders, no automatic trading.
> It never promises winning stocks. Scores are for deciding what to read first, **not** probabilities.

**Spending plan: $0 additional.** Free data sources only; no paid subscriptions; no paid AI calls.
The app does the gathering, screening, checking and record-keeping. The *interpretation* is yours: read the
brief and research packet, or have your existing Claude review them (see [Using Claude](#using-your-claude-at-0)).

**Status: refreshed dashboard, real market explorer, and guided setup.** The no-key Yahoo price and headline
connections have been tested on this Windows computer. The SEC/Massive/Finnhub automated daily pipeline
still requires the owner's credentials and a successful live run.

## Start here — everyday use

- Double-click **start_app.bat** for the live workspace at <http://localhost:8501>.
- **Markets** is the default home page and works without API keys. Search a real stock symbol to see its
  latest available price, daily chart, headline links, and watchlist controls. Prices may be delayed;
  quote time and retrieval time are shown. Quotes are cached for five minutes and news for fifteen minutes.
- Double-click **start_demo.bat** for the fictional offline demo at <http://localhost:8502>.
- Both can stay open together. The helpers reopen a running dashboard instead of starting another copy.
- In the live workspace, choose **Setup & connections**. Enter your SEC contact details (name and email),
  Massive key, and Finnhub key in the private form. Blank fields preserve already-saved values.
- Click **Check live connections**, then **Run today's research**. The first full run may take several minutes.
- After a successful real run and recent connection checks, **Enable 6 a.m. schedule** becomes available.
  Keep Windows on, awake, online, and signed in overnight. The app starts checking from 4:30 a.m. Eastern.
- **Today** presents the candidate, price history, screening factors, risks, and source timeline.
  Use **Numbers & sources** for the supporting data or **Full checked brief** for the original text.
- After a failed run, fix the connection and click **Run today's research** again. The failed entry remains
  in the journal. Successful daily results are not duplicated.

The real Markets view and offline demo are usable immediately. Verified daily candidates and the morning
schedule require your own source credentials; passing offline tests does not verify those accounts.
Reddit remains optional and needs separate approval. Public Yahoo endpoints can change or become unavailable;
the app reports failures rather than substituting fictional data. Market-explorer quotes do not enter the
verified daily-pick or paper-portfolio price history.

### Windows fixes and setup safeguards

Backup database handles are now closed before old copies are removed. Connection checks return failure
when required sources fail, use the NYSE calendar, bypass cached responses, and validate response formats.
Edits to `.env` are picked up without restarting the dashboard. Editing a checked brief requires a new check
before it can be saved. The scheduler cannot be installed before setup verification completes.

---

## 1. One-time setup (Windows)

1. Install **Python 3.11 or newer** from <https://www.python.org/downloads/windows/> and tick
   **"Add python.exe to PATH"** during install.
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
.venv\Scripts\python.exe -m pytest
```

`py -3 --version` must print 3.11 or higher; the last line should end with "passed".
(Double-clicking `setup_windows.bat` does the venv/install/init steps for you.)

**Already installed an earlier phase?** Update with `git pull` then
`.venv\Scripts\python.exe -m pip install -r requirements.txt`. Your database upgrades itself automatically and
keeps all history.

### Try the offline demo first
```powershell
.venv\Scripts\python.exe -m omkaka demo
```
Opens <http://localhost:8502> with **fictional** companies (DEMOX, DEMOA, …) and a red DEMO banner on every page.
Press **Ctrl+C** in PowerShell to stop.

## 2. Free data sources and keys

| Source | What it gives | Cost / limit | Setting in `.env` |
|---|---|---|---|
| SEC EDGAR | Listed companies, share counts, filings (8-K announcements, 10-Q, S-3…), reported financials | Free, no key; max 10 requests/second | `SEC_USER_AGENT` = your name + email |
| Massive (formerly Polygon.io) "Stocks Basic" | End-of-day prices for every US stock (and SPY) | Free; 5 calls/minute; personal use | `MARKET_DATA_API_KEY` |
| Finnhub | Company news headlines and summaries | Free; ~60 calls/minute; personal use | `NEWS_API_KEY` |
| Reddit (official API only) | Post links, counts, timestamps | Free, but Reddit must **approve** your access first | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |

Sign up for the free Massive and Finnhub plans, then:
```powershell
copy .env.example .env
notepad .env
```
Fill in values after the `=`, save, close. **Never paste keys into chat, screenshots, or commits.** `.env` is
ignored by git; the app only ever shows "set" / "not set". Then test each source once:
```powershell
.venv\Scripts\python.exe -m omkaka sources-check
```
Until Reddit approves you, Reddit shows as "Data unavailable … UNKNOWN, not neutral". That is expected.

## 3. Turn on the 6 a.m. schedule

```powershell
.venv\Scripts\python.exe -m omkaka schedule install
.venv\Scripts\python.exe -m omkaka daily
```
(or double-click `install_schedule.bat`). The second line runs the daily job once right now so you can see it work.

How it behaves:
- Windows runs `run_daily.bat` **every 30 minutes**. Each time, the job checks **New York time** itself (daylight
  saving handled; your PC's timezone doesn't matter) and usually does nothing:
  - **weekends and NYSE holidays** → nothing (no brief that day);
  - **before 4:30 a.m. New York** → nothing (too early);
  - **today's result already recorded** → nothing (no duplicates);
  - otherwise it screens, picks one candidate or none, checks the brief, writes the packet, and journals it.
- Target: ready by **6:00 a.m.** Anything finished later is labeled **LATE** with its real time.
- **Your computer must be on and awake.** If it was off, the run happens as soon as it's back (the same day), and
  trading days that were missed entirely are recorded as **"Missed run"**. They are never back-filled with later data.
- If sources fail, it retries on the next triggers (up to 3 attempts, before 6:00), then records
  "No qualifying candidate: research unavailable" with the reason.
- The task does **not** wake a sleeping PC by default (it would wake it every 30 minutes). If you want that:
  `python -m omkaka schedule install --wake`. Remove the task with `python -m omkaka schedule uninstall`.
- Log file: `data\logs\daily.log`.
- Unscheduled market closures can't be predicted: add them to `extra_closed_dates` in `config/settings.toml`.

## 4. Daily use

1. Open the dashboard: `.venv\Scripts\python.exe -m omkaka app` (or `start_app.bat`).
2. **Today**: the candidate (or "No qualifying candidate"), its check result (PASSED / FLAGGED), source coverage,
   and the brief. Red **STALE** means there is no result yet for today; **LATE** means it arrived after 6:00.
3. **Watchlist & screening**: the shortlist, score components, risk flags, why others were excluded, your
   watchlist, and the research-packet download.
4. **Review a brief**: paste your own or Claude's brief; the app checks it (details below) and saves it.
5. **Paper portfolio**: paper cash, orders, holdings, and your return vs SPY.
6. **Journal**: the permanent record, integrity check, notes and corrections.
7. **Health**: the setup check, schedule status, missed runs, sources, $0 spending, backups.

### How the candidate is chosen (no AI)
1. **Whole market:** NYSE/Nasdaq companies with price ≥ $2, market cap $100M–$5B, average daily dollar
   volume ≥ $1M, and fresh price history. Missing data means **insufficient data**, never a pass.
2. **Prioritization score (0–100):** volume spike, 20-day momentum, recent 8-K filings, smaller size.
   Every component is shown. Reddit never adds points.
3. **Shortlist (top 15 + your watchlist):** filings, financials, news, Reddit; **risk flags** raised for
   dilution filings, share-count growth, cash burn, thin liquidity, unexplained spikes, promotion signs.
4. **Selection gates** (all must pass): passed screening, score ≥ 35, SEC filings **and** SEC financial data
   retrieved, no blocking flag (late filing, filings unavailable, promotion signs), and an identifiable recent
   catalyst (8-K or news). Highest score among those that pass wins; if none pass → **No qualifying candidate**.
5. **Automatic data-only brief:** every line cites its metric or evidence ID and is checked before publishing.
   It deliberately does **not** write a narrative, price target, or time horizon.

All thresholds live in `config/settings.toml` with plain-English comments.

### Using your Claude at $0
- Download the **research packet** (Screening page, or `python -m omkaka packet` → `data\packets\`). It contains
  review rules, source coverage, every number with its source and time, and fenced evidence text.
- Attach it to your Claude, or open this folder in **Claude Code** and say *"review the latest research packet"*
  (`CLAUDE.md` tells Claude Code the rules).
- Paste Claude's answer into **Review a brief** (or `python -m omkaka review brief.md`). The app checks:
  - every number matches the **cited** metric for the **right company, metric and period** (or appears in the
    cited evidence text), and names the problem when it doesn't ("wrong company", "wrong period", "not cited");
  - every evidence/metric ID exists and was available **before the run's cutoff**;
  - labels: `CONFIRMED` needs a primary source (SEC filing/reported number); `THIRD_PARTY` needs a citation;
    `AI_ESTIMATE` may not introduce new numbers except a shown, re-computed calculation;
  - no price targets, probabilities, or promises; the candidate must have passed the gates;
  - instruction-like text and links not from the packet are flagged.
  Result: **passed**, **flagged** (usable, read warnings), or **rejected** (saved only as a rejected note).

### Paper portfolio rules
- Orders only when **you** place them (the daily candidate is never bought automatically).
- Fill = the **close of the first trading day that ends after you placed the order**, 0.10% worse (slippage),
  using only prices the app had actually downloaded. No back-dated fills. No price for 5 trading days → rejected.
- Max 15 holdings; no margin; no short selling.
- Dividends/splits: record them yourself with a source note; big one-day drops are flagged as possible splits.
- **SPY comparison:** every deposit/withdrawal is mirrored into SPY with the same fill rule; both are price-only.
- "What happened after each candidate" tracks every published candidate vs SPY (hypothetical, not trades).
- Commands: `python -m omkaka paper deposit 10000`, `paper buy TICKER 10 --brief ENTRY_ID`, `paper sell TICKER 5`,
  `paper status`, `paper dividend TICKER 2026-10-01 0.25 "source"`, `paper split TICKER 2026-10-01 2 "source"`.

## 5. All commands (PowerShell, in the project folder, prefix `.venv\Scripts\python.exe -m omkaka`)

| Command | What it does |
|---|---|
| `app` / `demo` / `demo-reset` | live dashboard / offline demo / rebuild the demo |
| `sources-check` | one small free test call to each data source |
| `screen` | run a screening now (several minutes the first time) |
| `packet [--run RUN]` | write the research packet |
| `daily` | the daily job (safe any time; does nothing if not due) |
| `schedule install [--wake]` / `uninstall` / `status` | Windows Task Scheduler |
| `review FILE [--run RUN]` | check a brief and save it to the journal |
| `watch add TICKER` / `watch remove TICKER` / `watch list` | watchlist |
| `paper …` | paper portfolio (see above) |
| `backup` | safe copy of the database into `data\backups` (newest 30 kept) |
| `doctor` | check the whole setup and explain problems in plain English |
| `verify` | journal tamper check |
| `status` | settings, budget, which keys are set |

Double-click helpers: `setup_windows.bat`, `start_demo.bat`, `start_app.bat`, `install_schedule.bat`,
`run_checks.bat` (tests + doctor + journal check).

## 6. Backups and restore

- Automatic: after every successful daily run (kept in `data\backups`, newest 30).
- Manual: `python -m omkaka backup`, or the button on the Health page.
- Off-computer copy (recommended weekly): copy the `data\backups` folder to a USB drive or cloud folder.
- **Restore:** close the app and scheduled runs (`schedule uninstall`), rename `data\omkaka.db` to
  `data\omkaka-broken.db`, copy the backup file to `data\omkaka.db`, run `python -m omkaka doctor`, then
  `schedule install` again.

## 7. How the data rules are enforced

| Rule | How |
|---|---|
| Missing data is never zero or neutral | Every result has a status (OK, no results, partial, failed, rate-limited, no coverage, no access, not configured). The database refuses a number for a failed item. Screens show **"Data unavailable — reason"**. |
| "Found nothing" ≠ "failed" | Separate statuses; a holiday with no trading is "no results", a 429 is "rate limited". |
| Stale vs fresh | Prices must be from the latest trading day (NYSE calendar) or the screen stops; displayed numbers show fresh/STALE. |
| Times | Every record has `fetched_at` (UTC); plus `published_at` and market `effective_at` where applicable. Non-UTC times are rejected by the database. |
| Numbers keep their context | Company (ticker + CIK), metric, period, unit, kind (reported / guidance / forecast / market / calculated), source URL and ID. Calculations store inputs and formula. |
| What was known when | Each run has a decision cutoff; research, briefs and packets use only evidence fetched **and** published by then. |
| Append-only history | Evidence, numbers, source checks, screening results, journal, watchlist and paper trades cannot be edited or deleted. Corrections are new entries. A hash chain detects outside tampering (`verify`, `doctor`). |
| No unverified claims | Briefs are checked against the evidence before they are published or saved as accepted. |
| External text is data | Stored as quoted evidence only; fenced in packets; never executed or obeyed. |
| Demo never mixes with live | Separate database files stamped `demo`/`live`; fictional companies; all demo links go to example.com. |
| $0 spending | Every provider is `paid = false`; paid or unknown providers are refused before any request. |

## 8. Verification and remaining setup

- **Real Yahoo Finance prices, charts, and headlines were verified on this Windows computer** on
  September 24, 2026, including Apple and Microsoft ticker searches. The Markets page needs no API keys.
  Quotes may be delayed; inspect their displayed timestamps.
- **186 automated checks pass.** The live and demo dashboards launch on Windows using separate data stores.
- **SEC, Massive, and Finnhub connections still need credentials and live verification.** Run the connection
  check in Setup, then today's research. Failed or unavailable sources are displayed honestly.
- **Windows Task Scheduler is not installed yet.** Installation remains gated until required connections
  and a live screening run pass. Check `python -m omkaka schedule status` and `data\logs\daily.log` afterward.
- Massive's free-plan terms were confirmed only through search results; confirm on massive.com when signing up.
- SEC's bulk share-count data can miss companies with several share classes; they show as "insufficient data".
- Reddit access depends on Reddit's manual approval.

## 9. Not included under the $0 plan

Automatic AI research agents (fundamentals / news / sentiment / skeptic / editor) running unattended, an
AI-written narrative brief waiting at 6 a.m. without you, and AI reading of full filing text. Each would need a
paid API (the Anthropic API is billed separately from a Claude subscription) or a local model (free to run, e.g.
Ollama, but needs a capable PC and gives weaker results). The review loop in section 4 is the $0 substitute.

## 10. Troubleshooting

Start with `python -m omkaka doctor`. It explains most problems.

- **`py` is not recognized**: reinstall Python with "Add python.exe to PATH", or use `python` instead of `py -3`.
- **Port already in use**: another dashboard is open. Close it (Ctrl+C) and retry.
- **`sources-check` says NOT_CONFIGURED**: that key is missing from `.env`.
- **SEC NO_ACCESS (HTTP 403)**: `SEC_USER_AGENT` must contain your name and a real email.
- **Massive NO_ACCESS**: the free plan may not include that date yet (end-of-day data appears after the close).
- **RATE_LIMITED**: wait a few minutes; cached data is reused on the next run.
- **"Latest price data is stale"**: yesterday's prices couldn't be downloaded; the screen refuses to rank on old
  prices. It retries on the next trigger.
- **Today page says STALE**: no result yet today: check the Health page (missed runs, last failed run) and
  `data\logs\daily.log`; run `python -m omkaka daily` by hand to see what happens.
- **"is a 'demo' database; expected 'live'"**: safety stop; don't rename database files.
- **Journal integrity PROBLEM**: the database file was edited outside the app. Restore from a backup (section 6).

## Project layout

```
app.py                 dashboard entry point
omkaka/
  config.py            settings + secrets (values never printed)
  models.py            statuses, claim labels, value kinds
  timeutil.py          UTC storage, New York display
  market_calendar.py   NYSE trading days and holidays
  db.py                database schema, protections, automatic upgrades
  store.py             runs, source checks, evidence, numbers; cutoff queries; watchlist
  journal.py           append-only, hash-chained journal
  display.py           honest formatting ("Data unavailable — reason", STALE)
  budget.py            spending gate ($0 plan)
  sources/             free data sources (SEC, Massive, Finnhub, Reddit), request layer, offline fixtures
  screening.py         screening rules, scores, risk flags
  pipeline.py          a screening run
  selection.py         candidate gates and choice
  brief.py             automatic data-only brief
  review.py            brief checker (numbers, citations, labels, cutoff)
  packet.py            research packet for your own review
  daily.py             the daily job (timing, duplicates, late/missed runs, retries, lock)
  schedule.py          Windows Task Scheduler setup
  portfolio.py         paper portfolio, fills, SPY mirror, candidate outcomes
  maintenance.py       backups and doctor
  demo.py              builds the offline demo
  ui/pages.py          dashboard pages
config/settings.toml   every threshold and rule, with comments (no secrets)
fixtures/demo/         FICTIONAL demo data
tests/                 automated checks (offline; no paid or live calls)
```
