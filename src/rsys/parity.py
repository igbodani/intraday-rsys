"""Offline parity comparator (R3-B / docs/REPLAY_CORRECTIONS_AND_R3_HANDOFF.md 7.5).

A comparator, not a provider downloader: this module never fetches or certifies real market
data. It compares a builder-derived `BarVersion` against a `ProviderBarEvent` only when their
instrument/feed/interval/bucket boundaries actually match; anything else is an explicit
not-comparable result, never a silently skipped or falsely-passing comparison. A forming
(not yet closed) version is never compared against a final provider bar -- that comparison is
meaningless by construction, not merely unverified.

Passing synthetic fixtures here proves comparator *behavior*; it is never evidence of real
provider parity (no verified Alpaca payload is used or implied by this module).
"""
from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from rsys.data.bar_version_store import BarVersion
from rsys.data.events import ProviderBarEvent


class FieldMismatch(NamedTuple):
    field: str
    builder_value: object
    provider_value: object


class ParityResult(NamedTuple):
    comparable: bool
    match: bool
    mismatches: tuple[FieldMismatch, ...]
    not_comparable_reason: str | None


_PREREQUISITE_FIELDS = ("instrument_id", "feed", "interval", "bar_start_ns", "bar_end_ns")
_EXACT_FIELDS = ("open", "high", "low", "close", "volume")


def _not_comparable(reason: str) -> ParityResult:
    return ParityResult(comparable=False, match=False, mismatches=(), not_comparable_reason=reason)


def compare_bar_version_to_provider_bar(
    version: BarVersion,
    provider_bar: ProviderBarEvent,
    *,
    vwap_precision: int | None = None,
) -> ParityResult:
    """Compare one builder-derived `BarVersion` to one `ProviderBarEvent`.

    `vwap_precision`, if given, is the declared number of decimal places the VWAP comparison is
    rounded to before comparing -- never a wide tolerance chosen to conceal a defect.
    """
    mismatched_prerequisites = [
        field for field in _PREREQUISITE_FIELDS if getattr(version, field) != getattr(provider_bar, field)
    ]
    if mismatched_prerequisites:
        return _not_comparable(
            f"prerequisite mismatch on {mismatched_prerequisites}: instrument/feed/interval/bucket "
            "boundaries must match before a field-by-field comparison is meaningful"
        )

    if version.status == "forming":
        return _not_comparable(
            "a forming (not yet closed) version cannot be compared against a final provider bar"
        )

    is_revision = getattr(provider_bar, "bar_revision", 1) > 1
    if version.revision == 0 and is_revision:
        return _not_comparable(
            "comparing a first-publication version against a provider revision is not a like-for-like "
            "as-of comparison; select the provider's first publication or the version's matching revision"
        )

    mismatches = [
        FieldMismatch(field, getattr(version, field), getattr(provider_bar, field))
        for field in _EXACT_FIELDS
        if getattr(version, field) != getattr(provider_bar, field)
    ]

    if vwap_precision is not None:
        provider_trade_count = getattr(provider_bar, "trade_count", None)
        if version.trade_count != provider_trade_count:
            mismatches.append(FieldMismatch("trade_count", version.trade_count, provider_trade_count))
        if version.vwap_denominator != 0 and provider_bar.vwap is not None:
            quantum = Decimal(1).scaleb(-vwap_precision)
            builder_vwap = (version.vwap_numerator / version.vwap_denominator).quantize(quantum)
            provider_vwap = provider_bar.vwap.quantize(quantum)
            if builder_vwap != provider_vwap:
                mismatches.append(FieldMismatch("vwap", builder_vwap, provider_vwap))

    return ParityResult(comparable=True, match=len(mismatches) == 0, mismatches=tuple(mismatches), not_comparable_reason=None)
