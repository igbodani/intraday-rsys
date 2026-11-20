"""Public tape download/replay with deterministic multi-symbol Alpaca-shaped pages."""

import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest

from rsys.application.intrabar import run_tape_command
from rsys.cli import main
from rsys.data.alpaca import AlpacaTradesClient
from rsys.data.trade_tape import minute_flags

ROOT = Path(__file__).resolve().parents[1]


def config(tmp_path):
    cfg = json.loads((ROOT / "configs/intrabar-trades.example.json").read_text())
    cfg.update(cache=str(tmp_path / "cache"), symbols=["AAA", "BBB"])
    cfg["strategy"]["strategy_config"] = {"range_minutes": 1}
    cfg["scanner"].update(lookback_sessions=1, min_dollar_volume="1")
    cfg["action_review"] = {
        s: {
            "status": "verified_none",
            "source": "synthetic test",
            "start": "2024-09-11",
            "end": "2024-09-12",
        }
        for s in cfg["symbols"]
    }
    return cfg


def market(params):
    s = params["symbols"]
    date = params["start"][:10]
    rows = [("30:00.001", 10, 10), ("30:20.000", 9, 10)]
    if date.endswith("11"):
        rows.extend([("31:00.000", 10, 2), ("31:45.999", 10, 1000)])
    else:
        rows.extend(
            [
                ("31:01.000", 10, 5 if s == "AAA" else 50),
                ("31:02.100", 11 if s == "AAA" else 10, 100 if s == "AAA" else 1),
                ("31:02.101", 12 if s == "AAA" else 10, 1),
                ("31:03.000", 11 if s == "AAA" else 10, 1 if s == "AAA" else 300),
                ("31:05.000", 8 if s == "AAA" else 10, 1),
            ]
        )
    return {
        "trades": {
            s: [
                {
                    "t": f"{date}T13:{time}Z",
                    "p": str(price),
                    "s": size,
                    "i": i,
                    "x": "V",
                    "z": "C",
                    "c": ["@"],
                }
                for i, (time, price, size) in enumerate(rows)
            ]
        },
        "next_page_token": None,
    }


def acquire(cfg, tmp_path, transport=market):
    return (
        run_tape_command(
            cfg,
            tmp_path / "runs",
            acquire=True,
            client=AlpacaTradesClient(transport=transport),
        )
        / "pins.json"
    )


def replay(cfg, tmp_path, pins, watch=None):
    p = run_tape_command(
        cfg, tmp_path / "runs", acquire=False, pins_path=pins, watch=watch
    )
    with gzip.open(p / "events.jsonl.gz", "rt") as f:
        return (
            [json.loads(line) for line in f],
            json.loads((p / "result.json").read_text()),
            p,
        )


def test_public_intrabar_replay_ranks_then_fills_and_manages_off_screen(
    tmp_path, monkeypatch
):
    cfg = config(tmp_path)
    pins = acquire(cfg, tmp_path)
    monkeypatch.setattr(
        AlpacaTradesClient,
        "pages",
        lambda *_: pytest.fail("offline replay did network IO"),
    )
    cfgfile = tmp_path / "config.json"
    cfgfile.write_text(json.dumps(cfg))
    assert (
        main(
            [
                "trade-backtest",
                "--config",
                str(cfgfile),
                "--out",
                str(tmp_path / "local"),
                "--pins",
                str(pins),
            ]
        )
        == 0
    )
    (p,) = (tmp_path / "local" / "trade-attempts").iterdir()
    with gzip.open(p / "events.jsonl.gz", "rt") as f:
        events = [json.loads(line) for line in f]
    batches = [e for e in events if e["kind"] == "trade_batch"]
    during = next(e for e in batches if e["accepted"])
    assert during["accepted"] == during["top"] == ["AAA"]
    assert batches[batches.index(during) - 1]["top"] == ["BBB"]
    assert during["scanner"]["AAA"]["prior_mean_volume"] == "22"
    assert during["scanner"]["AAA"]["volume"] == "125"
    assert during["scanner"]["AAA"]["opening_gap"] == "0"
    assert during["scanner"]["AAA"]["gain"] == "0.1"
    assert during["forming"]["AAA"]["high"] == "11"
    buys, sells = [e for e in events if e["kind"] == "fill"]
    assert buys["reference_id"].endswith("AAA:V:5")
    assert buys["available_at_ms"] > during["available_at_ms"]
    assert buys["available_at_ms"] < (9 * 60 + 32) * 60_000
    assert sells["reason"] == "STOP_TRADE"
    assert batches[-1]["top"] == ["BBB"]
    bars = [e for e in events if e["kind"] == "minute_close"]
    assert len(bars) == 390 and bars[1]["bars"]["AAA"]["close"] == "8"
    assert bars[2]["bars"] == {}
    assert (
        json.loads((p / "result.json").read_text())["metrics"]["completed_trade_count"]
        == 1
    )


