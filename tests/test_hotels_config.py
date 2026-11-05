from datetime import date
from pathlib import Path

import pytest

from vacation_planner.config import ConfigError, guests_on, load_config
from vacation_planner.models import BookedRule, HotelSource, PointsRule, Travellers, Child


def test_loads_the_three_watches(config_dir: Path):
    cfg = load_config(config_dir, env={"APIFY_TOKEN": "t"})
    ids = [w.id for w in cfg.hotels]
    assert ids == ["ikos-pfingsten-2027", "waldorf-herbst-2027", "mango-herbst-2027"]
    ikos, waldorf, mango = cfg.hotels
    assert ikos.nights == 7 and ikos.sources[HotelSource.BOOKING].startswith("https://www.booking.com/")
    assert ikos.rule == BookedRule(5640, 0.10, date(2027, 4, 7))
    assert waldorf.rule == PointsRule(2, 350, date(2027, 10, 9))
    assert mango.sources[HotelSource.HILTON] == "SEZITOL" and mango.rule.surcharge == 0
    assert cfg.point_value_eur == 0.0044
    assert cfg.secrets.apify_token == "t"


def test_room_match_is_a_case_insensitive_substring(config_dir: Path):
    ikos = load_config(config_dir, env={}).hotels[0]
    assert ikos.matches_room("Junior Suite mit eigenem Garten")
    assert ikos.matches_room("JUNIOR SUITE WITH PRIVATE GARDEN, all inclusive")
    assert not ikos.matches_room("Junior Suite mit Gartenblick")
    assert not ikos.matches_room(None)


def test_a_watch_without_room_list_matches_every_room(config_dir: Path):
    waldorf = load_config(config_dir, env={}).hotels[1]
    assert waldorf.room == () and waldorf.matches_room("anything") and waldorf.matches_room(None)


def test_missing_hotels_file_means_no_watches(config_dir: Path):
    (config_dir / "hotels.yaml").unlink()
    cfg = load_config(config_dir, env={})
    assert cfg.hotels == [] and cfg.point_value_eur is None


@pytest.mark.parametrize("old,new,message", [
    ("slot: pfingsten-2027", "slot: nowhere", "unknown slot nowhere"),
    ("check_out: 2027-05-14", "check_out: 2027-05-07", "check_out must be after check_in"),
    ("check_out: 2027-05-14", "check_out: 2027-05-17", "Google Hotels prices at most 8 nights"),
    ("type: booked", "type: maybe", "rule"),
])
def test_invalid_watches_are_rejected(config_dir: Path, old, new, message):
    f = config_dir / "hotels.yaml"
    f.write_text(f.read_text().replace(old, new, 1))
    with pytest.raises(ConfigError, match=message):
        load_config(config_dir, env={})


def test_a_points_watch_needs_a_hilton_source(config_dir: Path):
    f = config_dir / "hotels.yaml"
    f.write_text(f.read_text().replace("      hilton: SEZPIWA\n", "", 1))
    with pytest.raises(ConfigError, match="points rule needs a hilton source"):
        load_config(config_dir, env={})


def test_a_points_watch_needs_a_point_value(config_dir: Path):
    f = config_dir / "hotels.yaml"
    f.write_text(f.read_text().replace("hilton:\n  point_value_eur: 0.0044\n", ""))
    with pytest.raises(ConfigError, match="point_value_eur"):
        load_config(config_dir, env={})


def test_the_sons_age_is_taken_on_the_check_in_date():
    t = Travellers(2, (Child(date(2019, 4, 21)),))
    assert guests_on(t, date(2027, 4, 20)) == (2, (7,))
    assert guests_on(t, date(2027, 4, 21)) == (2, (8,))
    assert guests_on(t, date(2031, 5, 1)) == (3, ())      # 12 and older pays as an adult


def test_duplicate_watch_ids_are_rejected(config_dir: Path):
    f = config_dir / "hotels.yaml"
    f.write_text(f.read_text().replace("id: mango-herbst-2027", "id: waldorf-herbst-2027", 1))
    with pytest.raises(ConfigError, match="hotels.yaml: duplicate watch id waldorf-herbst-2027"):
        load_config(config_dir, env={})
