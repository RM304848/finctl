"""Gemeinsamer Rahmen fuer alle Tests.

Die Tests lesen sonst die ECHTE config/server.yaml. Seit dort ein Passwort
steht, sah der Testclient nur noch die Anmeldeseite und hundert Tests wurden
rot, ohne dass sich am Code etwas geaendert hatte. Jeder Test bekommt deshalb
eine leere Serverkonfiguration: localhost, kein Passwort. Tests, die die
Anmeldung selbst pruefen, setzen `auth.PFAD` weiterhin selbst.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# ------------------------------------------------------------------ Testwurzel
#
# Die Tests lesen echte Daten -- das Hauptbuch, config/ -- und einige
# schreiben dort hinein und raeumen danach wieder auf. Das hatte drei Folgen:
# ein abgebrochener Lauf liess halbe Aenderungen in config/ zurueck, ein
# laufendes Dashboard sah waehrend der Tests fremde Zahlen, und parallel
# laufen konnten die Tests nicht, weil sie sich dieselben Dateien teilten.
#
# Deshalb arbeitet jeder Testprozess in einer eigenen Kopie: config/ und
# data/ werden in einen Wegwerfordner gelegt (Auszuege als harte Links, das
# kostet keinen Platz), alles andere -- Code, Tests, docs/ -- wird verlinkt,
# und das Arbeitsverzeichnis wechselt dorthin. Relative Pfade wie
# `Path("data/finance.db")` treffen damit die Kopie.
#
# HOME, APPDATA und XDG_DATA_HOME zeigen ebenfalls in den Wegwerfordner.
# Sonst gewaenne ein eingetragener Datenordner (`finctl ort --setzen`) gegen
# das Arbeitsverzeichnis, und die Tests liefen doch wieder auf den echten
# Daten -- und ein eingetragenes Sicherungsziel unter ~ bliebe erreichbar.
#
# Das muss vor dem ersten `import finctl` geschehen: die Pfade werden beim
# Import gelesen.


def _quelle() -> Path:
    """Wo die echten Daten liegen -- die Kopiervorlage, nur gelesen.

    Laufen die Tests parallel (`-n auto`), erbt jeder Arbeitsprozess die
    Umgebung des Hauptprozesses, der schon in SEINER Kopie steht. Die echte
    Quelle kommt deshalb aus der Umgebung, sobald sie einmal bestimmt ist.
    """
    import importlib

    if os.environ.get("FINCTL_TEST_QUELLE"):
        return Path(os.environ["FINCTL_TEST_QUELLE"])
    if os.environ.get("FINCTL_TESTDATEN") == "muster":
        return _musterhaushalt()

    from finctl import pfade

    importlib.reload(pfade)
    wurzel = pfade.wurzel_mit_grund()[0].resolve()
    # Ohne eigene Daten -- ein frischer Klon, die automatische Pruefung --
    # laufen die Tests auf dem erfundenen Haushalt statt auf nichts.
    return wurzel if (wurzel / "config").is_dir() else _musterhaushalt()


def _musterhaushalt() -> Path:
    """Den erfundenen Haushalt einmal bauen (tests/musterhaushalt/bauen.py).

    In einem eigenen Prozess, weil finctl die Pfade beim Import liest. Die
    Arbeitsprozesse von `-n auto` bauen nicht noch einmal: Sie erben
    FINCTL_TEST_QUELLE, das `_umziehen` danach setzt.
    """
    import atexit
    import subprocess
    import sys

    # Wer fragt, auf welchen Daten er laeuft (tests/test_leck.py), liest es hier.
    os.environ["FINCTL_TESTDATEN"] = "muster"
    ziel = Path(tempfile.mkdtemp(prefix="finctl-muster-"))
    atexit.register(shutil.rmtree, ziel, ignore_errors=True)
    skript = Path(__file__).resolve().parent / "musterhaushalt" / "bauen.py"
    umgebung = {k: v for k, v in os.environ.items() if k != "FINCTL_DATEN"}
    lauf = subprocess.run([sys.executable, str(skript), str(ziel)], env=umgebung,
                          capture_output=True, text=True, check=False)
    if lauf.returncode:
        raise SystemExit(f"Musterhaushalt nicht gebaut:\n{lauf.stdout}\n{lauf.stderr}")
    return ziel


def _harter_link(quelle: str, ziel: str) -> None:
    try:
        os.link(quelle, ziel)
    except OSError:
        shutil.copy2(quelle, ziel)


def _testwurzel_anlegen(quelle: Path) -> Path:
    projekt = Path(__file__).resolve().parent.parent
    wurzel = Path(tempfile.mkdtemp(prefix="finctl-test-"))
    daten = wurzel / "data"
    daten.mkdir()
    if (quelle / "config").is_dir():
        shutil.copytree(quelle / "config", wurzel / "config",
                        ignore=shutil.ignore_patterns("*.lock"))
    for eintrag in (quelle / "data").iterdir() if (quelle / "data").is_dir() else ():
        if eintrag.name.startswith(".") or eintrag.name.startswith("referenz"):
            continue
        if eintrag.is_dir():
            shutil.copytree(eintrag, daten / eintrag.name, copy_function=_harter_link)
        elif eintrag.suffix == ".db":
            shutil.copy2(eintrag, daten / eintrag.name)
    for eintrag in projekt.iterdir():
        if eintrag.name not in ("config", "data") and not (wurzel / eintrag.name).exists():
            (wurzel / eintrag.name).symlink_to(eintrag, target_is_directory=eintrag.is_dir())
    (wurzel / "_heim").mkdir()
    return wurzel


def _umziehen() -> None:
    import atexit
    import importlib

    quelle = _quelle()
    wurzel = _testwurzel_anlegen(quelle)
    atexit.register(shutil.rmtree, wurzel, ignore_errors=True)
    heim = wurzel / "_heim"
    # Fuer Tests, die bewusst die echten Daten lesen muessen (tests/referenz.py).
    os.environ["FINCTL_TEST_QUELLE"] = str(quelle)
    os.environ.pop("FINCTL_DATEN", None)
    os.environ.update(HOME=str(heim), APPDATA=str(heim / "appdata"),
                      XDG_DATA_HOME=str(heim / "share"), USERPROFILE=str(heim))
    os.chdir(wurzel)

    from finctl import pfade

    importlib.reload(pfade)
    if pfade.wurzel_mit_grund()[0].resolve() != wurzel.resolve():
        raise SystemExit("Die Tests liefen nicht in ihrer Kopie -- abgebrochen, "
                         "bevor sie echte Daten anfassen.")


_umziehen()

# Wie die App: die Module melden sich bei der Basis an (ops.module_anschliessen).
from finctl import ops as _ops  # noqa: E402, F401
from finctl.web import auth  # noqa: E402

# ------------------------------------------------------------------ langsam
#
# Die schnelle Runde (`pytest -m "not langsam"`) ist fuer die Minuten zwischen
# zwei Aenderungen; vor jedem Commit laeuft alles. Langsam ist, was Seiten
# rendert oder echte Auszuege einliest -- gemessen, nicht geschaetzt: diese
# Dateien trugen am 25.09.2026 zusammen ueber neun Zehntel der Laufzeit.
LANGSAM = {
    "test_web.py", "test_design.py", "test_parsers_golden.py", "test_dkb_parser.py",
    "test_referenz.py", "test_jahre.py", "test_forecast.py", "test_ziele_seite.py",
    "test_determinism.py", "test_einrichtung.py", "test_rechenweg.py", "test_erstlauf.py",
    "test_regeln.py",
}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.name in LANGSAM:
            item.add_marker(pytest.mark.langsam)


@pytest.fixture(autouse=True)
def _server_ohne_passwort(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "PFAD", tmp_path / "server.yaml")


@pytest.fixture(autouse=True)
def _overrides_als_kopie(tmp_path, monkeypatch):
    """Tests schreiben Handentscheidungen in eine Kopie, nie in die echte Datei.

    Die Aufteilungstests zerlegen eine echte Buchung ueber die API. Das Ledger
    stellten sie danach wieder her, die Entscheidung in config/overrides.yaml
    blieb aber stehen -- und machte beim naechsten categorize aus einer Miete
    zwei Abo-Posten.
    """
    import shutil
    from pathlib import Path

    from finctl.rules import categorize

    # Ein eigener Unterordner: Tests legen unter tmp_path selbst eine
    # overrides.yaml an und duerfen dort keine fremden Eintraege vorfinden.
    kopie = tmp_path / "_echte_config" / "overrides.yaml"
    kopie.parent.mkdir()
    echt = Path("config/overrides.yaml")
    if echt.exists():
        shutil.copy(echt, kopie)
    monkeypatch.setattr(categorize, "OVERRIDES_PATH", kopie)


@pytest.fixture
def ohne_plaene(tmp_path):
    """Die Planung ohne eingeschaltete Plaene -- nur die Verpflichtungen.

    Ein Test, der eine Eigenschaft der Rechnung prueft, darf nicht davon
    abhaengen, was gerade auf /planung ausprobiert wird: ein eingeschaltetes
    Eigenheim verschiebt Kreditraten und Endkapital, und fuenf Tests wurden
    rot, ohne dass sich am Code etwas geaendert hatte.
    """
    from pathlib import Path

    import yaml

    quelle = Path("config/szenarien.yaml")
    spec = (yaml.safe_load(quelle.read_text(encoding="utf-8")) or {}) if quelle.exists() else {}
    for s in spec.get("szenarien") or []:
        if not s.get("pflicht"):
            s["aktiv"] = False
    pfad = tmp_path / "szenarien_ohne_plaene.yaml"
    pfad.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return pfad
