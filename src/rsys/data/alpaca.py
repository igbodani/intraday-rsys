"""Alpaca historical stock bars transport. Credentials never enter retained data."""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AlpacaBarsClient:
    ENDPOINT = "https://data.alpaca.markets/v2/stocks/bars"
    PAYLOAD_KEY = "bars"

    def __init__(self, *, transport=None, sleep=time.sleep, observer=None):
        if transport is not None and not callable(transport):
            raise TypeError("transport must be callable or None")
        self.transport = self._request if transport is None else transport
        self.sleep = sleep
        self.observer = observer
        self.stats = {
            "request_count": 0,
            "retry_count": 0,
            "page_count": 0,
            "http_errors": {},
        }

    def _observe(self, kind, http_status=None):
        if self.observer is not None:
            self.observer(
                {
                    "kind": kind,
                    "http_status": http_status,
                    **{
                        k: self.stats[k]
                        for k in ("request_count", "retry_count", "page_count")
                    },
                }
            )

    @classmethod
    def _request(cls, params):
        key, secret = (
            os.environ.get("APCA_API_KEY_ID"),
            os.environ.get("APCA_API_SECRET_KEY"),
        )
        if not key or not secret:
            raise ValueError(
                "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment"
            )
        if any(
            not value.strip() or not value.isascii() or not value.isprintable()
            for value in (key, secret)
        ):
            raise ValueError("Invalid Alpaca credential format")
        request = Request(
            cls.ENDPOINT + "?" + urlencode(params),
            headers={
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
            },
        )
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            return json.loads(response.read(), parse_float=Decimal)

    def pages(self, params):
        params = dict(params)
        seen = set()
        for _page in range(10000):
            for attempt in range(3):
                self.stats["request_count"] += 1
                self._observe("request")
                try:
                    page = self.transport(params)
                    break
                except ValueError:
                    raise ValueError(
                        "Invalid Alpaca credentials or response; verify APCA_API_KEY_ID and APCA_API_SECRET_KEY"
                    ) from None
                except HTTPError as exc:
                    code = str(exc.code)
                    self.stats["http_errors"][code] = (
                        self.stats["http_errors"].get(code, 0) + 1
                    )
                    if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                        self.stats["retry_count"] += 1
                        self._observe("retry", exc.code)
                        self.sleep(2**attempt)
                        continue
                    self._observe("http_error", exc.code)
                    raise ValueError(
                        f"Alpaca request failed (HTTP {exc.code}); check access or retry later"
                    ) from None
                except (URLError, TimeoutError, OSError):
                    if attempt < 2:
                        self.stats["retry_count"] += 1
                        self._observe("retry")
                        self.sleep(2**attempt)
                        continue
                    raise ValueError(
                        "Alpaca transport unavailable after three attempts"
                    ) from None
            if not isinstance(page, dict) or not isinstance(
                page.get(self.PAYLOAD_KEY), dict
            ):
                raise TypeError(f"Invalid Alpaca {self.PAYLOAD_KEY} response")
            self.stats["page_count"] += 1
            self._observe("page")
            yield page
            token = page.get("next_page_token")
            if token is None:
                return
            if not isinstance(token, str) or not token or token in seen:
                raise ValueError("Invalid or repeated Alpaca pagination token")
            seen.add(token)
            params = {**params, "page_token": token}
        raise ValueError("Alpaca pagination exceeded safety limit")


class AlpacaTradesClient(AlpacaBarsClient):
    """Same authenticated, bounded transport for acquisition-only gap evidence."""

    ENDPOINT = "https://data.alpaca.markets/v2/stocks/trades"
    PAYLOAD_KEY = "trades"
