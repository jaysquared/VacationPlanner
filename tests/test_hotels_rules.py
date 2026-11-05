from datetime import date

from vacation_planner.hotels.rules import evaluate, evaluate_booked, evaluate_points
from vacation_planner.models import BookedRule, HotelRate, HotelSource, HotelWatch, PointsRule

IKOS = HotelWatch("ikos-pfingsten-2027", "Ikos Porto Petro", "pfingsten-2027", date(2027, 5, 7), date(2027, 5, 14),
                  ("Junior Suite mit eigenem Garten",), {HotelSource.BOOKING: "u"},
                  BookedRule(5640, 0.10, date(2027, 4, 7)))
WALDORF = HotelWatch("waldorf-herbst-2027", "Waldorf Astoria Seychelles Platte Island", "herbst-2027",
                     date(2027, 10, 10), date(2027, 10, 15), (), {HotelSource.HILTON: "SEZPIWA"},
                     PointsRule(2, 350, date(2027, 10, 9)))
GARDEN = "Junior Suite mit eigenem Garten"


def cash(total, room=GARDEN, refundable=True, channel="booking", rate_name="All-Inclusive"):
    return HotelRate(channel, "cash", total=total, room=room, rate_name=rate_name, refundable=refundable)


def points(n, award_type="standard"):
    return HotelRate("direct", "points", points=n, room="Villa", award_type=award_type)


def test_booked_alerts_at_exactly_the_threshold():
    v = evaluate_booked(IKOS, [cash(5076)])
    assert (v.state, v.reason, v.value, v.threshold) == ("alert", "cheaper_room", 5076, 5076)
    assert v.headline == "Rebook: Junior Suite mit eigenem Garten 5,076 € on booking (you pay 5,640 €, alert at ≤ 5,076 €)"


def test_booked_does_not_alert_one_euro_above():
    v = evaluate_booked(IKOS, [cash(5077), cash(6898)])
    assert (v.state, v.value) == ("fine", 5077)
    assert v.headline == "Keep your booking: cheapest Junior Suite mit eigenem Garten 5,077 € on booking (you pay 5,640 €)"


def test_booked_ignores_other_rooms_and_non_refundable_rates():
    v = evaluate_booked(IKOS, [cash(4000, room="Junior Suite mit Gartenblick"), cash(4500, refundable=False),
                               cash(4600, refundable=None), cash(6000)])
    assert (v.state, v.value) == ("fine", 6000)
    assert "non-refundable 4,500 €" in v.headline


def test_booked_without_the_room_is_no_rates():
    v = evaluate_booked(IKOS, [cash(4000, room="Superior Doppelzimmer")])
    assert v.state == "no_rates" and v.headline == "Junior Suite mit eigenem Garten not offered this week"


def test_points_cost_is_standard_points_times_value_plus_surcharge():
    v = evaluate_points(WALDORF, [points(850000), points(3925000, "premium"), cash(7402.5, channel="direct")], 0.0044)
    assert (v.state, v.threshold, v.value) == ("fine", 4090, 7402.5)
    assert v.headline == "Points still better: cash 7,402 € (direct) vs points 4,090 € (850,000 pts + 350 €)"   # f"{7402.5:,.0f}" rounds half to even


def test_points_alert_when_refundable_cash_is_cheaper():
    v = evaluate_points(WALDORF, [points(850000), cash(4089, channel="agoda")], 0.0044)
    assert (v.state, v.reason, v.value) == ("alert", "cash_beats_points", 4089)
    assert v.headline == "Pay cash: 4,089 € (agoda) vs points 4,090 € (850,000 pts + 350 €)"


def test_points_ignore_non_refundable_and_unknown_cash():
    v = evaluate_points(WALDORF, [points(850000), cash(3000, refundable=False, channel="agoda"),
                                  cash(3100, refundable=None, channel="expedia"), cash(7402.5, channel="direct")], 0.0044)
    assert (v.state, v.value) == ("fine", 7402.5)


def test_only_premium_rewards_alert_once_as_no_standard_reward():
    v = evaluate_points(WALDORF, [points(3925000, "premium"), cash(7402.5, channel="direct")], 0.0044)
    assert (v.state, v.reason) == ("alert", "no_standard_reward")
    assert v.headline == "Only Premium rewards this week — no Standard Room Reward for 2 adults"


def test_points_without_any_cash_still_reports_the_points_cost():
    v = evaluate_points(WALDORF, [points(850000)], 0.0044)
    assert (v.state, v.threshold) == ("no_rates", 4090)


def test_no_points_rows_at_all_is_no_rates_not_an_alert():
    v = evaluate_points(WALDORF, [cash(7402.5, channel="direct")], 0.0044)
    assert (v.state, v.reason) == ("no_rates", None)
    assert v.headline == "No award availability this week"


def test_a_watch_past_until_has_ended():
    v = evaluate(IKOS, [cash(1000)], None, date(2027, 4, 8))
    assert (v.state, v.headline) == ("ended", "Watch ended on 7 Apr 2027")
    assert evaluate(IKOS, [cash(1000)], None, date(2027, 4, 7)).state == "alert"
