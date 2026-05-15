
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List
from xml.etree import ElementTree as ET


@dataclass
class LineEntry:
    name: str = ""
    application_path: str = ""
    application_arguments: str = ""

    @property
    def working_directory(self) -> str:
        # the directory containing qbase.exe is also its working dir; qbase resolves
        # Settings.xml and other relative assets out of there.
        path = self.application_path.strip().strip('"')
        if not path:
            return ""
        return str(Path(path).parent)

    @property
    def settings_xml_path(self) -> str:
        wd = self.working_directory
        return str(Path(wd) / "Settings.xml") if wd else ""


@dataclass
class LauncherConfig:
    window_title: str = "DCI PRESS SELECTION"
    lines: List[LineEntry] = field(default_factory=list)
    config_file: Path = field(default_factory=lambda: Path("SimpleAppLauncher.exe.config"))


CONFIG_NAMESPACES = {
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xsd": "http://www.w3.org/2001/XMLSchema",
}


def _find_settings_root(root: ET.Element) -> ET.Element | None:
    # the .net config xmlns shifts between framework versions, so match by tag
    # suffix instead of pinning a specific namespace and praying.
    for parent in root.iter():
        for child in list(parent):
            if child.tag.endswith("SimpleAppLauncher.Properties.Settings"):
                return child
    return None


def load_config(path: str | os.PathLike) -> LauncherConfig:
    cfg_path = Path(path)
    config = LauncherConfig(config_file=cfg_path)

    if not cfg_path.exists():
        return config

    tree = ET.parse(cfg_path)
    root = tree.getroot()
    settings = _find_settings_root(root)
    if settings is None:
        return config

    for setting in settings.findall("setting"):
        name = setting.attrib.get("name", "")
        value_el = setting.find("value")
        if value_el is None:
            continue

        if name == "WindowTitle":
            config.window_title = (value_el.text or "").strip()
        elif name == "AppConfigurations":
            array = value_el.find("ArrayOfAppConfiguration")
            if array is None:
                continue
            for entry in array.findall("AppConfiguration"):
                config.lines.append(LineEntry(
                    name=_text(entry, "Name"),
                    application_path=_text(entry, "ApplicationPath"),
                    application_arguments=_text(entry, "ApplicationArguments"),
                ))

    return config


def _text(parent: ET.Element, tag: str) -> str:
    el = parent.find(tag)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def save_config(config: LauncherConfig) -> None:
    cfg_path = config.config_file
    # always snapshot the existing config before rewriting so a partial write
    # is something we can actually recover from.
    _backup(cfg_path)

    if cfg_path.exists():
        tree = ET.parse(cfg_path)
        root = tree.getroot()
    else:
        root = _new_skeleton()
        tree = ET.ElementTree(root)

    settings = _find_settings_root(root)
    if settings is None:
        app_settings = ET.SubElement(root, "applicationSettings")
        settings = ET.SubElement(app_settings, "SimpleAppLauncher.Properties.Settings")

    for child in list(settings):
        settings.remove(child)

    title = ET.SubElement(settings, "setting", {"name": "WindowTitle", "serializeAs": "String"})
    ET.SubElement(title, "value").text = config.window_title

    apps = ET.SubElement(settings, "setting", {"name": "AppConfigurations", "serializeAs": "Xml"})
    apps_value = ET.SubElement(apps, "value")
    array = ET.SubElement(apps_value, "ArrayOfAppConfiguration", {
        "xmlns:xsi": CONFIG_NAMESPACES["xsi"],
        "xmlns:xsd": CONFIG_NAMESPACES["xsd"],
    })
    for line in config.lines:
        entry = ET.SubElement(array, "AppConfiguration")
        ET.SubElement(entry, "Name").text = line.name
        ET.SubElement(entry, "ApplicationPath").text = line.application_path
        ET.SubElement(entry, "ApplicationArguments").text = line.application_arguments

    _indent(root)
    tree.write(cfg_path, encoding="utf-8", xml_declaration=True)


def _new_skeleton() -> ET.Element:
    root = ET.Element("configuration")
    sections = ET.SubElement(root, "configSections")
    group = ET.SubElement(sections, "sectionGroup", {
        "name": "applicationSettings",
        "type": "System.Configuration.ApplicationSettingsGroup, System, "
                "Version=2.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089",
    })
    ET.SubElement(group, "section", {
        "name": "SimpleAppLauncher.Properties.Settings",
        "type": "System.Configuration.ClientSettingsSection, System, "
                "Version=2.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089",
        "requirePermission": "false",
    })
    startup = ET.SubElement(root, "startup")
    ET.SubElement(startup, "supportedRuntime", {"version": "v4.0", "sku": ".NETFramework,Version=v4.8"})
    return root


def _backup(path: Path) -> None:
    if not path.exists():
        return
    backup_dir = path.parent / "_launcher_backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, backup_dir / f"{path.name}.{stamp}.bak")


def _indent(elem: ET.Element, level: int = 0) -> None:
    pad = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "  "
        for child in elem:
            _indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = pad + "  "
        if not child.tail or not child.tail.strip():
            child.tail = pad
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = pad
