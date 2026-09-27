"""Der Veroeffentlichungsbefehl (werkzeuge/release.py): was er ins Protokoll schreibt."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release", WURZEL / "werkzeuge" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

PROTOKOLL = """# Änderungen

## Unveröffentlicht

- Neu: etwas.

## 0.1.0 – 2026-09-27

- Alt.
"""


def test_the_open_section_is_what_the_new_version_describes():
    assert release.offene_aenderungen(PROTOKOLL) == "- Neu: etwas."


def test_publishing_turns_the_open_section_into_the_version_and_opens_a_new_one():
    neu = release.changelog_schreiben(PROTOKOLL, "0.2.0", date(2026, 10, 1))
    assert "## Unveröffentlicht\n\n## 0.2.0 – 2026-10-01\n\n- Neu: etwas." in neu
    assert release.offene_aenderungen(neu) == ""
    assert "## 0.1.0 – 2026-09-27" in neu


def test_a_version_number_has_three_parts():
    assert release._teile("0.10.2") > release._teile("0.9.9")
    with pytest.raises(SystemExit):
        release._teile("0.2")


def test_the_version_lives_in_one_place():
    """pyproject.toml nennt keine eigene Nummer, es liest die aus dem Paket."""
    import finctl

    assert release.aktuelle_version() == finctl.__version__
    assert 'dynamic = ["version"]' in (WURZEL / "pyproject.toml").read_text(encoding="utf-8")


def test_the_changelog_has_an_open_section():
    text = (WURZEL / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.OFFEN in text


def test_the_release_notes_are_the_versions_own_section():
    spec = importlib.util.spec_from_file_location(
        "notizen", WURZEL / "werkzeuge" / "paket" / "notizen.py")
    notizen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notizen)
    neu = release.changelog_schreiben(PROTOKOLL, "0.2.0", date(2026, 10, 1))
    assert notizen.abschnitt(neu, "0.2.0") == "- Neu: etwas."
    assert notizen.abschnitt(neu, "0.1.0") == "- Alt."
    with pytest.raises(SystemExit):
        notizen.abschnitt(neu, "9.9.9")
