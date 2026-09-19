import re
from datetime import date
from pathlib import Path

import pytest

from vacation_planner.config import ConfigError, ProviderSettings, load_config
from vacation_planner.models import Cabin, Nights, Provider


def test_loads_repo_config(config_dir: Path):
    cfg = load_config(config_dir, env={})
    assert cfg.travellers.adults == 2
    assert cfg.travellers.children[0].birthdate == date(2019, 4, 21)
    assert cfg.destination("BKK").cabin is Cabin.BUSINESS
    assert cfg.destination("PMI").cabin is Cabin.ANY
    herbst = next(s for s in cfg.slots if s.id == "herbst-2026")
    assert herbst.start == date(2026, 10, 19) and herbst.targets == ("BKK",)
    assert cfg.settings.providers.primary is Provider.SERPAPI
    assert cfg.settings.budget.serpapi_per_month == 100
    assert cfg.settings.nights == Nights(7, 14)
    assert cfg.secrets.serpapi_key is None


def test_secrets_from_env(config_dir: Path):
    env = {"SERPAPI_KEY": "k", "MAIL_TO": "a@x.de, b@x.de", "SMTP_PORT": "2525"}
    cfg = load_config(config_dir, env=env)
    assert cfg.secrets.serpapi_key == "k"
    assert cfg.secrets.mail_to == ["a@x.de", "b@x.de"]
    assert cfg.secrets.smtp_port == 2525


def test_unknown_target_is_rejected(config_dir: Path):
    hol = config_dir / "holidays.yaml"
    hol.write_text(hol.read_text().replace("targets: [BKK]", "targets: [XXX]", 1))
    with pytest.raises(ConfigError, match="XXX"):
        load_config(config_dir, env={})


def test_duplicate_slot_id_is_rejected(config_dir: Path):
    hol = config_dir / "holidays.yaml"
    hol.write_text(hol.read_text().replace("id: herbst-2027", "id: herbst-2026", 1))
    with pytest.raises(ConfigError, match="herbst-2026"):
        load_config(config_dir, env={})


def test_bad_yaml_value_names_file_and_key(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("max_stops: 1", "max_stops: many"))
    with pytest.raises(ConfigError, match="settings.yaml.*max_stops"):
        load_config(config_dir, env={})


def test_price_is_total_is_per_provider(config_dir: Path):
    cfg = load_config(config_dir, env={})
    assert cfg.settings.providers.price_is_total == {Provider.SERPAPI: True, Provider.FAST_FLIGHTS: True}


def test_price_is_total_accepts_a_bare_bool(config_dir: Path):
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"  price_is_total:\n(?:    .*\n)+", "  price_is_total: false\n", st.read_text()))
    cfg = load_config(config_dir, env={})
    assert cfg.settings.providers.price_is_total == {Provider.SERPAPI: False, Provider.FAST_FLIGHTS: False}


def test_price_is_total_defaults_to_total_for_both_providers():
    ps = ProviderSettings(primary=Provider.SERPAPI, backup=Provider.FAST_FLIGHTS)
    assert ps.price_is_total == {Provider.SERPAPI: True, Provider.FAST_FLIGHTS: True}
