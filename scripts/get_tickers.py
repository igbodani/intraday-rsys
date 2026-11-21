import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

request = Request(
    "https://paper-api.alpaca.markets/v2/assets?asset_class=us_equity&status=active",
    headers={
        "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
    },
)

with urlopen(request, timeout=60) as response:
    assets = json.load(response)

symbols = sorted({asset["symbol"] for asset in assets})
output = Path(__file__).with_name("tickers.json")
output.write_text(json.dumps({"symbols": symbols}, indent=2), encoding="utf-8")
print(f"Saved {len(symbols):,} tickers to {output}")