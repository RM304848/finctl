"""Persoenliches steht nur in `config/` -- gemessen ueber das ganze Repository.

`docs/design_conventions.yaml` prueft die verbotenen Namen nur im SICHTBAREN
TEXT der Vorlagen. Im Code, in Kommentaren, in Docstrings und in Testdaten
standen sie weiter: **219 Fundstellen in 37 Dateien**, als diese Datei
entstand. 103 nach dem zweiten Durchgang, 69 nach dem dritten, **null seit dem
23.09.2026**.

WIE DIE LETZTEN VERSCHWUNDEN SIND, in drei Mustern -- keins davon war
Suchen-und-Ersetzen:

1. **Als Beleg gedachte Namen wurden zum Sachverhalt.** Ein Kommentar, der
   sagt "bei dieser Wohnung steigt die Rate und tilgt dabei weniger", belegt
   nicht die Wohnung, sondern den Effekt. Die Begruendung ist geblieben.
2. **Feste Kennungen wurden aus der Konfiguration gelesen.** Ein Test, der ein
   bestimmtes Objekt, eine bestimmte Regel oder eine bestimmte Klammer nennt,
   ist zugleich bruechig UND persoenlich. Mehrere dieser Tests pruefen seither
   MEHR als vorher: nicht eine Regel, sondern alle ihrer Form.
3. **Beschriftungen und eingereichte Zahlen sind nach `config/` gewandert** --
   die Policen nach `renten.policen`, die eingereichten Schuldzinsen nach
   `filed_<jahr>.schuldzinsen_cents`. Sie stehen jetzt neben der Unterlage,
   aus der sie stammen.

Der Grund steht in CLAUDE.md: Das Werkzeug soll spaeter anderen dienen, und
alles, was jetzt im Code landet, muss dann einzeln wieder heraus.

AB HIER IST DER TEST EINE SPERRE, KEINE RATSCHE. Solange nichts gefunden wird,
ist jede neue Fundstelle ein Fehler -- es gibt keinen geduldeten Rest mehr, in
dem sie untergehen koennte.

Nicht geprueft wird `docs/`. Befunde von frueher stehen in den Notizen im
Datenordner, und die Liste der Namen selbst in `config/verbotene_namen.yaml`
-- beide ausserhalb des Repositorys.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WURZEL = Path(__file__).resolve().parent.parent

#: Wo gezaehlt wird. Dieselbe Reichweite, die CLAUDE.md nennt: Code, Tests,
#: Kommentare und Vorlagen.
ORDNER = ("finctl", "tests", "werkzeuge")

#: Und in welchen Dateien. `.sql` und `.yaml` kamen am 23.09.2026 dazu: Das
#: Datenbankschema nannte in einem Kommentar eine Wohnung, und die
#: STARTVORLAGE `finctl/vorgaben/rules.yaml` -- also ausgerechnet die Datei,
#: die ein neuer Nutzer als Erstes bekommt -- nannte eine Strasse. Beide lagen
#: ausserhalb der Reichweite dieses Tests und fielen deshalb nie auf.
#: `.md` kam am 25.09.2026 dazu: Seit das Handbuch im Paket mitgeht, liegt
#: eine Fassung unter `finctl/` -- und die ging bis dahin ungeprueft an jeden
#: raus, der die App installiert. Die Fassung an der Wurzel haelt
#: `tests/test_paket.py` byteweise gleich, also ist sie mitgeprueft.
ENDUNGEN = (".py", ".html", ".sql", ".yaml", ".yml", ".md")

#: Der geduldete Rest. Leer seit dem 23.09.2026 -- und er darf nicht wieder
#: wachsen: ein Eintrag hier ist eine Fundstelle, die jemand spaeter sucht.
FUNDSTELLEN: dict[str, int] = {}


def verbotene_namen() -> list[str]:
    """Die Liste aus dem Datenordner -- die Konventionen nennen nur die Datei.

    Leer, wenn es die Datei nicht gibt: auf dem Musterhaushalt und in der
    automatischen Pruefung gibt es keine eigenen Namen, die fehlen muessten.
    """
    regeln = yaml.safe_load(
        (WURZEL / "docs" / "design_conventions.yaml").read_text(encoding="utf-8"))
    for regel in regeln["regeln"]:
        datei = (regel.get("pruefung") or {}).get("verbotene_namen")
        if datei:
            pfad = Path(datei)          # relativ zum Datenordner der Tests
            if not pfad.exists():
                return []
            return list(yaml.safe_load(pfad.read_text(encoding="utf-8"))["verbotene_namen"])
    raise AssertionError("verbotene_namen fehlt in den Konventionen")


def _gezaehlt() -> dict[str, int]:
    namen = verbotene_namen()
    if not namen:
        pytest.skip("keine Liste verbotener Namen im Datenordner")
    muster = re.compile("|".join(re.escape(n) for n in namen), re.I)
    out = {}
    for ordner in ORDNER:
        for pfad in sorted((WURZEL / ordner).rglob("*")):
            if pfad.suffix not in ENDUNGEN or "__pycache__" in str(pfad):
                continue
            text = pfad.read_text(encoding="utf-8", errors="ignore")
            rel = pfad.relative_to(WURZEL).as_posix()
            if treffer := len(muster.findall(text)):
                out[rel] = treffer
    return out


def test_the_repository_carries_no_personal_names():
    """Null Fundstellen, und das bleibt so.

    Keine Ratsche mehr, sondern eine Sperre: Weil nichts geduldet ist, faellt
    jede einzelne neue Stelle auf, statt in einem Restposten unterzugehen.
    """
    gefunden = _gezaehlt()
    zuviel = {datei: zahl for datei, zahl in gefunden.items()
              if zahl > FUNDSTELLEN.get(datei, 0)}
    assert not zuviel, (
        "Diese Dateien nennen Personen, Objekte oder Anbieter:\n  "
        + "\n  ".join(f"{datei}: {zahl}" for datei, zahl in sorted(zuviel.items()))
        + "\n\nErfundene Namen nehmen, die Angabe nach config/ verschieben, oder\n"
          "die Kennung zur Laufzeit von dort lesen -- wie es `_ein_objekt` in\n"
          "tests/test_web.py und `_geteilte_regeln` in tests/test_rules.py tun.")


def test_the_tolerated_remainder_stays_empty():
    """`FUNDSTELLEN` darf nicht wieder wachsen.

    Ohne diesen Test koennte eine neue Fundstelle einfach eingetragen werden,
    und die Sperre waere wieder eine Ratsche.
    """
    assert FUNDSTELLEN == {}, sorted(FUNDSTELLEN)