def test_future_volume_and_price_do_not_change_earlier_rank_or_trade(tmp_path):
    cfg = config(tmp_path)

    def mutation(params, future):
        page = market(params)
        if params["start"].startswith("2024-09-12") and params["symbols"] == "AAA":
            row = page["trades"]["AAA"][-1 if future else 3]
            row.update(s=99999, p="8" if future else "9")
        return page

    def run(transport):
        return replay(cfg, tmp_path, acquire(cfg, tmp_path, transport))[0]

    old = run(market)
    cutoff = next(
        e["available_at_ms"]
        for e in old
        if e["kind"] == "trade_batch" and e["accepted"]
    )

    def prefix(events):
        return [e for e in events if e["available_at_ms"] <= cutoff]

    assert prefix(run(lambda p: mutation(p, True))) == prefix(old)
    assert not any(e.get("accepted") for e in prefix(run(lambda p: mutation(p, False))))


def test_holdout_and_tamper_are_rejected(tmp_path):
    cfg = config(tmp_path)

    def forbidden(_):
        pytest.fail("authorization must precede acquisition")

    protected = deepcopy(cfg)
    protected["partitions"][0]["name"] = "final"
    with pytest.raises(ValueError, match="denied"):
        acquire(protected, tmp_path, forbidden)
    pins = acquire(cfg, tmp_path)
    broken = json.loads(pins.read_text())
    broken["2024-09-12"]["AAA"] = broken["2024-09-11"]["AAA"]
    pins.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="request mismatch"):
        replay(cfg, tmp_path, pins)
    cfg["action_review"]["AAA"]["status"] = "pending"
    with pytest.raises(ValueError, match="Action review"):
        replay(cfg, tmp_path, pins)


def test_condition_flags_and_unknown_condition():
    assert minute_flags("C", ["@", "I"]) == (False, True, False)
    assert minute_flags("C", ["Q"]) == (False, False, False)
    assert minute_flags("C", ["@", "F"]) == (True, True, True)
    with pytest.raises(ValueError, match="Unmapped"):
        minute_flags("C", ["?"])


def test_break_and_reversal_in_same_millisecond_and_precise_history(tmp_path):
    cfg = config(tmp_path)
    cfg["account"]["costs"]["feed_delay_ms"] = 0

    def ticks(params):
        page = market(params)
        rows = page["trades"][params["symbols"]]
        if params["start"].startswith("2024-09-11"):
            # Future volume within the same millisecond is still unavailable.
            rows[-1]["t"] = "2024-09-11T13:31:02.100175Z"
        else:
            rows[3]["t"] = "2024-09-12T13:31:02.100150Z"
            rows[4].update(t="2024-09-12T13:31:02.100200Z", p="9")
        return page

    events, _, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path, ticks))
    up = next(e for e in events if e.get("accepted"))
    down = events[events.index(up) + 1]
    assert up["signals"][0]["strategy_id"] == "orb_forming_cross_v1"
    assert (
        up["forming"]["AAA"]["close"] == "11" and down["forming"]["AAA"]["close"] == "9"
    )
    assert down["market_time_ns"] - up["market_time_ns"] == 50_000
    assert up["scanner"]["AAA"]["prior_mean_volume"] == "22"
    assert down["scanner"]["AAA"]["prior_mean_volume"] == "1022"
    buy = next(e for e in events if e["kind"] == "fill")
    assert (
        buy["reference_id"].endswith("AAA:V:5")
        and buy["available_at_ns"] > down["available_at_ns"]
    )


