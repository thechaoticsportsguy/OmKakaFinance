"""Phase 6: backups and the doctor check."""
import sqlite3
from datetime import datetime, timedelta, timezone

from omkaka import db, journal
from omkaka.config import load_settings
from omkaka.maintenance import backup_database, doctor


def test_backup_is_a_valid_copy_and_old_copies_are_pruned(tmp_path, monkeypatch):
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    conn = db.connect(s.db_path, "live")
    journal.append_entry(conn, "note", "keep me", "body")
    conn.close()
    s.raw["backup"]["keep_last"] = 2
    t0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    paths = [backup_database(s, now=t0 + timedelta(minutes=i)) for i in range(3)]
    remaining = sorted((s.db_path.parent / "backups").glob("*.db"))
    assert remaining == paths[1:]
    copy = db.connect(paths[-1], "live")
    assert journal.list_entries(copy)[0]["title"] == "keep me" and journal.verify_chain(copy)[0]
    copy.close()
    assert s.db_path.exists()  # the live database is never touched


def test_doctor_reports_problems_in_plain_english(tmp_path):
    s = load_settings("live")
    rows = doctor(s)
    assert any(r[0] == "PROBLEM" and "not found" in r[2] for r in rows)  # no database yet
    db.init_db(s.db_path, "live")
    rows = {name: (status, detail) for status, name, detail in doctor(s)}
    assert rows["Journal integrity"][0] == "OK" and rows["Database file integrity"][0] == "OK"
    assert rows["SEC_USER_AGENT"][0] in ("OK", "WARN")


def test_doctor_detects_tampering(tmp_path):
    s = load_settings("live")
    db.init_db(s.db_path, "live")
    conn = db.connect(s.db_path, "live")
    journal.append_entry(conn, "note", "a", "original")
    conn.execute("DROP TRIGGER journal_entries_no_update")
    conn.execute("UPDATE journal_entries SET body='changed'")
    conn.close()
    rows = {name: status for status, name, _ in doctor(s)}
    assert rows["Journal integrity"] == "PROBLEM"
