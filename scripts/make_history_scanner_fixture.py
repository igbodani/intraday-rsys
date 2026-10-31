"""Write an offline, two-symbol/two-session history-scanner demonstration config.

All numbers are synthetic test values, not research defaults or provider data.
Usage: uv run python scripts/make_history_scanner_fixture.py --mode bar --out /tmp/history.json
"""

import argparse
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from rsys.data.calendar import session_duration_minutes, session_open_utc
from rsys.data.history import candidate_prior_session_dates


def build_fixture(mode="bar"):
    from datetime import date

    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / "fixtures" / f"sessions_{mode}_fixture.json").read_text()
    )
    history = {"AAA": [], "BBB": []}
    for session in config["sessions"]:
        del session["scanner_inputs"]
        session["symbols"]["BBB"] = deepcopy(session["symbols"]["AAA"])
        day = candidate_prior_session_dates(
            date.fromisoformat(session["date"]), max_lookback_calendar_sessions=1
        )[0]
        for symbol, volume in (("AAA", "50"), ("BBB", "200")):
            history[symbol].extend(
                {
                    "session_date": day.isoformat(),
                    "minute_index": minute,
                    "available_at": (
                        session_open_utc(day) + timedelta(minutes=minute + 1)
                    ).isoformat(),
                    "revision": 0,
                    "source": "synthetic-history-demo",
                    "status": "TRADED",
                    "close": "100",
                    "volume": volume,
                }
                for minute in range(session_duration_minutes(day))
            )
    config["top_n"] = 1
    config["scanner"] = {
        "mode": "minute_history",
        "feed": "fixture",
        "unit_basis": "raw",
        "corporate_actions": "none",
        "lookback_sessions": 1,
        "max_lookback_calendar_sessions": 1,
        "min_dollar_volume": "1000",
        "max_current_bar_age_ms": 60_000,
        "history": history,
    }
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("bar", "trade"), default="bar")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(
        json.dumps(build_fixture(args.mode), indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