def test_vwap_partial_bar_confirms_before_close_with_one_warmup_per_minute(tmp_path):
    cfg = config(tmp_path)
    cfg["symbols"] = ["AAA"]
    del cfg["action_review"]["BBB"]
    cfg["strategy"] = {"strategy": "vwap_forming_confirm_v1", "strategy_config": {}}
    raw = json.loads((ROOT / "fixtures/strategy_cases.json").read_text())
    bars = next(c for c in raw["cases"] if c["id"] == "S03")["bars"]

    def transport(params):
        if params["start"].startswith("2024-09-11"):
            return market(params)
        rows = []
        for bar in bars:
            minute = "2024-09-12T13:" + bar["bar_start"][14:16]
            for sec, key in enumerate(("open", "low", "high", "close"), 1):
                rows.append(
                    {
                        "t": f"{minute}:{sec:02}.100Z",
                        "p": bar[key],
                        "s": 25,
                        "i": len(rows),
                        "x": "V",
                        "z": "C",
                        "c": ["@"],
                    }
                )
        rows.append(
            {**rows[-1], "t": "2024-09-12T13:53:30Z", "p": "98", "i": len(rows)}
        )
        return {"trades": {"AAA": rows}, "next_page_token": None}

    events, result, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path, transport))
    proposals = [e for e in events if e.get("accepted")]
    assert len(proposals) == 1
    signal = proposals[0]["signals"][0]
    assert signal["strategy_id"] == "vwap_forming_confirm_v1"
    assert signal["stop"] == "99.99"
    # 22 real warmup/arm/touch minutes precede the forming confirmation.
    assert (
        len(
            [
                e
                for e in events[: events.index(proposals[0])]
                if e["kind"] == "minute_close"
            ]
        )
        == 22
    )
    assert signal["decision_time_ms"] < (9 * 60 + 53) * 60_000
    assert result["metrics"]["completed_trade_count"] == 1


def test_public_download_paginates_and_offline_status_detects_changed_artifact(
    tmp_path, monkeypatch
):
    from rsys.research.errors import InspectionError
    from rsys.research.inspection import inspect_attempt

    cfg = config(tmp_path)
    cfg["feed"] = "iex"
    cfgpath = tmp_path / "config.json"
    cfgpath.write_text(json.dumps(cfg))
    calls = []

    def pages(params):
        calls.append(dict(params))
        payload = market(params)
        s = params["symbols"]
        rows = payload["trades"][s]
        if "page_token" in params:
            return {"trades": {s: rows[1:]}, "next_page_token": None}
        return {"trades": {s: rows[:2]}, "next_page_token": "second"}

    monkeypatch.setattr(AlpacaTradesClient, "_request", staticmethod(pages))
    out = tmp_path / "attempts"
    assert main(["trade-download", "--config", str(cfgpath), "--out", str(out)]) == 0
    (attempt,) = (out / "trade-attempts").iterdir()
    assert len(calls) == 8 and all(call["feed"] == "iex" for call in calls)
    assert all(call["end"].endswith(".999999999Z") for call in calls)
    assert sum("page_token" in call for call in calls) == 4
    events, result, run = replay(cfg, tmp_path, attempt / "pins.json")
    assert sum(len(e.get("prints", [])) for e in events) == 14
    assert result["estimate_label"] == "IEX-based estimates"
    view = inspect_attempt(tmp_path / "runs", run.name)
    assert view.outcome == "success"
    (run / "events.jsonl.gz").write_bytes(b"modified")
    with pytest.raises(InspectionError, match="integrity mismatch"):
        inspect_attempt(tmp_path / "runs", run.name)
    pins = json.loads((attempt / "pins.json").read_text())
    (
        tmp_path / "cache" / "trade-tapes" / (pins["2024-09-12"]["AAA"] + ".json")
    ).write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        replay(cfg, tmp_path, attempt / "pins.json")


def test_early_close_and_missing_prices_only_affect_comparable_snapshots(tmp_path):
    cfg = config(tmp_path)
    cfg["sessions"] = ["2024-12-02"]
    cfg["partitions"][0].update(start="2024-11-27", end="2024-12-02")
    for review in cfg["action_review"].values():
        review.update(start="2024-11-27", end="2024-12-02")

    def data(params):
        s = params["symbols"]
        day = params["start"][:10]

        def row(at, size, p, i, condition="@"):
            return {
                "t": f"{day}T{at}Z",
                "p": p,
                "s": size,
                "i": i,
                "x": "V",
                "z": "C",
                "c": [condition],
            }

        if s == "BBB":
            rows = []
        elif day == "2024-11-29":
            rows = [row("14:30:00", 20, "10", 1)]
        elif day == "2024-11-27":
            rows = [row("14:30:00", 50, "10", 1)]
        else:
            rows = [
                row("14:30:00", 40, "12", 1),
                row("19:00:00", 5, "50", 2, "I"),
                row("19:00:01", 5, "13", 3),
            ]
        return {"trades": {s: rows}, "next_page_token": None}

    events, _, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path, data))
    morning, odd, regular = [e for e in events if e["kind"] == "trade_batch"]
    assert morning["scanner"]["AAA"]["rvol"] == "2"
    assert morning["scanner"]["AAA"]["opening_gap"] == "0.2"
    assert odd["scanner"]["AAA"]["prior_mean_volume"] == "50"
    assert odd["scanner"]["AAA"]["fresh"] is False and odd["top"] == []
    assert odd["forming"] == {} and odd["scanner"]["AAA"]["price"] == "12"
    assert regular["forming"]["AAA"]["volume"] == "10"
    assert regular["scanner"]["AAA"]["rvol"] == "1" and regular["top"] == ["AAA"]
    assert regular["scanner"]["BBB"]["price"] is None


