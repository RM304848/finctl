"""Was am Monatsanfang zu tun ist -- und was davon das Werkzeug selbst weiss.

Zwei Sorten Zeile, und der Unterschied ist der ganze Punkt:

* Wo der Ledger es BEWEISEN kann, hakt die Seite selbst ab. Ein geparstes
  Konto ist aktuell, wenn ein Auszug den letzten abgeschlossenen Monat
  abdeckt; das steht in `statements` und braucht weder Haken noch
  Ehrlichkeit.
* Wo nur ein getippter Stand existiert -- Depots, Krypto, Renten -- zaehlt
  das DATUM dieses Standes. Stammt er aus der laufenden Periode, ist die
  Zeile erledigt.

KEIN Haken zum Anklicken, und das ist eine bewusste Entscheidung des
Eigentuemers: "ich will das nicht checken, ich will einfach ablegen". Ein
Haken ist eine Behauptung ueber die Daten, die neben den Daten liegt und
falsch werden kann, ohne dass es auffaellt -- abgehakt und trotzdem veraltet
ist genau der Zustand, den diese Seite verhindern soll.

Hat sich ein Wert nicht geaendert, wird er trotzdem abgelegt: derselbe Betrag
mit heutigem Datum. Ein Klick, und danach steht in den Daten, was der Haken
nur behauptet haette.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from finctl.pfade import CONFIG_DIR


@dataclass(slots=True)
class Posten:
    id: str
    name: str
    gruppe: str                 # konten | vermoegen | jaehrlich | renten
    periode: str                # 2026-09 oder 2026
    stand: date | None = None
    beleg: str = ""             # woher der Haken kommt, wenn automatisch
    hinweis: str = ""           # was fehlt, wenn nicht
    zusatz: str = ""            # Nebeninformation, keine eigene Aufgabe
    schluessel: str = ""        # wie /api/bestand die Position nennt
    ziel: str = ""              # wohin die Zeile fuehrt
    art: str = ""               # kind aus balances.yaml: depot, krypto, giro, ...
    cents: int | None = None
    automatisch: bool = False
    erledigt: bool = False


@dataclass(slots=True)
class Abschluss:
    periode: str
    jahr: str
    posten: list[Posten] = field(default_factory=list)
    schritte: list[dict] = field(default_factory=list)

    @property
    def offen(self) -> int:
        return sum(1 for p in self.posten if not p.erledigt)

    @property
    def erledigt(self) -> int:
        return sum(1 for p in self.posten if p.erledigt)


def _vormonat(heute: date) -> date:
    """Der letzte VOLLSTAENDIGE Monat.

    Am 13. September kann es den Septemberauszug nicht geben; die Frage ist,
    ob der August da ist. Gegen den laufenden Monat zu pruefen haette jeden
    Monatsanfang alles rot gefaerbt.
    """
    return date(heute.year - 1, 12, 1) if heute.month == 1 \
        else date(heute.year, heute.month - 1, 1)


def _monatsende(m: date) -> date:
    return (date(m.year + 1, 1, 1) if m.month == 12
            else date(m.year, m.month + 1, 1))


def konten(conn: sqlite3.Connection, heute: date, periode: str,
           balances: list[dict] | None = None) -> list[Posten]:
    """Geparste Konten: der Auszug ist der Beleg, kein Haken noetig.

    Der getippte Livestand desselben Kontos steht als Zusatz in derselben
    Zeile und nicht als eigene Aufgabe. Zwei Zeilen fuer "DKB Giro" -- eine
    fuer den Auszug, eine fuer den Stand aus der Banking-App -- waeren zwei
    Haken fuer einen Handgriff.
    """
    live = {str(b.get("account_id")): b for b in (balances or [])
            if b.get("account_id")}
    vor = _vormonat(heute)
    noetig = _monatsende(vor)              # exklusiv: Auszug muss bis dahin reichen
    out = []
    for row in conn.execute(
            "SELECT a.id, a.display_name, MAX(s.period_end) AS bis "
            "FROM accounts a LEFT JOIN statements s ON s.account_id = a.id "
            "WHERE a.ingest_mode = 'parsed' "
            "GROUP BY a.id, a.display_name ORDER BY a.display_name"):
        bis = date.fromisoformat(row["bis"]) if row["bis"] else None
        deckt = bool(bis and bis >= noetig - _EIN_TAG)
        p = Posten(id=f"konto:{row['id']}", name=row["display_name"] or row["id"],
                   gruppe="konten", periode=periode, stand=bis,
                   automatisch=True, erledigt=deckt)
        if deckt:
            p.beleg = f"Auszug bis {bis.isoformat()}"
        else:
            # Der Beleg bleibt stehen, auch wenn ein Monat fehlt: wie weit der
            # Auszug reicht, ist genau das, was man zum Nachholen wissen muss.
            p.hinweis = (f"Auszug bis {bis.isoformat()} · {vor:%Y-%m} fehlt" if bis
                         else "noch kein Auszug eingelesen")
        b = live.get(row["id"])
        if b and b.get("as_of"):
            a = b["as_of"]
            a = a if isinstance(a, date) else date.fromisoformat(str(a))
            # NUR WENN ER NEUER IST ALS DER AUSZUG. Ein Livestand zum selben
            # Tag wiederholt nur den Auszugsschluss -- derselbe Saldo stand
            # sonst zweimal da, einmal belegt und einmal getippt. Mit Betrag,
            # weil der Zusatz sonst nicht sagt, WARUM er dasteht, etwa ein
            # Verkaufserloes, der nach dem Auszug eingegangen ist.
            if bis is None or a > bis:
                from finctl.ledger.db import format_eur

                p.zusatz = f"Livestand {a.isoformat()}"
                if b.get("cents") is not None:
                    p.zusatz += f" · {format_eur(int(b['cents']))}"
        out.append(p)
    return out


_EIN_TAG = __import__("datetime").timedelta(days=1)


def abos_offen(conn: sqlite3.Connection, heute: date, periode: str) -> list[Posten]:
    """Geteilte Abos, bei denen das Einsammeln ansteht.

    Erst ab dem Einsammel-Monat, und nur solange etwas offen ist. Keine Zeile
    zum Abhaken: sie verschwindet, sobald die Rueckzahlungen im Import
    stehen -- dasselbe Prinzip wie bei den Konten, deren Beleg der Auszug ist.
    """
    from finctl import abos as _ab
    from finctl.ledger.db import format_eur

    out = []
    for abo in _ab.alle_vertraege():
        for z in _ab.abrechnung(conn, abo, heute=heute):
            if z["status"] != "faellig":
                continue
            wer = ", ".join(f"{p['name']} {format_eur(p['offen'])}"
                            for p in z["personen"] if p["offen"] > 0)
            out.append(Posten(
                id=f"abo:{abo['id']}:{z['von']}",
                name=f"{abo['name']} · {z['von']} bis {z['bis']}",
                gruppe="abos", periode=periode, cents=z["offen"],
                hinweis=f"offen: {wer}", ziel=f"/abos#abo-{abo['id']}"))
    return out


def bestaende(balances: list[dict], heute: date, periode: str,
              jahr: str, geparst: set[str] | None = None) -> list[Posten]:
    """Getippte Staende: Depots, Krypto, Konten ohne Parser, Renten.

    Erledigt, wenn der Stand aus der laufenden Periode stammt -- wer eine Zahl
    eintraegt, hat offensichtlich nachgesehen. Sonst offen, und die Zeile
    bietet an, denselben Wert mit heutigem Datum abzulegen.
    """
    geparst = geparst or set()
    out = []
    for b in balances:
        # Konten mit Parser haben ihre eigene Zeile weiter oben.
        if b.get("account_id") in geparst:
            continue
        art = str(b.get("kind") or "")
        name = b.get("name") or b.get("account_id") or "?"
        jaehrlich = art == "rentenversicherung"
        per = jahr if jaehrlich else periode
        stand = b.get("as_of")
        stand = stand if isinstance(stand, date) else (
            date.fromisoformat(str(stand)) if stand else None)
        frisch = bool(stand and (
            stand.year == heute.year if jaehrlich
            else (stand.year, stand.month) == (heute.year, heute.month)))
        pid = f"bestand:{b.get('account_id') or name}"
        p = Posten(id=pid, name=name,
                   gruppe="jaehrlich" if jaehrlich else "vermoegen",
                   periode=per, stand=stand, automatisch=frisch,
                   erledigt=frisch)
        p.schluessel = str(b.get("account_id") or name)
        p.art = art
        p.cents = b.get("cents")
        if frisch:
            p.beleg = f"Stand {stand.isoformat()}"
        elif stand:
            p.hinweis = f"Stand {stand.isoformat()}"
        else:
            p.hinweis = "kein Wert"
        out.append(p)
    return out


def schritte(conn: sqlite3.Connection, views: list[dict] | None = None) -> list[dict]:
    """Der Ablauf, den der Eigentuemer selbst beschrieben hat, als Zeilen.

    Review, Planung, Konten, Fortschritt -- vier Seiten, die man sich bisher
    merken musste. Hier stehen sie in der Reihenfolge, in der sie Sinn
    ergeben, jede mit dem einen Satz, der sagt, ob dort etwas klemmt.

    Rot nur, wo wirklich etwas klemmt. "6 offen" ist normal; ein Konto, das
    ins Minus laeuft, ist es nicht. Eine Seite, auf der alles rot leuchtet,
    liest man nach dem zweiten Monat nicht mehr.
    """
    from finctl import ops
    from finctl.forecast import szenarien as sz

    out: list[dict] = []

    n, summe = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) "
        "FROM v_review_queue").fetchone()
    out.append({"id": "review", "titel": "Zuordnen", "ziel": "/transactions?ansicht=offen",
                "text": f"{n} ohne Kategorie" if n else "nichts offen",
                "cents": summe if n else None, "warnung": False})

    aktiv = [s for s in sz.load(_szspec()) if s.active]
    plan = [s for s in aktiv if not s.obligation and s.summiert]
    text = ", ".join(s.name for s in aktiv) or "keine eingeschaltet"
    out.append({"id": "planung", "titel": "Planung", "ziel": "/planung",
                "text": text, "cents": None,
                # Zwei eingeschaltete PLAENE sind selten Absicht: "Neuwagen
                # 2027" und "Gebrauchtwagen 2027" summiert die Prognose
                # klaglos, und das faellt nirgends sonst auf.
                "warnung": len(plan) > 1,
                "warnhinweis": ("mehr als ein Plan gleichzeitig an -- die "
                                "Prognose summiert sie")
                               if len(plan) > 1 else ""})

    if views is None:
        try:
            views = ops.household_accounts(conn, months=12)
        except Exception:
            views = []
    eng = [v for v in views if v.get("breach_months")]
    if eng:
        schlimmste = min(eng, key=lambda v: v["worst_trough_cents"])
        out.append({"id": "konten", "titel": "Konten", "ziel": "/konten",
                    "text": f"{schlimmste['display_name']} unter Grenze, Tiefpunkt "
                            f"{_monat(schlimmste['worst_month'])}",
                    "cents": schlimmste["worst_trough_cents"], "warnung": True})
    else:
        out.append({"id": "konten", "titel": "Konten", "ziel": "/konten",
                    "text": "alle zwoelf Monate ueber ihrer Grenze",
                    "cents": None, "warnung": False})
    return out


def _monat(wert) -> str:
    d = wert if isinstance(wert, date) else date.fromisoformat(str(wert))
    return f"{d:%Y-%m}"


def _szspec() -> dict:
    import yaml

    pfad = CONFIG_DIR / "szenarien.yaml"
    return (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}) \
        if pfad.exists() else {}


def planung_offen(conn: sqlite3.Connection, heute: date, periode: str) -> list[Posten]:
    """Begonnene Planzeilen mit passenden Buchungen, aber ohne Zuordnung.

    Ohne Zuordnung zaehlt nichts: ein Wegfall zieht nichts ab, neue Kosten
    zaehlen voll, auch wenn sie laengst gebucht sind. Die Zeile verschwindet
    mit der Zuordnung, nicht mit einem Haken.
    """
    import yaml

    from finctl.forecast import abgleich as ag
    from finctl.forecast import szenarien as sz

    pfad = CONFIG_DIR / "szenarien.yaml"
    if not pfad.exists():
        return []
    geladen = sz.load(yaml.safe_load(pfad.read_text(encoding="utf-8")) or {})
    f = ag.fenster(conn)
    monat = heute.replace(day=1)
    out = []
    for s in sz.active(geladen):
        for i, line in enumerate(s.lines):
            if (line.buchungen or line.frequency == "einmalig" or line.start is None
                    or line.start > monat):
                continue
            passend = sz.vorschlaege(conn, line, f)
            if not passend:
                continue
            out.append(Posten(
                id=f"planung:{s.id}:{i}", name=f"{s.name} · {line.label}",
                gruppe="planung", periode=periode,
                hinweis=f"{len(passend)} passende Buchungen, noch nichts zugeordnet",
                ziel=f"/planung#klammer-{s.id}"))
    return out


def renten(heute: date, jahr: str) -> list[Posten]:
    """Was im Ruhestand hereinkommt, laut dem letzten Schreiben.

    Renteninformation und Standmitteilung kommen einmal im Jahr, und mit
    ihnen eine neue Hochrechnung. Aktuell, wenn das Schreiben aus diesem Jahr
    ist -- wie die Staende der Rentenversicherungen.
    """
    from finctl import renten as _renten

    out = []
    for q in _renten.quellen():
        frisch = q.aktuell(heute)
        p = Posten(id=f"rente:{q.id}", name=q.name, gruppe="renten", periode=jahr,
                   stand=q.stand, automatisch=frisch, erledigt=frisch)
        p.schluessel, p.art, p.cents = q.id, q.art, q.cents
        p.zusatz = (_renten.ARTEN[q.art]
                    + (", heutige Kaufkraft" if q.kaufkraft == "heute" else ", nominal"))
        if frisch:
            p.beleg = f"Stand {q.stand.isoformat()}"
        else:
            p.hinweis = f"Stand {q.stand.isoformat()}" if q.stand else "ohne Stand"
        out.append(p)
    return out
