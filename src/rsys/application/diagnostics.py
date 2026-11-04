"""Observe execution once; retain progress and a counted explanation of each session.

Wall time only throttles operational checkpoints. The semantic trace, strategy
clock, adapter calls and account outcomes are unaffected by these observations.
"""

from dataclasses import asdict
from time import monotonic

from rsys.application.models import SessionFunnel
from rsys.research.models import DiagnosticEventCode as Code

FUNNEL_UNITS = {
    "opportunities": "one actual completed-bar strategy call per symbol",
    "scanner_eligible": "opportunities selected in the scanner top N",
    "proposals": "non-None results of those strategy calls",
    "acceptances": "proposals accepted by the acceptance book",
    "entry_fills": "committed entry fills (cancellations are not fills)",
    "exits": "committed exit fills",
    "reasons": "occurrences by stage; reference checks are not proposals or trades",
}


class ExecutionDiagnostics:
    def __init__(self, invocation, dates, *, clock=monotonic, interval=1.0):
        self.invocation = invocation
        self.sessions = {day: SessionFunnel(day) for day in dates}
        self.current = None
        self.processed_events = self.entry_fills = self.exits = 0
        self._clock, self._interval, self._last = clock, interval, None
        self._halt = None

    def session(self, boundary, session, *, checkpoint=True):
        self.current = session["session_date"]
        row = self.sessions[self.current]
        row.status = session["status"]
        if boundary == "start":
            self._halt = None
        if checkpoint:
            self.checkpoint(force=True)

    def checkpoint(self, *, force=False):
        now = self._clock()
        if not force and self._last is not None and now - self._last < self._interval:
            return
        self.invocation.event(
            Code.PROGRESS,
            self.invocation.stage,
            details={
                "date": self.current,
                "completed_sessions": sum(
                    s.status == "success" for s in self.sessions.values()
                ),
                "total_sessions": len(self.sessions),
                "processed_events": self.processed_events,
                "entry_fills": self.entry_fills,
                "exits": self.exits,
            },
        )
        self._last = now

    def observation(self, observation):
        row = self.sessions[self.current]
        if observation.kind == "strategy":
            row.opportunities += 1
            row.scanner_eligible += observation.eligible
            row.proposals += observation.proposed
        details = asdict(observation)
        self._reason(details)

    def _reason(self, details):
        self.invocation.event(
            Code.EXECUTION_OBSERVATION,
            self.invocation.stage,
            details={"date": self.current, **details},
        )
        key = f"{details['kind']}:{details['code']}"
        reason = self.sessions[self.current].reasons.setdefault(
            key,
            {
                "stage": details["kind"],
                "code": details["code"],
                "count": 0,
                "examples": [],
            },
        )
        reason["count"] += 1
        if len(reason["examples"]) < 3:
            reason["examples"].append(
                {
                    "event_id": details.get("event_id"),
                    "symbol": details.get("symbol"),
                    "journal_sequence": self.invocation.sequence,
                }
            )

    def trace(self, row):
        session = self.sessions[self.current]
        kind = row["kind"]
        base = {"event_id": row["event_id"], "available_at_ms": row["available_at_ms"]}
        if kind == "fill":
            if row["status"] == "FILLED":
                self.entry_fills += 1
                session.entry_fills += 1
            elif row["status"] == "EXIT":
                self.exits += 1
                session.exits += 1
            else:
                self._reason(
                    {
                        **base,
                        "kind": "intent",
                        "code": row["status"],
                        "symbol": row["symbol"],
                    }
                )
        else:
            self.processed_events += 1
        if kind == "accept":
            session.acceptances += len(row["accepted"])
            for symbol, code in row["rejected"].items():
                self._reason(
                    {**base, "kind": "acceptance", "code": code, "symbol": symbol}
                )
        if kind == "decision":
            if not row["candidates"]:
                self._reason(
                    {**base, "kind": "scanner_batch", "code": "NO_SCANNER_CANDIDATE"}
                )
            for symbol, detail in (
                row.get("scanner_evidence", {}).get("symbols", {}).items()
            ):
                # These are existing feature results, not a second scanner evaluation.
                codes = {detail.get("current_status")}
                for feature in ("rvol", "gain"):
                    result = detail.get(feature, {})
                    if result.get("value") is None:
                        codes.add(result.get("status"))
                for code in sorted(c for c in codes if c):
                    self._reason(
                        {**base, "kind": "history", "code": code, "symbol": symbol}
                    )
        halt = row.get("risk", {}).get("halt_reason")
        if halt and halt != self._halt:
            self._reason({**base, "kind": "risk", "code": halt})
            self._halt = halt
        self.checkpoint()

    def snapshot(self):
        return {
            "schema_version": 1,
            "units": FUNNEL_UNITS,
            "invocation_id": self.invocation.invocation_id,
            "sessions": [asdict(s) for s in self.sessions.values()],
        }
