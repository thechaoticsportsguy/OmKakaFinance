"""Loads settings (config/settings.toml) and secrets (.env).

Secrets are only reported as "set" / "not set" - their values are never
printed, logged, or shown in the dashboard.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.toml"

SECRET_NAMES = (
    "ANTHROPIC_API_KEY",
    "SEC_USER_AGENT",
    "MARKET_DATA_API_KEY",
    "NEWS_API_KEY",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USER_AGENT",
)

MODES = ("live", "demo")


class SecretValue:
    """Wraps a secret so it cannot be accidentally printed."""

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('********')"

    __str__ = __repr__


@dataclass(frozen=True)
class Settings:
    mode: str
    db_path: Path
    raw: dict = field(repr=False)

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def budget(self) -> dict:
        return self.raw["budget"]

    def staleness_hours(self, source_type: str) -> float:
        table = self.raw.get("staleness_hours", {})
        return float(table.get(source_type, table.get("default", 72)))


def get_mode() -> str:
    """'live' (default) or 'demo', from the OMKAKA_MODE environment variable."""
    mode = os.environ.get("OMKAKA_MODE", "live").strip().lower()
    if mode not in MODES:
        raise ValueError(f"OMKAKA_MODE must be one of {MODES}, got {mode!r}")
    return mode


def load_settings(mode: str | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    raw = tomllib.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    mode = mode or get_mode()
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}")
    key = "demo_db_path" if mode == "demo" else "live_db_path"
    # OMKAKA_DB_PATH lets tests point at a temporary database.
    override = os.environ.get("OMKAKA_DB_PATH")
    db_path = Path(override) if override else PROJECT_ROOT / raw["storage"][key]
    return Settings(mode=mode, db_path=db_path, raw=raw)


def get_secret(name: str) -> SecretValue | None:
    if name not in SECRET_NAMES:
        raise KeyError(f"Unknown secret name {name!r}")
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    value = os.environ.get(name, "").strip()
    return SecretValue(value) if value else None


def secret_status() -> dict[str, bool]:
    """{name: is_set} - safe to display; never includes values."""
    return {name: get_secret(name) is not None for name in SECRET_NAMES}
