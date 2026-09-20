from datetime import date, datetime, timezone

from vacation_planner.calendar import Window
from vacation_planner.explain import explain
from vacation_planner.models import Cabin, DealReason, Destination, Nights, Provider, SeatClass, Slot
from vacation_planner.report import RouteSummary
from vacation_planner.storage import Observation, OfferRow, SearchRow

NOW = datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)
SLOT = Slot("weihnachten-2026", "Weihnachtsferien 2026/27", date(2026, 12, 21), date(2027, 1, 1),
            ("HKT",), Nights(10, 14))
HKT = Destination("HKT", "Phuket", Cabin.BUSINESS, 2000.0)
WINDOW = Window(date(2026, 12, 19), date(2027, 1, 3))


def offer(price: float, level: str | None = None, origin: str = "HAM") -> OfferRow:
    return OfferRow(id=1, search_id=1, provider=Provider.SERPAPI, price_total=price,
                    per_person=price / 3, airlines=["LX"], stops=2, duration_minutes=900,
                    departs_at="", arrives_at="", price_level=level, typical_low=None,
                    typical_high=None, google_url="https://g/1")


def search(origin: str = "HAM") -> SearchRow:
    return SearchRow(id=1, run_id=1, slot_id=SLOT.id, origin=origin, destination="HKT",
                     outbound_date=date(2026, 12, 19), return_date=date(2026, 12, 29),
                     seat=SeatClass.BUSINESS, adults=2, children=1, provider=Provider.SERPAPI,
                     requested_at=NOW, status="ok", error=None)


def route(median: float | None = 8900.0, best_price: float = 7000.0) -> RouteSummary:
    best = Observation(search(), offer(best_price))
    return RouteSummary(slot=SLOT, destination=HKT, seat=SeatClass.BUSINESS, best=best,
                        median=median, ratio=None, is_deal=True, page="p.html")


def test_below_median_names_the_gap_and_the_median():
    assert explain([DealReason.BELOW_MEDIAN], offer(6900), route(), HKT) \
        == "22 % below the usual price for this route (median 8,900 €)"


def test_below_median_without_a_median_stays_vague():
    assert explain([DealReason.BELOW_MEDIAN], offer(6900), route(median=None), HKT) \
        == "below the usual price for this route"
    assert explain([DealReason.BELOW_MEDIAN], offer(6900), None, HKT) \
        == "below the usual price for this route"


def test_google_low():
    assert explain([DealReason.GOOGLE_LOW], offer(6900, "low"), route(), HKT) \
        == "Google rates this fare as low for these dates"


def test_under_max_names_the_ceiling():
    assert explain([DealReason.UNDER_MAX], offer(5400), route(), HKT) \
        == "under your ceiling of 2,000 € per person"


def test_under_max_without_a_ceiling():
    anywhere = Destination("HKT", "Phuket", Cabin.BUSINESS, None)
    assert explain([DealReason.UNDER_MAX], offer(5400), route(), anywhere) \
        == "under your price ceiling"


def test_new_low():
    assert explain([DealReason.NEW_LOW], offer(6900), route(), HKT) \
        == "lowest price seen so far for this trip"


def test_cheaper_than_home_uses_the_home_price_on_the_row():
    assert explain([DealReason.CHEAPER_THAN_HOME], offer(5250), route(best_price=7000), HKT) \
        == "cheaper than the best Hamburg fare (7,000 €) by 25 %"


def test_cheaper_than_home_without_a_home_price():
    assert explain([DealReason.CHEAPER_THAN_HOME], offer(5250), None, HKT) \
        == "clearly cheaper than from Hamburg"
    # the row's best fare is the alternate one itself: no Hamburg price to compare against
    assert explain([DealReason.CHEAPER_THAN_HOME], offer(5250), route(best_price=5250), HKT) \
        == "clearly cheaper than from Hamburg"


def test_several_reasons_are_joined_with_semicolons():
    text = explain([DealReason.BELOW_MEDIAN, DealReason.GOOGLE_LOW, DealReason.NEW_LOW],
                   offer(6900, "low"), route(), HKT)
    assert text == ("22 % below the usual price for this route (median 8,900 €); "
                    "Google rates this fare as low for these dates; "
                    "lowest price seen so far for this trip")


def test_no_reasons_is_empty():
    assert explain([], offer(6900), route(), HKT) == ""
