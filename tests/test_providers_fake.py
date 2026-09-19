from datetime import date

import pytest

from vacation_planner.models import Offer, Provider, SearchRequest, SeatClass
from vacation_planner.providers.base import ProviderError, QuotaExhausted, filter_excluded, per_person
from vacation_planner.providers.fake import FakeFlightClient


def req(dest="BKK"):
    return SearchRequest("herbst-2026", "HAM", dest, date(2026, 10, 17), date(2026, 10, 31), SeatClass.BUSINESS, 2, 1)


def mk(airlines):
    return Offer(Provider.FAKE, 1, "EUR", 1, list(airlines), 0, 1, "", "", None, None, None, "", {})


def test_filter_excluded_is_case_insensitive():
    kept = filter_excluded([mk(["LH"]), mk(["ai", "LH"]), mk(["EK"])], ["AI"])
    assert [o.airlines for o in kept] == [["LH"], ["EK"]]


def test_per_person_split():
    assert per_person(3000, req(), True) == (3000, 1000)
    assert per_person(1000, req(), False) == (3000, 1000)


def test_fake_default_and_configured_prices():
    c = FakeFlightClient(prices={("HAM", "DXB"): [900, 800]})
    r = c.search(req())
    assert r.provider is Provider.FAKE and [o.price_total for o in r.offers] == [2400]  # 1000 + 100*14
    assert [o.price_total for o in c.search(req("DXB")).offers] == [900, 800]
    assert len(c.calls) == 2


def test_fake_failures():
    c = FakeFlightClient(fail={("HAM", "BKK")}, quota_after=1)
    with pytest.raises(ProviderError):
        c.search(req())
    c.search(req("DXB"))
    with pytest.raises(QuotaExhausted):
        c.search(req("DXB"))
