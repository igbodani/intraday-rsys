"""Escaped provider-validation evidence, distinct from trading results."""

import json
from html import escape


def provider_html(result):
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Alpaca sample validation</title>'
        "<h1>Alpaca sample validation</h1><p>Software success does not complete the real-provider review.</p>"
        "<pre>"
        + escape(json.dumps(result, indent=2, sort_keys=True, default=str))
        + "</pre></html>"
    )
