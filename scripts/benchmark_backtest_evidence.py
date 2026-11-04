"""Offline synthetic benchmark; run each workload in a fresh Python process.

No provider client, credentials or network. Deliberately retains the full scanner
history search budget and evidence. Settings are fixture values, not research defaults.
"""

import argparse
import json
import os
import platform
import sys
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter

from rsys.application.historical import backtest, plan_backtest
from rsys.application.preflight import check_data
from rsys.data.bar_cache import BarCache, request_identity
from rsys.data.calendar import session_duration_minutes, session_open_utc


class FixturePages:
    def pages(self, request):
        day = date.fromisoformat(request["start"][:10])
        opening = session_open_utc(day)
        yield {
            "bars": {
                "AAPL": [
                    {
                        "t": (opening + timedelta(minutes=m)).isoformat(),
                        "o": "100",
                        "h": "100",
                        "l": "100",
                        "c": "100",
                        "v": "100",
                    }
                    for m in range(session_duration_minutes(day))
                ]
            }
        }


def workload_config(out, workload):
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures/sessions_bar_fixture.json"
        ).read_text()
    )
    sessions = (
        ["2024-11-29"] if workload == "early-close" else ["2024-12-02", "2024-12-03"]
    )
    config = {
        "schema_version": 1,
        "cache": str(out / "cache"),
        "dataset": {"symbol": "AAPL", "feed": "sip", "asof": "-"},
        "purpose": "development",
        "partitions": [
            {"name": "development", "start": "2024-01-01", "end": "2024-12-31"}
        ],
        "sessions": sessions,
        "objects": {},
        "timing_basis": "final_bars_modeled_at_end",
        "account": {
            k: deepcopy(fixture[k])
            for k in (
                "initial_cash",
                "sizing",
                "risk",
                "costs",
                "execution",
                "completion_policy",
            )
        },
        "strategy": {"strategy": "orb_close_cross_v1", "strategy_config": {}},
        "scanner": {
            "top_n": 1,
            "lookback_sessions": 1 if workload == "early-close" else 2,
            "max_lookback_calendar_sessions": 1 if workload == "early-close" else 3,
            "min_dollar_volume": "1000",
            "max_current_bar_age_ms": 60000,
        },
        "action_review": {
            "status": "verified_none",
            "source": "synthetic benchmark; no provider evidence",
            "start": "2024-01-01",
            "end": "2024-12-31",
        },
    }
    config["account"]["flatten_at"] = "12:58:00"
    from rsys.data.history import candidate_prior_session_dates

    days = {date.fromisoformat(d) for d in sessions}
    for day in tuple(days):
        days.update(
            candidate_prior_session_dates(
                day,
                max_lookback_calendar_sessions=config["scanner"][
                    "max_lookback_calendar_sessions"
                ],
            )
        )
    config["objects"] = {str(d): "0" * 64 for d in days}
    # Use the real authorization/identity planner even for generated fixtures.
    plan_backtest(config, [])
    cache = BarCache(config["cache"])
    for day in sorted(days):
        request = request_identity("AAPL", day, "sip", "-")
        config["objects"][str(day)] = cache.acquire(request, FixturePages())
    return config


def file_inventory(root):
    return {
        str(p.relative_to(root)): p.stat().st_size
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def peak_memory():
    try:
        import resource
    except ImportError:
        return None, "unavailable: platform has no resource.getrusage"
    if sys.platform not in ("linux", "darwin"):
        return None, "unavailable: ru_maxrss units not supported on this platform"
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(
        peak * (1024 if sys.platform == "linux" else 1)
    ), "process lifetime peak RSS, including setup/imports; not simulation-only RAM"


def host_memory():
    """Reported physical host RAM; container limits can be substantially lower."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def benchmark(out, workload):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    config = workload_config(out, workload)
    preflight, _ = check_data(config, out / "preflight")
    if not preflight["can_execute"]:
        raise ValueError("Synthetic benchmark preflight failed")
    started_run = perf_counter()
    _, run_id, trace, manifest = backtest(
        config, json.dumps(config).encode(), out / "runs"
    )
    elapsed_run = perf_counter() - started_run
    files = file_inventory(out / "runs")
    memory, memory_basis = peak_memory()
    report = {
        "evidence_type": "synthetic_software_benchmark",
        "workload": workload,
        "hardware": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "reported_host_ram_bytes": host_memory(),
            "ram_scope": "physical host RAM if available; not container allowance or free memory",
        },
        "scanner": config["scanner"],
        "inventory": preflight["inventory"],
        "execution_minutes": sum(
            session_duration_minutes(date.fromisoformat(d)) for d in config["sessions"]
        ),
        "elapsed_backtest_seconds": elapsed_run,
        "elapsed_total_seconds": perf_counter() - start,
        "peak_memory_bytes": memory,
        "peak_memory_basis": memory_basis,
        "backtest_output_bytes": sum(files.values()),
        "backtest_files": files,
        "output_scope": "runs directory including SQLite, diagnostics, resolved inputs, trace, manifests and reports; excludes cache, preflight and this benchmark report",
        "run_id": run_id,
        "trace": str(trace),
        "manifest": str(manifest),
        "limits": "One local measurement, not a production resource guarantee. Scanner history snapshots can multiply output size. No data or revisions were dropped to reduce costs.",
    }
    (out / "benchmark.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workload", choices=("early-close", "multi-session"), required=True
    )
    parser.add_argument(
        "--out",
        required=True,
        help="New directory, preserving earlier benchmark attempts",
    )
    args = parser.parse_args()
    result = benchmark(args.out, args.workload)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "workload",
                    "elapsed_backtest_seconds",
                    "peak_memory_bytes",
                    "backtest_output_bytes",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
