from pathlib import Path

from typer.testing import CliRunner

from vacation_planner.cli import app

runner = CliRunner()


def test_plan_lists_the_hotel_checks_without_calling_anything(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    r = runner.invoke(app, ["--config-dir", str(config_dir), "--db", str(tmp_path / "db.sqlite"),
                            "--today", "2026-11-16", "plan"])
    assert r.exit_code == 0, r.output
    assert "hotel   ikos-pfingsten-2027      booking  rates   2027-05-07..2027-05-14 2A (8)" in r.output
    assert "hotel   waldorf-herbst-2027      hilton   award   2027-10-10..2027-10-15 2A" in r.output
    assert "10 hotel checks (3 via SerpApi)" in r.output


def test_fake_run_skips_hotels(config_dir: Path, tmp_path: Path):
    r = runner.invoke(app, ["--config-dir", str(config_dir), "--db", str(tmp_path / "db.sqlite"),
                            "--today", "2026-11-16", "run", "--fake"])
    assert r.exit_code == 0, r.output
    assert "hotels: skipped (fake run)" in r.output
