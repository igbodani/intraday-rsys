"""Immutable full-session stock trade pages. A read always verifies its request and pin."""

import hashlib
import json
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from rsys.data.bar_cache import encode, validate_stock_request
from rsys.data.calendar import to_exchange_utc
from rsys.data.models import TapePrint
from rsys.data.tape_calendar import tape_hours
from rsys.data.timestamps import parse_utc_timestamp_ns


def tape_request(symbol, day, feed, mode="regular"):
    validate_stock_request(symbol, feed, "-")
    hours = tape_hours(day, mode)
    midnight = datetime.combine(day, time.min)
    opening = to_exchange_utc(midnight + timedelta(minutes=hours.data_open))
    closing = to_exchange_utc(midnight + timedelta(minutes=hours.data_close))
    return {
        "symbols": symbol,
        "start": opening.isoformat(),
        "end": (closing - timedelta(seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.999999999Z"
        ),
        "feed": feed,
        "asof": "-",
        "sort": "asc",
        "limit": 10000,
    }


_PRICE = frozenset("FKLOTX56")
_VOLUME = frozenset("CHINPRUVZ47")
_CTA = (_PRICE | {" ", "E"}, _VOLUME | {"B"}, " ")
_MINUTE_CODES = {
    "A": _CTA,
    "B": _CTA,
    "C": (_PRICE | set("@ABDY"), _VOLUME | set("GW"), "@"),
}


# Alpaca Market Data FAQ, minute (M) rows for US equity tapes.
# A print may update volume without updating price. Use the most restrictive flag.
def minute_flags(tape, conditions, *, extended=False):
    price_codes, volume_codes, regular = _MINUTE_CODES[tape]
    excluded = {"M", "Q", "9"}
    codes = set(conditions)
    if not codes or codes - price_codes - volume_codes - excluded:
        raise ValueError(
            f"Unmapped Alpaca minute conditions for tape {tape}: {conditions!r}"
        )
    return (
        codes <= price_codes,
        not bool(codes & excluded),
        codes <= ({regular, "F", "T"} if extended else {regular, "F"}),
    )


def normalize_tape(pages, params, *, classify=True, extended_window=None):
    """Normalize final historical trades and verify overlapping page records."""
    if (
        not pages
        or "next_page_token" not in pages[-1]
        or pages[-1]["next_page_token"] is not None
    ):
        raise ValueError("Incomplete trade tape pagination")
    symbol = params["symbols"]
    start = parse_utc_timestamp_ns(params["start"])
    end = parse_utc_timestamp_ns(params["end"])
    seen, tokens, prints = {}, set(), []
    for index, page in enumerate(pages):
        token = page.get("next_page_token")
        if index < len(pages) - 1:
            if not isinstance(token, str) or not token or token in tokens:
                raise ValueError("Invalid trade tape pagination")
            tokens.add(token)
        trades = page["trades"]
        if not isinstance(trades, dict) or set(trades) - {symbol}:
            raise ValueError("Unexpected symbols in trade tape")
        rows = trades.get(symbol, [])
        if not isinstance(rows, list):
            raise TypeError("Invalid trade list")
        for row in rows:
            stamp = parse_utc_timestamp_ns(row["t"])
            price, size = Decimal(str(row["p"])), Decimal(str(row["s"]))
            venue, seq, conditions = row["x"], row["i"], row["c"]
            if (
                not start <= stamp <= end
                or type(seq) is not int
                or seq < 0
                or not isinstance(venue, str)
                or not venue
                or row["z"] not in ("A", "B", "C")
                or (params["feed"] == "iex" and venue != "V")
                or not isinstance(conditions, list)
                or not all(isinstance(c, str) for c in conditions)
                or any(not n.is_finite() or n <= 0 for n in (price, size))
            ):
                raise ValueError("Invalid print in trade tape")
            update = row.get("u")
            if update in ("incorrect", "canceled"):
                continue
            if update not in (None, "corrected"):
                raise ValueError(
                    f"Unknown trade update for {symbol} at {row['t']}: {update!r}"
                )
            identity = f"{venue}:{seq}"
            if venue == "D":
                # FINRA reporting facilities reuse IDs within one symbol/session.
                # Always qualify D IDs so a later print cannot rename an earlier one.
                identity = f"{identity}@{stamp}"
            signature = (stamp, price, size, tuple(sorted(conditions)), row["z"])
            if identity in seen:
                if seen[identity] != signature:
                    raise ValueError(
                        f"Conflicting trade identity for {symbol} at {row['t']}: "
                        f"{identity} (first timestamp ns={seen[identity][0]})"
                    )
                continue
            seen[identity] = signature
            if classify:
                outside_regular = (
                    extended_window is not None
                    and not extended_window[0] <= stamp < extended_window[1]
                )
                flags = minute_flags(row["z"], conditions, extended=outside_regular)
                prints.append(
                    TapePrint(stamp - start, symbol, identity, price, size, *flags)
                )
    return sorted(prints, key=lambda p: (p.offset_ns, p.identity))


def acquire_tape(cache, params, client):
    pages = list(client.pages(params))
    normalize_tape(pages, params, classify=False)
    raw = encode(
        {"format": "alpaca_stock_trades_v1", "request": params, "pages": pages}
    )
    pin = hashlib.sha256(raw).hexdigest()
    root = Path(cache) / "trade-tapes"
    root.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=root, delete=False) as tmp:
        temp = Path(tmp.name)
        try:
            tmp.write(raw)
        except BaseException:
            tmp.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(root / (pin + ".json"))
    finally:
        temp.unlink(missing_ok=True)
    return pin


def load_tape(cache, params, pin, *, extended_window=None):
    if not isinstance(pin, str) or not re.fullmatch("[0-9a-f]{64}", pin):
        raise ValueError("Invalid trade tape pin")
    raw = (Path(cache) / "trade-tapes" / (pin + ".json")).read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("Trade tape hash mismatch")
    blob = json.loads(raw, parse_float=Decimal)
    if blob["format"] != "alpaca_stock_trades_v1" or blob["request"] != params:
        raise ValueError("Trade tape request mismatch")
    return normalize_tape(blob["pages"], params, extended_window=extended_window)
