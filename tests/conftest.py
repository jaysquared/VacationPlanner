import smtplib
from pathlib import Path

import pytest

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"

#: Anything that could make a test talk to the outside world or mail a real inbox.
SECRET_ENV = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO",
              "SERPAPI_KEY", "SEARCHAPI_KEY")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No real credentials in a test process.

    Importing `fast_flights` calls `dotenv.load_dotenv()`, which pushes the repo's
    own `.env` into `os.environ` at collection time — so a test that lets the CLI
    read the environment would otherwise pick up live SMTP settings and API keys.
    """
    for name in SECRET_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_real_smtp(monkeypatch):
    """The tests that need SMTP inject a fake through `smtp_factory`; nothing else may connect."""
    def refuse(*args, **kwargs):
        raise AssertionError("real SMTP in tests")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Copy of the repo config so tests can edit files freely."""
    dst = tmp_path / "config"
    dst.mkdir()
    for f in REPO_CONFIG.glob("*.yaml"):
        (dst / f.name).write_text(f.read_text())
    return dst
