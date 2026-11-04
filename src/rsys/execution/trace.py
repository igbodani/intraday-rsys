"""Copy-isolated observation; no I/O or wall clock enters an execution runner."""

from copy import deepcopy

from rsys.execution.models import TraceSink


def emit_trace(rows: list[dict], row: dict, sink: TraceSink | None) -> None:
    rows.append(row)
    if sink is not None:
        # A callback may retain or mutate its payload, but never the account,
        # strategy inputs or the runner's original semantic history.
        sink(deepcopy(row))
