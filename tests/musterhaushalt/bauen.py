"""Der Musterhaushalt: ein erfundener Datenordner, auf dem die Tests laufen.

WARUM. Die Tests lasen bis hierhin die echten Daten -- `config/` und das
Hauptbuch aus den echten Auszuegen. Auf einem anderen Rechner, in einer
automatischen Pruefung oder in einem Repository ohne `config/` liefen sie
deshalb nicht. Hier steht ein Haushalt, der jede Funktion benutzt, die die
App hat, und von dem nichts echt ist: Konten, Namen, IBANs, Betraege.

WAS ER HAT. `config/` liegt als Datei daneben. Die Auszuege entstehen hier,
als CSV in den Formaten zweier Banken (`ing_csv`, `volksbank_csv`), fuer die
letzten 24 vollen Monate und den laufenden bis gestern. Relativ zu heute,
damit die Daten nicht veralten: Eine Pruefung, die "die letzten zwoelf
Monate" misst, findet sie auch in drei Jahren noch.

Aus demselben Grund stehen in `config/` einige Daten als Marken:
`{J}` ist das laufende Jahr, `{J+1}` das naechste, `{M}` der laufende Monat
als JJJJ-MM, `{M-6}` der sechs Monate davor. Gemeint ist das, was an einen
Stichtag gebunden ist -- ein Stand, der nicht in der Zukunft liegen darf,
ein Termin, der naechstes Jahr faellig ist. Was fest ist, bleibt fest.

    python tests/musterhaushalt/bauen.py ZIEL        # Datenordner in ZIEL bauen

`tests/conftest.py` ruft das auf, wenn die Tests auf dem Musterhaushalt
laufen sollen (FINCTL_TESTDATEN=muster oder kein `config/` im Projekt).
"""

from __future__ import annotations

import calendar
import os
import random
import re
import shutil
import sys
from datetime import date
from pathlib import Path

HIER = Path(__file__).resolve().parent
MONATE = 24

IBAN = {"giro": "DE10100000000000000101", "tagesgeld": "DE10100000000000000102",
        "alltag": "DE20200000000000000201", "ratenkonto": "DE20200000000000000202"}
ORDNER = {"ratenkonto": "rate"}
ANFANG = {"giro": 400000, "tagesgeld": 2500000, "alltag": 30000, "ratenkonto": 52000}
ICH = "Kim Beispiel"


# ------------------------------------------------------------------ Marken

