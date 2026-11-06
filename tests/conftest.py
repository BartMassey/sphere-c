"""Keep tests independent of the user's installed network and settings."""

import pytest


@pytest.fixture(autouse=True)
def isolated_xdg_paths(tmp_path, monkeypatch):
    for variable, directory in (
        ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"),
        ("XDG_RUNTIME_DIR", "runtime"),
    ):
        monkeypatch.setenv(variable, str(tmp_path / directory))
    for name in ("SPHEROSPHERE_CONFIG", "SPHEROSPHERE_STATE", "SPHEROSPHERE_SOCKET"):
        monkeypatch.delenv(name, raising=False)
