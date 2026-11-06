"""Entry point for the Spherosphere Qt desktop application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from ..paths import socket_path as default_socket_path


SOURCE_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]
ICON_NAME = "spherosphere.svg"


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
    parser = argparse.ArgumentParser(description="Spherosphere: control your local Sphere C lights")
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
            "spherosphere requires the optional GUI dependencies; "
            "install spherosphere[gui]",
            file=sys.stderr,
        )
        print(f"details: {exc}", file=sys.stderr)
        return 2

    QQuickStyle.setStyle("Material")
    application = QGuiApplication([sys.argv[0], *qt_args])
    application.setApplicationName("Spherosphere")
    application.setApplicationDisplayName("Spherosphere")
    application.setOrganizationName("Spherosphere")
    application.setDesktopFileName("spherosphere")
    icon_path = application_icon_path()
    themed_icon = (
        QIcon(str(icon_path))
        if icon_path is not None
        else QIcon.fromTheme("spherosphere")
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
