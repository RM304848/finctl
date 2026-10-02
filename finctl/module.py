"""Welche Teile es gibt, was sie brauchen, und welche eingeschaltet sind.

DER GRUNDGEDANKE: Wer mit Finanzen nicht gut kann, braucht zuerst nur eines --
sehen, wohin das Geld ging. Auszuege einlesen, zuordnen, zurueckblicken, und
das gesichert. Das ist die **Basis**, und sie ist immer an. Alles andere --
Vertraege, Energie, Geteiltes, Kredite, Immobilien, Steuer, Prognose, Ziele --
ist ein **Modul**, das man in der Einrichtung dazuschaltet, wenn man es
braucht. Eine App mit dreissig Seiten am ersten Tag ist keine Hilfe.

JE MODUL steht hier: welche Seiten ihm gehoeren, welche Dateien in `finctl/`,
was es **benoetigt** (ohne das geht es nicht; wird mit eingeschaltet) und was
es **nutzt** (rechnet mit, wenn vorhanden; geht auch ohne). Die Pruefung in
`tests/test_module.py` haelt die Dateien daran: ein Modul importiert nur den
Kern, die Basis und was hier bei ihm steht.

AUS HEISST AUSGEBLENDET, NICHT GELOESCHT. Seiten und Navigation verschwinden;
was in `config/` eingetragen ist, bleibt liegen und ist nach dem Einschalten
wieder da. Die Einrichtung sagt dazu, wenn ein ausgeschaltetes Modul noch
Eintraege hat -- sie rechnen dort, wo sie genutzt werden, weiter mit.

WO DER SCHALTER STEHT: `config/module_custom.yaml`. Fehlt die Datei, ist
alles an -- so bleibt eine bestehende Einrichtung, wie sie war. Eine neue
Einrichtung (noch kein Konto) bekommt beim ersten `init` nur die Basis.
"""

from __future__ import annotations

from dataclasses import dataclass

from finctl import overlays

DATEI = "module_custom.yaml"
SCHLUESSEL = "module"

KOPF = """# Welche Module eingeschaltet sind -- geschrieben von /einrichtung.
#
# Die Basis (Konten, Auszuege einlesen, Kategorien und Regeln, Rueckblick)
# ist immer an und steht hier nicht. Ausschalten blendet ein Modul aus und
# loescht nichts: seine Eintraege in config/ bleiben liegen.

"""


@dataclass(frozen=True, slots=True)
class Modul:
    id: str
    name: str
    #: Die Frage, die das Modul beantwortet -- fuer die Einrichtung.
    frage: str
    seiten: tuple[str, ...] = ()
    #: Pfade unter finctl/, Ordner mit abschliessendem "/".
    dateien: tuple[str, ...] = ()
    benoetigt: tuple[str, ...] = ()
    nutzt: tuple[str, ...] = ()
    basis: bool = False
    #: Dateien in config/, an denen man sieht, ob etwas eingetragen ist.
    eintraege: tuple[str, ...] = ()


#: Immer da, gehoert keinem Modul. Darf nur sich selbst importieren.
KERN = ("finctl/__init__.py", "finctl/pfade.py", "finctl/overlays.py",
        "finctl/kalender.py", "finctl/module.py", "finctl/ledger/",
        "finctl/vorgaben/", "finctl/migrate/", "finctl/recurring/")

#: Die Oberflaeche und die Ablaeufe darueber. Darf alles importieren.
APP = ("finctl/cli.py", "finctl/ops.py", "finctl/monatsabschluss.py", "finctl/starter.py",
       "finctl/menueleiste.py",
       "finctl/web/")

#: Seiten, die keinem Modul gehoeren.
APP_SEITEN = ("/", "/monatsabschluss", "/einrichtung", "/handbuch", "/healthz", "/login")

MODULE: tuple[Modul, ...] = (
    # ------------------------------------------------------------ Basis
    Modul("konten", "Konten", "Welche Konten es gibt und wie ihre Auszüge gelesen werden.",
          seiten=("/kontenregister",),
          dateien=("finctl/konten.py", "finctl/kontenregeln.py"), basis=True),
    Modul("einlesen", "Auszüge einlesen", "Was auf den Kontoauszügen steht.",
          dateien=("finctl/ingest/",), basis=True),
    Modul("regeln", "Kategorien und Regeln", "Wofür das Geld ausgegeben wurde.",
          seiten=("/regeln", "/kategorien", "/review", "/transactions", "/flagged", "/tx"),
          dateien=("finctl/rules/", "finctl/tax/"), basis=True),
    Modul("auswertung", "Rückblick", "Wie sich die Ausgaben gegenüber dem Vorjahr entwickeln.",
          seiten=("/rueckblick", "/report", "/monitor"), dateien=("finctl/reports.py",),
          basis=True),
    # ------------------------------------------------------------ Module
    Modul("vertraege", "Verträge und Abos",
          "Was regelmäßig abgeht, wann, und bis wann es läuft.",
          seiten=("/vertraege", "/abos", "/versicherungen"), dateien=("finctl/abos.py",),
          eintraege=("abos.yaml", "abos_custom.yaml", "versicherungen.yaml",
                     "versicherungen_custom.yaml")),
    Modul("energie", "Energie",
          "Ob die Abschläge für Strom, Gas und Wasser reichen und wie lange der Vorrat hält.",
          seiten=("/energie", "/strom"), dateien=("finctl/strom.py", "finctl/energie/"),
          eintraege=("strom.yaml", "energie.yaml")),
    Modul("geteilt", "Geteilte Ausgaben", "Wer bei gemeinsamen Ausgaben wem was schuldet.",
          seiten=("/projekte", "/salden", "/geteilt"), dateien=("finctl/geteilt.py",),
          eintraege=("geteilt_custom.yaml",)),
    Modul("kredite", "Kredite", "Wie Kredite getilgt werden und was sie kosten.",
          seiten=("/kredite",), dateien=("finctl/kredite.py", "finctl/realestate/loan.py"),
          eintraege=("loans.yaml", "loans_custom.yaml")),
    Modul("immobilien", "Immobilien", "Was ein Objekt kostet, abwirft und abschreibt.",
          seiten=("/immobilien", "/immobilie"),
          dateien=("finctl/objekte.py", "finctl/realestate/__init__.py",
                   "finctl/realestate/kpi.py"),
          nutzt=("kredite",), eintraege=("properties.yaml", "properties_custom.yaml")),
    Modul("steuer", "Steuer", "Was in die Steuererklärung gehört.",
          seiten=("/steuer",), nutzt=("immobilien",)),
    Modul("prognose", "Prognose und Planung",
          "Ob die Konten reichen und was sich in den nächsten Jahren anhäuft.",
          seiten=("/konten", "/planung", "/annahmen", "/hochrechnung", "/prognosebasis",
                  "/bestaende", "/renten"),
          dateien=("finctl/forecast/", "finctl/assumptions.py", "finctl/bestaende.py",
                   "finctl/person.py", "finctl/renten.py"),
          nutzt=("vertraege", "kredite", "immobilien"),
          eintraege=("szenarien.yaml", "balances_custom.yaml", "renten_custom.yaml")),
    Modul("ziele", "Ziele", "Ob du bis zu einem Stichtag ankommst.",
          seiten=("/ziele",), dateien=("finctl/forecast/ziele.py",),
          benoetigt=("prognose",), eintraege=("goals.yaml", "ziele_custom.yaml")),
)