def _monat(heute: date, verschiebung: int) -> date:
    n = heute.year * 12 + heute.month - 1 + verschiebung
    return date(n // 12, n % 12 + 1, 1)


def datieren(text: str, heute: date) -> str:
    """`{J+1}` wird zum Jahr, `{M-6}` zum Monat -- relativ zu heute."""
    def ersetzen(m: re.Match) -> str:
        n = int(m.group(2) or 0)
        if m.group(1) == "J":
            return str(heute.year + n)
        return _monat(heute, n).strftime("%Y-%m")
    return re.sub(r"\{([JM])([+-]\d+)?\}", ersetzen, text)


# ------------------------------------------------------------------ Buchungen

def _tag(monat: date, tag: int) -> date:
    return monat.replace(day=min(tag, calendar.monthrange(monat.year, monat.month)[1]))


def _gehalt(monat: date) -> int:
    return round(360000 * 1.02 ** (monat.year - 2024))


def _giro(monat: date, heute: date, zufall: random.Random) -> list[tuple]:
    """(Tag, Gegenpartei, Buchungstext, Zweck, Betrag) -- das Girokonto."""
    name = monat.strftime("%m/%Y")
    z = [
        (1, "Hausverwaltung Nord", "Dauerauftrag", f"Miete {name}", -115000),
        (1, ICH, "Dauerauftrag", "Umbuchung Alltag", -40000),
        (1, ICH, "Dauerauftrag", "Umbuchung Ratenkonto", -52000),
        (2, "Mieter Nord", "Gutschrift", "Miete Lindenweg 3", 78000),
        (2, "Mieter Sued", "Gutschrift", "Miete Am Hang 9", 52000),
        (3, "Kreditbank", "Lastschrift", "Darlehen 4711000001 Rate", -48000),
        (3, "WEG Lindenweg", "Lastschrift", "Hausgeld", -26000),
        (4, "Beispiel Versicherung AG", "Lastschrift", "BU Beitrag", -6200),
        (4, "Fondspolice Leben AG", "Lastschrift", "Beitrag Police A", -15000),
        (5, "Muster PKV", "Lastschrift", "Beitrag Krankenversicherung", -42000),
        (5, "Stadtwerke Beispielstadt", "Lastschrift", "Abschlag Strom", -5500),
        (6, "Mobilfunk GmbH", "Lastschrift", "Rechnung", -2000),
        (7, "Streamdienst", "Lastschrift", "Monatsabo", -1299),
        (8, "Mitbewohner", "Gutschrift", "Anteil Nebenkosten", 6000),
        (10, ICH, "Dauerauftrag", "Gemeinschaftskonto Haushalt", -30000),
        (15, "Beispielbank Depot", "Dauerauftrag", "Sparplan", -20000),
        (20, ICH, "Dauerauftrag", "Umbuchung Tagesgeld", -70000),
        (28, "Beispiel Werke AG", "Gehalt/Rente", f"Lohn/Gehalt {name}", _gehalt(monat)),
    ]
    for tag, markt in ((8, "REWE Markt"), (15, "EDEKA Center"), (22, "REWE Markt"),
                       (27, "EDEKA Center")):
        z.append((tag, markt, "Kartenzahlung", "Einkauf", -zufall.randint(4500, 9500)))
    z.append((12, "Drogerie Muster", "Kartenzahlung", "Einkauf", -zufall.randint(1500, 3000)))
    z += [(tag, "Kiosk am Markt", "Kartenzahlung", "Einkauf", -zufall.randint(300, 900))
          for tag in (16, 26)]
    if monat.month % 3 == 0:
        z.append((22, "REWE Markt", "Gutschrift", "Erstattung Pfand", 450))
    z += {
        1: [(15, "Kfz Versicherung Muster", "Lastschrift", "Beitrag", -48000)],
        2: [(1, "Haftpflicht Versicherung", "Lastschrift", "Jahresbeitrag", -7200),
            (15, "Stadt Musterort", "Lastschrift", "Grundsteuer Am Hang 9", -9000)],
        3: [(10, "Bundeskasse", "Lastschrift", "Kfz-Steuer", -12000)],
        4: [(28, "Beispiel Werke AG Lohnbuero", "Gehalt/Rente", "Bonus", 150000)],
        5: [(15, "Stadt Musterort", "Lastschrift", "Grundsteuer Am Hang 9", -9000)],
        6: [(20, "Kuestenamt", "Lastschrift", "Pacht Ferienhaus", -30000)],
        7: [(18, "Finanzamt Beispielstadt", "Gutschrift", "Erstattung ESt", 90000)],
        8: [(15, "Stadt Musterort", "Lastschrift", "Grundsteuer Am Hang 9", -9000)],
        11: [(15, "Stadt Musterort", "Lastschrift", "Grundsteuer Am Hang 9", -9000)],
    }.get(monat.month, [])
    if monat == _monat(heute, -1):
        z.append((15, "Beispielbank", "Gutschrift", "Verkauf Wertpapiere", 250000))
    return z


def _tagesgeld(monat: date, heute: date, saldo: int) -> list[tuple]:
    z = [(20, ICH, "Gutschrift", "Umbuchung Tagesgeld", 70000)]
    if monat in (_monat(heute, -12), _monat(heute, -6)):
        z.append((12, "Bautraeger Kueste", "Ueberweisung", "Anzahlung Ferienhaus", -800000))
    z.append((31, "Beispielbank", "Abschluss", "Zinsgutschrift", round(saldo * 0.02 / 12)))
    return z


def _alltag(monat: date, heute: date, zufall: random.Random) -> list[tuple]:
    z = [(1, ICH, "Dauerauftrag", "Umbuchung Alltag", 40000, IBAN["giro"])]
    z += [(tag, "Tankstelle Nord", "Kartenzahlung", "Kraftstoff",
           -zufall.randint(5000, 7500), "") for tag in (9, 23)]
    z += [(tag, ort, "Kartenzahlung", "Rechnung", -zufall.randint(2000, 4500), "")
          for tag, ort in ((6, "Trattoria Beispiel"), (14, "Cafe Muster"),
                           (19, "Trattoria Beispiel"))]
    z.append((17, "Buchladen Muster", "Kartenzahlung", "Buecher", -zufall.randint(1500, 2500), ""))
    if monat < _monat(heute, -6):
        z.append((25, "Gemeinschaftskonto", "Ueberweisung", "Nachschuss",
                  -zufall.randint(10000, 20000), ""))
    if monat.month == 11:
        z.append((11, "Haendler Online", "Kartenzahlung", "Office-Paket", -6900, ""))
    if monat.month == 3:
        z.append((10, "Videodienst", "Lastschrift", "Jahresabo", -9999, ""))
    return z


def _ratenkonto(monat: date) -> list[tuple]:
    return [(1, ICH, "Dauerauftragsgutschrift", "Umbuchung Ratenkonto", 52000, IBAN["giro"]),
            (30, "Bausparkasse", "Lastschrift", "Darlehen 001 Rate", -52000, "")]


# ------------------------------------------------------------------ Formate

def _de(cents: int) -> str:
    vor = "-" if cents < 0 else ""
    euro, cent = divmod(abs(cents), 100)
    return f"{vor}{euro:,}".replace(",", ".") + f",{cent:02d}"


def _ing(konto: str, name: str, zeilen: list[tuple], saldo: int) -> tuple[str, int]:
    kopf = ["Umsatzanzeige;Datei erstellt am: 01.01.2000 08:00", "",
            f"IBAN;{IBAN[konto]}", f"Kontoname;{name}", "Bank;Beispielbank",
            f"Kunde;{ICH}", "",
            ("Buchung;Wertstellungsdatum;Auftraggeber/Empfänger;"
             "Buchungstext;Verwendungszweck;Saldo;Währung;Betrag;Währung")]
    rumpf = []
    for tag, gegen, art, zweck, betrag in zeilen:
        saldo += betrag
        d = tag.strftime("%d.%m.%Y")
        rumpf.append(f"{d};{d};{gegen};{art};{zweck};{_de(saldo)};EUR;{_de(betrag)};EUR")
    return "\n".join(kopf + rumpf) + "\n", saldo


def _volksbank(konto: str, zeilen: list[tuple], saldo: int) -> tuple[str, int]:
    kopf = ("Bezeichnung Auftragskonto;IBAN Auftragskonto;BIC Auftragskonto;"
            "Bankname Auftragskonto;Buchungstag;Valutadatum;Name Zahlungsbeteiligter;"
            "IBAN Zahlungsbeteiligter;BIC (SWIFT-Code) Zahlungsbeteiligter;Buchungstext;"
            "Verwendungszweck;Betrag;Waehrung;Saldo nach Buchung;Bemerkung;Kategorie;"
            "Steuerrelevant;Glaeubiger ID;Mandatsreferenz")
    rumpf = []
    for tag, gegen, art, zweck, betrag, iban in zeilen:
        saldo += betrag
        d = tag.strftime("%d.%m.%Y")
        rumpf.append(f"Girokonto;{IBAN[konto]};GENODEF1XXX;Musterbank eG;{d};{d};{gegen};"
                     f"{iban};;{art};{zweck};{_de(betrag)};EUR;{_de(saldo)};;;;;")
    return "\n".join([kopf, *rumpf]) + "\n", saldo


def auszuege(ordner: Path, heute: date) -> None:
    """Je Konto und Monat ein Auszug, vom ersten der 24 Monate bis gestern."""
    saldo = dict(ANFANG)
    for n in range(-MONATE, 1):
        monat = _monat(heute, n)
        zufall = random.Random(monat.toordinal())
        roh = {
            "giro": _giro(monat, heute, zufall),
            "tagesgeld": _tagesgeld(monat, heute, saldo["tagesgeld"]),
            "alltag": _alltag(monat, heute, zufall),
            "ratenkonto": _ratenkonto(monat),
        }
        for konto, zeilen in roh.items():
            datiert = sorted(((_tag(monat, z[0]), *z[1:]) for z in zeilen),
                             key=lambda z: z[0])
            datiert = [z for z in datiert if z[0] < heute]
            if not datiert:
                continue
            if konto in ("giro", "tagesgeld"):
                text, saldo[konto] = _ing(konto, konto.capitalize(), datiert, saldo[konto])
            else:
                text, saldo[konto] = _volksbank(konto, datiert, saldo[konto])
            ziel = ordner / ORDNER.get(konto, konto)
            ziel.mkdir(parents=True, exist_ok=True)
            (ziel / f"{monat:%Y-%m}.csv").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ Bauen

def dateien(ziel: Path, heute: date) -> None:
    """`config/` mit eingesetzten Daten und die Auszuege nach `ziel`."""
    (ziel / "config").mkdir(parents=True, exist_ok=True)
    for datei in sorted((HIER / "config").glob("*.yaml")):
        text = datieren(datei.read_text(encoding="utf-8"), heute)
        (ziel / "config" / datei.name).write_text(text, encoding="utf-8")
    auszuege(ziel / "data" / "statements", heute)


def bauen(ziel: Path, heute: date | None = None) -> dict:
    """Datenordner samt Hauptbuch. Im eigenen Prozess: die Pfade werden beim
    ersten `import finctl` gelesen, und FINCTL_DATEN muss dann schon stehen."""
    heute = heute or date.today()
    if os.environ.get("FINCTL_DATEN") != str(ziel):
        raise SystemExit("FINCTL_DATEN muss auf das Ziel zeigen")
    dateien(ziel, heute)
    from typer.testing import CliRunner

    from finctl import ops
    from finctl.cli import app
    from finctl.ledger import db as ledger
    from finctl.pfade import DB_PATH

    ergebnis = CliRunner().invoke(app, ["init", "--json"])
    if ergebnis.exit_code:
        raise SystemExit(f"init scheiterte:\n{ergebnis.output}")
    conn = ledger.connect(DB_PATH)
    try:
        bericht = ops.update(conn)
        conn.commit()
    finally:
        conn.close()
    return bericht


def main() -> None:
    ziel = Path(sys.argv[1]).resolve()
    if ziel.exists() and any(ziel.iterdir()):
        shutil.rmtree(ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    os.environ["FINCTL_DATEN"] = str(ziel)
    sys.path.insert(0, str(HIER.parent.parent))
    bericht = bauen(ziel)
    if not bericht["ok"]:
        raise SystemExit(f"Musterhaushalt nicht sauber: {bericht['problems']}")
    print(f"Musterhaushalt in {ziel}: {bericht['ingest']['imported']} Auszuege, "
          f"{bericht['categorize']['matched']} Buchungen zugeordnet")


if __name__ == "__main__":
    main()

