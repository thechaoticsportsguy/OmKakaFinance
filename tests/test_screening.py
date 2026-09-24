from omkaka.config import load_settings
from omkaka.screening import prefilter, score_components, shortlist_flags

CFG = load_settings("live").raw["screening"]
MEMBER = {"ticker": "ABCD", "exchange": "Nasdaq", "cik": "0000000001", "name": "ABCD Inc"}


def bars(n=20, close=10.0, vol=200_000, last_vol=None):
    out = [{"trade_date": f"2026-09-{i + 1:02d}", "close": close, "volume": vol} for i in range(n)]
    if last_vol:
        out[-1]["volume"] = last_vol
    return out


def run(member=MEMBER, b=None, shares={"val": 30e6, "end": "2026-08-01"}, n8k=0, latest="2026-09-20"):
    b = bars() if b is None else b
    return prefilter(member, b, shares, n8k, CFG, latest)


def test_passes_and_scores_with_visible_components():
    ev = run(b=bars(last_vol=600_000), n8k=2)
    assert ev.outcome == "passed"
    assert set(ev.components) == {"volume_spike", "momentum", "recent_filings", "smaller_size"}
    assert ev.score == sum(c["points"] for c in ev.components.values()) and 0 <= ev.score <= 100
    assert ev.inputs["market_cap"] == 10.0 * 30e6


def test_missing_shares_is_insufficient_data_not_a_pass():
    ev = run(shares=None)
    assert ev.outcome == "insufficient_data" and "shares outstanding unavailable" in ev.reasons[0]


def test_stale_or_short_price_history_is_insufficient():
    assert run(latest="2026-09-25").outcome == "insufficient_data"
    assert run(b=bars(n=5)).outcome == "insufficient_data"
    assert run(b=[]).outcome == "insufficient_data"


def test_threshold_failures_are_explained():
    assert "price" in run(b=bars(close=1.0), shares={"val": 300e6, "end": "x"}).reasons[0]
    assert "above maximum" in run(shares={"val": 1e9, "end": "x"}).reasons[0]
    assert "$ volume" in run(b=bars(vol=1_000)).reasons[0]
    assert run(member={**MEMBER, "exchange": "OTC"}).outcome == "failed_threshold"
    assert run(member={**MEMBER, "ticker": "ABCDW"}).outcome == "failed_threshold"
    assert run(member={**MEMBER, "ticker": "BRK-B"}).outcome == "failed_threshold"


def test_unknown_filing_count_is_labeled_unavailable():
    ev = run(n8k=None)
    c = ev.components["recent_filings"]
    assert c["points"] == 0 and c["input"] is None and "unavailable" in c["note"]


def test_reddit_never_adds_points():
    inputs = run().inputs
    quiet = shortlist_flags(inputs, [], {}, {"posts": 0, "independent_stories": 0, "distinct_authors": 0}, CFG)
    loud = shortlist_flags(inputs, [], {}, {"posts": 50, "independent_stories": 2, "distinct_authors": 2}, CFG)
    assert quiet == [] and any("promotion" in f for f in loud)
    assert score_components(inputs, CFG) == run().components  # no Reddit input exists in scoring


def test_risk_flags():
    inputs = {"avg_dollar_volume": 1_500_000, "one_day_change": 0.8}
    filings = [{"form": "S-3", "filing_date": "2026-09-01"}, {"form": "NT 10-Q", "filing_date": "2026-08-15"}]
    flags = shortlist_flags(inputs, filings, {"shares_outstanding": 130, "shares_outstanding_year_ago": 100,
                                              "operating_cash_flow": -5}, None, CFG)
    text = " | ".join(flags)
    for expected in ("dilution: recent S-3", "late-filing", "share count up 30%", "negative operating cash",
                     "thin liquidity", "no recent 8-K"):
        assert expected in text


def test_unavailable_filings_raise_a_flag():
    assert any("FILINGS UNAVAILABLE" in f for f in shortlist_flags({}, None, {}, None, CFG))


def test_exclusion_reasons_group_by_rule():
    from omkaka.display import reason_category
    assert reason_category("price $1.45 below $2.00") == reason_category("price $0.90 below $2.00") == "price … below …"
    assert reason_category("market cap $12,000M above maximum") == "market cap … above maximum"
    assert reason_category("price stale: last trade date 2026-09-17") == "price stale"
