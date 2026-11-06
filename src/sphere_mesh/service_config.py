"""Load and validate the optional daemon service configuration."""

from pathlib import Path
import tomllib

from .transport import adapter_selector


def load_service_config(path: Path) -> dict:
    """Read a flat TOML configuration, rejecting unsupported or invalid values."""
    try:
        with path.expanduser().open("rb") as source:
            config = tomllib.load(source)
    except (OSError, RuntimeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read config {path}: {exc}") from exc

    allowed = {
        "mode", "adapter", "verbose",
        "experimental_enrollment", "enroll_seconds",
    }
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"unknown config key(s): {', '.join(sorted(unknown))}")
    if "mode" in config and config["mode"] not in ("proxy", "mesh"):
        raise ValueError("config mode must be 'proxy' or 'mesh'")
    if "adapter" in config:
        if not isinstance(config["adapter"], str):
            raise ValueError("config adapter must be a MAC address or hciN")
        try:
            config["adapter"] = adapter_selector(config["adapter"])
        except ValueError as exc:
            raise ValueError(f"config {exc}") from exc
    for key in ("verbose", "experimental_enrollment"):
        if key in config and type(config[key]) is not bool:
            raise ValueError(f"config {key} must be a boolean")
    if "enroll_seconds" in config and (
        type(config["enroll_seconds"]) is not int
        or not 0 <= config["enroll_seconds"] <= 3600
    ):
        raise ValueError("config enroll_seconds must be an integer in 0..3600")
    return config
