"""Der Stromzaehler in `config/strom.yaml`.

Die Rechnung steht in `finctl/energie/zaehler.py` und gilt fuer jeden
Zaehler; hier liegt nur, wo der Strom gespeichert wird. Die Datei war vor den
anderen Zaehlern da und bleibt, wie sie ist -- ein Umzug in eine gemeinsame
Datei haette nichts gerechnet, aber jede bestehende Einrichtung angefasst.

Die Namen der Rechnung werden weitergereicht, damit `strom.rechnen` und
`strom.Zeitraum` weiter gelten.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from finctl.energie.zaehler import (  # noqa: F401 -- weitergereicht
    STROM,
    Ablesung,
    Abschlag,
    Messung,
    Posten,
    Rechnung,
    Schritt,
    Tarif,
    Zeitraum,
    ablesung_pruefen,
    als_roh,
    aus_roh,
    lies_datum,
    lies_zahl,
    naechster_zeitraum,
    rechnen,
    zahlungstermine,
    zeitraum_als_yaml,
    zeitraum_aus,
    zeitraum_fuer,
    zeitraum_pruefen,
)
from finctl.pfade import CONFIG_DIR

PFAD = CONFIG_DIR / "strom.yaml"

KOPF = """\
# Strom -- geschrieben von der Seite /strom.
#
# Von Hand aendern geht, die Seite liest die Datei bei jedem Aufruf neu.
# Zeitraeume: das neueste zuletzt. Ablesungen: Datum und Zaehlerstand in kWh.
# Geld in Cent; Arbeitspreis in Cent je kWh.

"""


def laden(pfad: Path = PFAD) -> tuple[list[Zeitraum], list[Ablesung]]:
    if not pfad.exists():
        return [], []
    return aus_roh(yaml.safe_load(pfad.read_text(encoding="utf-8")) or {})


def schreiben(zeitraeume: list[Zeitraum], ablesungen: list[Ablesung],
              pfad: Path = PFAD) -> None:
    # Erst in eine Nachbardatei, dann umbenennen: bricht das Schreiben ab,
    # bleibt die alte Datei ganz und nicht halb.
    neu = pfad.with_suffix(pfad.suffix + ".neu")
    neu.write_text(KOPF + yaml.safe_dump(als_roh(zeitraeume, ablesungen),
                                         allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    os.replace(neu, pfad)
