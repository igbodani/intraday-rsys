"""Date authorization before cache/network reads, including scanner warmup."""

from datetime import date

from rsys.application.session_config import fields
from rsys.research.models import DatePartition


class DatasetPolicy:
    def __init__(self, rows, purpose):
        if purpose not in ("development", "validation"):
            raise ValueError("Only development or validation access is implemented")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Declare nonempty date partitions")
        self.partitions = []
        for row in rows:
            fields(row, {"name", "start", "end"}, {"name", "start", "end"})
            part = DatePartition(**row)
            if part.name not in (
                "development",
                "validation",
                "final",
            ) or date.fromisoformat(part.start) > date.fromisoformat(part.end):
                raise ValueError("Invalid date partition")
            if any(
                part.start <= p.end and p.start <= part.end for p in self.partitions
            ):
                raise ValueError("Date partitions overlap")
            if (
                date.fromisoformat(part.start).isoformat() != part.start
                or date.fromisoformat(part.end).isoformat() != part.end
            ):
                raise ValueError("Use canonical partition dates")
            self.partitions.append(part)
        self.purpose = purpose
        self.log = []

    def check(self, day):
        value = day.isoformat()
        part = next((p for p in self.partitions if p.start <= value <= p.end), None)
        allowed = (
            part is not None
            and part.name != "final"
            and (self.purpose == "validation" or part.name == "development")
        )
        self.log.append(
            {
                "date": value,
                "partition": part.name if part else "UNKNOWN",
                "purpose": self.purpose,
                "allowed": allowed,
            }
        )
        if not allowed:
            raise ValueError(
                f"Dataset access denied for {value} (including warmup); final evaluation remains blocked"
            )
