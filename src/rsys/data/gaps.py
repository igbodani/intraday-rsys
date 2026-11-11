"""Feed-scoped evidence for absent candles; never creates a price or execution.

A bounded subset of Alpaca's documented minute-bar conditions is supported.
Unknown combinations remain unknown rather than guessing their eligibility.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from rsys.data.timestamps import parse_utc_timestamp_ns

GAP_POLICY_VERSION = "alpaca_sparse_minutes_v1"
KNOWN_GAPS = {"NO_REPORTED_TRADES", "VOLUME_ONLY"}


def trade_request(request, minute):
    start = datetime.fromisoformat(minute)
    # The endpoint's end is inclusive; preserve the final nanosecond of the minute.
    end = start + timedelta(minutes=1, seconds=-1)
    return {
        **{k: request[k] for k in ("symbols", "feed", "asof", "sort", "limit")},
        "start": minute,
        "end": end.strftime("%Y-%m-%dT%H:%M:%S") + ".999999999Z",
    }


def classify_gap(pages, request, minute):
    """Require complete pagination, valid rows and unambiguous trade identities."""
    if not pages or pages[-1].get("next_page_token") is not None:
        raise ValueError("Incomplete gap trade pagination")
    seen_tokens, trades = set(), {}
    start = parse_utc_timestamp_ns(minute)
    for index, page in enumerate(pages):
        token = page.get("next_page_token")
        if index < len(pages) - 1:
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise ValueError("Invalid gap pagination chain")
            seen_tokens.add(token)
        payload = page.get("trades")
        if not isinstance(payload, dict) or set(payload) - {request["symbols"]}:
            raise ValueError("Invalid gap trade symbols")
        rows = payload.get(request["symbols"], [])
        if not isinstance(rows, list):
            raise TypeError("Invalid gap trade list")
        for row in rows:
            try:
                ns = parse_utc_timestamp_ns(row["t"])
                price, size = Decimal(str(row["p"])), Decimal(str(row["s"]))
                if not start <= ns < start + 60_000_000_000:
                    raise ValueError("Gap trade outside requested minute")
                if any(not v.is_finite() or v <= 0 for v in (price, size)):
                    raise ValueError("Invalid gap trade amounts")
                if (
                    type(row["i"]) is not int
                    or row["i"] < 0
                    or not isinstance(row["x"], str)
                    or not row["x"]
                    or not isinstance(row["z"], str)
                    or not isinstance(row["c"], list)
                    or any(not isinstance(c, str) for c in row["c"])
                ):
                    raise ValueError("Invalid gap trade identity/conditions")
                if request["feed"] == "iex" and row["x"] != "V":
                    raise ValueError("IEX gap trade came from a different exchange")
                key = (row["x"], row["i"])
                value = (ns, price, size, tuple(sorted(row["c"])), row["z"])
                if key in trades and trades[key] != value:
                    raise ValueError("Conflicting gap trade identity")
                trades[key] = value
            except (KeyError, TypeError, InvalidOperation) as exc:
                raise ValueError("Malformed gap trade") from exc
    if not trades:
        status, volume = "NO_REPORTED_TRADES", Decimal(0)
    elif all(
        "I" in value[3]
        and set(value[3]) <= {"@", "I", "F"}
        and value[4] in {"A", "B", "C"}
        for value in trades.values()
    ):
        status = "VOLUME_ONLY"
        volume = sum((value[2] for value in trades.values()), Decimal(0))
    else:
        # Regular trades contradict the absence; unsupported conditions need review.
        status, volume = "UNKNOWN", None
    return {
        "minute": minute,
        "status": status,
        "volume": str(volume) if volume is not None else None,
        "trade_count": len(trades),
    }


def validate_gap_evidence(evidence, request, missing_minutes):
    if not isinstance(evidence, list) or len(evidence) != len(missing_minutes):
        raise ValueError("Gap evidence does not cover exactly the missing minutes")
    results = []
    for item, minute in zip(evidence, missing_minutes, strict=True):
        expected = trade_request(request, minute)
        if item["request"] != expected:
            raise ValueError("Gap request identity mismatch")
        results.append(classify_gap(item["pages"], expected, minute))
    return results


def dataset_gaps(dataset):
    """Old bar-only objects remain usable, but do not establish quiet minutes."""
    return dataset.get(
        "gaps",
        [
            {"minute": t, "status": "UNKNOWN", "volume": None, "trade_count": None}
            for t in dataset["quality"]["missing_minutes"]
        ],
    )


def estimate_label(feed):
    return "IEX-based estimates" if feed == "iex" else "SIP-based estimates"
