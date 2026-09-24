from datetime import timedelta

import pytest

from omkaka import journal

from .conftest import T0


def test_correction_is_a_new_entry_and_original_is_unchanged(live_conn):
    original = journal.append_entry(live_conn, "note", "Revenue up", "Revenue rose 10%", now=T0)
    before = dict(live_conn.execute("SELECT * FROM journal_entries WHERE entry_id=?", (original,)).fetchone())
    fix = journal.add_correction(live_conn, original, "Fix", "It rose 8%, not 10%", now=T0 + timedelta(hours=1))
    after = dict(live_conn.execute("SELECT * FROM journal_entries WHERE entry_id=?", (original,)).fetchone())
    assert before == after
    assert [c["entry_id"] for c in journal.corrections_for(live_conn, original)] == [fix]


def test_cannot_correct_missing_entry(live_conn):
    with pytest.raises(KeyError):
        journal.add_correction(live_conn, "j-doesnotexist", "t", "b")


def test_chain_verifies(live_conn):
    for i in range(3):
        journal.append_entry(live_conn, "note", f"n{i}", "body", now=T0 + timedelta(minutes=i))
    assert journal.verify_chain(live_conn) == (True, [])


def test_tampering_outside_the_app_is_detected(live_conn):
    """Someone with the file could drop the protection trigger; the hash chain still catches it."""
    journal.append_entry(live_conn, "note", "a", "original text", now=T0)
    journal.append_entry(live_conn, "note", "b", "second", now=T0)
    live_conn.execute("DROP TRIGGER journal_entries_no_update")
    live_conn.execute("UPDATE journal_entries SET body = 'rewritten' WHERE title = 'a'")
    ok, problems = journal.verify_chain(live_conn)
    assert not ok and "altered" in problems[0]


def test_deleted_entry_is_detected(live_conn):
    for t in "abc":
        journal.append_entry(live_conn, "note", t, "x", now=T0)
    live_conn.execute("DROP TRIGGER journal_entries_no_delete")
    live_conn.execute("DELETE FROM journal_entries WHERE title = 'b'")
    ok, problems = journal.verify_chain(live_conn)
    assert not ok and "broken" in problems[0]


def test_dedupe_key_prevents_duplicate_entries(live_conn):
    a = journal.append_entry(live_conn, "research_brief", "Brief", "x", dedupe_key="brief-2026-09-24", now=T0)
    b = journal.append_entry(live_conn, "research_brief", "Brief again", "y", dedupe_key="brief-2026-09-24", now=T0)
    assert a == b
    assert len(journal.list_entries(live_conn)) == 1
