"""Jeder Pfad haengt an einer Wurzel.

`CONFIG_DIR = Path("config")` stand zehnmal im Code, dazu neun weitere
`Path("config/…")` als Modulkonstanten und viermal `data/`. Ein Pfad, der an
dreiundzwanzig Stellen definiert ist, laesst sich nicht umstellen, ohne
zweiundzwanzig davon zu vergessen -- und genau das steht dem Datenordner im
Weg, den `docs/Generalization_plan.md` vorsieht.

Die Konstanten werden beim Import gelesen. Deshalb laeuft die Probe, ob
`FINCTL_DATEN` wirkt, in einem eigenen Prozess: im laufenden waere das Modul
laengst importiert.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent

#: Die Module, die ihre Pfade selbst bilden duerfen. Nur eines.
EIGENE_WURZEL = {"finctl/pfade.py"}

# Was jedes dieser Module unter welchem Namen fuehrt, und wohin es zeigt.
KONSTANTEN = [
    ("finctl.cli", "CONFIG_DIR", "config"),
    ("finctl.cli", "DATA_DIR", "data"),
    ("finctl.ops", "CONFIG_DIR", "config"),
    ("finctl.ops", "DATA_DIR", "data"),
    ("finctl.assumptions", "PATH", "config/assumptions.yaml"),
    ("finctl.overlays", "CONFIG_DIR", "config"),
    ("finctl.bestaende", "CONFIG_DIR", "config"),
    ("finctl.kontenregeln", "CONFIG_DIR", "config"),
    ("finctl.monatsabschluss", "CONFIG_DIR", "config"),
    ("finctl.ledger.db", "DEFAULT_DB", "data/finance.db"),
    ("finctl.ledger.gruppen", "CONFIG_PATH", "config/groups.yaml"),
    ("finctl.tax.taxonomy", "TAXONOMY_PATH", "config/taxonomy.yaml"),
    ("finctl.tax.taxonomy", "CUSTOM_PATH", "config/taxonomy_custom.yaml"),
    ("finctl.rules.engine", "RULES_PATH", "config/rules.yaml"),
    ("finctl.rules.categorize", "OVERRIDES_PATH", "config/overrides.yaml"),
    ("finctl.ingest.importer", "ADJUSTMENTS_PATH", "config/adjustments.yaml"),
    ("finctl.forecast.jahre", "CONFIG_DIR", "config"),
    ("finctl.forecast.abgleich", "CONFIG_DIR", "config"),
    ("finctl.web.basis", "DB_PATH", "data/finance.db"),
    ("finctl.web.auth", "PFAD", "config/server.yaml"),
]

_PROBE = """
import importlib, pathlib, sys
for modul, name, _ in {paare!r}:
    # Mit `/` auch auf Windows: verglichen wird der Aufbau, nicht die Trenner.
    print(pathlib.PurePath(getattr(importlib.import_module(modul), name)).as_posix())
