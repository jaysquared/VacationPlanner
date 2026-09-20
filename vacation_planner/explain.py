"""Deal reasons in plain words, for the digest email.

`deals.py` records *why* an offer is a deal as a list of `DealReason` enums. Those read
like database columns ("below_median"); an email should say what they mean, with the
numbers that made the rule fire.
"""

from __future__ import annotations

from .models import DealReason, Destination
from .report import RouteSummary, money
from .storage import OfferRow


def _below_median(offer: OfferRow, median: float | None) -> str:
    if not median:
        return "below the usual price for this route"
    pct = round((median - offer.price_total) / median * 100)
    return f"{pct} % below the usual price for this route (median {money(median)})"


def _under_max(destination: Destination) -> str:
    if destination.max_price_per_person is None:
        return "under your price ceiling"
    return f"under your ceiling of {money(destination.max_price_per_person)} per person"


def _cheaper_than_home(offer: OfferRow, route: RouteSummary | None) -> str:
    # `route.best` is the home origin whenever a home fare is on record, so it is the
    # price this alternate-origin fare was measured against.
    home = route.best.offer.price_total if route else None
    if not home or home <= offer.price_total:
        return "clearly cheaper than from Hamburg"
    pct = round((home - offer.price_total) / home * 100)
    return f"cheaper than the best Hamburg fare ({money(home)}) by {pct} %"


def explain(reasons: list[DealReason], offer: OfferRow, route: RouteSummary | None,
            destination: Destination, median: float | None = None) -> str:
    """One sentence per reason, in the order they were recorded, joined by "; ".

    `median` is the reference price this offer was judged against (`report.deal_median`);
    without one the row's median is quoted, which is the home origin's.
    """
    if median is None and route is not None:
        median = route.median
    parts: list[str] = []
    for reason in reasons:
        if reason is DealReason.BELOW_MEDIAN:
            parts.append(_below_median(offer, median))
        elif reason is DealReason.GOOGLE_LOW:
            parts.append("Google rates this fare as low for these dates")
        elif reason is DealReason.UNDER_MAX:
            parts.append(_under_max(destination))
        elif reason is DealReason.NEW_LOW:
            parts.append("lowest price seen so far for this trip")
        elif reason is DealReason.CHEAPER_THAN_HOME:
            parts.append(_cheaper_than_home(offer, route))
    return "; ".join(parts)
