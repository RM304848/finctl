"""Mitgelieferte Startdateien -- damit der erste Start kein Absturz ist.

Gemessen statt angenommen: `finctl init` lief gegen einen leeren Datenordner
sauber durch, und danach warf die STARTSEITE einen 500er, weil
`balances.yaml` fehlte. Ebenso /ziele, /planung, /annahmen, /regeln und
/monatsabschluss. Sechs von siebzehn Seiten. Wer dieses Werkzeug uebernimmt,
sah als Erstes einen Fehler.

DIE VORGABEN LIEGEN IM CODE, die Daten im Datenordner. Beim ersten Start
wird kopiert, was fehlt -- und nur das. Eine vorhandene Datei wird nie
angefasst: sie traegt Handentscheidungen, und ein Programm, das sie beim
Start ueberschreibt, ist keine Hilfe, sondern eine Falle.

WAS HIER LIEGT, IST BEWUSST DUENN -- mit EINER Ausnahme. Startwerte, keine
Zahlen von jemandem: ein leerer Regelsatz, leere Bestaende, vorsichtige
Annahmen.

Die Ausnahme ist `taxonomy.yaml`, und sie ist Absicht. Ein leerer
Kategoriebaum heisst, dass sich nichts zuordnen laesst -- das Werkzeug
startet dann zwar, tut aber nichts. Der mitgelieferte Baum ist gewachsen und
nicht ausgedacht, und wer ihn nicht mag, aendert ihn: Ab dem ersten Start ist
es seine Datei, und diese hier wird nie wieder angefasst.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ORDNER = Path(__file__).resolve().parent

#: Was eine frisch eingerichtete Installation braucht, um zu laufen.
#: Die Liste ist gemessen, nicht geraten: genau diese Dateien fehlten, als
#: `tests/test_erstlauf.py` zum ersten Mal lief.
STARTDATEIEN = ("assumptions.yaml", "balances.yaml", "goals.yaml",
                "groups.yaml", "lebensplan.yaml", "rules.yaml",
                "taxonomy.yaml")


def sicherstellen(config_dir: Path) -> list[str]:
    """Fehlende Startdateien anlegen. Gibt zurueck, was angelegt wurde."""
    config_dir.mkdir(parents=True, exist_ok=True)
    angelegt = []
    for name in STARTDATEIEN:
        ziel = config_dir / name
        if ziel.exists():
            continue
        shutil.copyfile(ORDNER / name, ziel)
        angelegt.append(name)
    return angelegt
