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
    assert "10 hotel checks (up to 6 SerpApi credits)" in r.output


def test_fake_run_skips_hotels(config_dir: Path, tmp_path: Path):
    r = runner.invoke(app, ["--config-dir", str(config_dir), "--db", str(tmp_path / "db.sqlite"),
                            "--today", "2026-11-16", "run", "--fake"])
    assert r.exit_code == 0, r.output
    assert "hotels: skipped (fake run)" in r.output


def test_a_crashing_hotel_check_still_writes_the_report(config_dir: Path, tmp_path: Path, monkeypatch):
    import vacation_planner.cli as cli

    out = tmp_path / "site"
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("output_dir: docs/site", f"output_dir: {out}"))
    real_build = cli.build_clients
    monkeypatch.setattr(cli, "build_clients", lambda config, fake: real_build(config, True))   # no network flights

    def boom(*args, **kwargs):
        raise RuntimeError("hotel bug")

    monkeypatch.setattr(cli, "check_hotels", boom)
    r = runner.invoke(app, ["--config-dir", str(config_dir), "--db", str(tmp_path / "db.sqlite"),
                            "--today", "2026-11-16", "run", "--limit", "2"])
    assert r.exit_code == 0, r.output
    assert "hotels: failed (RuntimeError: hotel bug)" in r.output
    assert (out / "index.html").exists()