def test_public_experiment_compares_two_strategies_on_one_frozen_tape(
    tmp_path, monkeypatch
):
    cfg = config(tmp_path)
    pins = acquire(cfg, tmp_path)
    monkeypatch.setattr(
        AlpacaTradesClient,
        "pages",
        lambda *_: pytest.fail("network access during experiment"),
    )
    (tmp_path / "intrabar-trades.local.json").write_text(json.dumps(cfg))
    plan = json.loads((ROOT / "configs/experiment.intrabar.example.json").read_text())
    plan["trade_pins"] = str(pins)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    out = tmp_path / "experiment"
    assert main(["experiment", "--plan", str(path), "--out", str(out)]) == 0
    (attempt,) = (out / "experiments").iterdir()
    result = json.loads((attempt / "experiment.json").read_text())
    assert result["promotion_status"] == "unscored" and result["status"] == "success"
    assert result["trade_pins"] == json.loads(pins.read_text())
    orb, vwap = result["arms"]
    assert [r["state"] for r in result["registry_trials"]] == ["success", "success"]
    assert orb["scorecard"]["metrics"]["completed_trades"] == 1
    assert vwap["scorecard"]["metrics"]["completed_trades"] == 0
    assert orb["scorecard"]["estimate_label"] == "SIP-based estimates"
    assert float(vwap["scorecard"]["comparison"]["result"]["ending_equity_delta"]) > 0
    for arm in (orb, vwap):
        assert (
            json.loads((Path(arm["manifest"]).parent / "pins.json").read_text())
            == result["trade_pins"]
        )
    assert main(["experiment", "--plan", str(path), "--out", str(out)]) != 0
    assert (
        len(list((out / "trade-attempts").iterdir())) == 2
    )  # spent budget retained across reruns


def test_trade_ordering_independent_of_config_and_api_page_order(tmp_path):
    cfg = config(tmp_path)
    first, metrics, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path))
    cfg["symbols"].reverse()

    def reversed_rows(params):
        page = market(params)
        page["trades"][params["symbols"]].reverse()
        return page

    second, again, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path, reversed_rows))
    assert first == second
    assert metrics["metrics"] == again["metrics"]
    cfg["account"]["execution"]["decision_latency_ms"] = 5000
    delayed, stats, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path))
    assert any(e.get("accepted") for e in delayed)
    assert any(e.get("status") == "EXPIRED" for e in delayed)
    assert stats["metrics"]["completed_trade_count"] == 0


def test_incomplete_position_retains_its_real_entry_and_failed_attempt(tmp_path):
    from rsys.execution.errors import IncompleteSessionError
    from rsys.research.inspection import inspect_attempt

    cfg = config(tmp_path)

    def missing_exit(params):
        page = market(params)
        if params["start"].startswith("2024-09-12"):
            page["trades"][params["symbols"]].pop()
        return page

    pins = acquire(cfg, tmp_path, missing_exit)
    with pytest.raises(IncompleteSessionError) as error:
        replay(cfg, tmp_path, pins)
    attempt = error.value.manifest_path.parent
    assert inspect_attempt(tmp_path / "runs", attempt.name).outcome == "failed"
    with gzip.open(attempt / "events.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert [row["status"] for row in rows if row["kind"] == "fill"] == ["FILLED"]
    assert any(row["kind"] == "flatten" for row in rows)


def test_later_session_starts_with_shared_equity_and_a_fresh_strategy(tmp_path):
    cfg = config(tmp_path)
    cfg["sessions"].append("2024-09-13")
    cfg["partitions"][0]["end"] = "2024-09-13"
    for r in cfg["action_review"].values():
        r["end"] = "2024-09-13"
    _, result, _ = replay(cfg, tmp_path, acquire(cfg, tmp_path))
    first, second = result["sessions"]
    assert len(first["fills"]) == len(second["fills"]) == 2
    from decimal import Decimal

    first_cash = Decimal(first["ending_equity"])
    second_pnl = Decimal(second["fills"][1]["net_pnl"])
    assert first_cash != Decimal(cfg["account"]["initial_cash"])
    assert Decimal(second["ending_equity"]) == first_cash + second_pnl
    assert result["metrics"]["completed_trade_count"] == 2
