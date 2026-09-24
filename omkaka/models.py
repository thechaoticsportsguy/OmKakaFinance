"""Shared vocabulary: data statuses, claim labels, and value kinds.

The central rule of this project: missing data is never neutral.
A failed Reddit request is NOT "neutral sentiment"; missing revenue is NOT 0.
"""
from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    """What happened when we asked a source for data."""

    OK = "OK"                          # request worked and returned data
    NO_RESULTS = "NO_RESULTS"          # request worked; genuinely nothing matched
    PARTIAL = "PARTIAL"                # request worked but data is incomplete
    FAILED = "FAILED"                  # error / timeout / bad response
    RATE_LIMITED = "RATE_LIMITED"      # provider told us to slow down
    NO_COVERAGE = "NO_COVERAGE"        # provider does not cover this company/metric
    NO_ACCESS = "NO_ACCESS"            # we lack permission/approval (e.g. Reddit API)
    NOT_CONFIGURED = "NOT_CONFIGURED"  # no key / source not set up yet


# Statuses meaning "we do not have the data".
UNAVAILABLE_STATUSES = {
    Status.FAILED,
    Status.RATE_LIMITED,
    Status.NO_COVERAGE,
    Status.NO_ACCESS,
    Status.NOT_CONFIGURED,
}

# Statuses that must always carry a human-readable reason.
REASON_REQUIRED = UNAVAILABLE_STATUSES | {Status.PARTIAL}

# A single number (metric) either exists or it does not; "partial" or
# "no results" do not make sense for one value.
METRIC_ALLOWED_STATUSES = {Status.OK} | UNAVAILABLE_STATUSES


class ClaimLabel(str, Enum):
    """Provenance label every research claim must carry (used from Phase 3)."""

    CONFIRMED = "CONFIRMED"      # primary source (filing, company release). Provenance only;
                                 # it does not independently verify management's claims.
    THIRD_PARTY = "THIRD_PARTY"  # news, analysts, Reddit
    AI_ESTIMATE = "AI_ESTIMATE"  # interpretation/hypothesis; numbers need shown inputs


class ValueKind(str, Enum):
    """What kind of number this is, so results and forecasts never blur together."""

    REPORTED = "REPORTED"                          # actual reported result
    GUIDANCE = "GUIDANCE"                          # management's forecast
    THIRD_PARTY_FORECAST = "THIRD_PARTY_FORECAST"  # analyst/other forecast
    MARKET_DATA = "MARKET_DATA"                    # price, volume, etc.
    CALCULATED = "CALCULATED"                      # computed by our own code (inputs stored)


class SourceType(str, Enum):
    FILING = "filing"
    PRESS_RELEASE = "press_release"
    NEWS = "news"
    REDDIT = "reddit"
    MARKET_DATA = "market_data"


PRIMARY_SOURCE_TYPES = {SourceType.FILING, SourceType.PRESS_RELEASE}


def check_status_and_reason(status: Status | str, reason: str | None) -> Status:
    """Validate a status/reason pair and return the Status."""
    status = Status(status)
    if status in REASON_REQUIRED and not (reason and reason.strip()):
        raise ValueError(f"Status {status.value} requires a reason explaining what went wrong.")
    return status


def check_metric_value(status: Status | str, value: float | None, reason: str | None) -> Status:
    """A metric's value must be present when OK and empty (None) otherwise."""
    status = check_status_and_reason(status, reason)
    if status not in METRIC_ALLOWED_STATUSES:
        raise ValueError(f"Status {status.value} is not valid for a single metric value.")
    if status is Status.OK and value is None:
        raise ValueError("Status OK requires a value.")
    if status is not Status.OK and value is not None:
        raise ValueError("Unavailable metrics must have value None (never 0 or a guess).")
    return status
