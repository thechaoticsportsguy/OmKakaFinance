"""SQLite storage: schema, protections, and connections.

Protections enforced by the database itself (not just by Python code):
  * Every time column must be UTC in the standard format (see timeutil.py).
  * Evidence, metrics, source checks, and the journal are APPEND-ONLY:
    SQLite triggers reject UPDATE and DELETE on those tables.
  * A metric marked OK must have a value; any other status must have NO value
    (NULL) plus a reason. Missing data can never be stored as 0.
  * Each database file is stamped 'live' or 'demo' at creation, and opening
    it in the wrong mode is refused, so demo fixtures never mix with research.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import METRIC_ALLOWED_STATUSES, REASON_REQUIRED, Status
from .timeutil import to_utc_iso, utc_now

SCHEMA_VERSION = 2  # latest; see MIGRATIONS below

# SQLite GLOB pattern for our one allowed time format (UTC, microseconds).
_TS = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]+00:00'"


def _ts(col: str, nullable: bool = False) -> str:
    check = f"{col} GLOB {_TS}"
    return f"CHECK ({col} IS NULL OR {check})" if nullable else f"CHECK ({check})"


def _quoted(values) -> str:
    return ", ".join(f"'{v.value}'" for v in sorted(values, key=lambda s: s.value))


_ALL_STATUSES = _quoted(Status)
_REASON_STATUSES = _quoted(REASON_REQUIRED)
_METRIC_STATUSES = _quoted(METRIC_ALLOWED_STATUSES)
_REASON_RULE = f"CHECK (status NOT IN ({_REASON_STATUSES}) OR length(trim(coalesce(status_reason, ''))) > 0)"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per research run (demo, manual, or scheduled).
CREATE TABLE IF NOT EXISTS runs (
    run_id             TEXT PRIMARY KEY,
    run_kind           TEXT NOT NULL CHECK (run_kind IN ('demo', 'manual', 'scheduled')),
    is_demo            INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
    started_at         TEXT NOT NULL {_ts('started_at')},
    decision_cutoff_at TEXT NOT NULL {_ts('decision_cutoff_at')},
    finished_at        TEXT {_ts('finished_at', True)},
    status             TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'paused_budget')),
    status_reason      TEXT,
    settings_json      TEXT NOT NULL DEFAULT '{{}}',
    fetched_at         TEXT NOT NULL {_ts('fetched_at')}   -- when this row was recorded
);

-- Did each source work? Distinguishes failure vs. "searched, found nothing".
CREATE TABLE IF NOT EXISTS source_checks (
    check_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    source        TEXT NOT NULL,
    provider      TEXT NOT NULL,
    query         TEXT,
    ticker        TEXT,
    status        TEXT NOT NULL CHECK (status IN ({_ALL_STATUSES})),
    status_reason TEXT,
    result_count  INTEGER,            -- NULL when unknown (never faked as 0)
    fetched_at    TEXT NOT NULL {_ts('fetched_at')},
    {_REASON_RULE},
    CHECK (status != 'NO_RESULTS' OR result_count IS NULL OR result_count = 0)
);

-- Documents we collected: filings, releases, articles, posts, price snapshots.
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id       TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES runs(run_id),
    ticker            TEXT,
    company_name      TEXT,
    cik               TEXT,
    source_type       TEXT NOT NULL CHECK (source_type IN ('filing', 'press_release', 'news', 'reddit', 'market_data')),
    is_primary_source INTEGER NOT NULL CHECK (is_primary_source IN (0, 1)),
    provider          TEXT NOT NULL,
    source_identifier TEXT NOT NULL,  -- stable id from the source (accession no., article id, ...)
    url               TEXT,
    title             TEXT,
    excerpt           TEXT,           -- stored only where source terms allow
    content_hash      TEXT,
    storage_note      TEXT,
    published_at      TEXT {_ts('published_at', True)},   -- when the source published it
    effective_at      TEXT {_ts('effective_at', True)},   -- market-data "as of" time
    fetched_at        TEXT NOT NULL {_ts('fetched_at')},  -- when WE retrieved it
    status            TEXT NOT NULL CHECK (status IN ({_ALL_STATUSES})),
    status_reason     TEXT,
    {_REASON_RULE}
);

-- Individual numbers, tied to company + metric + period + source so later
-- validation can check a cited number is the RIGHT number, not just present.
CREATE TABLE IF NOT EXISTS metric_values (
    metric_id         TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES runs(run_id),
    evidence_id       TEXT REFERENCES evidence(evidence_id),
    ticker            TEXT NOT NULL,
    cik               TEXT,
    metric            TEXT NOT NULL,   -- e.g. 'revenue', 'close_price', 'market_cap'
    period_label      TEXT NOT NULL,   -- e.g. 'FY2026 Q2', 'as_of'
    period_end        TEXT,            -- YYYY-MM-DD when applicable
    value             REAL,            -- NULL = unavailable. Never 0 as a stand-in.
    unit              TEXT NOT NULL,   -- 'USD', 'shares', 'USD/share', ...
    value_kind        TEXT NOT NULL CHECK (value_kind IN ('REPORTED', 'GUIDANCE', 'THIRD_PARTY_FORECAST', 'MARKET_DATA', 'CALCULATED')),
    calculation_json  TEXT,            -- inputs + formula for CALCULATED values
    provider          TEXT NOT NULL,
    source_url        TEXT,
    source_identifier TEXT,
    published_at      TEXT {_ts('published_at', True)},
    effective_at      TEXT {_ts('effective_at', True)},
    fetched_at        TEXT NOT NULL {_ts('fetched_at')},
    status            TEXT NOT NULL CHECK (status IN ({_METRIC_STATUSES})),
    status_reason     TEXT,
    {_REASON_RULE},
    CHECK ((status = 'OK' AND value IS NOT NULL) OR (status != 'OK' AND value IS NULL)),
    CHECK (value_kind != 'CALCULATED' OR status != 'OK' OR calculation_json IS NOT NULL)
);

-- The permanent research journal. Hash-chained so tampering is detectable.
CREATE TABLE IF NOT EXISTS journal_entries (
    entry_seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id          TEXT NOT NULL UNIQUE,
    entry_type        TEXT NOT NULL CHECK (entry_type IN ('note', 'research_brief', 'no_candidate', 'correction', 'paper_trade', 'system')),
    run_id            TEXT REFERENCES runs(run_id),
    ticker            TEXT,
    title             TEXT NOT NULL,
    body              TEXT NOT NULL,
    payload_json      TEXT NOT NULL DEFAULT '{{}}',
    corrects_entry_id TEXT REFERENCES journal_entries(entry_id),
    dedupe_key        TEXT UNIQUE,     -- prevents duplicate entries (e.g. one brief per day)
    fetched_at        TEXT NOT NULL {_ts('fetched_at')},  -- when the entry was written
    prev_hash         TEXT NOT NULL,
    entry_hash        TEXT NOT NULL,
    CHECK ((entry_type = 'correction') = (corrects_entry_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_evidence_ticker ON evidence(ticker, fetched_at);
CREATE INDEX IF NOT EXISTS idx_metric_lookup ON metric_values(ticker, metric, period_label);
CREATE INDEX IF NOT EXISTS idx_checks_run ON source_checks(run_id);
"""