"""


_ORT_PROBE = """
from finctl import pfade
ordner, grund = pfade.wurzel_mit_grund()
print(grund)
print(ordner)
print(pfade.standard())
"""


def _rein(heim: Path) -> dict:
    """Eine Umgebung ohne fremde Vorbelegung.

    Der Zeiger liegt an der Vorgabestelle des Systems, und die haengt am
    Heimatverzeichnis. Ohne ein eigenes haenge jeder Test hier davon ab, ob
    auf DIESEM Rechner schon ein Datenordner eingetragen ist -- er ginge beim
    Entwickler durch und beim naechsten Menschen nicht.
    """
    import os

    ohne = {k: v for k, v in os.environ.items() if k != "FINCTL_DATEN"}
    return {**ohne, "HOME": str(heim),
            "XDG_DATA_HOME": str(heim / "share"),
            "APPDATA": str(heim / "appdata")}


def _ausgeben(umgebung: dict, cwd: Path = WURZEL) -> list[str]:
    fertig = subprocess.run(
        [sys.executable, "-c", _PROBE.format(paare=KONSTANTEN)],
        cwd=cwd, capture_output=True, text=True, check=True, env=umgebung)
    # Zeilenweise: ein Datenordner wie "Application Support" hat Leerzeichen.
    return fertig.stdout.splitlines()


def _ort(cwd: Path, umgebung: dict) -> tuple[str, str, str]:
    """Grund, Datenordner und Systemvorgabe -- aus einem eigenen Prozess."""
    fertig = subprocess.run(
        [sys.executable, "-c", _ORT_PROBE],
        cwd=cwd, capture_output=True, text=True, check=True, env=umgebung)
    grund, ordner, vorgabe = fertig.stdout.splitlines()
    return grund, ordner, vorgabe


def test_no_module_builds_its_own_config_path():
    """Ein zweiter Ort fuer denselben Pfad ist ein Ort, den der Umbau uebersieht.

    Die Liste darf kuerzer werden und nichts dazubekommen -- wie
    `ZU_VERZWEIGT` und die Ausnahmen in `docs/design_conventions.yaml`.
    """
    gefunden = []
    for pfad in sorted(WURZEL.glob("finctl/**/*.py")):
        rel = pfad.relative_to(WURZEL).as_posix()
        if rel in EIGENE_WURZEL:
            continue
        for knoten in ast.walk(ast.parse(pfad.read_text(encoding="utf-8"))):
            if (isinstance(knoten, ast.Call)
                    and getattr(knoten.func, "id", None) == "Path"
                    and knoten.args
                    and isinstance(knoten.args[0], ast.Constant)
                    and isinstance(knoten.args[0].value, str)
                    and knoten.args[0].value.split("/")[0] in ("config", "data",
                                                               "statements")):
                gefunden.append(f"{rel}:{knoten.lineno}  {knoten.args[0].value}")
    assert not gefunden, (
        "Diese Stellen bauen ihren Pfad selbst, statt ihn von finctl/pfade.py "
        "zu nehmen:\n  " + "\n  ".join(gefunden))


def test_an_existing_install_stays_where_it_is(tmp_path):
    """Im Projektordner aendert sich nichts: dieselben relativen Pfade wie vor
    dem Umbau.

    Das ist die dritte Regel aus `pfade.py`, und sie ist der Grund, warum eine
    bestehende Einrichtung eine neue Version ueberlebt: Wer `config/` neben
    sich liegen hat, arbeitet weiter damit, statt einem leeren Ordner im
    Heimatverzeichnis gegenueberzustehen.
    """
    # Ein eigener Projektordner: der Code selbst traegt seit dem Umzug in den
    # Datenordner kein config/ mehr neben sich.
    projekt = tmp_path / "projekt"
    (projekt / "config").mkdir(parents=True)
    erwartet = [ziel for _, _, ziel in KONSTANTEN]
    assert _ausgeben(_rein(tmp_path), projekt) == erwartet

    grund, ordner, _ = _ort(projekt, _rein(tmp_path))
    assert ordner == "." and "Arbeitsverzeichnis" in grund


def test_the_data_root_moves_every_path(tmp_path):
    """Und mit der Variablen wandert ALLES mit -- Konfiguration wie Hauptbuch.

    Wandert nur ein Teil, liegt das Hauptbuch im Datenordner und die
    Handentscheidungen daneben im Code, und ein Backup traegt die Haelfte.
    """
    umgebung = {**_rein(tmp_path), "FINCTL_DATEN": str(tmp_path / "woanders")}
    erwartet = [(tmp_path / "woanders" / ziel).as_posix() for _, _, ziel in KONSTANTEN]
    assert _ausgeben(umgebung) == erwartet


# ------------------------------------------------------- wer die Wurzel waehlt

def test_a_fresh_install_lands_in_the_place_this_system_provides(tmp_path):
    """Ohne alles: der Ordner, den das System fuer Anwendungsdaten vorsieht.

    Vorher war es das Arbeitsverzeichnis. Wer das Werkzeug installiert, statt
    es auszuchecken, hat aber keines, in dem etwas liegen duerfte -- und legte
    sonst ein Hauptbuch dort an, wo er zufaellig gerade stand.
    """
    leer = tmp_path / "leer"
    leer.mkdir()
    grund, ordner, vorgabe = _ort(leer, _rein(tmp_path))
    assert ordner == vorgabe and "Vorgabe" in grund
    # Und die Vorgabe liegt im Heimatverzeichnis, nicht irgendwo.
    assert str(tmp_path) in vorgabe


def test_the_registered_folder_beats_the_working_directory(tmp_path):
    """Der Zeiger ist die ausdrueckliche Antwort und gilt ueberall.

    Stuende das Arbeitsverzeichnis davor, entschiede ein fremdes `config/` im
    gerade offenen Ordner darueber, welches Hauptbuch gemeint ist.
    """
    umgebung = _rein(tmp_path)
    _, _, vorgabe = _ort(tmp_path, umgebung)

    eigener = tmp_path / "meine daten"
    eigener.mkdir()
    zeiger = Path(vorgabe) / "ort.txt"
    zeiger.parent.mkdir(parents=True, exist_ok=True)
    zeiger.write_text(f"{eigener}\n", encoding="utf-8")

    # WURZEL hat ein config/ -- der Zeiger gewinnt trotzdem.
    grund, ordner, _ = _ort(WURZEL, umgebung)
    assert ordner == str(eigener) and "eingetragen" in grund


def test_the_variable_beats_the_registered_folder(tmp_path):
    """`FINCTL_DATEN` steht ganz oben -- fuer einen Blick in eine
    Wiederherstellung, ohne den eingetragenen Bestand anzufassen."""
    umgebung = _rein(tmp_path)
    _, _, vorgabe = _ort(tmp_path, umgebung)
    zeiger = Path(vorgabe) / "ort.txt"
    zeiger.parent.mkdir(parents=True, exist_ok=True)
    zeiger.write_text(f"{tmp_path / 'eingetragen'}\n", encoding="utf-8")

    grund, ordner, _ = _ort(WURZEL, {**umgebung,
                                     "FINCTL_DATEN": str(tmp_path / "probe")})
    assert ordner == str(tmp_path / "probe") and grund == "FINCTL_DATEN"


def test_registering_a_folder_does_not_move_anything(tmp_path, monkeypatch):
    """`ort_setzen` schreibt eine Zeile und sonst nichts.

    Ein Befehl, der gleichzeitig verschiebt, ist einer, nach dem niemand mehr
    weiss, wo die Daten liegen, wenn er in der Mitte abbricht.
    """
    from finctl import pfade

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    alt = tmp_path / "alt"
    (alt / "config").mkdir(parents=True)
    (alt / "config" / "rules.yaml").write_text("rules: []\n", encoding="utf-8")

    neu = tmp_path / "neu"
    assert pfade.ort() is None
    pfade.ort_setzen(neu)

    assert pfade.ort() == neu
    assert pfade.zeiger().read_text(encoding="utf-8").strip() == str(neu)
    # Nichts umgezogen, nichts angelegt.
    assert (alt / "config" / "rules.yaml").exists()
    assert not neu.exists()


def test_the_venv_hint_matches_the_running_platform(monkeypatch):
    """Eine Anleitung, die den falschen Pfad nennt, hilft niemandem.

    Unix legt die Programme der virtuellen Umgebung unter `.venv/bin`,
    Windows unter `.venv\\Scripts` und mit `.exe`. Der Hinweis stand fuenfmal
    als fester Text im Code und sagte ueberall das Unix-Format -- ein
    Windows-Nutzer haette ihn abgetippt und eine Fehlermeldung bekommen, die
    er nicht deuten kann, und zwar beim allerersten Versuch.
    """
    from finctl import pfade

    monkeypatch.setattr(pfade.os, "name", "posix")
    assert pfade.aus_umgebung("finctl") == ".venv/bin/finctl"

    monkeypatch.setattr(pfade.os, "name", "nt")
    assert pfade.aus_umgebung("finctl") == ".venv\\Scripts\\finctl.exe"
