"""Optional human progress on stderr. A broken console never changes a run result."""

from time import monotonic

from rsys.research.models import DiagnosticEventCode as Code


class ProgressReporter:
    def __init__(self, mode, stream, *, clock=monotonic, interval=1.0):
        self.enabled = mode == "on" or (mode == "auto" and stream.isatty())
        self.stream, self.clock, self.interval = stream, clock, interval
        self.started = clock()
        self.last = None
        self.details = {}
        self.invocation_id = self.run_id = None
        self.stage = "preparing"

    def __call__(self, event):
        boundary_before = (
            self.details.get("date"),
            self.details.get("completed_sessions"),
            self.details.get("total_sessions"),
        )
        self.invocation_id, self.run_id, self.stage = (
            event.invocation_id,
            event.run_id,
            event.stage,
        )
        if event.event != Code.EXECUTION_OBSERVATION:
            self.details.update(event.details)
        force = event.event in (
            Code.INVOCATION_START,
            Code.SESSION_START,
            Code.SESSION_END,
            Code.FAILURE,
            Code.ARTIFACTS_READY,
        )
        if event.event == Code.PROGRESS:
            force = force or boundary_before != (
                self.details.get("date"),
                self.details.get("completed_sessions"),
                self.details.get("total_sessions"),
            )
        if event.event == Code.EXECUTION_OBSERVATION:
            return
        self._show(event.event.value, force=force)

    def finish(self, outcome):
        self._show(outcome, force=True)

    def _show(self, event, *, force=False):
        if not self.enabled:
            return
        now = self.clock()
        if not force and self.last is not None and now - self.last < self.interval:
            return
        d = self.details
        message = (
            f"progress: invocation={self.invocation_id} run={self.run_id or 'unresolved'} "
            f"stage={self.stage} event={event} session={d.get('date') or 'unknown'} "
            f"sessions={d.get('completed_sessions', 0)}/{d.get('total_sessions', 'unknown')} "
            f"events={d.get('processed_events', 0)} entry_fills={d.get('entry_fills', 0)} "
            f"exits={d.get('exits', 0)} elapsed={max(0, now - self.started):.1f}s"
        )
        try:
            print(message, file=self.stream, flush=True)
        except (OSError, ValueError):
            self.enabled = False
        self.last = now
