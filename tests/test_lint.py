"""Ruff laeuft als Test, nicht als Gewohnheit.

Ruff stand bisher als Entwicklungsabhaengigkeit in pyproject.toml und wurde
von nichts aufgerufen. Entsprechend sammelten sich 225 Funde an, darunter vier
Tests, die doppelt definiert waren und deshalb nie liefen. Ein Werkzeug, das
man aufrufen muss, um davon zu profitieren, wird irgendwann nicht mehr
aufgerufen -- also laeuft es hier mit.

Die Regelauswahl steht in pyproject.toml, mit Begruendung je Ausnahme.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent


def _ruff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "ruff", "check", "--no-cache", *args],
                          cwd=WURZEL, capture_output=True, text=True, check=False)


def test_ruff_finds_nothing():
    """Null Funde, nicht "wenige".

    Eine Liste geduldeter Funde waere nach dem dritten Eintrag nur noch eine
    Liste. Was geduldet wird, gehoert in die Konfiguration -- dort steht
    daneben, warum.
    """
    lauf = _ruff("--output-format", "concise", ".")
    assert lauf.returncode == 0, "ruff meldet:\n" + (lauf.stdout or lauf.stderr)


# Funktionen, deren Verzweigungstiefe ueber 10 liegt, mit dem Wert von heute.
#
# Diese Liste darf kuerzer werden und keinen Eintrag dazubekommen. Sie ist
# kein Schoenheitsmass: jeder Eintrag ist eine Stelle, an der sich beim Lesen
# nicht mehr im Kopf behalten laesst, welcher Fall gerade gilt -- und genau
# dort entstehen die Fehler, die kein Test findet, weil niemand den Fall
# bedacht hat.
#
# Warum die Zahlen mitstehen: faellt eine Funktion unter 10, faellt sie aus
# der Liste und der Test verlangt das auch. Wird sie schwerer, ohne die
# Schwelle zu reissen, sieht man es hier.
ZU_VERZWEIGT = {
    "finctl/abos.py::abrechnung": 11,
    "finctl/cli.py::init": 17,
    "finctl/forecast/abgleich.py::planned": 11,
    "finctl/forecast/engine.py::project": 16,
    "finctl/forecast/jahre.py::project": 13,
    "finctl/forecast/konten.py::_anzeigenamen": 11,
    "finctl/forecast/konten.py::account_forecast": 12,
    "finctl/forecast/konten.py::household_accounts": 18,
    "finctl/forecast/prognosebasis.py::_grund": 12,
    "finctl/forecast/treffer.py::konten": 13,
    "finctl/ingest/profiles/c24_giro.py::parse": 16,
    "finctl/ingest/profiles/dkb_giro.py::parse": 16,
    "finctl/ingest/profiles/sparda_giro.py::parse": 13,
    "finctl/rules/categorize.py::categorize": 15,
    "finctl/web/handbuch.py::als_html": 19,
    "finctl/web/routen/buchungen.py::transactions": 14,
    "finctl/web/routen/kategorien.py::api_category": 18,
    "finctl/web/routen/konten.py::api_bestand": 11,
    "finctl/web/routen/planung.py::api_szenario_zeile": 24,
    "finctl/web/routen/planung.py::planung": 11,
    "finctl/web/routen/ziele.py::api_ziel": 21,
    "finctl/web/routen/ziele.py::ziele": 12,
    "tests/test_design.py::_zwecktext_funde": 19,
}

_ZEILE = re.compile(r"^(?P<datei>[^:]+):\d+:\d+: C901 `(?P<name>[^`]+)` "
                    r"is too complex \((?P<wert>\d+) > \d+\)$")


def _gemessen() -> dict[str, int]:
    lauf = _ruff("--select", "C901", "--output-format", "concise", ".")
    gefunden = {}
    for zeile in lauf.stdout.splitlines():
        if treffer := _ZEILE.match(zeile.strip()):
            # Windows meldet `finctl\abos.py`; die Liste fuehrt `/`.
            datei = treffer["datei"].replace("\\", "/")
            schluessel = f"{datei}::{treffer['name']}"
            gefunden[schluessel] = max(gefunden.get(schluessel, 0), int(treffer["wert"]))
    return gefunden


def test_no_new_function_becomes_too_branched():
    """Die Ratsche: dazukommen darf nichts."""
    neu = sorted(set(_gemessen()) - set(ZU_VERZWEIGT))
    assert not neu, (
        "Diese Funktionen sind neu ueber der Schwelle. Entweder aufteilen, "
        "oder mit Begruendung in ZU_VERZWEIGT aufnehmen:\n  " + "\n  ".join(neu))


def test_the_known_heavy_functions_do_not_get_heavier():
    """Und die bekannten duerfen nicht weiter wachsen."""
    gemessen = _gemessen()
    schlimmer = [f"{name}: {ZU_VERZWEIGT[name]} -> {wert}"
                 for name, wert in gemessen.items()
                 if name in ZU_VERZWEIGT and wert > ZU_VERZWEIGT[name]]
    assert not schlimmer, "Verzweigung gewachsen:\n  " + "\n  ".join(schlimmer)


@pytest.mark.parametrize("name", sorted(ZU_VERZWEIGT))
def test_an_entry_that_is_no_longer_too_branched_leaves_the_list(name):
    """Wer aufgeraeumt hat, traegt es hier aus -- sonst waere die Liste in
    einem Jahr eine Sammlung von Namen, die nichts mehr bedeuten."""
    gemessen = _gemessen()
    if name not in gemessen:
        pytest.fail(f"{name} ist unter der Schwelle: aus ZU_VERZWEIGT entfernen.")
    assert gemessen[name] <= ZU_VERZWEIGT[name]
