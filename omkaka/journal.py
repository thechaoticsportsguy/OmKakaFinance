"""The permanent, append-only research journal.

* Entries are never edited or deleted (the database refuses).
* A correction is a NEW entry that points at the entry it corrects.
* Each entry stores a hash of its contents plus the previous entry's hash
  (a "hash chain"). If anyone edits the database file with outside tools,
  verify_chain() reports exactly where history was altered.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime

from .db import transaction
from .timeutil import to_utc_iso, utc_now

GENESIS_HASH = "0" * 64
ENTRY_TYPES = ("note", "research_brief", "no_candidate", "correction", "paper_trade", "system")


def _entry_hash(fields: dict, prev_hash: str) -> str:
    canonical = json.dumps({**fields, "prev_hash": prev_hash}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_HASHED_FIELDS = ("entry_id", "entry_type", "run_id", "ticker", "title", "body",
                  "payload_json", "corrects_entry_id", "dedupe_key", "fetched_at")


def append_entry(
    conn: sqlite3.Connection, entry_type: str, title: str, body: str,
    payload: dict | None = None, run_id: str | None = None, ticker: str | None = None,
    corrects_entry_id: str | None = None, dedupe_key: str | None = None,
    now: datetime | None = None,
) -> str:
    """Add an entry and return its entry_id.

    If `dedupe_key` was already used, nothing new is written and the existing
    entry's id is returned (so a re-run cannot create duplicate entries).
    """
    if entry_type not in ENTRY_TYPES:
        raise ValueError(f"Unknown entry type {entry_type!r}")
    if not title.strip() or not body.strip():
        raise ValueError("Journal entries need a title and body.")
    with transaction(conn):
        if dedupe_key:
            existing = conn.execute(
                "SELECT entry_id FROM journal_entries WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            if existing:
                return existing["entry_id"]
        if corrects_entry_id and not conn.execute(
            "SELECT 1 FROM journal_entries WHERE entry_id = ?", (corrects_entry_id,)
        ).fetchone():
            raise KeyError(f"Cannot correct unknown entry {corrects_entry_id!r}")
        last = conn.execute(
            "SELECT entry_hash FROM journal_entries ORDER BY entry_seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = last["entry_hash"] if last else GENESIS_HASH
        fields = {
            "entry_id": f"j-{uuid.uuid4().hex[:16]}",
            "entry_type": entry_type,
            "run_id": run_id,
            "ticker": ticker.upper() if ticker else None,
            "title": title,
            "body": body,
            "payload_json": json.dumps(payload or {}, sort_keys=True, ensure_ascii=False),
            "corrects_entry_id": corrects_entry_id,
            "dedupe_key": dedupe_key,
            "fetched_at": to_utc_iso(now or utc_now()),
        }
        conn.execute(
            f"""INSERT INTO journal_entries({", ".join(_HASHED_FIELDS)}, prev_hash, entry_hash)
                VALUES ({", ".join("?" * len(_HASHED_FIELDS))}, ?, ?)""",
            (*[fields[k] for k in _HASHED_FIELDS], prev_hash, _entry_hash(fields, prev_hash)),
        )
    return fields["entry_id"]


def add_correction(conn, original_entry_id: str, title: str, body: str,
                   payload: dict | None = None, now: datetime | None = None) -> str:
    """Correct an earlier entry. The original stays exactly as it was."""
    return append_entry(conn, "correction", title, body, payload=payload,
                        corrects_entry_id=original_entry_id, now=now)


def list_entries(conn, entry_type: str | None = None) -> list[sqlite3.Row]:
    if entry_type:
        return conn.execute(
            "SELECT * FROM journal_entries WHERE entry_type = ? ORDER BY entry_seq", (entry_type,)
        ).fetchall()
    return conn.execute("SELECT * FROM journal_entries ORDER BY entry_seq").fetchall()


def corrections_for(conn, entry_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM journal_entries WHERE corrects_entry_id = ? ORDER BY entry_seq", (entry_id,)
    ).fetchall()


def verify_chain(conn) -> tuple[bool, list[str]]:
    """Recompute every hash. Returns (ok, list of problems found)."""
    problems: list[str] = []
    prev_hash = GENESIS_HASH
    for row in list_entries(conn):
        fields = {k: row[k] for k in _HASHED_FIELDS}
        if row["prev_hash"] != prev_hash:
            problems.append(f"Entry #{row['entry_seq']} ({row['entry_id']}): link to previous entry is broken "
                            "(an entry was removed or reordered).")
        if _entry_hash(fields, row["prev_hash"]) != row["entry_hash"]:
            problems.append(f"Entry #{row['entry_seq']} ({row['entry_id']}): contents were altered after writing.")
        prev_hash = row["entry_hash"]
    return (not problems, problems)
