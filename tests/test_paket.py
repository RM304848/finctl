"""Was ins Paket muss -- gebaut und nachgezaehlt, nicht gelesen.

Am 25.09.2026 gemessen: ein Rad aus diesem Projekt enthielt genau EINE
Nicht-Python-Datei, `schema.sql`. Keine der vierzig Vorlagen, keine der sieben
Startdateien, kein Handbuch. Eine installierte App waere auf jeder Seite mit
`TemplateNotFound` abgestuerzt, `init` haette keine Startdateien angelegt, und
das Handbuch waere leer geblieben.

In `pyproject.toml` stand nichts Falsches -- es stand nur nichts da. Genau
deshalb prueft dieser Test das ERGEBNIS und nicht die Deklaration: Wer einen
Ordner mit Vorlagen dazunimmt und die Zeile vergisst, merkt es sonst erst,
wenn jemand anders die App installiert.

Der Bau dauert ein paar Sekunden. Das ist der Preis dafuer, dass die Frage
"ist das Paket vollstaendig" eine Antwort hat statt einer Vermutung.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent

#: Was zur Laufzeit gebraucht wird, und wo es im Quellbaum liegt.
MITZULIEFERN = {
    "Vorlagen": ("finctl/web/templates", "*.html"),
    "Startdateien": ("finctl/vorgaben", "*.yaml"),
    "Schema": ("finctl/ledger", "*.sql"),
}


# Ein Rad fuer alle drei Tests, in EINEM Prozess: parallel gebaut stiessen
# sich drei Laeufe am selben `build/` im Projekt.
pytestmark = pytest.mark.xdist_group("paket")


@pytest.fixture(scope="module")
def rad(tmp_path_factory) -> list[str]:
    """Die Dateiliste eines frisch gebauten Rads."""
    try:
        import build  # noqa: F401
    except ImportError:
        pytest.skip("`build` nicht installiert")

    ziel = tmp_path_factory.mktemp("rad")
    # setuptools legt `build/` IM PROJEKT an, nicht im Zielordner. Bleibt es
    # liegen, findet ruff dort eine zweite Kopie jeder Datei -- und die
    # Verzweigungsratsche meldet jede Funktion als neu ueber der Schwelle.
    # Ein Test, der die Arbeitskopie veraendert zuruecklaesst, ist ein Test,
    # der den naechsten zum Luegner macht.
    vorher = (WURZEL / "build").exists()
    try:
        fertig = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(ziel)],
            cwd=WURZEL, capture_output=True, text=True, check=False)
        assert fertig.returncode == 0, fertig.stderr[-2000:]

        [datei] = sorted(ziel.glob("*.whl"))
        with zipfile.ZipFile(datei) as z:
            return z.namelist()
    finally:
        if not vorher:
            shutil.rmtree(WURZEL / "build", ignore_errors=True)
        shutil.rmtree(WURZEL / "finctl.egg-info", ignore_errors=True)


def test_the_wheel_carries_every_file_the_app_reads_at_runtime(rad):
    """Jede einzelne Datei, nicht nur eine je Ordner.

    Eine Stichprobe haette genuegt, um den Fehler von damals zu finden -- aber
    nicht den naechsten: ein Rad, das neunundreissig von vierzig Vorlagen
    enthaelt, stuerzt auf genau einer Seite ab.
    """
    fehlt = []
    for was, (ordner, muster) in MITZULIEFERN.items():
        quellen = sorted((WURZEL / ordner).glob(muster))
        assert quellen, f"{was}: im Quellbaum ist nichts zu finden"
        for quelle in quellen:
            pfad = quelle.relative_to(WURZEL).as_posix()
            if pfad not in rad:
                fehlt.append(f"{was}: {pfad}")
    assert not fehlt, (
        "Diese Dateien landen NICHT im Paket:\n  " + "\n  ".join(fehlt)
        + "\n\nIn pyproject.toml unter `[tool.setuptools.package-data]` "
          "eintragen -- ein Paket nimmt nur mit, was dort steht.")


def test_the_wheel_carries_the_handbook(rad):
    """Ohne Handbuch zeigt jede Fusszeile der App ins Leere."""
    assert "finctl/HANDBUCH.md" in rad


def test_the_shipped_handbook_is_the_one_that_is_maintained():
    """Zwei Orte, eine Datei.

    Im Repository liegt das Handbuch an der Wurzel, wo man es bearbeitet; ein
    Rad nimmt aber nur mit, was INNERHALB eines Pakets liegt. Die Kopie unter
    `finctl/` ist deshalb noetig -- und ohne diesen Test waere sie nach der
    ersten Aenderung eine zweite, falsche Wahrheit.
    """
    wurzel = (WURZEL / "HANDBUCH.md").read_bytes()
    paket = (WURZEL / "finctl" / "HANDBUCH.md").read_bytes()
    assert wurzel == paket, (
        "Die Kopie im Paket ist nicht mehr die gepflegte Fassung.\n"
        "  cp HANDBUCH.md finctl/HANDBUCH.md")


def test_the_entry_point_is_declared(rad):
    """Ohne Eintragspunkt gibt es nach der Installation kein `finctl`."""
    [eintraege] = [n for n in rad if n.endswith(".dist-info/entry_points.txt")]
    assert eintraege
