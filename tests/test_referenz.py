"""Der Neuaufbau aus `config/` und den Auszuegen ergibt die Referenz.

Das Sicherheitsnetz fuer den Umbau in Module: Hauptbuch und jede Seite
muessen nach jedem Schritt Zeile fuer Zeile gleich bleiben. Die Referenz
liegt in `data/referenz/` (echte Betraege, deshalb nicht im Repository) und
wird mit `python tests/referenz.py schreiben` festgehalten -- ohne sie wird
dieser Test uebersprungen.

Aendert sich ein Ergebnis mit Absicht (eine neue Regel, ein korrigierter
Fehler), wird die Referenz neu geschrieben, und zwar in einem eigenen
Schritt: ein Umbau, der zugleich die Referenz verschiebt, beweist nichts.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import referenz

from finctl.pfade import wurzel_mit_grund

# Die Tests laufen in einer Kopie (tests/conftest.py); die Referenz liegt aber
# bei den echten Daten, und von dort wird auch neu gebaut -- nur gelesen.
QUELLE = Path(os.environ.get("FINCTL_TEST_QUELLE") or wurzel_mit_grund()[0]).resolve()
ORDNER = referenz.referenz_ordner(QUELLE)


@pytest.mark.skipif(not (ORDNER / "meta.json").exists(), reason="keine Referenz festgehalten")
def test_neuaufbau_entspricht_der_referenz(tmp_path):
    meta = json.loads((ORDNER / "meta.json").read_text(encoding="utf-8"))
    neu = tmp_path / "stand"
    referenz.neu_bauen(QUELLE, dt.date.fromisoformat(meta["stichtag"]), neu)
    funde = referenz.unterschiede(ORDNER, neu)
    assert not funde, (f"{len(funde)} Dateien weichen von der Referenz ab:\n\n"
                       + "\n\n".join(funde[:10]))


def test_vergleich_findet_eine_abweichung(tmp_path):
    """Der Vergleich selbst: gleich ist leer, eine geaenderte Zahl nicht."""
    alt, neu = tmp_path / "alt", tmp_path / "neu"
    for ordner, betrag in ((alt, -9990), (neu, -9990)):
        (ordner / "hauptbuch").mkdir(parents=True)
        (ordner / "hauptbuch" / "splits.json").write_text(
            json.dumps([{"amount_cents": betrag}]), encoding="utf-8")
    assert referenz.unterschiede(alt, neu) == []

    (neu / "hauptbuch" / "splits.json").write_text(
        json.dumps([{"amount_cents": -9991}]), encoding="utf-8")
    funde = referenz.unterschiede(alt, neu)
    assert len(funde) == 1 and "splits.json" in funde[0]


def test_fester_tag_liefert_echte_datumswerte():
    """Nach dem Festnageln bleibt alles Konstruierte ein echtes `date` --
    sonst scheitern YAML und sqlite, die ihre Typen genau nachschlagen."""
    import subprocess

    code = (
        "import sys, datetime as d; sys.path.insert(0, sys.argv[1]); import referenz\n"
        "echt = d.date\n"
        "referenz.tag_festnageln(echt(2030, 1, 2))\n"
        "from datetime import date, datetime\n"
        "assert date.today() == echt(2030, 1, 2)\n"
        "assert type(date(2024, 5, 6)) is echt\n"
        "assert type(date.fromisoformat('2024-05-06')) is echt\n"
        "assert isinstance(echt(2024, 1, 1), date)\n"
        "assert datetime.now().date() == echt(2030, 1, 2)\n"
    )
    lauf = subprocess.run([sys.executable, "-c", code, str(Path(__file__).parent)],
                          capture_output=True, text=True, check=False)
    assert lauf.returncode == 0, lauf.stderr
