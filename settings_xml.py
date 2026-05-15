"""Targeted edits to a DCI Settings.xml file. Preserves all unrelated content."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


GENERAL_FIELDS = {
    "system_name": "systemname",
    "image_path": "imagepath",
    "data_source": "sysdatasrc",
    "data_password": "datapwd",
    "sql_server": "SqlServerName",
}


@dataclass
class LineSettings:
    system_name: str = ""
    image_path: str = ""
    data_source: str = ""
    data_password: str = ""
    sql_server: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {k: getattr(self, k) for k in GENERAL_FIELDS}


def read_settings(path: str | Path) -> Optional[LineSettings]:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        # called from inside tk callbacks; raising here just gets eaten by the
        # event loop, so return None and let the caller decide what to do.
        return None

    text = p.read_text(encoding="utf-8", errors="replace")
    settings = LineSettings()
    for attr, tag in GENERAL_FIELDS.items():
        value = _extract_value(text, tag)
        if value is not None:
            setattr(settings, attr, value)
    return settings


def write_settings(path: str | Path, updates: Dict[str, str]) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Settings file not found: {p}")

    _backup(p)
    text = p.read_text(encoding="utf-8", errors="replace")

    for attr, value in updates.items():
        tag = GENERAL_FIELDS.get(attr)
        if tag is None:
            continue
        text = _replace_value(text, tag, value)

    p.write_text(text, encoding="utf-8")


def _block_pattern(tag: str) -> re.Pattern[str]:
    # bounded to </tag> on purpose. unbounded .*? happily eats the rest of the file.
    return re.compile(
        rf"<{tag}\b[^>]*>.*?</{tag}>",
        re.DOTALL | re.IGNORECASE,
    )


_VALUE_RE = re.compile(r"<value>(.*?)</value>", re.DOTALL)
_EMPTY_VALUE_RE = re.compile(r"<value\s*/>")


def _extract_value(text: str, tag: str) -> Optional[str]:
    block = _block_pattern(tag).search(text)
    if not block:
        return None
    block_text = block.group(0)
    match = _VALUE_RE.search(block_text)
    if match:
        return match.group(1)
    if _EMPTY_VALUE_RE.search(block_text):
        return ""
    return None


def _replace_value(text: str, tag: str, new_value: str) -> str:
    block = _block_pattern(tag).search(text)
    if not block:
        return text

    block_text = block.group(0)
    new_value_tag = f"<value>{_escape(new_value)}</value>"
    if _VALUE_RE.search(block_text):
        replacement = _VALUE_RE.sub(new_value_tag, block_text, count=1)
    elif _EMPTY_VALUE_RE.search(block_text):
        replacement = _EMPTY_VALUE_RE.sub(new_value_tag, block_text, count=1)
    else:
        return text
    return text[: block.start()] + replacement + text[block.end():]


def _escape(value: str) -> str:
    # not html.escape, that one rewrites quotes too and the .NET reader on the
    # qbase side throws a fit when it sees them inside <value>.
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _backup(path: Path) -> None:
    # a corrupted Settings.xml takes the line out of production, so always keep a
    # timestamped copy around before we rewrite anything.
    backup_dir = path.parent / "_launcher_backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, backup_dir / f"{path.name}.{stamp}.bak")
