"""Entry point for the optional Sphere C Qt desktop application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


SOURCE_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]
ICON_NAME = "sphere-mesh-gui.svg"


def default_socket_path() -> Path:
    """Choose a stable development or installed service socket."""
    override = os.environ.get("SPHERE_MESH_SOCKET")
    if override:
        return Path(override).expanduser()
    checkout_roots = (Path.cwd(), SOURCE_CHECKOUT_ROOT)
    for root in checkout_roots:
        development = root / "state" / "sphere.sock"
        if development.exists() or (
            root / "state" / "sphere_state.json"
        ).exists():
            return development
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "sphere-mesh" / "control.sock"
    return Path.cwd() / "state" / "sphere.sock"


def application_icon_path() -> Path | None:
    """Find the project icon when the desktop theme has no entry."""
    candidates = (
        SOURCE_CHECKOUT_ROOT / "packaging" / "desktop" / ICON_NAME,
        Path(sys.prefix)
        / "share"
        / "icons"
        / "hicolor"
        / "scalable"
        / "apps"
        / ICON_NAME,
    )
    return next((path for path in candidates if path.is_file()), None)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Control an Olight Sphere C")
    parser.add_argument(
        "--socket",
        type=Path,
        default=default_socket_path(),
        help="Sphere service socket (default: detected service or %(default)s)",
    )
    parser.add_argument(
        "--test-quit-after-load",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, qt_args = parse_args(argv)
    try:
        from PySide6.QtCore import QCoreApplication, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication, QIcon
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle

        from .backend import SphereBackend
    except (ImportError, RuntimeError) as exc:
        print(
            "sphere-mesh-gui requires the optional GUI dependencies; "
            "install sphere-mesh-lab[gui]",
            file=sys.stderr,
        )
        print(f"details: {exc}", file=sys.stderr)
        return 2

    QQuickStyle.setStyle("Material")
    application = QGuiApplication([sys.argv[0], *qt_args])
    application.setApplicationName("Sphere Mesh")
    application.setApplicationDisplayName("Sphere C")
    application.setOrganizationName("Sphere Mesh")
    application.setDesktopFileName("sphere-mesh-gui")
    icon_path = application_icon_path()
    themed_icon = (
        QIcon(str(icon_path))
        if icon_path is not None
        else QIcon.fromTheme("sphere-mesh-gui")
    )
    if not themed_icon.isNull():
        application.setWindowIcon(themed_icon)

    backend = SphereBackend(args.socket)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("sphere", backend)
    main_qml = Path(__file__).with_name("qml") / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    if not engine.rootObjects():
        backend.close()
        return 1
    if args.test_quit_after_load:
        QTimer.singleShot(0, application.quit)

    exit_code = application.exec()
    del engine
    QCoreApplication.processEvents()
    backend.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
