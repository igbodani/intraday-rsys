"""Black-box checks for date expansion, research evidence and broker-hour execution."""

from datetime import date
from decimal import Decimal

import pytest
from test_intrabar_tape import acquire, config, market, replay

from rsys.application.intrabar import plan_trade_tapes
from rsys.data.tape_calendar import execution_dates, tape_hours
from rsys.data.trade_tape import tape_request
from rsys.execution.intrabar import IntrabarSession


def select(cfg, start, end, mode="regular"):
    cfg.pop("sessions", None)
    cfg["date_range"] = {"start": start, "end": end}
    cfg["partitions"][0].update(start="2018-12-01", end=end)
    for review in cfg["action_review"].values():
        review.update(start="2018-12-01", end=end)
    cfg["session_mode"] = mode
    del cfg["account"]["flatten_at"]
    cfg["account"]["flatten_minutes_before_close"] = 5
    if mode != "regular":
        cfg.update(
            strategy_anchor="eligible_open",
            limit_offset_bps="100",
            print_policy="alpaca_day_limit_v1",
        )
    return cfg


def test_seven_year_planning_and_calendar_exceptions(tmp_path):
    cfg = select(config(tmp_path), "2019-01-01", "2025-12-31")
    log = []
    days, prior, requests = plan_trade_tapes(cfg, log, replay=True)
    assert [
        d.year for i, d in enumerate(days) if i == 0 or days[i - 1].year != d.year
    ] == list(range(2019, 2026))
    assert len(days) == 1760
    assert days[0] == date(2019, 1, 2) and prior[days[0]] == [date(2018, 12, 31)]
    assert days[-1] == date(2025, 12, 31)
    assert date(2025, 1, 9) not in days  # national day of mourning
    assert date(2021, 12, 31) in days
    assert date(2022, 6, 20) not in days
    assert dates_authorized(log) == set(requests)
    for params in requests.values():
        assert set(params) == {"AAA", "BBB"}
    cfg["partitions"][0]["start"] = "2019-01-01"
    with pytest.raises(ValueError, match="denied"):
        plan_trade_tapes(cfg, [], replay=True)


def dates_authorized(log):
    return {date.fromisoformat(row["date"]) for row in log if row["allowed"]}


@pytest.mark.parametrize(
    ("date_range", "message"),
    [
        ({"start": "2024-01-01", "end": "2024-01-01"}, "No trading"),
        ({"start": "2024-01-12", "end": "2024-01-02"}, "No trading"),
        ({"start": "2014-12-31", "end": "2015-01-06"}, "2015 through"),
    ],
)
def test_invalid_date_ranges_are_rejected(date_range, message):
    with pytest.raises(ValueError, match=message):
        execution_dates({"date_range": date_range})
    with pytest.raises(ValueError, match="exactly one"):
        execution_dates({"date_range": date_range, "sessions": []})


def test_historical_rollout_local_clock_dst_and_half_days():
    assert tape_hours(date(2019, 1, 2), "extended").order_open == 570
    before, after = [tape_hours(date(2022, 2, d), "extended") for d in (23, 24)]
    assert (before.order_open, before.order_close) == (540, 1080)
    assert (after.order_open, after.order_close) == (240, 1200)
    short = tape_hours(date(2024, 11, 29), "extended")
    assert (short.regular_close, short.order_close) == (780, 1020)
    for day, expected in [(date(2024, 3, 8), "09:00"), (date(2024, 3, 11), "08:00")]:
        assert tape_request("AAA", day, "sip", "extended")["start"][11:16] == expected
    assert tape_request("AAA", date(2024, 11, 29), "sip", "extended")["end"].endswith(
        "21:59:59.999999999Z"
    )
    assert tape_hours(date(2024, 9, 12), "premarket_regular").order_close == 960
    assert tape_hours(date(2024, 9, 12), "regular_afterhours").order_open == 570


def tape(params):
    day = params["start"][:10]
    symbol = params["symbols"]
    rows = []

    def add(stamp, price, size=100, cond="@"):
        rows.append(
            {
                "t": f"{day}T{stamp}Z",
                "s": size,
                "p": str(price),
                "x": "V",
                "i": len(rows),
                "z": "C",
                "c": [cond],
            }
        )

    if day.endswith("11"):
        add("08:00:00", 8, cond="T")
        add("13:30:00", 10)
        add("20:01:00", 50, cond="T")
    else:
        add("08:00:00", 10, cond="T")
        add("08:00:10", 9, cond="T")
        add("08:01:05", 11, cond="T")  # signal, buy limit 11.11
        add("08:01:06", 13, cond="T")  # beyond limit
        add("08:01:07", 11, cond="T")  # later eligible entry
        add("13:30:05", 10)
        add("20:03:00", 8, cond="T")  # stop signal, sell limit 7.92
        add("20:03:01", 6, cond="T")  # cannot fill below limit
        add("20:03:02", 8, cond="T")  # actual post-market exit
    return {"trades": {symbol: rows}, "next_page_token": None}


