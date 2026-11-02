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

    def __init__(self, *, transport=None, sleep=time.sleep):
        self.transport = transport or self._request
        self.sleep = sleep

    @staticmethod
    def _request(params):
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
            AlpacaBarsClient.ENDPOINT + "?" + urlencode(params),
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
                try:
                    page = self.transport(params)
                    break
                except ValueError:
                    raise ValueError(
                        "Invalid Alpaca credentials or response; verify APCA_API_KEY_ID and APCA_API_SECRET_KEY"
                    ) from None
                except HTTPError as exc:
                    if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                        self.sleep(2**attempt)
                        continue
                    raise ValueError(
                        f"Alpaca request failed (HTTP {exc.code}); check access or retry later"
                    ) from None
                except (URLError, TimeoutError, OSError):
                    if attempt < 2:
                        self.sleep(2**attempt)
                        continue
                    raise ValueError(
                        "Alpaca transport unavailable after three attempts"
                    ) from None
            if not isinstance(page, dict) or not isinstance(page.get("bars"), dict):
                raise TypeError("Invalid Alpaca bars response")
            yield page
            token = page.get("next_page_token")
            if token is None:
                return
            if not isinstance(token, str) or not token or token in seen:
                raise ValueError("Invalid or repeated Alpaca pagination token")
            seen.add(token)
            params = {**params, "page_token": token}
        raise ValueError("Alpaca pagination exceeded safety limit")