APPEND_ONLY_TABLES = ("source_checks", "evidence", "metric_values", "journal_entries")

TRIGGERS = "\n".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, 'append-only table {t}: updates are not allowed'); END;
CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, 'append-only table {t}: deletes are not allowed'); END;
"""
    for t in APPEND_ONLY_TABLES
) + """
CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'runs cannot be deleted'); END;
-- A run may be updated only once, to record how it finished.
CREATE TRIGGER IF NOT EXISTS runs_limited_update BEFORE UPDATE ON runs
WHEN OLD.finished_at IS NOT NULL
  OR NEW.run_id IS NOT OLD.run_id
  OR NEW.run_kind IS NOT OLD.run_kind
  OR NEW.is_demo IS NOT OLD.is_demo
  OR NEW.started_at IS NOT OLD.started_at
  OR NEW.decision_cutoff_at IS NOT OLD.decision_cutoff_at
  OR NEW.settings_json IS NOT OLD.settings_json
  OR NEW.fetched_at IS NOT OLD.fetched_at
BEGIN SELECT RAISE(ABORT, 'runs: only an unfinished run can be marked finished'); END;
CREATE TRIGGER IF NOT EXISTS meta_mode_locked_update BEFORE UPDATE ON meta
WHEN OLD.key IN ('db_mode', 'created_at')
BEGIN SELECT RAISE(ABORT, 'database mode cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS meta_mode_locked_delete BEFORE DELETE ON meta
WHEN OLD.key IN ('db_mode', 'created_at')
BEGIN SELECT RAISE(ABORT, 'database mode cannot be removed'); END;
"""


# ---------------------------------------------------------------- upgrades
# Each entry: (version, SQL). Applied in order, once, to every database.
MIGRATIONS: list[tuple[int, str]] = [
    (2, f"""
-- Phase 2: data sources and screening.
ALTER TABLE evidence ADD COLUMN doc_type TEXT;        -- e.g. '8-K', '10-Q', 'news', 'reddit_post'
ALTER TABLE evidence ADD COLUMN dedupe_group TEXT;    -- same group = same story (reposts are not independent)

-- Raw HTTP responses. A CACHE, not research history: rows may be replaced or expire.
-- The URL never contains secrets (keys are sent in headers).
CREATE TABLE IF NOT EXISTS http_cache (
    cache_key   TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    url         TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    body        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL {_ts('fetched_at')},
    expires_at  TEXT NOT NULL {_ts('expires_at')}
);

-- End-of-day prices (unadjusted, as traded). Append-only.
CREATE TABLE IF NOT EXISTS market_bars (
    ticker       TEXT NOT NULL,
    trade_date   TEXT NOT NULL,              -- YYYY-MM-DD (New York)
    open REAL, high REAL, low REAL,
    close        REAL NOT NULL,
    volume       REAL NOT NULL,
    vwap         REAL,
    provider     TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    effective_at TEXT NOT NULL {_ts('effective_at')},   -- the 4:00 PM ET close
    fetched_at   TEXT NOT NULL {_ts('fetched_at')},
    run_id       TEXT REFERENCES runs(run_id),
    PRIMARY KEY (ticker, trade_date, provider)
);

-- Every screening decision, with reasons. Append-only.
CREATE TABLE IF NOT EXISTS screening_results (
    result_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    stage         TEXT NOT NULL CHECK (stage IN ('prefilter', 'shortlist')),
    ticker        TEXT NOT NULL,
    cik           TEXT,
    company_name  TEXT,
    exchange      TEXT,
    outcome       TEXT NOT NULL CHECK (outcome IN ('passed', 'failed_threshold', 'insufficient_data', 'watchlist_only')),
    score         REAL,                      -- prioritization only; NOT a probability
    rank          INTEGER,
    components_json TEXT NOT NULL DEFAULT '{{}}',
    flags_json      TEXT NOT NULL DEFAULT '[]',
    reasons_json    TEXT NOT NULL DEFAULT '[]',
    inputs_json     TEXT NOT NULL DEFAULT '{{}}',
    fetched_at    TEXT NOT NULL {_ts('fetched_at')}
);
CREATE INDEX IF NOT EXISTS idx_screen_run ON screening_results(run_id, stage, outcome);

-- Your watchlist, as a history of add/remove actions. Append-only.
CREATE TABLE IF NOT EXISTS watchlist_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL,
    action     TEXT NOT NULL CHECK (action IN ('add', 'remove')),
    note       TEXT,
    fetched_at TEXT NOT NULL {_ts('fetched_at')}
);
""" + "\n".join(
        f"""CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, 'append-only table {t}: updates are not allowed'); END;
CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, 'append-only table {t}: deletes are not allowed'); END;"""
        for t in ("market_bars", "screening_results", "watchlist_events")
    )),
]


class WrongDatabaseMode(RuntimeError):
    """Raised when a demo database is opened as live, or vice versa."""


def _raw_connect(path: Path) -> sqlite3.Connection:
    # isolation_level=None: we control transactions explicitly (see transaction()).
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | str, mode: str, target_version: int = SCHEMA_VERSION) -> None:
    """Create the database (if needed), stamp it with its mode, and upgrade it.

    Upgrades only ADD tables/columns; existing history is never rewritten.
    """
    if mode not in ("live", "demo"):
        raise ValueError(f"mode must be 'live' or 'demo', got {mode!r}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _raw_connect(path)
    try:
        # executescript manages its own commit; every statement is IF NOT EXISTS,
        # so running it on an existing database changes nothing.
        conn.executescript(SCHEMA + TRIGGERS)
        with transaction(conn):
            row = conn.execute("SELECT value FROM meta WHERE key = 'db_mode'").fetchone()
            if row is None:
                conn.execute("INSERT INTO meta(key, value) VALUES ('db_mode', ?)", (mode,))
                conn.execute("INSERT INTO meta(key, value) VALUES ('created_at', ?)", (to_utc_iso(utc_now()),))
                conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
            elif row["value"] != mode:
                raise WrongDatabaseMode(f"{path} is a {row['value']!r} database, not {mode!r}.")
        _migrate(conn, target_version)
    finally:
        conn.close()


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()["value"])


def _migrate(conn: sqlite3.Connection, target_version: int) -> None:
    current = schema_version(conn)
    for version, script in MIGRATIONS:
        if current < version <= target_version:
            # executescript commits on its own; each step is small and additive.
            conn.executescript(script)
            with transaction(conn):
                conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
            current = version


def connect(path: Path | str, expected_mode: str) -> sqlite3.Connection:
    """Open an existing database, refusing if its mode does not match."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No database at {path}. Run the init/demo command first.")
    conn = _raw_connect(path)
    row = conn.execute("SELECT value FROM meta WHERE key = 'db_mode'").fetchone()
    actual = row["value"] if row else None
    if actual != expected_mode:
        conn.close()
        raise WrongDatabaseMode(f"{path} is a {actual!r} database; expected {expected_mode!r}.")
    return conn


def db_mode(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT value FROM meta WHERE key = 'db_mode'").fetchone()["value"]


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All-or-nothing block. BEGIN IMMEDIATE takes the write lock up front,
    so two writers cannot interleave (important for the journal hash chain)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
