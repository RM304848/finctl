"""Worauf die beiden Prognosen stehen -- je Kategorie, nebeneinander.

Zwei Rechnungen, zwei Basen, und keine sagte es. /konten nimmt je Konto den
Median der letzten Monate, mit einer Mindestzahl an Vorkommen, und setzt fuer
das Gehalt den Floor ein. Die Jahresrechnung nimmt die Zwoelfmonatssumme je
Kategorie und fuer das Gehalt den Median. Beides ist gewollt -- die eine fragt
nach dem Dispo, die andere nach dem Vermoegen --, aber eine Differenz, deren
Grund niemand sieht, sieht aus wie ein Fehler oder wird fuer keinen gehalten.

Hier steht je Kategorie: die gemessenen Monate, was /konten daraus macht, was
die Jahresrechnung daraus macht, und warum beide verschieden sind.

NICHT FORTSCHREIBEN wirkt in beiden Rechnungen. Eine Schenkung, die in zwei von
zwoelf Monaten kam, schrieb die Jahresrechnung als 367 im Monat fort -- ein
Einkommen, das es nicht gibt. Die Wahl liegt in
config/prognosebasis_custom.yaml, und dort steht nur, was abgewaehlt ist.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from finctl import overlays
from finctl.forecast import abgleich as _ag

DATEI = "prognosebasis_custom.yaml"
SCHLUESSEL = "nicht_fortschreiben"

KOPF = """# Unter Prognosebasis (/annahmen) abgewaehlte Kategorien.
#
# Eine Kategorie hier wird in KEINER Prognose fortgeschrieben -- weder auf
# /konten noch in der Jahresrechnung (/annahmen, /ziele). Gemessen und im
# Abgleich gezeigt wird sie weiter.
#
# Gedacht fuer Unregelmaessiges, das zufaellig im Messfenster lag: eine
# Schenkung, eine Erstattung. Was wirklich wiederkehrt, gehoert nicht hierher.

