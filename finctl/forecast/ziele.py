"""Progress against the declared goals.

A goal is only useful if the same number can be recomputed next month without
remembering how it was built. So each goal names its target, its date and which
holdings count towards it, and the progress is derived rather than typed in.

Two rules that decide whether the bar tells the truth:

* An obligation is subtracted, never counted. The 17.000 held for his sister
  sits in the same Scalable balance as his own buffer, and counting it would
  make the portfolio look a third larger than it is.

* A target with a date is compared against a CURRENT balance, so the bar
  measures what is saved, not what it will have grown into. Projecting the
  balance forward would show 60% for a portfolio that is barely started, which
  is exactly the reassurance a progress bar should not give.

* Every goal counts the SAME liquid assets. Letting each goal name its own
  list produced two Barista FIRE style targets measured against different
  pots, which made them incomparable for no reason -- and one of the lists
  quietly named kinds that no longer existed, so it counted nothing at all.
  There is one question here, "what is available today", and one answer.

No return is assumed anywhere. The gap to a target is the target minus what
exists, and the monthly rate is that gap divided by the months left. Compound
growth would shrink both, and a target that looks closer because of an assumed
return is the one thing this page must not produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Everything the owner counts as his own free capital, by his decision:
# accounts, savings, depots, crypto and the private pension policies.
#
# The policies come with a caveat worth keeping visible rather than acting on
# unilaterally: one cannot be drawn before a given age and the other not before
# its maturity date, so a target dated earlier cannot actually be met with
# them. Counting them answers "what have I built", which is the question being
# asked here; the phase model in lebensplan.yaml answers "what can I spend
# when", and that one keeps them apart.
LIQUID_KINDS = ("giro", "tagesgeld", "depot", "krypto", "rentenversicherung")

#: Arten, die nur anders heissen. Krypto ist ein Depot wie jedes andere: es
#: waechst mit dem Depotsatz, liegt im Depot-Topf und wird im selben Abschnitt
#: gepflegt. Eine eigene Art davon machte aus einer Frage zwei Haken, und ein
#: Ziel, das "alle Depots" meinte, liess die Coins still draussen.
GLEICHE_ART = {"krypto": "depot"}


def art(kind: str | None) -> str:
    """Die Bestandsart, unter der eine Position zaehlt und gezeigt wird."""
    k = (kind or "").strip().lower()
    return GLEICHE_ART.get(k, k)


def _as_due(value) -> date | None:
    """A date from YAML, which may arrive parsed or as a string.

    goals.yaml is read by the YAML loader and yields date objects; the
    dashboard overlay writes ISO strings. Accepting only the former is why a
    date typed into the page vanished the moment it was saved.
    """
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass(slots=True)
class Progress:
    goal_id: str
    name: str
    target_cents: int
    have_cents: int
    due: date | None = None
    # The return to assume when working out the saving rate, declared by the
    # goal itself. This has to travel with the target or the two get mixed:
    # the Barista FIRE figure is NOMINAL at 2045, so discounting it at a REAL
    # 2% overstates the required saving rate by about a thousand a month. A
    # nominal target needs a nominal return.
    # A future payment that is already committed to this goal -- an endowment
    # maturing, a refund due. Held SEPARATE from have_cents on purpose: it is
    # not saved yet, and folding it in would let the bar show a goal as met
    # years before any money exists. It answers a different question, namely
    # whether the goal still needs a saving plan at all.
    covered_cents: int = 0
    #: Gegen welche Bestandsarten gemessen wird. None heisst: gegen alles
    #: Liquide, wie bisher.
    basis: tuple[str, ...] | None = None
    basis_text: str = "liquidem Vermögen"
    #: Positionen in dieser Basis, die gar keinen Wert tragen. Sie werden
    #: uebersprungen statt als null gezaehlt -- aber bei einer verengten
    #: Basis faellt eine von fuenf ins Gewicht, und ein Balken, der sie
    #: verschweigt, sieht praezise aus und ist es nicht.
    ohne_wert: tuple[str, ...] = ()
    #: Der Notgroschen, der von diesem Ziel abgeht -- schon aus have_cents heraus.
    abzug_cents: int = 0

    @property
    def pct(self) -> float:
        if self.target_cents <= 0:
            return 100.0
        return min(100.0, 100.0 * self.have_cents / self.target_cents)

    @property
    def uncovered_cents(self) -> int:
        """What neither saved nor promised money covers."""
        return max(0, self.target_cents - self.have_cents - self.covered_cents)

    def months_left(self, today: date) -> int | None:
        if self.due is None:
            return None
        return max(0, (self.due.year - today.year) * 12
                   + (self.due.month - today.month))

    def monthly_needed_cents(self, today: date) -> int | None:
        """What has to be put aside every month to close the gap in time.

        Straight division, no compounding. A required rate computed against an
        assumed return is smaller than the real one, and being told to save
        less than necessary is the expensive direction to be wrong in.

        Returns None for a goal with no date: "per month until never" has no
        answer.
        """
        months = self.months_left(today)
        if months is None:
            return None
        return self.uncovered_cents if months == 0 else \
            int(round(self.uncovered_cents / months))


def liquid_cents(balances: dict, kinds: tuple[str, ...] | None = None) -> int:
    """Was heute da ist, abzueglich dessen, was schon jemandem gehoert.

    A holding with no stated value is skipped rather than counted as zero:
    staked ETH is worth something, and showing it as nothing would look like a
    measurement instead of a gap in the data.

    `kinds` verengt die Basis auf einzelne Bestandsarten -- "alle Depots"
    statt "alles Liquide". Verbindlichkeiten werden dann NICHT abgezogen. Beim
    Notgroschen gehoert der fremde Anteil am Tagesgeld nach Entscheidung des
    Eigentuemers ausdruecklich dazu, und von einem Depotziel abgezogen hiesse
    es, dasselbe Geld zweimal zu verplanen.
    """
    verengt = kinds is not None
    erlaubt = tuple(k.lower() for k in (kinds or LIQUID_KINDS))
    total = 0
    for row in balances.get("balances") or []:
        if row.get("cents") is None:
            continue
        if _passt(row, erlaubt):
            total += int(row["cents"])
    if verengt:
        return max(0, total)
    owed = sum(int(o["cents"]) for o in (balances.get("obligations") or [])
               if o.get("cents") is not None)
    return max(0, total - owed)


def _passt(row: dict, erlaubt: tuple[str, ...]) -> bool:
    """Zaehlt diese Position zur Basis?

    Eine Basis nennt entweder eine ART ("alle Depots") oder eine einzelne
    POSITION ("dkb-giro"). Beides in einer Liste, weil beides gemeint sein
    kann und der Eigentuemer in Konten denkt, nicht in Kategorien: sein
    Notgroschen ist "DKB, TR, C24, Scalable, Sparda" und nicht "alles
    Tagesgeld".
    """
    erlaubt = tuple(art(k) for k in erlaubt)
    return (art(row.get("kind")) in erlaubt
            or str(row.get("account_id") or "").lower() in erlaubt
            or str(row.get("name") or "").lower() in erlaubt)


#: In welchen Topf der Jahresrechnung eine Bestandsart faellt.
TOPF = {"tagesgeld": "tagesgeld", "giro": "tagesgeld", "depot": "depot",
        "krypto": "depot", "rentenversicherung": "policen"}


def toepfe(balances: dict) -> dict[str, int]:
    """Das liquide Vermoegen, aufgeteilt so, wie die Jahresrechnung es fuehrt.

    Das Tagesgeld so, wie es auf den Konten liegt -- samt dem Anteil, der
    jemand anderem gehoert. Er gehoert zum Notgroschen, und ueber ihn geht er
    bei jedem Ziel ab, das ihn nicht mitzaehlt. Ihn hier zusaetzlich
    abzuziehen hiesse, ihn zweimal abzuziehen.
    """
    out = {"tagesgeld": 0, "depot": 0, "policen": 0}
    for row in balances.get("balances") or []:
        topf = TOPF.get((row.get("kind") or "").lower())
        if topf and row.get("cents") is not None:
            out[topf] += int(row["cents"])
    return out


def policen_je_konto(balances: dict) -> dict[str, int]:
    """Der Topf `policen`, je Police -- damit eine Auszahlung genau ihren
    Anteil aus dem Topf nimmt."""
    return {str(row.get("account_id")): int(row["cents"])
            for row in balances.get("balances") or []
            if (row.get("kind") or "").lower() == "rentenversicherung"
            and row.get("account_id") and row.get("cents") is not None}


def toepfe_fuer_basis(kinds: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """Welche Toepfe eine Basis abdeckt -- oder None, wenn sie quer dazu liegt.

    Hochgerechnet wird, was angehakt ist: jede Bestandsart zieht ihren Topf
    mit, und der waechst mit seinem Satz -- Depot und Policen mit dem
    Depotsatz, das Tagesgeld mit seinem. Nur eine Basis aus einzelnen Konten
    liegt quer zu den Toepfen und bleibt ohne Hochrechnung.
    """
    if not kinds or any(k not in TOPF for k in kinds):
        return None
    return tuple(sorted({TOPF[k] for k in kinds}))


#: Wie eine verengte Basis heisst, wenn die Seite sie nennt.
BASIS_LABEL = {
    "giro": "Girokonten", "tagesgeld": "Tagesgeld", "depot": "Depots",
    "krypto": "Krypto", "rentenversicherung": "Rentenversicherungen",
}


def basis_von(goal: dict) -> tuple[str, ...] | None:
    """Die Bestandsarten, gegen die ein Ziel misst -- oder None fuer alles."""
    roh = goal.get("basis")
    if not roh:
        return None
    if isinstance(roh, str):
        roh = [roh]
    return tuple(dict.fromkeys(art(k) for k in roh if str(k).strip()))


def basis_text(kinds: tuple[str, ...] | None,
               balances: dict | None = None) -> str:
    if not kinds:
        return "liquidem Vermögen"
    namen = {}
    for r in ((balances or {}).get("balances") or []):
        for schluessel in (r.get("account_id"), r.get("name")):
            if schluessel:
                namen[str(schluessel).lower()] = r.get("name") or str(schluessel)
    return " + ".join(BASIS_LABEL.get(k) or namen.get(k) or k for k in kinds)


def merge_edits(goals: dict, edits: dict) -> dict:
    """Apply dashboard edits over the documented goals.

    Kept as an overlay rather than a rewrite of goals.yaml: that file carries
    the derivation of every figure, and a dashboard that rewrote it would
    delete the reasoning on the first click. The overlay wins, and the base
    stays readable as the document it is.
    """
    if not edits:
        return _notgroschen_vorgabe(goals)
    out = dict(goals)
    if edits.get("reihenfolge"):
        out["reihenfolge"] = [str(x) for x in edits["reihenfolge"]]
    offen = dict(edits.get("ziele") or {})
    merged = []
    for goal in goals.get("ziele") or []:
        edit = offen.pop(goal.get("id"), None)
        if edit and edit.get("entfernt"):
            continue          # im Dashboard geloescht; goals.yaml bleibt, wie es ist
        merged.append({**goal, **edit} if edit else goal)
    # Was es nur hier gibt, wurde im Dashboard angelegt. Betrag und Herleitung
    # sind eingetragen, nicht berechnet -- wie bei jedem anderen Ziel.
    for gid, edit in offen.items():
        if edit and not edit.get("entfernt"):
            merged.append({**edit, "id": gid, "eigen": True})
    out["ziele"] = merged
    return _notgroschen_vorgabe(out)


#: Wie viele Monate der Gehalts-Untergrenze der Notgroschen ohne eigenen
#: Betrag umfasst.
NOTGROSCHEN_MONATE = 3


def _notgroschen_vorgabe(goals: dict) -> dict:
    """Ein Notgroschen ohne Betrag bekommt drei Monate Gehalts-Untergrenze.

    Die Startvorlage traegt ihn ohne Betrag: ein fester Betrag waere eine
    fremde Zahl, und ohne jeden Betrag stuende die Seite leer da. Wer einen
    eigenen Betrag eintraegt, ueberschreibt die Vorgabe; wer die Untergrenze
    auf /annahmen aendert, verschiebt sie mit.
    """
    ziele = goals.get("ziele") or []
    if not any(g.get("id") == PUFFER_ZIEL and not g.get("cents") for g in ziele):
        return goals
    from finctl import assumptions as _ann

    try:
        vorgabe = NOTGROSCHEN_MONATE * _ann.salary_floor_cents()
    except KeyError:
        return goals
    notiz = f"{NOTGROSCHEN_MONATE} × Gehalts-Untergrenze aus den Annahmen"
    return {**goals, "ziele": [
        {**g, "cents": vorgabe, "notiz": g.get("notiz") or notiz}
        if g.get("id") == PUFFER_ZIEL and not g.get("cents") else g for g in ziele]}


def puffer_cents(goals: dict) -> int | None:
    """Das Tagesgeld-Ziel: die Grenze der Kaskade in der Hochrechnung.

    Von Hand gesetzt, nicht gemessen. Wer von seinem Vermoegen zu leben
    beginnt, zieht es auf eine Jahresausgabe hoch -- die laesst sich aus den
    Berichten ablesen, und die Hochrechnung folgt dem Ziel sofort.
    """
    ziel = next((g for g in (goals.get("ziele") or []) if g.get("id") == PUFFER_ZIEL), None)
    return int(ziel["cents"]) if ziel and ziel.get("cents") else None


#: Welches Ziel die Grenze zwischen Tagesgeld und Depot setzt.
PUFFER_ZIEL = "tagesgeld-puffer"


def progress(goals: dict, balances: dict, *, today: date | None = None) -> list[Progress]:
    """One row per goal that has a target, obligations already deducted."""
    today = today or date.today()

    out: list[Progress] = []
    for goal in goals.get("ziele") or []:
        if goal.get("art") == "verbindlichkeit":
            continue          # a liability is not a thing to make progress on
        # Je Ziel, nicht einmal fuer alle: ein Ziel darf gegen EINEN Topf
        # messen statt gegen das ganze Vermoegen. Zwei Balken, die
        # verschiedene Dinge messen, muessen es sagen -- deshalb kommt die
        # Basis mit zurueck und steht neben dem Balken.
        kinds = basis_von(goal)
        have = liquid_cents(balances, kinds)
        # Der Notgroschen ist Grundstock, kein Zielkapital: er zaehlt bei
        # keinem Ziel mit ausser bei seinem eigenen. Abgezogen wird er dort, wo
        # er liegt -- im Tagesgeld --, und nur so weit, wie dort etwas liegt.
        # Ein reines Depotziel gibt nichts ab.
        abzug = 0
        puffer = puffer_cents(goals)
        if puffer and goal["id"] != PUFFER_ZIEL:
            if kinds is None:
                tagesgeld = toepfe(balances)["tagesgeld"]
                # Die Verbindlichkeiten stecken im Notgroschen und gehen mit
                # ihm ab -- nicht noch einmal obendrauf.
                have = liquid_cents(balances, LIQUID_KINDS)
            else:
                eigene = tuple(k for k in kinds if TOPF.get(k) == "tagesgeld")
                tagesgeld = liquid_cents(balances, eigene) if eigene else 0
            abzug = min(puffer, max(0, tagesgeld), have)
        erlaubt = tuple(k.lower() for k in (kinds or LIQUID_KINDS))
        offen = tuple(
            (r.get("name") or r.get("account_id") or "?")
            for r in (balances.get("balances") or [])
            if r.get("cents") is None and _passt(r, erlaubt))
        out.append(Progress(
            goal_id=goal["id"], name=goal.get("name", goal["id"]),
            basis=kinds, basis_text=basis_text(kinds, balances), ohne_wert=offen,
            target_cents=int(goal.get("cents") or 0), have_cents=have - abzug,
            abzug_cents=abzug,
            due=_as_due(goal.get("stichtag")),
            covered_cents=sum(int(c["cents"]) for c in (goal.get("gedeckt_durch") or [])
                              if c.get("cents") is not None)))

    # Erst, was ohne Termin immer gilt -- der Tagesgeld-Puffer ist eine
    # stehende Untergrenze und kein Meilenstein --, dann chronologisch von
    # nah nach fern. Die Liste liest sich damit als Weg und nicht als
    # Aufzaehlung: was als naechstes ansteht, steht oben.
    #
    # Bei gleichem Stichtag entscheidet die Reihenfolge in goals.yaml. Zwei
    # Ziele auf denselben Tag sollen nicht bei jedem Aufruf die Plaetze
    # tauschen.
    reihe = {g["id"]: i for i, g in enumerate(goals.get("ziele") or [])}
    out.sort(key=lambda p: (p.due is not None, p.due or date.min,
                            reihe.get(p.goal_id, 0)))
    # Eine auf der Seite gezogene Reihenfolge gewinnt. Was darin fehlt -- ein
    # spaeter angelegtes Ziel --, folgt dahinter in der Reihenfolge von oben;
    # die Sortierung ist stabil.
    gezogen = {gid: i for i, gid in enumerate(goals.get("reihenfolge") or [])}
    if gezogen:
        out.sort(key=lambda p: gezogen.get(p.goal_id, len(gezogen)))
    return out