NACH_ID = {m.id: m for m in MODULE}


# ------------------------------------------------------------------ Schalter

def _schalter() -> dict[str, bool] | None:
    """Was in der Datei steht. None, wenn es sie nicht gibt: dann ist alles an."""
    if not (overlays.CONFIG_DIR / DATEI).exists():
        return None
    return {str(k): bool(v) for k, v in overlays.lesen(DATEI, SCHLUESSEL).items()}


def aktive() -> set[str]:
    """Die eingeschalteten Module, Basis eingeschlossen.

    Ein Modul, das die Datei nicht nennt, ist an: so taucht ein spaeter
    hinzugekommenes Modul auf, statt unbemerkt zu fehlen. Und was ein
    eingeschaltetes Modul benoetigt, ist an, egal was in der Datei steht --
    Ziele ohne Prognose gibt es nicht.
    """
    schalter = _schalter()
    an = {m.id for m in MODULE if m.basis or schalter is None or schalter.get(m.id, True)}
    return _mit_benoetigtem(an)


def _mit_benoetigtem(an: set[str]) -> set[str]:
    offen = list(an)
    while offen:
        for b in NACH_ID[offen.pop()].benoetigt:
            if b not in an:
                an.add(b)
                offen.append(b)
    return an


def setzen(wahl: dict[str, bool]) -> set[str]:
    """Module ein- und ausschalten; gibt die danach aktiven zurueck.

    Einschalten nimmt mit, was benoetigt wird. Ausschalten nimmt mit, was
    davon abhaengt -- Prognose aus heisst Ziele aus. Die Basis laesst sich
    nicht ausschalten und wird gar nicht erst geschrieben.
    """
    an = aktive()
    for mid, wert in wahl.items():
        m = NACH_ID.get(mid)
        if m is None or m.basis:
            continue
        if wert:
            an.add(mid)
        else:
            an.discard(mid)
            an -= abhaengige(mid)
    an = _mit_benoetigtem(an)
    overlays.schreiben(DATEI, SCHLUESSEL,
                       {m.id: m.id in an for m in MODULE if not m.basis}, KOPF)
    return an


def abhaengige(mid: str) -> set[str]:
    """Was nicht mehr geht, wenn `mid` aus ist -- auch ueber mehrere Stufen."""
    raus: set[str] = set()
    for m in MODULE:
        if mid in m.benoetigt:
            raus |= {m.id} | abhaengige(m.id)
    return raus


def erststart(config_dir) -> bool:
    """Eine neue Einrichtung beginnt mit der Basis. True, wenn geschrieben.

    Nur wenn es noch keine Schalterdatei UND noch kein Konto gibt: eine
    bestehende Einrichtung ohne Datei behaelt alles, wie es war.
    """
    if (config_dir / DATEI).exists():
        return False
    if any((config_dir / n).exists() for n in ("accounts.yaml", "accounts_custom.yaml")):
        return False
    overlays.schreiben(DATEI, SCHLUESSEL,
                       {m.id: False for m in MODULE if not m.basis}, KOPF)
    return True


# ------------------------------------------------------------------ Seiten

def modul_der_seite(pfad: str) -> Modul | None:
    for m in MODULE:
        for s in m.seiten:
            if pfad == s or pfad.startswith(s + "/"):
                return m
    return None


def seite_an(*pfade: str, aktiv: set[str] | None = None) -> bool:
    """Ob mindestens eine der Seiten zu sehen ist -- fuer Navigation und Tuer."""
    aktiv = aktive() if aktiv is None else aktiv
    for pfad in pfade:
        m = modul_der_seite(pfad.split("?")[0])
        if m is None or m.id in aktiv:
            return True
    return False


def eingetragen(m: Modul) -> list[str]:
    """Welche seiner Dateien in config/ etwas enthalten -- fuer den Hinweis beim Ausschalten."""
    return [n for n in m.eintraege
            if (overlays.CONFIG_DIR / n).exists() and (overlays.CONFIG_DIR / n).stat().st_size > 0]
