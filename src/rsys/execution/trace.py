"""Copy-isolated observation; no I/O or wall clock enters an execution runner."""

from copy import deepcopy

from rsys.execution.models import ExecutionObservation, ObservationSink, TraceSink


def emit_trace(rows: list[dict], row: dict, sink: TraceSink | None) -> None:
    rows.append(row)
    if sink is not None:
        # A callback may retain or mutate its payload, but never the account,
        # strategy inputs or the runner's original semantic history.
        sink(deepcopy(row))


def observe_strategy(sink, event, point, symbol, scan, proposal):
    if sink is None:
        return
    eligible = symbol in scan.top
    code = (
        "PROPOSAL"
        if proposal is not None
        else "NO_PROPOSAL"
        if eligible
        else scan.excluded.get(
            symbol, "OUTSIDE_TOP_N" if symbol in scan.ranked else "NO_SCANNER_INPUT"
        )
    )
    sink(
        ExecutionObservation(
            "strategy",
            event.event_id,
            point,
            symbol,
            code,
            eligible,
            proposal is not None,
        )
    )


def observe_incomplete(sink: ObservationSink | None, runner) -> None:
    if sink is None:
        return
    point = runner.trace[-1]["available_at_ms"] if runner.trace else 0
    for symbol in sorted(runner.exit_due & runner.portfolio.positions.keys()):
        sink(
            ExecutionObservation(
                "completion", "session_end", point, symbol, "MISSING_EXIT_REFERENCE"
            )
        )
    for symbol in sorted(runner.pending_intents):
        sink(
            ExecutionObservation(
                "completion", "session_end", point, symbol, "PENDING_INTENT_AT_END"
            )
        )
