"""Readable, escaped representation of the retained preflight JSON."""

import json
from html import escape


def preflight_html(result):
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Offline data preflight</title><h1>Offline data preflight</h1>"
        f"<p>Can execute: {result['can_execute']}. Research status: unscored.</p>"
        "<p>Missing minutes are unavailable, not verified zero volume. "
        "Execution eligibility does not establish strategy quality.</p><pre>"
        + escape(json.dumps(result, sort_keys=True, indent=2, default=str))
        + "</pre></html>"
    )
