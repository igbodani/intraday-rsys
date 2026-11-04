"""Readable labels for observed reason codes; unknown codes remain explicit."""

REASON_TEXT = {
    "NO_SCANNER_CANDIDATE": "No symbol was selected by the scanner at this decision time.",
    "NO_SCANNER_INPUT": "No scanner input was supplied for this symbol at this decision time.",
    "OUTSIDE_TOP_N": "Symbol ranked outside the configured scanner selection.",
    "NO_PROPOSAL": "Strategy returned no proposal; its internal reason is not available.",
    "PROPOSAL": "Strategy returned a proposal (acceptance and fill are separate stages).",
    "NO_COMPLETED_BAR": "No completed bar was available for this symbol.",
    "STALE_CURRENT_BAR": "Latest completed bar exceeded the configured age limit.",
    "INCOMPLETE_CURRENT_MINUTES": "Current-session minute coverage is incomplete.",
    "MISSING_SELECTED_HISTORY": "Selected history is missing; it cannot be treated as zero volume.",
    "INSUFFICIENT_HISTORY": "The declared history window has too few eligible sessions.",
    "ZERO_DENOMINATOR": "Verified historical volume is zero; relative volume is unavailable.",
    "MISSING_RVOL": "Relative volume is unavailable; inspect the history evidence.",
    "MISSING_GAIN": "Session gain is unavailable; inspect the previous-close evidence.",
    "MISSING_PREVIOUS_CLOSE": "The required previous-session closing price is unavailable.",
    "MIXED_FEED_FEATURE": "Current and historical feature inputs use different feeds.",
    "RISK_BUDGET": "The declared risk budget could not fund the minimum permitted quantity.",
    "INSUFFICIENT_SPENDABLE_CASH": "Spendable cash could not fund the reservation.",
    "ENTRY_THROUGH_STOP": "Entry reference was at or beyond the stop.",
    "ENTRY_WINDOW_CLOSED": "The configured entry window had already closed.",
    "NOT_DOLLAR_VOLUME_ELIGIBLE": "Dollar-volume eligibility was not met or could not be established.",
    "INSUFFICIENT_CASH": "Available cash could not fund the proposed entry under the declared sizing rules.",
    "DAILY_LOSS_HALT": "Configured daily loss limit halted new entries.",
    "ACCOUNT_DRAWDOWN_HALT": "Configured account drawdown limit halted new entries.",
    "EXPIRED": "An accepted intent expired without an entry fill.",
    "REJECTED_EXPIRED": "This reference arrived after the intent expired.",
    "REJECTED_CONDITION": "Trade conditions did not permit use as an execution reference.",
    "REJECTED_BAD_QUALITY": "Trade quality did not permit use as an execution reference.",
    "REJECTED_STALE": "Trade was stale or invalidated for this execution attempt.",
    "REJECTED_SELF_CONSUMED": "Trade was already consumed by the decision or entry.",
    "REJECTED_NOT_READY": "Entry was not ready at this reference's availability time.",
    "REJECTED_ENTRY_THROUGH_STOP": "Entry reference was at or beyond the stop.",
    "SKIPPED": "Eligible trade did not trigger an exit.",
    "MISSING_EXIT_REFERENCE": "A required exit had no eligible subsequent execution reference; the session is incomplete.",
    "PENDING_INTENT_AT_END": "Input ended with an unresolved intent and no expiry.",
}


def explain_reason(code):
    return REASON_TEXT.get(
        code,
        f"Recorded reason: {code}. See the original event; no additional cause is inferred.",
    )
