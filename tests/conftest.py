from pathlib import Path

import pytest

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Copy of the repo config so tests can edit files freely."""
    dst = tmp_path / "config"
    dst.mkdir()
    for f in REPO_CONFIG.glob("*.yaml"):
        (dst / f.name).write_text(f.read_text())
    return dst
