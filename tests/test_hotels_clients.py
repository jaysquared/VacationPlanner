import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from vacation_planner.hotels.clients import (BOOKING_ACTOR, HILTON_ACTOR, ApifyActor, BookingClient,
                                             GoogleHotelsClient, HiltonClient, booking_url, parse_booking,
                                             parse_google, parse_hilton)
from vacation_planner.models import HotelSource, HotelStay
from vacation_planner.providers.base import AuthError, ProviderError

FIX = Path(__file__).parent / "fixtures" / "hotels"
BOOKING_URL = "https://www.booking.com/hotel/es/ikos-porto-petro.de.html"
IKOS = HotelStay("ikos-pfingsten-2027", HotelSource.BOOKING, BOOKING_URL, "rates",
                 date(2027, 5, 7), date(2027, 5, 14), 2, (8,))
IKOS_GOOGLE = HotelStay("ikos-pfingsten-2027", HotelSource.GOOGLE, "ChkI2u_o2PSToOZmGg0vZy8xMXQwbTM2Y3piEAE",
                        "rooms", date(2027, 5, 7), date(2027, 5, 14), 2, (8,))


def waldorf(mode, adults=2, ages=(8,), source=HotelSource.HILTON, ref="SEZPIWA"):
    return HotelStay("waldorf-herbst-2027", source, ref, mode, date(2027, 10, 10), date(2027, 10, 15), adults, ages)


def load(name):
    return json.loads((FIX / name).read_text())


# ---- Booking.com ----

def test_booking_url_carries_dates_guests_and_child_ages():
    u = booking_url(BOOKING_URL, IKOS)
    assert u.startswith(BOOKING_URL + "?")
    for part in ("checkin=2027-05-07", "checkout=2027-05-14", "group_adults=2", "group_children=1",
                 "age=8", "no_rooms=1", "selected_currency=EUR"):
        assert part in u


def test_parse_booking_returns_every_room_with_its_refundability():
    res = parse_booking(load("booking_ikos.json"), IKOS)
    assert res.status == "ok"
    garden = [r for r in res.rates if r.room == "Junior Suite mit eigenem Garten"]
    assert len(garden) == 1
    g = garden[0]
    # the payable early-booker price (displayedPrice + excludedTaxesPrice), not the struck-through `price`
    assert (g.channel, g.kind, g.total, g.refundable) == ("booking", "cash", 6273.60, True)
    assert "Kostenlose Stornierung vor dem 7. April 2027" in g.rate_name
    assert len(res.rates) == 17


def test_parse_booking_undiscounted_option_total_is_displayed_plus_excluded_taxes():
    res = parse_booking(load("booking_ikos.json"), IKOS)
    deluxe = [r for r in res.rates if r.room and r.room.startswith("Deluxe Junior Suite mit teilweise")]
    assert [r.total for r in deluxe] == [6881.10]


def test_parse_booking_falls_back_to_price_without_displayed_price():
    items = load("booking_ikos.json")
    opt = items[0]["rooms"][0]["options"][0]
    del opt["displayedPrice"]
    opt["excludedTaxesPrice"] = None
    assert parse_booking(items, IKOS).rates[0].total == 6503.58


def test_parse_booking_rejects_other_dates():
    items = load("booking_ikos.json")
    items[0]["checkInDate"] = "2027-05-08"
    res = parse_booking(items, IKOS)
    assert res.status == "no_rates" and res.rates == [] and "other dates" in res.note


def test_parse_booking_with_nothing_found_is_no_rates():
    assert parse_booking([], IKOS).status == "no_rates"


def test_parse_booking_advance_purchase_option_is_non_refundable_even_if_booking_says_free_cancellation():
    items = load("booking_ikos.json")
    opt = items[0]["rooms"][0]["options"][0]
    opt["yourChoices"] = ["Adv. Purchase Rate"]
    opt["freeCancellation"] = True   # Booking's own flag says yes; the plan name overrides it
    res = parse_booking(items, IKOS)
    assert res.rates[0].refundable is False


def test_parse_booking_skips_non_eur_options():
    items = load("booking_ikos.json")
    items[0]["rooms"][0]["options"][0]["currency"] = "$"
    res = parse_booking(items, IKOS)
    assert len(res.rates) == 16   # the USD option for that room is dropped, the other 16 remain


# ---- Hilton ----

