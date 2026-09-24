"""The daily job (Phase 4):  python -m omkaka daily

Safe to trigger as often as you like (Windows Task Scheduler runs it every 30
minutes). Each time, it decides what to do, in New York time:

  market closed today (weekend/holiday)   -> nothing
  before [schedule].window_start          -> nothing (too early)
  today's result already recorded         -> nothing (no duplicates)
  another copy is running (lock file)     -> nothing
  otherwise: screen -> pick ONE candidate or "No qualifying candidate" ->
             automatic data-only brief -> check it -> packet -> journal

Results finished after [schedule].deadline are labeled LATE with the real time.
If the computer was off, the missed trading days are recorded as "missed" the
next time the job runs; they are never back-filled with later data.
If data sources fail, the job retries on later triggers (up to
max_attempts_per_day, and only before the deadline), then records
"No qualifying candidate: research unavailable" with the reason.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable

from . import journal, store
from .brief import candidate_brief, no_candidate_brief
from .config import Settings
from .market_calendar import closed_reason, trading_days_before
from .packet import write_packet
from .pipeline import extra_closed, run_screen
from .review import save_brief, validate
from .selection import select_candidate
from .timeutil import NEW_YORK, to_utc_iso, utc_now

LOCK_STALE_AFTER = timedelta(hours=2)


class Lock:
    """A lock file so two copies of the daily job never run at the same time."""

    def __init__(self, path: Path, now: datetime):
        self.path, self.now, self.held = path, now, False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    started = datetime.fromisoformat(self.path.read_text(encoding="utf-8").split("|")[1])
                except (OSError, IndexError, ValueError):
                    started = self.now - LOCK_STALE_AFTER * 2
                if self.now - started < LOCK_STALE_AFTER:
                    return False
                self.path.unlink(missing_ok=True)  # left behind by a crashed run
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{os.getpid()}|{to_utc_iso(self.now)}")
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


def _daily_key(day) -> str:
    return f"daily-{day.isoformat()}"


def record_missed_days(conn, settings: Settings, today, now: datetime) -> list[str]:
    """Trading days after the first daily result that have neither a result nor a 'missed' note."""
    first = conn.execute("SELECT MIN(dedupe_key) FROM journal_entries WHERE dedupe_key LIKE 'daily-%'").fetchone()[0]
    if not first:
        return []
    first_day = first.removeprefix("daily-")
    lookback = int(settings.raw["schedule"]["missed_day_lookback"])
    recorded = []
    for d in trading_days_before(today, lookback, extra_closed(settings)):
        if d.isoformat() <= first_day:
            continue
        have = conn.execute("SELECT 1 FROM journal_entries WHERE dedupe_key IN (?, ?)",
                            (_daily_key(d), f"missed-{d.isoformat()}")).fetchone()
        if not have:
            journal.append_entry(
                conn, "system", f"Missed run: no research for {d.isoformat()}",
                f"No daily result was produced for trading day {d.isoformat()} (computer off or asleep, or the "
                "scheduled task did not run). It is not back-filled with later data.",
                payload={"missed_trading_date": d.isoformat()}, dedupe_key=f"missed-{d.isoformat()}", now=now)
            recorded.append(d.isoformat())
    return recorded


def daily_job(conn, settings: Settings, make_clients: Callable[[], object], now: datetime | None = None,
              run_kind: str = "scheduled", log: Callable[[str], None] = print,
              lock_path: Path | None = None) -> dict:
    now = now or utc_now()
    sched = settings.raw["schedule"]
    now_ny = now.astimezone(NEW_YORK)
    today = now_ny.date()
    closed = closed_reason(today, extra_closed(settings))
    if closed:
        return {"action": "skipped", "reason": f"market closed today ({closed})"}
    if now_ny.time() < dtime.fromisoformat(sched["window_start"]):
        return {"action": "skipped", "reason": f"too early (research window opens {sched['window_start']} New York time)"}
    if conn.execute("SELECT 1 FROM journal_entries WHERE dedupe_key = ?", (_daily_key(today),)).fetchone():
        return {"action": "skipped", "reason": f"today's result ({today}) is already recorded"}

    lock = Lock(lock_path or settings.db_path.parent / "daily.lock", now)
    if not lock.acquire():
        return {"action": "skipped", "reason": "another daily run is in progress"}
    try:
        missed = record_missed_days(conn, settings, today, now)
        day_start = datetime.combine(today, dtime(0, 0), tzinfo=NEW_YORK)
        attempts = conn.execute("SELECT COUNT(*) FROM runs WHERE run_id LIKE ? AND started_at >= ?",
                                (f"daily-{today.isoformat()}-%", to_utc_iso(day_start))).fetchone()[0]
        run_id = f"daily-{today.isoformat()}-{attempts + 1}"
        log(f"Daily research for {today} (attempt {attempts + 1}), run {run_id}")
        clients = make_clients()
        summary = run_screen(conn, settings, clients, run_kind=run_kind, run_id=run_id, log=log)
        done_at = clients.http.now()
        deadline = datetime.combine(today, dtime.fromisoformat(sched["deadline"]), tzinfo=NEW_YORK)
        extra = {"trading_date": today.isoformat(), "available_at": to_utc_iso(done_at),
                 "late": done_at > deadline, "missed_days_recorded": missed}

        if summary.get("status") != "completed":
            max_attempts = int(sched["max_attempts_per_day"])
            if attempts + 1 < max_attempts and done_at < deadline:
                return {"action": "retry_later", "reason": summary.get("reason"), "run_id": run_id}
            text = no_candidate_brief(run_id, [], reason=f"Research unavailable: {summary.get('reason')}")
            report = validate(conn, text, run_id)
            entry = save_brief(conn, text, report, "Automatic result (research failed)", extra,
                               dedupe_key=_daily_key(today))
            return {"action": "failed_final", "reason": summary.get("reason"), "entry": entry, "run_id": run_id}

        result = publish_result(conn, settings, run_id, extra, dedupe_key=_daily_key(today))
        _after_success(conn, settings, now, log)
        return result
    finally:
        lock.release()


def publish_result(conn, settings: Settings, run_id: str, extra: dict, dedupe_key: str | None) -> dict:
    """Pick one candidate (or none) for a completed screening run, check the brief, write the packet, journal it."""
    chosen, table = select_candidate(conn, run_id, settings)
    text = (candidate_brief(conn, run_id, chosen, settings.is_demo) if chosen
            else no_candidate_brief(run_id, table))
    report = validate(conn, text, run_id)
    if report.status == "rejected":  # should never happen; never publish an unchecked candidate
        save_brief(conn, text, report, "Automatic brief (failed its own checks)")
        text = no_candidate_brief(run_id, table, reason="The automatic brief failed its own evidence checks.")
        report = validate(conn, text, run_id)
    packet_path, packet_meta = write_packet(conn, settings, settings.db_path.parent / "packets", run_id)
    extra = {**extra, "packet_path": str(packet_path), "packet_sha256": packet_meta["sha256"], "eligibility": table}
    entry = save_brief(conn, text, report, "Automatic data-only brief (no AI)", extra, dedupe_key=dedupe_key)
    return {"action": "done", "run_id": run_id, "entry": entry, "late": extra.get("late"),
            "candidate": chosen["ticker"] if chosen else None, "check": report.status}


def _after_success(conn, settings: Settings, now: datetime, log) -> None:
    """Housekeeping after a successful daily run: fill paper orders, back up the database."""
    try:
        from .portfolio import fill_pending
        for f in fill_pending(conn, settings, now=now):
            log(f"Paper order {f['order_id']}: {f['status']} {f.get('reason') or ''}")
    except ImportError:
        pass
    try:
        from .maintenance import backup_database
        if not settings.is_demo:
            log(f"Backup saved: {backup_database(settings)}")
    except ImportError:
        pass


def latest_daily_status(conn) -> dict:
    row = conn.execute("SELECT * FROM journal_entries WHERE dedupe_key LIKE 'daily-%' ORDER BY entry_seq DESC LIMIT 1"
                       ).fetchone()
    if row is None:
        return {}
    p = json.loads(row["payload_json"])
    return {"trading_date": p.get("trading_date"), "available_at": p.get("available_at"), "late": p.get("late"),
            "title": row["title"]}
