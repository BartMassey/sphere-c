#!/usr/bin/env python3
"""Preserve private Bluetooth diagnostic evidence for a later report."""

from pathlib import Path
import hashlib
import json
import os
import shutil


ROOT = Path(__file__).resolve().parent.parent
DESTINATION = ROOT / "old/bluez-issue-evidence/2026-11-01"


def main() -> None:
    os.umask(0o077)
    DESTINATION.mkdir(parents=True, exist_ok=True, mode=0o700)
    sources = []
    for path in (ROOT / "state/captures").iterdir():
        if (path.is_file() and path.stat().st_size
                and any(word in path.name for word in
                        ("bluez", "forwarding", "coexist", "conservative"))):
            sources.append((path, DESTINATION / "captures" / path.name))
    debug = Path("/tmp/sphere-bluez-debug")
    if debug.exists():
        sources.extend((path, DESTINATION / "debug" / path.relative_to(debug))
                       for path in debug.rglob("*") if path.is_file())
    sources.append((Path("/usr/libexec/bluetooth/bluetooth-meshd"),
                    DESTINATION / "bluetooth-meshd"))
    for relative in (
        "src/spherosphere/bluez_mesh.py", "src/spherosphere/enrollment.py",
        "src/spherosphere/daemon.py", "src/spherosphere/transport.py",
        "src/spherosphere/provision.py", "scripts/check_bluez_mesh.py",
        "scripts/check_mesh_members.py", "scripts/archive_bluez_issue.py",
        "bin/spherosphere-bt", "pyproject.toml",
    ):
        sources.append((ROOT / relative, DESTINATION / "helpers" / relative))
    manifest = {"archived_date": "2026-11-05", "private": True,
                "share_only_after_redaction": True, "files": []}
    for source, destination in sources:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not destination.exists():
            shutil.copyfile(source, destination)
        with destination.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest["files"].append({
            "source": str(source), "archive": str(destination.relative_to(DESTINATION)),
            "size": destination.stat().st_size, "sha256": digest,
        })
    for path in (*DESTINATION.glob("*.txt"), *DESTINATION.glob("controller-*.json")):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest["files"].append({"archive": path.name, "size": path.stat().st_size,
                                  "sha256": digest})
    (DESTINATION / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    owner = int(os.environ.get("SUDO_UID", os.getuid()))
    group = int(os.environ.get("SUDO_GID", os.getgid()))
    for path in [DESTINATION, *DESTINATION.rglob("*")]:
        path.chmod(0o700 if path.is_dir() else 0o600)
        if os.geteuid() == 0:
            os.chown(path, owner, group)
    print(f"Archived {len(manifest['files'])} evidence files privately")
    print(DESTINATION / "manifest.json")


if __name__ == "__main__":
    main()