def test_parse_hilton_rates_keeps_cash_and_marks_advance_purchase_non_refundable():
    res = parse_hilton(load("hilton_waldorf_rates.json"), waldorf("rates"))
    assert res.status == "ok"
    assert all(r.kind == "cash" and r.channel == "direct" and r.total > 0 for r in res.rates)
    villa = {r.rate_name: r for r in res.rates if r.room == "One Bedroom Hawksbill Pool Villa"}
    assert villa["Honors Book Early and Save Breakfast Included"].total == 7402.5
    assert villa["Honors Book Early and Save Breakfast Included"].refundable is True
    # Hilton's data says refundable: true; an Advance Purchase rate is not.
    assert villa["Advance Purchase Breakfast Included"].refundable is False
    assert "Premium Room Rewards" not in villa   # a points plan with price_total 0 is not a cash rate


def test_parse_hilton_award_tells_standard_from_premium():
    res = parse_hilton(load("hilton_waldorf_award_2a.json"), waldorf("award", ages=()))
    std = [r for r in res.rates if r.award_type == "standard"]
    assert [(r.room, r.points) for r in std] == [("One Bedroom Hawksbill Garden Pool Villa", 850000)]
    assert all(r.kind == "points" for r in res.rates)
    assert {r.award_type for r in res.rates} == {"standard", "premium"}


def test_parse_hilton_parity_keeps_other_channels_and_unknown_refundability():
    res = parse_hilton(load("hilton_waldorf_parity.json"), waldorf("parity"))
    by = {r.channel: r for r in res.rates}
    # direct is covered by `rates`; ctrip had no hotel; expedia/hotelscom quoted USD, not comparable
    assert set(by) == {"booking", "agoda"}
    assert (by["agoda"].total, by["agoda"].refundable) == (6470, False)
    assert all(r.total != 7905 for r in res.rates)   # the USD 7905 quotes never survive


def test_parse_hilton_rejects_rows_for_other_guests_or_dates():
    rows = load("hilton_waldorf_rates.json")
    assert parse_hilton(rows, waldorf("rates", adults=3, ages=())).status == "no_rates"
    rows[0]["checkin"] = "2027-10-11"
    assert parse_hilton(rows, waldorf("rates")).status == "no_rates"


def mango(mode, adults=2, ages=(8,)):
    return HotelStay("mango-herbst-2027", HotelSource.HILTON, "SEZITOL", mode,
                     date(2027, 10, 15), date(2027, 10, 20), adults, ages)


def test_parse_hilton_treats_abbreviated_advance_purchase_names_as_non_refundable():
    res = parse_hilton(load("hilton_mango_rates.json"), mango("rates"))
    assert res.status == "ok"
    for r in res.rates:
        if "adv" in r.rate_name.lower() and "purchase" in r.rate_name.lower():
            assert r.refundable is False, r.rate_name
    flexible = [r for r in res.rates if r.rate_name == "Flexible Rate incl Breakfast" and r.room == "King Deluxe Room"]
    assert flexible and flexible[0].refundable is True   # a genuinely flexible rate still alerts


# ---- Google Hotels ----

def test_parse_google_reads_sites_and_rooms():
    res = parse_google(load("google_ikos.json"), IKOS_GOOGLE)
    assert res.status == "ok"
    rooms = {r.room for r in res.rates if r.room}
    assert "Junior Suite mit Gartenblick" in rooms
    assert any(r.channel == "Booking.com" and r.room is None and r.total == 6840 for r in res.rates)


def test_parse_google_detects_the_silent_wrong_date_fallback():
    stay = waldorf("rooms", source=HotelSource.GOOGLE, ref="ChkInpaOvMuKrLtZGg0vZy8xMWw3M3ZodDQ1EAE")
    res = parse_google(load("google_waldorf_wrong_dates.json"), stay)
    assert res.status == "no_rates" and res.rates == [] and "other dates" in res.note


#: One room from google_waldorf_wrong_dates.json's "Villa mit 1 Schlafzimmer und Pool": a
#: non-refundable 1,652 rate and a free-cancellation 1,942 rate for the same room.
GOOGLE_ROOM_WITH_MIXED_RATES = {
    "search_parameters": {"currency": "EUR"},
    "rate_per_night": {"extracted_lowest": 1652}, "total_rate": {"extracted_lowest": 1652},
    "featured_prices": [{"source": "Booking.com", "rooms": [{
        "name": "Villa mit 1 Schlafzimmer und Pool", "link": "https://example.com/room",
        "total_rate": {"extracted_lowest": 1652},
        "rates": [
            {"link": "https://example.com/rate1", "breakfast_included": True,
             "total_rate": {"extracted_lowest": 1652}, "inclusions": ["3 Gäste", "Frühstück"]},
            {"link": "https://example.com/rate2", "free_cancellation": True, "breakfast_included": True,
             "total_rate": {"extracted_lowest": 1942},
             "inclusions": ["3 Gäste", "Kostenlose Stornierung bis zum 12. Nov.", "Frühstück"]},
        ],
    }]}],
}


