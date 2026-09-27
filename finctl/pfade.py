"""Wo die Daten liegen -- an einer Stelle.

Jeder Pfad dieses Werkzeugs haengt an EINER Wurzel, dem Datenordner. Darin
liegen `config/`, `data/` und `data/statements/`.

Der Grund, eine Wurzel zu haben, steht in `cli.py` als Fehlermeldung: weil
jeder Pfad relativ zum Arbeitsverzeichnis war, scheiterte das Werkzeug von
jedem anderen Ordner aus, und der naechstliegende Rat -- `finctl init` --
legte ein zweites, leeres Hauptbuch im Heimatverzeichnis an. Ein Pfad, der an
zwoelf Stellen definiert ist, laesst sich nicht umstellen, ohne elf davon zu
vergessen.

WER DIE WURZEL BESTIMMT, in dieser Reihenfolge:

1. `FINCTL_DATEN`. Wer die Variable setzt, meint es -- fuer einen zweiten
   Bestand, fuer einen Test, fuer einen Blick in eine Wiederherstellung.
2. Der eingetragene Ort (`ort.txt`, siehe unten). Das ist die Antwort aus der
   Einrichtung, und sie gilt von jedem Arbeitsverzeichnis aus.
3. Ein `config/` im Arbeitsverzeichnis. Eine bestehende Einrichtung zieht
   nicht von selbst um, nur weil das Werkzeug neuer ist.
4. Sonst die Vorgabe des Systems -- `~/Library/Application Support/Finance OS`
   auf macOS, `%APPDATA%\\Finance OS` auf Windows, `~/.local/share/finance-os`
   sonst.

Der Zeiger steht VOR dem Arbeitsverzeichnis, weil er die ausdrueckliche
Antwort ist: sonst entschiede ein fremdes `config/` im gerade offenen Ordner
darueber, welches Hauptbuch gemeint ist.

DIE KONSTANTEN WERDEN BEIM IMPORT GELESEN. Wer sie in einem Test umbiegt,
setzt sie am benutzenden Modul (`monkeypatch.setattr(cli, "CONFIG_DIR", ...)`),
nicht hier -- die Module halten eigene Namen, damit genau das weiter geht.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Wie der Ordner heisst, den das Werkzeug sich selbst anlegt. Mit Leerzeichen
#: und gross, weil macOS und Windows ihre Anwendungsordner so beschriften; auf
#: Linux klein und mit Bindestrich, weil dort alles darunter so aussieht.
ORDNER = "Finance OS"
ORDNER_UNIX = "finance-os"

#: Die Datei, die den Datenordner nennt. Ein Pfad, eine Zeile.
ZEIGER = "ort.txt"


def standard() -> Path:
    """Wo der Datenordner liegt, wenn niemand etwas anderes sagt.

    Je System der Ort, an dem eine Anwendung ihre Daten ablegen soll. Das
    Arbeitsverzeichnis ist es ausdruecklich NICHT mehr: wer das Werkzeug
    installiert statt es auszuchecken, hat keins, in dem etwas liegen duerfte.
    """
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or "~/AppData/Roaming").expanduser() / ORDNER
    if sys.platform == "darwin":
        return Path("~/Library/Application Support").expanduser() / ORDNER
    basis = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(basis).expanduser() / ORDNER_UNIX


def zeiger() -> Path:
    """Die Datei, in der steht, wo der Datenordner liegt.

    Sie liegt an der Vorgabestelle des Systems und nicht im Datenordner
    selbst -- der ist ja gerade das, was sie erst findet. Wer die Vorgabe
    behaelt, sieht sie nie.
    """
    return standard() / ZEIGER


def ort() -> Path | None:
    """Der eingetragene Datenordner. `None`, wenn keiner eingetragen ist."""
    try:
        text = zeiger().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text).expanduser() if text else None


def ort_setzen(ordner: Path | str) -> Path:
    """Eintragen, wo der Datenordner liegt.

    VERSCHIEBT NICHTS. Der Eintrag sagt, wo gesucht wird; die Daten dorthin zu
    bringen ist ein zweiter, sichtbarer Schritt. Ein Befehl, der beides tut,
    waere einer, nach dem man nicht mehr weiss, wo die Daten sind, wenn er in
    der Mitte abbricht.
    """
    ziel = Path(ordner).expanduser()
    pfad = zeiger()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(f"{ziel}\n", encoding="utf-8")
    return ziel


def wurzel_mit_grund() -> tuple[Path, str]:
    """Der Datenordner und warum er es ist -- fuer `finctl ort`.

    Die Begruendung mitzugeben ist kein Luxus: Wenn das Werkzeug das falsche
    Hauptbuch aufmacht, ist die einzige nuetzliche Auskunft, WELCHE der vier
    Regeln gegriffen hat.
    """
    gesetzt = os.environ.get("FINCTL_DATEN")
    if gesetzt:
        return Path(gesetzt).expanduser(), "FINCTL_DATEN"
    eingetragen = ort()
    if eingetragen:
        return eingetragen, f"eingetragen in {zeiger()}"
    if Path("config").is_dir():
        return Path("."), "config/ im Arbeitsverzeichnis"
    return standard(), "Vorgabe dieses Systems"


def wurzel() -> Path:
    """Der Datenordner."""
    return wurzel_mit_grund()[0]


#: Die Wurzel, einmal beim Import bestimmt.
DATEN = wurzel()

#: Handentscheidungen und Stammdaten als YAML.
CONFIG_DIR = DATEN / "config"

#: Abgeleitetes: das Hauptbuch und die eingelesenen Auszuege.
DATA_DIR = DATEN / "data"
STATEMENTS_DIR = DATA_DIR / "statements"
DB_PATH = DATA_DIR / "finance.db"


def aus_umgebung(programm: str) -> str:
    """Wie ein Programm der virtuellen Umgebung aufgerufen wird.

    Unix legt sie unter `.venv/bin`, Windows unter `.venv\\Scripts` und mit
    `.exe`. Das steht hier und nicht fuenfmal im Text, weil eine Anleitung,
    die den falschen Pfad nennt, den Nutzer in eine Fehlermeldung schickt,
    die er nicht deuten kann -- und das ausgerechnet beim ersten Versuch.
    """
    if os.name == "nt":
        return f".venv\\Scripts\\{programm}.exe"
    return f".venv/bin/{programm}"
