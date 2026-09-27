"""Die Lizenztexte, die mit den Paketen weitergegeben werden."""

from __future__ import annotations

import importlib.util
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "drittlizenzen", WURZEL / "werkzeuge" / "paket" / "drittlizenzen.py")
drittlizenzen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drittlizenzen)


def test_every_runtime_dependency_brings_its_licence_text():
    """Gesammelt aus dem, was installiert ist: eine neue Abhaengigkeit ist dabei,
    ohne dass jemand sie eintraegt."""
    namen = drittlizenzen._abhaengigkeiten()
    assert {"fastapi", "uvicorn", "pdfplumber", "pyyaml"} <= set(namen)
    text = drittlizenzen.sammeln()
    assert "Python " in text and "PSF" in text
    for name in ("fastapi", "uvicorn", "pdfplumber"):
        assert f"\n{name} " in text.lower() or f"\n{name} " in text, name
    # Nicht nur der Name der Lizenz, sondern ihr Text.
    assert "Permission is hereby granted" in text


def test_the_own_licence_forbids_commercial_use_and_changes():
    text = (WURZEL / "LICENSE.md").read_text(encoding="utf-8")
    assert "PolyForm Strict License 1.0.0" in text
    assert "other than distributing the software or making changes" in text
    assert 'license = "LicenseRef-PolyForm-Strict-1.0.0"' in (
        WURZEL / "pyproject.toml").read_text(encoding="utf-8")
