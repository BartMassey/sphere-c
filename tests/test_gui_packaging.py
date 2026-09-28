import ast
import configparser
from pathlib import Path
import tomllib
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
GUI_ROOT = ROOT / "src" / "sphere_mesh" / "gui"
DESKTOP_ROOT = ROOT / "packaging" / "desktop"


def _metadata():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_gui_optional_dependency_and_entry_point():
    project = _metadata()["project"]

    assert any(dependency.startswith("PySide6") for dependency in
               project["optional-dependencies"]["gui"])
    assert project["scripts"]["sphere-mesh-gui"] == (
        "sphere_mesh.gui.app:main"
    )

    app = ast.parse((GUI_ROOT / "app.py").read_text())
    assert any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == "main" for node in app.body)


def test_gui_resources_are_included_as_package_data():
    data = _metadata()["tool"]["setuptools"]["package-data"]
    patterns = data["sphere_mesh.gui"]
    packaged = {
        path.relative_to(GUI_ROOT)
        for pattern in patterns
        for path in GUI_ROOT.glob(pattern)
        if path.is_file()
    }
    qml_files = set((GUI_ROOT / "qml").rglob("*.qml"))
    assets = {path for path in (GUI_ROOT / "assets").rglob("*")
              if path.is_file()}

    assert qml_files
    assert {
        path.relative_to(GUI_ROOT) for path in qml_files | assets
    } <= packaged


def test_desktop_launcher_and_icon_are_installed():
    data_files = _metadata()["tool"]["setuptools"]["data-files"]
    assert "packaging/desktop/sphere-mesh-gui.desktop" in (
        data_files["share/applications"]
    )
    assert "packaging/desktop/sphere-mesh-gui.svg" in (
        data_files["share/icons/hicolor/scalable/apps"]
    )
    assert "packaging/desktop/ASSETS.md" in (
        data_files["share/doc/sphere-mesh-lab"]
    )

    launcher = configparser.ConfigParser(interpolation=None)
    launcher.read(DESKTOP_ROOT / "sphere-mesh-gui.desktop")
    entry = launcher["Desktop Entry"]
    assert entry["Type"] == "Application"
    assert entry["Exec"] == "sphere-mesh-gui"
    assert entry["Icon"] == "sphere-mesh-gui"
    assert entry["Terminal"] == "false"

    icon = ET.parse(DESKTOP_ROOT / "sphere-mesh-gui.svg").getroot()
    assert icon.tag == "{http://www.w3.org/2000/svg}svg"
