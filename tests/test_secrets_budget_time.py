from datetime import datetime, timezone
from pathlib import Path

import pytest

from omkaka.budget import PaidCallsBlocked, budget_summary, require_paid_calls_allowed
from omkaka.config import PROJECT_ROOT, SECRET_NAMES, get_secret, load_settings, secret_status
from omkaka.timeutil import format_new_york, to_utc_iso


def test_secret_value_never_prints(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-THIS-MUST-NOT-LEAK")
    s = get_secret("ANTHROPIC_API_KEY")
    assert "LEAK" not in repr(s) and "LEAK" not in str(s) and "LEAK" not in f"{s}"
    assert s.reveal().endswith("LEAK")
    status = secret_status()
    assert status["ANTHROPIC_API_KEY"] is True
    assert "LEAK" not in repr(status)


def test_env_example_has_only_blank_placeholders():
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in SECRET_NAMES:
        line = next(l for l in text.splitlines() if l.startswith(f"{name}="))
        assert line == f"{name}=", f"{name} should be blank in .env.example"


def test_gitignore_blocks_env_and_data():
    lines = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines and "data/" in lines


def test_budget_is_zero_and_paid_calls_blocked():
    s = load_settings("live")
    b = budget_summary(s)
    assert b["ceiling_usd"] == 0.0 and b["period"] == "total_no_reset"
    assert "paid_data_services" in b["covers"] and "ai_api_calls" in b["covers"]
    assert b["paid_calls_enabled_in_settings"] is False
    with pytest.raises(PaidCallsBlocked, match="Research paused"):
        require_paid_calls_allowed(s, 0.01)


def test_new_york_time_handles_daylight_saving():
    summer = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    winter = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)
    assert format_new_york(summer) == "2026-07-01 06:00 AM EDT"
    assert format_new_york(winter) == "2026-01-15 06:00 AM EST"


def test_utc_format_is_uniform():
    assert to_utc_iso(datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)) == "2026-09-24T09:00:00.000000+00:00"
