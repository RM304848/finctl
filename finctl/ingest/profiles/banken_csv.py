"""Die CSV-Exporte der gaengigen deutschen Banken, als Beschreibung.

Jeder Eintrag sagt, woran der Export zu erkennen ist und welche Spalte was
bedeutet (`finctl/ingest/csvbank.py`). Die Aufbauten stammen aus der Hilfe
der Banken und oeffentlichen Beschreibungen der Exporte -- NICHT aus einem
echten Export, der hier gelesen worden waere. `quelle` sagt das je Bank.
Weicht ein echter Export ab, erkennt ihn das Profil nicht, und die
Spaltenzuordnung in der Einrichtung faengt ihn auf; die Korrektur gehoert
dann hierher, mit einem anonymisierten Beispiel unter tests/beispiele/.

Die Beispieldateien der Goldtests (tests/beispiele/banken/) sind erfunden und
folgen genau dieser Beschreibung.
"""

from __future__ import annotations

from finctl.ingest.csvbank import CsvBankParser, CsvProfil

_NACH_DOKU = "nach Beschreibung des Exports, nicht an einem echten Export geprüft"
_DE_BETRAG = r"(?P<betrag>-?[\d.]+,\d{2})"

PROFILE: tuple[CsvProfil, ...] = (
    CsvProfil(
        id="ing_csv", bank="ING",
        erkennung=("Buchung", "Wertstellungsdatum", "Auftraggeber/Empfänger", "Saldo", "Betrag"),
        datum="Buchung", wertstellung="Wertstellungsdatum", betrag="Betrag",
        gegenpartei=("Auftraggeber/Empfänger",), zweck=("Verwendungszweck",),
        buchungstext="Buchungstext", saldo="spalte", saldospalte="Saldo",
        konto_kopf=r"^IBAN;(?P<iban>DE[\d ]{20,26})", quelle=_NACH_DOKU),
    CsvProfil(
        id="comdirect_csv", bank="comdirect",
        erkennung=("Buchungstag", "Wertstellung (Valuta)", "Vorgang", "Umsatz in EUR"),
        datum="Buchungstag", wertstellung="Wertstellung (Valuta)", betrag="Umsatz in EUR",
        zweck=("Buchungstext",), buchungstext="Vorgang", datum_offen="offen",
        saldo="kopf", saldo_anfang=r'Alter Kontostand"?;"?' + _DE_BETRAG,
        saldo_ende=r'Neuer Kontostand"?;"?' + _DE_BETRAG, quelle=_NACH_DOKU),
    CsvProfil(
        id="dkb_csv", bank="DKB",
        erkennung=("Buchungsdatum", "Wertstellung", "Status", "Zahlungspflichtige*r",
                   "Zahlungsempfänger*in", "Betrag (€)"),
        datum="Buchungsdatum", datumsformat="%d.%m.%y|%d.%m.%Y", wertstellung="Wertstellung",
        betrag="Betrag (€)", gegenpartei=("Zahlungsempfänger*in",),
        gegenpartei_eingang=("Zahlungspflichtige*r",), zweck=("Verwendungszweck",),
        iban="IBAN", buchungstext="Umsatztyp", nur_wenn=("Status", "Gebucht"),
        nur_suchtext=("Gläubiger-ID", "Mandatsreferenz", "Kundenreferenz"),
        # Der Kontostand ist der vom Tag des Exports, nicht der vom Ende des
        # Zeitraums: nur ein Export bis heute laesst sich abstimmen. Dieser
        # Tag ist noch offen; der Auszug endet am Vortag.
        saldo="ende", saldo_ende=r'Kontostand vom (?P<datum>[\d.]+):?"?;"?' + _DE_BETRAG,
        letzter_tag_offen=True,
        zeitraum=r'^"?Zeitraum:?"?;"?(?P<von>[\d.]{10})\s*-\s*(?P<bis>[\d.]{10})',
        konto_kopf=r'^"?(?:Giro)?[Kk]onto"?;"?[^"\n]*?(?P<iban>DE[\d ]{20,26})',
        quelle="an einem echten Export geprüft (09/2026)"),
    CsvProfil(
        id="sparkasse_csv", bank="Sparkasse",
        erkennung=("Auftragskonto", "Buchungstag", "Valutadatum", "Buchungstext",
                   "Beguenstigter/Zahlungspflichtiger", "Betrag"),
        datum="Buchungstag", datumsformat="%d.%m.%y|%d.%m.%Y", wertstellung="Valutadatum",
        betrag="Betrag", gegenpartei=("Beguenstigter/Zahlungspflichtiger",),
        zweck=("Verwendungszweck",), iban="Kontonummer/IBAN", buchungstext="Buchungstext",
        nur_wenn=("Info", "Umsatz gebucht"), konto_spalte="Auftragskonto",
        saldo="keiner", quelle=_NACH_DOKU + " (CSV-CAMT)"),
    CsvProfil(
        id="volksbank_csv", bank="Volks- und Raiffeisenbanken",
        erkennung=("IBAN Auftragskonto", "Buchungstag", "Name Zahlungsbeteiligter",
                   "Betrag", "Saldo nach Buchung"),
        datum="Buchungstag", wertstellung="Valutadatum", betrag="Betrag",
        gegenpartei=("Name Zahlungsbeteiligter",), zweck=("Verwendungszweck",),
        iban="IBAN Zahlungsbeteiligter", buchungstext="Buchungstext",
        saldo="spalte", saldospalte="Saldo nach Buchung", konto_spalte="IBAN Auftragskonto",
        quelle="an einem echten Export geprüft (Atruvia-Export einer Sparda-Bank, 10/2026)"),
    CsvProfil(
        id="commerzbank_csv", bank="Commerzbank",
        erkennung=("Buchungstag", "Wertstellung", "Umsatzart", "Buchungstext", "Betrag",
                   "Währung"),
        datum="Buchungstag", wertstellung="Wertstellung", betrag="Betrag",
        zweck=("Buchungstext",), buchungstext="Umsatzart", iban="IBAN Auftraggeberkonto",
        saldo="keiner", quelle=_NACH_DOKU),
    CsvProfil(
        id="n26_csv", bank="N26",
        erkennung=("Booking Date", "Value Date", "Partner Name", "Amount (EUR)"),
        trenner=",", dezimal=".", datum="Booking Date", datumsformat="%Y-%m-%d",
        wertstellung="Value Date", betrag="Amount (EUR)", gegenpartei=("Partner Name",),
        zweck=("Payment Reference",), iban="Partner Iban", buchungstext="Type",
        saldo="keiner", quelle=_NACH_DOKU),
)

PARSERS = {p.id: CsvBankParser(p) for p in PROFILE}
