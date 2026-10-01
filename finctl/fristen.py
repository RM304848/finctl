"""Was einen Termin hat, an dem man handeln muss -- und ab wann es "bald" ist.

Der Monatsabschluss prueft, ob die Staende aktuell sind. Was als Naechstes
KOMMT, stand nirgends: ein Jahresbeitrag, der im November abgeht, ein Vorrat,
der endet, eine Zinsbindung, fuer die man ein Jahr vorher Angebote einholt.
Die Daten dazu liegen laengst in den Vertraegen und Krediten.

JE ART EIN VORLAUF. Ab `datum - vorlauf` ist eine Frist "bald faellig" und
steht auf dem Monatsabschluss; in den Kalender geht sie als ganztaegiger
Termin an genau diesem Tag, die eigentliche Frist im Titel. Ein Termin am
Fristtag selbst kaeme zu spaet, um noch etwas zu entscheiden.

KEIN LINK ZU EINEM KALENDERDIENST. Eine .ics-Datei bleibt auf dem Rechner,
bis man sie selbst oeffnet; ein Link zu Google Kalender schickte Namen und
Betraege beim Klick an Google.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from finctl.kalender import plus_monate
from finctl.ledger.db import format_eur

#: Tage zwischen "bald faellig" und der Frist, je Art.
VORLAUF = {
    # Geld bereithalten -- oder kuendigen, solange es noch geht.
    "zahlung": 30,
    # Verlaengern, auf monatlich gehen oder kuendigen.
    "vorrat": 30,
    # Angebote vergleichen: ein Forward-Darlehen braucht Vorlauf, eine
    # Prolongation kommt sonst als einziges Angebot drei Monate vorher.
    "zinsbindung": 365,
}

#: Wie weit Zahlungen in den Kalender gehen. Danach hat sich der Vertrag
#: vermutlich geaendert, und die Datei waere veraltet, bevor sie zaehlt.
HORIZONT_MONATE = 12

#: Monatliches gehoert zum Alltag und kommt in keine Frist. Ab vierteljaehrlich
#: ist eine Zahlung ein Brocken, den man vergisst.
TAKT_AB = 3

PRODID = "-//finctl//Fristen//DE"


@dataclass(frozen=True, slots=True)
class Frist:
    uid: str
    art: str
    name: str
    #: Der Tag, an dem es passiert: Abbuchung, Vorratsende, Ende der Bindung.
    datum: date
    #: Was zu tun ist, in einem Satz.
    tun: str
    #: Wo man es tut.
    ziel: str
    cents: int | None = None

    @property
    def ab(self) -> date:
        return self.datum - timedelta(days=VORLAUF[self.art])

    def bald(self, heute: date) -> bool:
        return self.ab <= heute <= self.datum

    @property
    def titel(self) -> str:
        was = {"zahlung": "fällig", "vorrat": "Vorrat endet",
               "zinsbindung": "Zinsbindung endet"}[self.art]
        betrag = f" ({format_eur(self.cents)})" if self.cents is not None else ""
        return f"{self.name}: {was} am {self.datum.isoformat()}{betrag}"


def aus_vertraegen(vertraege: list[dict], heute: date) -> list[Frist]:
    """Zahlungen ab vierteljaehrlich im naechsten Jahr, und jedes Vorratsende."""
    from finctl import abos as _ab

    bis = plus_monate(heute, HORIZONT_MONATE)
    out = []
    for a in vertraege:
        reiter = "versicherungen" if a.get("art") == "versicherung" else "abos"
        ziel = f"/vertraege?ansicht={reiter}#abo-{a['id']}"
        if a["takt"] >= TAKT_AB:
            out += [Frist(uid=f"zahlung-{a['id']}-{p['faellig']:%Y%m%d}", art="zahlung",
                          name=a["name"], datum=p["faellig"], cents=p["betrag_cents"],
                          tun="Geld bereithalten oder rechtzeitig kündigen", ziel=ziel)
                    for p in _ab.posten([a], a["konto"], ab=heute, bis=bis)
                    # Die Zahlung am Ende eines Vorrats IST die Verlaengerung;
                    # sie steht als Vorratsende da, nicht zweimal.
                    if p["faellig"] != a["vorrat_bis"]]
        if a["vorrat_bis"] and a["vorrat_bis"] >= heute:
            out.append(Frist(uid=f"vorrat-{a['id']}", art="vorrat", name=a["name"],
                             datum=a["vorrat_bis"], cents=a["betrag_cents"],
                             tun="Verlängern, auf laufende Zahlung gehen oder kündigen",
                             ziel=ziel))
    return out


def aus_krediten(kredite: list[dict], heute: date, eingetragen: set[str]) -> list[Frist]:
    """Das Ende jeder Zinsbindung eines laufenden Kredits.

    `eingetragen`: Kredite mit einer Anschlusskondition aus /kredite. Steht
    eine Folge schon in den Segmenten (ein dokumentiertes Angebot), zaehlt
    das genauso: zu tun bleibt dann das Unterschreiben, nicht das Suchen.
    """
    out = []
    for k in kredite:
        if k.get("szenario") or (k.get("status") or "active") != "active":
            continue
        roh = k.get("zinsbindung_ende") or k.get("zinsbindung_end")
        if not roh:
            continue
        bindung = roh if isinstance(roh, date) else date.fromisoformat(str(roh))
        if bindung < heute:
            continue
        folge = str(k["id"]) in eingetragen or any(
            date.fromisoformat(str(s["start"])) > bindung for s in k.get("segments") or [])
        out.append(Frist(
            uid=f"zinsbindung-{k['id']}", art="zinsbindung", name=str(k.get("name") or k["id"]),
            datum=bindung, ziel=f"/kredite#kredit-{k['id']}",
            tun=("Anschluss steht — rechtzeitig unterschreiben" if folge
                 else "Angebote für den Anschluss einholen")))
    return out


def alle(heute: date) -> list[Frist]:
    """Alle Fristen aus den eingeschalteten Modulen, nach Datum."""
    from finctl import module as _mod

    out: list[Frist] = []
    if _mod.seite_an("/vertraege"):
        from finctl import abos as _ab

        # Eine kaputte Vertragsdatei zeigt /vertraege an der Stelle, wo sie
        # zu beheben ist; hier fehlen dann nur ihre Fristen.
        with contextlib.suppress(ValueError):
            out += aus_vertraegen(_ab.alle_vertraege(), heute)
    if _mod.seite_an("/kredite"):
        import yaml

        from finctl.pfade import CONFIG_DIR
        from finctl.realestate.loan import lade_kredite

        custom = CONFIG_DIR / "loans_custom.yaml"
        roh = (yaml.safe_load(custom.read_text(encoding="utf-8")) or {}) if custom.exists() else {}
        eingetragen = {str(k) for k in roh.get("anschluss") or {}}
        out += aus_krediten(lade_kredite(CONFIG_DIR), heute, eingetragen)
    return sorted(out, key=lambda f: (f.datum, f.name))


# ------------------------------------------------------------------ .ics

def ics(fristen: list[Frist], heute: date, jetzt: datetime | None = None) -> bytes:
    """Ein Kalender (RFC 5545): je Frist ein ganztaegiger Termin am Tag des Handelns.

    Liegt der schon zurueck, steht der Termin heute -- ein Termin in der
    Vergangenheit erinnert an nichts. Die UID bleibt je Frist gleich, damit
    ein zweiter Import ersetzt statt verdoppelt. Google uebergeht Alarme in
    importierten Dateien, deshalb ist der Termin selbst die Erinnerung;
    Apple und Outlook melden sich zusaetzlich um 9 Uhr.
    """
    stempel = (jetzt or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    zeilen = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
              "METHOD:PUBLISH"]
    for f in fristen:
        tag = max(f.ab, heute)
        beschreibung = f"{f.tun}.\nFrist: {f.datum.isoformat()}"
        if f.cents is not None:
            beschreibung += f"\nBetrag: {format_eur(f.cents)}"
        zeilen += [
            "BEGIN:VEVENT",
            f"UID:{f.uid}@finctl.local",
            f"DTSTAMP:{stempel}",
            f"DTSTART;VALUE=DATE:{tag:%Y%m%d}",
            f"DTEND;VALUE=DATE:{tag + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_maskieren(f.titel)}",
            f"DESCRIPTION:{_maskieren(beschreibung)}",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_maskieren(f.titel)}",
            "TRIGGER:PT9H",
            "END:VALARM",
            "END:VEVENT",
        ]
    zeilen.append("END:VCALENDAR")
    return "".join(_falten(z) + "\r\n" for z in zeilen).encode("utf-8")


def _maskieren(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\n", "\\n"))


def _falten(zeile: str) -> str:
    """Ueber 75 Bytes geht es nach CRLF und Leerzeichen weiter, nie mitten in einem Zeichen."""
    teile, aktuell, groesse = [], "", 0
    for ch in zeile:
        n = len(ch.encode("utf-8"))
        if groesse + n > (75 if not teile else 74):
            teile.append(aktuell)
            aktuell, groesse = "", 0
        aktuell += ch
        groesse += n
    teile.append(aktuell)
    return "\r\n ".join(teile)
