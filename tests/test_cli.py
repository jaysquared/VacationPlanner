import logging
import re
from pathlib import Path

from typer.testing import CliRunner

from vacation_planner.cli import app, build_clients
from vacation_planner.config import load_config
from vacation_planner.models import Provider
from vacation_planner.storage import Storage

runner = CliRunner()


def common(config_dir: Path, tmp_path: Path):
    return ["--config-dir", str(config_dir), "--db", str(tmp_path / "p.sqlite"), "--today", "2026-09-21"]


def test_holidays_lists_windows(config_dir, tmp_path):
    r = runner.invoke(app, common(config_dir, tmp_path) + ["holidays"])
    assert r.exit_code == 0, r.output
    assert "Herbstferien 2026" in r.output and "2026-10-17" in r.output and "BKK" in r.output


def test_plan_is_dry(config_dir, tmp_path):
    r = runner.invoke(app, common(config_dir, tmp_path) + ["plan"])
    assert r.exit_code == 0, r.output
    assert "serpapi" in r.output and "HAM" in r.output and "BKK" in r.output
    assert not (tmp_path / "p.sqlite").exists() or Storage(tmp_path / "p.sqlite").searches_in_run(1) == []


def only_key_providers(config_dir: Path) -> None:
    """Drop the keyless fast_flights provider so a missing key leaves nothing usable."""
    st = config_dir / "settings.yaml"
    st.write_text(re.sub(r"    - \{ name: fast_flights.*\n", "", st.read_text()))


def test_scan_requires_a_key_unless_fake(config_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("SEARCHAPI_KEY", raising=False)
    only_key_providers(config_dir)
    r = runner.invoke(app, common(config_dir, tmp_path) + ["scan"])
    assert r.exit_code != 0
    assert "SERPAPI_KEY" in r.output and "SEARCHAPI_KEY" in r.output


def test_build_clients_skips_a_provider_without_its_key(config_dir, caplog):
    cfg = load_config(config_dir, env={"SEARCHAPI_KEY": "k"})
    with caplog.at_level(logging.WARNING):
        clients = build_clients(cfg, fake=False)
    assert list(clients) == [Provider.SEARCHAPI, Provider.FAST_FLIGHTS]
    assert clients[Provider.SEARCHAPI].provider is Provider.SEARCHAPI
    assert "serpapi" in caplog.text and "SERPAPI_KEY" in caplog.text


def test_build_clients_fake_covers_every_listed_provider(config_dir):
    cfg = load_config(config_dir, env={})
    clients = build_clients(cfg, fake=True)
    assert list(clients) == [Provider.SERPAPI, Provider.SEARCHAPI, Provider.FAST_FLIGHTS]
    assert [c.provider for c in clients.values()] == list(clients)


def test_run_end_to_end_with_fake(config_dir, tmp_path):
    out = tmp_path / "site"
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("output_dir: docs/site", f"output_dir: {out}"))
    r = runner.invoke(app, common(config_dir, tmp_path) + ["run", "--fake", "--limit", "5"])
    assert r.exit_code == 0, r.output
    assert "searches: 5 ok" in r.output
    assert re.search(r"deals: [1-9]\d* \(", r.output), r.output   # at least one deal
    assert "under_max" in r.output                                   # fake prices are under the per-person max
    assert (out / "index.html").exists()
    db = Storage(tmp_path / "p.sqlite")
    assert len(db.searches_in_run(1)) == 5
    # second run: same prices -> no NEW_LOW, but UNDER_MAX deals still exist -> deals detected
    r2 = runner.invoke(app, common(config_dir, tmp_path) + ["run", "--fake", "--limit", "5"])
    assert r2.exit_code == 0, r2.output
    assert re.search(r"deals: [1-9]\d* \(", r2.output), r2.output   # at least one deal
    assert "under_max" in r2.output                                   # fake prices are under the per-person max


def test_fake_refuses_default_db(config_dir):
    r = runner.invoke(app, ["--config-dir", str(config_dir), "--today", "2026-09-21", "scan", "--fake"])
    assert r.exit_code != 0 and "--db" in r.output