#: One night, so the nightly-vs-total date-ratio guard (which only applies for 2+ nights) is skipped.
ONE_NIGHT_GOOGLE_STAY = HotelStay("mango-herbst-2027", HotelSource.GOOGLE, "tok", "rooms",
                                  date(2027, 10, 15), date(2027, 10, 16), 2, (8,))


def test_parse_google_emits_one_rate_per_entry_with_its_own_total_and_refundability():
    res = parse_google(GOOGLE_ROOM_WITH_MIXED_RATES, ONE_NIGHT_GOOGLE_STAY)
    assert res.status == "ok"
    by_total = {r.total: r for r in res.rates}
    assert by_total[1652].refundable is None   # no `free_cancellation` key on this entry: unknown, never alerts
    assert by_total[1942].refundable is True
    assert all(r.room == "Villa mit 1 Schlafzimmer und Pool" for r in res.rates)


def test_parse_google_advance_purchase_inclusions_are_non_refundable_even_with_free_cancellation_flag():
    data = json.loads(json.dumps(GOOGLE_ROOM_WITH_MIXED_RATES))   # deep copy
    entry = data["featured_prices"][0]["rooms"][0]["rates"][1]
    entry["inclusions"] = ["Advance Purchase Rate"]
    res = parse_google(data, ONE_NIGHT_GOOGLE_STAY)
    assert {r.refundable for r in res.rates} == {None, False}   # the 1942 entry is no longer refundable


def test_parse_google_non_eur_response_is_no_rates():
    data = json.loads(json.dumps(GOOGLE_ROOM_WITH_MIXED_RATES))
    data["search_parameters"]["currency"] = "USD"
    res = parse_google(data, IKOS_GOOGLE)
    assert res.status == "no_rates" and res.rates == []


def test_google_no_results_is_no_rates_not_an_error(httpx_mock):
    httpx_mock.add_response(json={"error": "Google Hotels hasn't returned any results for this query."})
    res = GoogleHotelsClient("KEY", http=httpx.Client()).check(IKOS_GOOGLE)
    assert res.status == "no_rates"


def test_google_auth_failure_is_an_auth_error_without_the_key(httpx_mock):
    httpx_mock.add_response(status_code=401, json={"error": "Invalid API key KEY"})
    with pytest.raises(AuthError) as e:
        GoogleHotelsClient("KEY", http=httpx.Client()).check(IKOS_GOOGLE)
    assert "KEY" not in str(e.value)


# ---- Apify ----

def test_apify_runs_the_actor_and_returns_its_items(httpx_mock):
    httpx_mock.add_response(
        url=f"https://api.apify.com/v2/acts/{BOOKING_ACTOR}/run-sync-get-dataset-items?token=TOKEN",
        status_code=201, json=load("booking_ikos.json"))
    res = BookingClient(ApifyActor("TOKEN", http=httpx.Client())).check(IKOS)
    assert res.status == "ok"
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["startUrls"][0]["url"] == booking_url(BOOKING_URL, IKOS)
    assert (body["adults"], body["children"], body["checkIn"], body["checkOut"]) == (2, 1, "2027-05-07", "2027-05-14")


def test_hilton_client_sends_mode_code_and_child_ages(httpx_mock):
    httpx_mock.add_response(status_code=201, json=load("hilton_waldorf_award_2a.json"))
    HiltonClient(ApifyActor("TOKEN", http=httpx.Client())).check(waldorf("award", ages=()))
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert (body["mode"], body["chain"], body["hotelCodes"], body["checkin"], body["los"], body["adults"],
            body["childrenAges"]) == ("award", "hilton", ["SEZPIWA"], "2027-10-10", 5, 2, [])
    assert httpx_mock.get_requests()[0].url.path == f"/v2/acts/{HILTON_ACTOR}/run-sync-get-dataset-items"


def test_a_failed_actor_run_is_a_provider_error_without_the_token(httpx_mock):
    httpx_mock.add_response(status_code=400, json={"error": {"type": "run-failed",
                                                            "message": "Actor run did not succeed (token TOKEN)"}})
    with pytest.raises(ProviderError) as e:
        BookingClient(ApifyActor("TOKEN", http=httpx.Client())).check(IKOS)
    assert "TOKEN" not in str(e.value) and "run-failed" in str(e.value)


def test_a_rejected_token_is_an_auth_error(httpx_mock):
    httpx_mock.add_response(status_code=401, json={"error": {"type": "user-or-token-not-found", "message": "x"}})
    with pytest.raises(AuthError):
        HiltonClient(ApifyActor("TOKEN", http=httpx.Client())).check(waldorf("rates"))
