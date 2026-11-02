"""Immutable, content-addressed regular-session datasets and independently checked coverage."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from rsys.data.calendar import CALENDAR_VERSION, session_close_utc, session_open_utc


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def request_identity(symbol, day, feed, asof):
    if not isinstance(symbol, str) or not re.fullmatch(
        r"[A-Z][A-Z0-9.\-]{0,14}", symbol
    ):
        raise ValueError("Invalid stock symbol")
    if feed not in ("sip", "iex"):
        raise ValueError("Select sip or iex explicitly")
    if asof != "-" and date.fromisoformat(asof).isoformat() != asof:
        raise ValueError("Declare a canonical symbol-mapping date or '-'")
    opening, closing = session_open_utc(day), session_close_utc(day)
    return {
        "symbols": symbol,
        "start": opening.isoformat(),
        "end": (closing - timedelta(microseconds=1)).isoformat(),
        "timeframe": "1Min",
        "feed": feed,
        "adjustment": "raw",
        "asof": asof,
        "sort": "asc",
        "limit": 10000,
    }


def normalize_pages(pages, request):
    from decimal import Decimal, InvalidOperation

    opening = datetime.fromisoformat(request["start"])
    closing = session_close_utc(opening.date())
    selected = {}
    duplicates = 0
    for page in pages:
        if set(page["bars"]) - {request["symbols"]}:
            raise ValueError("Alpaca returned an unexpected symbol")
        rows = page["bars"].get(request["symbols"], [])
        if not isinstance(rows, list):
            raise TypeError("Invalid Alpaca bars list")
        for row in rows:
            try:
                timestamp = datetime.fromisoformat(row["t"])
                if (
                    timestamp.utcoffset() is None
                    or timestamp.second
                    or timestamp.microsecond
                ):
                    raise ValueError(
                        "Bar timestamps must be timezone-aware minute starts"
                    )
                timestamp = timestamp.astimezone(UTC)
                if not opening <= timestamp < closing:
                    raise ValueError("Bar outside requested regular session")
                values = {k: Decimal(str(row[k])) for k in ("o", "h", "l", "c", "v")}
                if any(not v.is_finite() or v < 0 for v in values.values()):
                    raise ValueError("Invalid bar amount")
                if min(values[k] for k in ("o", "h", "l", "c")) <= 0:
                    raise ValueError("Bar prices must be positive")
                if (
                    not values["l"]
                    <= min(values["o"], values["c"])
                    <= max(values["o"], values["c"])
                    <= values["h"]
                ):
                    raise ValueError("Invalid OHLC bounds")
                bar = {
                    "t": timestamp.isoformat(),
                    **{k: str(v) for k, v in values.items()},
                }
            except (KeyError, TypeError, InvalidOperation) as exc:
                raise ValueError("Malformed Alpaca bar") from exc
            if bar["t"] in selected:
                if selected[bar["t"]] != bar:
                    raise ValueError(
                        "Conflicting duplicate bar; revision ordering is unavailable"
                    )
                duplicates += 1
            selected[bar["t"]] = bar
    expected = [
        opening + timedelta(minutes=m)
        for m in range(int((closing - opening).total_seconds() / 60))
    ]
    missing = [t.isoformat() for t in expected if t.isoformat() not in selected]
    return [selected[t] for t in sorted(selected)], {
        "missing_minutes": missing,
        "duplicate_rows": duplicates,
        "coverage_complete": not missing,
    }


class BarCache:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, kind, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("Invalid cache identity")
        path = self.root / kind / (identity + ".json")
        if path.resolve() != path or not path.resolve().is_relative_to(self.root):
            raise ValueError("Cache path escapes its root")
        return path

    @staticmethod
    def _write(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            temp.write_bytes(encode(payload))
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def load(self, identity, request):
        raw = self._path("objects/" + digest(request), identity).read_bytes()
        if hashlib.sha256(raw).hexdigest() != identity:
            raise ValueError("Cached dataset hash mismatch")
        payload = json.loads(raw)
        if (
            type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1
            or payload.get("provider") != "alpaca"
            or payload.get("timing_basis")
            != "final_historical_bars_not_recorded_arrivals"
        ):
            raise ValueError("Unsupported cached schema/provider/timing basis")
        if (
            payload.get("request") != request
            or payload.get("calendar_version") != CALENDAR_VERSION
        ):
            raise ValueError("Cached dataset identity mismatch")
        rows, quality = normalize_pages(payload["pages"], request)
        if rows != payload["bars"] or quality != payload["quality"]:
            raise ValueError("Cached normalization/coverage mismatch")
        return payload

    def acquire(self, request, client, *, refresh=False):
        index = self._path("requests", digest(request))
        if index.exists() and not refresh:
            identity = json.loads(index.read_bytes())["object_id"]
            self.load(identity, request)
            return identity
        pages = list(client.pages(request))
        bars, quality = normalize_pages(pages, request)
        payload = {
            "schema_version": 1,
            "provider": "alpaca",
            "calendar_version": CALENDAR_VERSION,
            "timing_basis": "final_historical_bars_not_recorded_arrivals",
            "request": request,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "pages": pages,
            "bars": bars,
            "quality": quality,
        }
        identity = digest(payload)
        target = self._path("objects/" + digest(request), identity)
        if not target.exists():
            self._write(target, payload)
        self._write(index, {"object_id": identity})
        return identity
