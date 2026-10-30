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

from rsys.data.models import (
    BarVersion,
    FieldMismatch,
    ParityResult,
    ProviderBarEvent,
    QualityState,
)

_PREREQUISITE_FIELDS = (
    "instrument_id",
    "feed",
    "interval",
    "bar_start_ns",
    "bar_end_ns",
    "adjustment_mode",
    "provider",
    "session_version",
)
_EXACT_FIELDS = ("open", "high", "low", "close", "volume")


def _not_comparable(reason: str) -> ParityResult:
    return ParityResult(
        comparable=False, match=False, mismatches=(), not_comparable_reason=reason
    )


def compare_bar_version_to_provider_bar(
    version: BarVersion,
    provider_bar: ProviderBarEvent,
    *,
    vwap_precision: int | None = None,
    as_of_ns: int | None = None,
) -> ParityResult:
    """Compare one builder-derived `BarVersion` to one `ProviderBarEvent`.

    `vwap_precision`, if given, is the declared number of decimal places the VWAP comparison is
    rounded to before comparing -- never a wide tolerance chosen to conceal a defect.
    """
    mismatched_prerequisites = [
        field
        for field in _PREREQUISITE_FIELDS
        if getattr(version, field) != getattr(provider_bar, field)
    ]
    if mismatched_prerequisites:
        return _not_comparable(
            f"prerequisite mismatch on {mismatched_prerequisites}: instrument/feed/interval/bucket "
            "boundaries and adjustment/unit basis must match before a field-by-field comparison is meaningful"
        )

    if provider_bar.quality_state != QualityState.OK:
        return _not_comparable(
            f"provider_bar quality_state is {provider_bar.quality_state.value!r}, not 'ok'; an "
            "invalid/suspect/unavailable observation is never comparable"
        )

    if version.status not in ("closed_as_of", "revised"):
        return _not_comparable(
            "a forming (not yet closed) version cannot be compared against a final provider bar"
        )

    is_revision = provider_bar.bar_revision > 1 or version.revision > 0
    if is_revision and as_of_ns is None:
        return _not_comparable(
            "revision comparison requires an explicit common as-of cutoff"
        )
    if (
        as_of_ns is not None
        and max(version.available_at_ns, provider_bar.available_at_ns) > as_of_ns
    ):
        return _not_comparable(
            "a selected publication was not available at the common cutoff"
        )

    mismatches = [
        FieldMismatch(field, getattr(version, field), getattr(provider_bar, field))
        for field in _EXACT_FIELDS
        if getattr(version, field) != getattr(provider_bar, field)
    ]

    # R3I-14: count is independent evidence, checked whenever the provider declares one --
    # never gated behind whether VWAP precision checking was also requested.
    provider_trade_count = getattr(provider_bar, "trade_count", None)
    if provider_trade_count is not None and version.trade_count != provider_trade_count:
        mismatches.append(
            FieldMismatch("trade_count", version.trade_count, provider_trade_count)
        )

    if (
        vwap_precision is not None
        and version.vwap_denominator != 0
        and provider_bar.vwap is not None
    ):
        quantum = Decimal(1).scaleb(-vwap_precision)
        builder_vwap = (version.vwap_numerator / version.vwap_denominator).quantize(
            quantum
        )
        provider_vwap = provider_bar.vwap.quantize(quantum)
        if builder_vwap != provider_vwap:
            mismatches.append(FieldMismatch("vwap", builder_vwap, provider_vwap))

    return ParityResult(
        comparable=True,
        match=len(mismatches) == 0,
        mismatches=tuple(mismatches),
        not_comparable_reason=None,
    )