"""

#: Mindestens so viele Monate mit Buchungen, sonst gilt eine Kategorie als
#: unregelmaessig -- sofern sie keine Fixkosten sind, die jaehrlich kommen.
REGELMAESSIG_AB = 6


def nicht_fortschreiben() -> set[str]:
    import yaml

    pfad = overlays.CONFIG_DIR / DATEI
    if not pfad.exists():
        return set()
    spec = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    return {str(k) for k in (spec.get(SCHLUESSEL) or [])}


def setzen(kategorie: str, fortschreiben: bool) -> set[str]:
    """Eine Kategorie ab- oder wieder anwaehlen. Gespeichert wird nur die
    Abweichung; ist nichts abgewaehlt, verschwindet die Datei."""
    import yaml

    aus = nicht_fortschreiben()
    if fortschreiben:
        aus.discard(kategorie)
    else:
        aus.add(kategorie)
    pfad = overlays.CONFIG_DIR / DATEI
    if not aus:
        pfad.unlink(missing_ok=True)
        return aus
    pfad.write_text(KOPF + yaml.safe_dump({SCHLUESSEL: sorted(aus)},
                                          allow_unicode=True),
                    encoding="utf-8")
    return aus


def _monate(conn: sqlite3.Connection, f) -> dict[str, dict[str, int]]:
    """Je Kategorie die Monatssummen im Fenster, haushaltsweit."""
    from finctl.forecast import abgleich as ag

    out: dict[str, dict[str, int]] = {}
    for r in conn.execute(f"""
            SELECT COALESCE(s.mgmt_category_id, '') AS kategorie,
                   substr(t.booking_date, 1, 7) AS monat,
                   SUM(s.amount_cents) AS cents
            FROM   splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE  t.booking_date BETWEEN ? AND ?
              AND  COALESCE(s.mgmt_category_id, '') NOT LIKE '{ag.EXCLUDED_PREFIX}%'
            GROUP  BY kategorie, monat""", (f.von.isoformat(), f.bis.isoformat())):
        out.setdefault(r["kategorie"], {})[r["monat"]] = int(r["cents"] or 0)
    return out


def _konten_je_kategorie(conn: sqlite3.Connection) -> tuple[dict[str, int], dict]:
    """Was /konten je Kategorie im Monat fortschreibt, ueber alle Konten.

    Aus DENSELBEN abgeleiteten Posten wie die Kontoprognose. Jahresposten
    zaehlen mit ihrem Monatsanteil, damit sie neben einem Median stehen koennen.
    """

    from finctl import abos as _ab
    from finctl.forecast import konten as ops

    fcfg = ops.forecast_config()
    abos = _ab.alle_vertraege()
    konten = [r["id"] for r in conn.execute(
        "SELECT id FROM accounts WHERE ingest_mode='parsed' AND active=1")]
    summe: dict[str, float] = {}
    for konto in konten:
        for item in ops.abgeleitete_posten(conn, konto, fcfg, abos, abwahl=False):
            if not item.category:
                continue
            summe[item.category] = (summe.get(item.category, 0.0)
                                    + item.amount_cents / (item.every_months or 1))
    overrides = ops._income_overrides(fcfg)
    return {k: round(v) for k, v in summe.items()}, {
        "fcfg": fcfg, "overrides": overrides,
        "abo_kategorien": {a["kategorie"] for a in abos if a.get("kategorie")},
        # Ein Vertrag ohne eine einzige zugeordnete Buchung kann seine Reihe
        # nicht aus der Messung nehmen -- er weiss ja nicht, welche sie ist.
        # Dann steht sein Beitrag zweimal da, und das muss jemand sagen.
        "ohne_zuordnung": {a["kategorie"] for a in abos
                           if a.get("kategorie") and not a["buchungen"]}}


def vergleich(conn: sqlite3.Connection) -> dict:
    """Je Kategorie: gemessen, /konten, Jahresrechnung, Differenz mit Grund."""
    from finctl.forecast import abgleich as ag
    from finctl.forecast import jahre as jm
    from finctl.forecast import szenarien as sz

    f = ag.fenster(conn)
    if f is None:
        return {"fenster": None, "zeilen": [], "monate": []}
    monatsliste = [f"{m.year}-{m.month:02d}" for m in f.monatsliste()]
    gemessen = _monate(conn, f)
    aus = nicht_fortschreiben()

    # Die Jahresrechnung, wie sie rechnet: Basis ohne die Abwahl, damit die
    # Seite auch zeigt, was eine abgewaehlte Kategorie waere.
    geladen = jm._szenarien_laden()
    basis = jm._base_kategorien(conn, f, sz.einmalig_zugeordnet(geladen),
                                auch_abgewaehlte=True)
    block_von = {k: b for b, kat in basis.items() for k in kat}

    konten, kontext = _konten_je_kategorie(conn)
    fcfg = kontext["fcfg"]
    ausgeschlossen_konten = set(fcfg.get("exclude_from_recurring") or [])
    min_vorkommen = int(fcfg.get("min_occurrences", 5))
    fixkosten = {r[0] for r in conn.execute(
        "SELECT id FROM mgmt_categories WHERE fixkosten = 1")}
    namen = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM mgmt_categories")}
    median_gehalt = jm.gehalt_median_cents(conn)

    zeilen = []
    for kategorie in sorted(set(gemessen) | set(konten)):
        werte = gemessen.get(kategorie, {})
        mit_buchung = sum(1 for m in monatsliste if werte.get(m))
        block = block_von.get(kategorie) or _block_fuer(conn, kategorie)
        abgewaehlt = kategorie in aus
        # Jahresrechnung je Monat.
        if block in jm.FROM_BASE:
            jahr = round(basis.get(block, {}).get(kategorie, 0) / 12)
        elif block == "gehalt":
            # Die Jahresrechnung ersetzt den ganzen Block durch EINEN Median,
            # und der misst nur einkommen/gehalt.
            jahr = (median_gehalt or 0) if kategorie == "einkommen/gehalt" else 0
        else:
            jahr = None
        konto = konten.get(kategorie)
        if kategorie in kontext["overrides"]:
            konto = kontext["overrides"][kategorie]
        konto_wirksam, jahr_wirksam = (None, None) if abgewaehlt else (konto, jahr)

        unregelmaessig = (block in jm.FROM_BASE and 0 < mit_buchung < REGELMAESSIG_AB
                          and kategorie not in fixkosten)
        zeilen.append({
            "kategorie": kategorie, "name": namen.get(kategorie, ""),
            "block": block, "werte": [werte.get(m, 0) for m in monatsliste],
            "mit_buchung": mit_buchung,
            "schnitt": round(sum(werte.values()) / f.monate),
            "konten": konto_wirksam, "jahr": jahr_wirksam,
            "konten_roh": konto, "jahr_roh": jahr,
            "delta": ((jahr_wirksam or 0) - (konto_wirksam or 0)),
            "grund": _grund(kategorie, block, konto, jahr, abgewaehlt,
                            ausgeschlossen_konten, min_vorkommen, fixkosten,
                            kontext, fcfg.get("window_months"),
                            str(fcfg.get("variable_kosten") or "") == "schnitt"),
            "unregelmaessig": unregelmaessig, "abgewaehlt": abgewaehlt,
            "abwaehlbar": block in jm.FROM_BASE,
        })
    return {"fenster": f, "monate": monatsliste, "zeilen": zeilen,
            "summe_konten": sum(z["konten"] or 0 for z in zeilen),
            "summe_jahr": sum(z["jahr"] or 0 for z in zeilen),
            "min_vorkommen": min_vorkommen, "fenster_konten": fcfg.get("window_months")}


def _block_fuer(conn: sqlite3.Connection, kategorie: str) -> str | None:
    """Der Block einer Kategorie ohne Buchung im Fenster -- per derselben
    Zuordnung wie im Abgleich."""
    from finctl.forecast import abgleich as ag

    row = conn.execute(
        f"SELECT {ag._case_expression()} AS block FROM (SELECT ? AS mgmt_category_id, "
        "NULL AS property_id) s LEFT JOIN mgmt_categories m ON m.id = s.mgmt_category_id",
        (kategorie,)).fetchone()
    return row["block"] if row else None


def _grund_vertrag(konto, ohne_zuordnung: bool) -> str:
    """Warum eine Kategorie mit Vertrag auf /konten so dasteht, wie sie dasteht.

    Drei Faelle, und zwei davon waren frueher stumm. Ein Vertrag nimmt SEINE
    Reihe aus der Messung, nicht die ganze Kategorie: was daneben liegt --
    eine Erstattung, ein zweiter Anbieter -- bleibt gemessen und gehoert auch
    dorthin. Ohne eine zugeordnete Buchung kennt er seine Reihe aber nicht und
    steht neben ihr statt an ihrer Stelle. Ohne diese Saetze stand die
    Restzahl da, als erklaere sie sich selbst.
    """
    if ohne_zuordnung:
        return ("Vertrag ohne zugeordnete Buchung: zählt doppelt, solange "
                "seine Buchungen im Median stehen — eine genügt")
    if konto is None:
        return "auf /konten als Vertrag mit Termin, nicht als Median"
    return ("Vertrag mit Termin für seine Reihe; was daneben in der "
            "Kategorie liegt, bleibt gemessen")


def _grund(kategorie, block, konto, jahr, abgewaehlt, ausgeschlossen_konten,
           min_vorkommen, fixkosten, kontext, fenster_konten,
           schnitt: bool = False) -> str:
    if abgewaehlt:
        return "abgewählt: in keiner Prognose fortgeschrieben"
    if block == "kredit":
        return "Kreditraten kommen in beiden Rechnungen aus dem Tilgungsplan"
    if block == "investment":
        return "Umschichtung, keine Ausgabe: in beiden Rechnungen nicht fortgeschrieben"
    if block in ("sondereffekt", _ag.OBJEKT):
        return "eigene Quelle: Planung bzw. Annahme, nicht die Messung"
    if kategorie in kontext["overrides"]:
        return "Floor statt Median: /konten rechnet vorsichtig für die Dispo-Frage"
    if block == "gehalt":
        return "Jahresrechnung nimmt nur den Median von einkommen/gehalt"
    if kategorie in ausgeschlossen_konten:
        return "in forecast.yaml ausgeschlossen: /konten schreibt nicht fort"
    if kategorie in kontext["abo_kategorien"]:
        return _grund_vertrag(konto, kategorie in kontext["ohne_zuordnung"])
    if konto is None:
        fenster = f"{fenster_konten} Monaten" if fenster_konten else "dem Fenster"
        return (f"/konten: auf keinem Konto in mindestens {min_vorkommen} von "
                f"{fenster} gebucht, deshalb nicht fortgeschrieben")
    if jahr is None:
        return ""
    if schnitt and kategorie not in fixkosten and (konto or 0) < 0:
        return (f"/konten: Schnitt je Konto über {fenster_konten or 'alle'} Monate; "
                "Jahresrechnung: Zwölfmonatsschnitt")
    return "/konten: Median je Konto über kürzeres Fenster; Jahresrechnung: Zwölfmonatsschnitt"


def monat_text(d: date) -> str:
    return f"{d.month:02d}/{d.year}"
