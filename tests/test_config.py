import re
from datetime import date
from pathlib import Path

import pytest

from vacation_planner.config import ConfigError, ProviderEntry, ProviderSettings, load_config
from vacation_planner.models import Cabin, Nights, Provider


def test_loads_repo_config(config_dir: Path):
    cfg = load_config(config_dir, env={})
    assert cfg.travellers.adults == 2
    assert cfg.travellers.children[0].birthdate == date(2019, 4, 21)
    assert cfg.destination("BKK").cabin is Cabin.BUSINESS
    assert cfg.destination("PMI").cabin is Cabin.ANY
    assert cfg.destination("HKT").origins == ("HAM", "FRA")   # long haul: Hamburg or Frankfurt
    assert cfg.destination("TFS").origins is None             # short haul: settings.origins
    weihnachten = next(s for s in cfg.slots if s.id == "weihnachten-2026")
    assert weihnachten.start == date(2026, 12, 21) and weihnachten.nights == Nights(10, 14)
    assert weihnachten.targets[0] == "HKT" and len(weihnachten.targets) == 17
    pfingsten = next(s for s in cfg.slots if s.id == "pfingsten-2027")
    assert pfingsten.targets == ("TFS", "LPA", "FUE")
    herbst = next(s for s in cfg.slots if s.id == "herbst-2026")
    assert herbst.start == date(2026, 10, 19) and herbst.targets == ()   # not searched this year
    order = cfg.settings.providers.order
    assert [e.name for e in order] == [Provider.SERPAPI, Provider.SEARCHAPI, Provider.FAST_FLIGHTS]
    assert [e.monthly_budget for e in order] == [250, 100, None]
    assert cfg.settings.providers.entry(Provider.FAST_FLIGHTS).pause_seconds == 5
    assert [e.name for e in cfg.settings.providers.budgeted()] == [Provider.SERPAPI, Provider.SEARCHAPI]
    assert cfg.settings.budget.max_searches_per_run == 120
    assert cfg.settings.nights == Nights(7, 14)
    assert cfg.secrets.serpapi_key is None and cfg.secrets.searchapi_key is None


def test_origins_default_to_none_and_are_read_per_destination(config_dir: Path):
    dst = config_dir / "destinations.yaml"
    dst.write_text("destinations:\n"
                   "  - { code: AAA, name: A, cabin: any }\n"
                   "  - { code: BBB, name: B, cabin: business, origins: [HAM, FRA, CPH] }\n")
    hol = config_dir / "holidays.yaml"
    hol.write_text("holidays:\n"
                   "  - { id: s1, name: S1, start: 2027-01-01, end: 2027-01-10, targets: [AAA, BBB] }\n")
    cfg = load_config(config_dir, env={})
    assert cfg.destination("AAA").origins is None
    assert cfg.destination("BBB").origins == ("HAM", "FRA", "CPH")


def test_secrets_from_env(config_dir: Path):
    env = {"SERPAPI_KEY": "k", "SEARCHAPI_KEY": "s", "MAIL_TO": "a@x.de, b@x.de", "SMTP_PORT": "2525"}
    cfg = load_config(config_dir, env=env)
    assert cfg.secrets.serpapi_key == "k"
    assert cfg.secrets.searchapi_key == "s"
    assert cfg.secrets.mail_to == ["a@x.de", "b@x.de"]
    assert cfg.secrets.smtp_port == 2525


def test_unknown_target_is_rejected(config_dir: Path):
    hol = config_dir / "holidays.yaml"
    hol.write_text(hol.read_text().replace("targets: [TFS, LPA, FUE]", "targets: [XXX]", 1))
    with pytest.raises(ConfigError, match="XXX"):
        load_config(config_dir, env={})


def test_duplicate_slot_id_is_rejected(config_dir: Path):
    hol = config_dir / "holidays.yaml"
    hol.write_text(hol.read_text().replace("id: herbst-2027", "id: herbst-2026", 1))
    with pytest.raises(ConfigError, match="herbst-2026"):
        load_config(config_dir, env={})


def test_bad_yaml_value_names_file_and_key(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("max_stops: 2", "max_stops: many"))
    with pytest.raises(ConfigError, match="settings.yaml.*max_stops"):
        load_config(config_dir, env={})


def test_price_is_total_is_per_provider(config_dir: Path):
    cfg = load_config(config_dir, env={})
    assert cfg.settings.providers.price_is_total == {
        Provider.SERPAPI: True, Provider.SEARCHAPI: True, Provider.FAST_FLIGHTS: True}


def test_price_is_total_accepts_a_bare_bool(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"  price_is_total:.*\n", "  price_is_total: false\n", st.read_text()))
    cfg = load_config(config_dir, env={})
    assert cfg.settings.providers.price_is_total == {
        Provider.SERPAPI: False, Provider.SEARCHAPI: False, Provider.FAST_FLIGHTS: False}


def test_price_is_total_defaults_to_total_for_listed_providers():
    ps = ProviderSettings(order=[ProviderEntry(name=Provider.SERPAPI),
                                 ProviderEntry(name=Provider.SEARCHAPI, monthly_budget=100),
                                 ProviderEntry(name=Provider.FAST_FLIGHTS, pause_seconds=5)],
                          price_is_total={Provider.SEARCHAPI: False})
    assert ps.price_is_total == {Provider.SERPAPI: True, Provider.SEARCHAPI: False, Provider.FAST_FLIGHTS: True}
    assert ps.entry(Provider.FAST_FLIGHTS).pause_seconds == 5
    assert [e.name for e in ps.budgeted()] == [Provider.SEARCHAPI]


def test_missing_price_is_total_key_defaults_to_true(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("searchapi: true, ", ""))
    cfg = load_config(config_dir, env={})
    assert cfg.settings.providers.price_is_total[Provider.SEARCHAPI] is True


def test_duplicate_provider_in_order_is_rejected(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("{ name: searchapi, monthly_budget: 100 }",
                                         "{ name: serpapi, monthly_budget: 100 }"))
    with pytest.raises(ConfigError, match="settings.yaml.*providers.order.*serpapi"):
        load_config(config_dir, env={})


def test_empty_provider_order_is_rejected(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"  order:\n(?:    - .*\n)+", "  order: []\n", st.read_text()))
    with pytest.raises(ConfigError, match="settings.yaml.*providers.order"):
        load_config(config_dir, env={})
