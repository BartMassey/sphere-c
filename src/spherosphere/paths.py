"""Shared XDG locations for Spherosphere programs and services.

Resolving a path never creates files or a new Mesh identity. Explicit
command-line paths take precedence in callers; environment overrides
take precedence over the common daemon configuration here.
"""

import os
from pathlib import Path
import tomllib


def _xdg(name: str, fallback: Path) -> Path:
    """Ignore empty or relative XDG values, as required by the spec."""
    value = os.environ.get(name, "")
    return Path(value) if value and Path(value).is_absolute() else fallback


def config_root() -> Path:
    """Return the user's XDG configuration root."""
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


def config_dir() -> Path:
    return config_root() / "spherosphere"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", Path.home() / ".local/state") / "spherosphere"


def runtime_dir() -> Path:
    """Use private persistent state when no login runtime directory exists."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and Path(runtime).is_absolute():
        return Path(runtime) / "spherosphere"
    return state_dir() / "runtime"


def _absolute(value: str, setting: str) -> Path:
    path = Path(value).expanduser()
    if not value or not path.is_absolute():
        raise ValueError(f"{setting} must be an absolute path")
    return path


def config_path() -> Path:
    value = os.environ.get("SPHEROSPHERE_CONFIG")
    return _absolute(value, "SPHEROSPHERE_CONFIG") if value is not None else config_dir() / "daemon.toml"


def _configured_path(name: str, fallback: Path, config: Path | None) -> Path:
    override = os.environ.get("SPHEROSPHERE_" + name.upper())
    if override is not None:
        return _absolute(override, "SPHEROSPHERE_" + name.upper())
    path = config if config is not None else config_path()
    if not path.exists():
        return fallback
    try:
        with path.open("rb") as stream:
            settings = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read config {path}: {exc}") from exc
    if name not in settings:
        return fallback
    if not isinstance(settings[name], str):
        raise ValueError(f"config {name} must be an absolute path")
    return _absolute(settings[name], "config " + name)


def state_path(config: Path | None = None) -> Path:
    """Locate the single persistent Mesh identity and device database."""
    return _configured_path("state", state_dir() / "state.json", config)


def socket_path(config: Path | None = None) -> Path:
    """Locate the same control endpoint for daemon, CLI and desktop app."""
    return _configured_path("socket", runtime_dir() / "control.sock", config)
