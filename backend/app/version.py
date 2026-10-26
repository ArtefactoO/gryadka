from __future__ import annotations

import os
from pathlib import Path


_VERSION_CANDIDATES = (
    Path(os.getenv("APP_VERSION_FILE", "")),
    Path("/app/VERSION"),
    Path(__file__).resolve().parents[2] / "VERSION",
    Path.cwd() / "VERSION",
)

_CHANGELOG_CANDIDATES = (
    Path(os.getenv("APP_CHANGELOG_FILE", "")),
    Path("/app/CHANGELOG.md"),
    Path(__file__).resolve().parents[2] / "CHANGELOG.md",
    Path.cwd() / "CHANGELOG.md",
)


def get_app_version() -> str:
    """Return application version from the checked-in VERSION file."""
    for path in _VERSION_CANDIDATES:
        if not str(path):
            continue
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            continue
    return "unknown"


def get_app_changelog() -> str:
    """Return application changelog from the checked-in changelog file."""
    for path in _CHANGELOG_CANDIDATES:
        if not str(path):
            continue
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""
