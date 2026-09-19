from pathlib import Path

from typer.testing import CliRunner

from vacation_planner.cli import app
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


def test_scan_requires_key_unless_fake(config_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    r = runner.invoke(app, common(config_dir, tmp_path) + ["scan"])
    assert r.exit_code != 0 and "SERPAPI_KEY" in r.output


def test_run_end_to_end_with_fake(config_dir, tmp_path):
    out = tmp_path / "site"
    st = config_dir / "settings.yaml"
    st.write_text(st.read_text().replace("output_dir: docs/site", f"output_dir: {out}"))
    r = runner.invoke(app, common(config_dir, tmp_path) + ["run", "--fake", "--limit", "5"])
    assert r.exit_code == 0, r.output
    assert "searches: 5 ok" in r.output
    assert (out / "index.html").exists()
    db = Storage(tmp_path / "p.sqlite")
    assert len(db.searches_in_run(1)) == 5
    # second run: same prices -> no NEW_LOW, but UNDER_MAX deals still exist -> deals detected
    r2 = runner.invoke(app, common(config_dir, tmp_path) + ["run", "--fake", "--limit", "5"])
    assert r2.exit_code == 0 and "deals:" in r2.output