def one_symbol(cfg):
    cfg["symbols"] = ["AAA"]
    del cfg["action_review"]["BBB"]
    return cfg


def test_premarket_signal_afterhours_limit_exit_and_regular_close_for_gap(tmp_path):
    cfg = one_symbol(select(config(tmp_path), "2024-09-12", "2024-09-12", "extended"))
    cfg["account"]["execution"]["max_hold_ms"] = None
    pins = acquire(cfg, tmp_path, tape)
    events, result, _ = replay(cfg, tmp_path, pins)
    buy, sell = [e for e in events if e["kind"] == "fill"]
    signal = next(e for e in events if e.get("accepted"))
    assert signal["signals"][0]["buy_limit"] == "11.11"
    assert buy["reference_id"].endswith("AAA:V:4") and sell["reference_id"].endswith(
        "AAA:V:8"
    )
    assert sell["reason"] == "STOP_TRADE"
    assert (
        signal["scanner"]["AAA"]["gain"] == "0.1"
    )  # prior core close 10, after-hours close 50
    assert signal["scanner"]["AAA"]["opening_gap"] is None
    assert [e["limit_price"] for e in events if e["kind"] == "sell_limit"] == ["7.92"]
    assert (
        result["multi_session_metrics"]["exposure"]["total_scheduled_ms"]
        == 16 * 3600000
    )
    assert result["calendar_year_results"][0]["completed_trades"] == 1
    assert result["sessions"][0]["print_counts"] == {"AAA": 9}
    assert len([e for e in events if e["kind"] == "minute_close"]) == 960
    cfg["strategy_anchor"] = "regular_open"
    later, _, _ = replay(cfg, tmp_path, pins)
    assert not any(
        e.get("accepted") and e["available_at_ms"] < 570 * 60000 for e in later
    )
    cfg["evidence_detail"] = "signals"
    compact, again, _ = replay(cfg, tmp_path, pins)
    assert all(
        e["kind"] not in ("trade_batch", "minute_close") or e["signals"]
        for e in compact
    )
    assert not again["metrics"]["completed_trade_count"]


def test_extended_profile_rejects_regular_pins_and_fractional_shares(tmp_path):
    cfg = one_symbol(config(tmp_path))
    pins = acquire(cfg, tmp_path)
    select(cfg, "2024-09-12", "2024-09-12", "extended")
    with pytest.raises(ValueError, match="request mismatch"):
        replay(cfg, tmp_path, pins)
    cfg["account"]["sizing"]["quantity_step"] = "0.1"
    with pytest.raises(ValueError, match="whole shares"):
        replay(cfg, tmp_path, pins)


def test_split_units_are_applied_only_on_the_effective_day(tmp_path):
    cfg = one_symbol(select(config(tmp_path), "2024-09-11", "2024-09-12"))
    cfg["action_review"]["AAA"].update(
        status="verified_splits_only",
        splits=[{"date": "2024-09-12", "ratio": "2", "known_before_open": True}],
    )

    def split_market(params):
        rows = market(params)
        for row in rows["trades"]["AAA"]:
            row["p"] = "5" if params["start"].startswith("2024-09-12") else "10"
            row["s"] = 20 if params["start"].startswith("2024-09-12") else 10
        return rows

    events, _, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path, split_market))
    for day in ("2024-09-11", "2024-09-12"):
        snap = next(
            e for e in events if e["session"] == day and e["kind"] == "trade_batch"
        )
        assert Decimal(snap["scanner"]["AAA"]["gain"]) == 0
        assert Decimal(snap["scanner"]["AAA"]["rvol"]) == 1


def test_multi_year_results_carry_equity_and_report_partial_years(tmp_path):
    cfg = one_symbol(select(config(tmp_path), "2023-12-29", "2024-01-02"))
    cfg["evidence_detail"] = "signals"

    def winter(params):
        page = market(params)
        for row in page["trades"]["AAA"]:
            row["t"] = row["t"].replace("T13:", "T14:")
        return page

    _, result, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path, winter))
    years = result["calendar_year_results"]
    assert [(y["year"], y["sessions"]) for y in years] == [(2023, 1), (2024, 1)]
    assert years[1]["starting_equity"] == years[0]["ending_equity"]
    assert sum(Decimal(y["net_change"]) for y in years) == Decimal(
        result["metrics"]["net_change"]
    )


def test_limit_rounding_at_one_dollar():
    runner = object.__new__(IntrabarSession)
    runner.limit_bps = Decimal(100)
    assert runner._limit_price(Decimal("0.995"), buy=True) == Decimal("1.00")
    assert runner._limit_price(Decimal("1.005"), buy=False) == Decimal("0.9950")
