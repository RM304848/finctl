"""Ein Flussdiagramm (Sankey) als SVG-Geometrie, ohne Bibliothek.

Drei Spalten: Quellen links, ein Knoten in der Mitte, Senken rechts. Die
Breite eines Bandes ist sein Betrag. Gehen mehr Einnahmen hinein als Ausgaben
hinaus, steht der Rest rechts als eigene Senke ("gespart"); im umgekehrten
Fall links als Quelle ("aus Rücklagen") -- so gehen beide Seiten auf, und die
Mitte ist eine Summe, keine Behauptung.

Kleine Posten fallen zu "übrige" zusammen: ein Band unter anderthalb Prozent
ist ein Strich, und zwanzig Striche mit Beschriftung sind unlesbar. Welche
darin stecken, steht im Tooltip.

Nur Geometrie und Text -- Farben und Links setzt die Vorlage. Kein CDN
(docs/design_conventions.yaml, kein-cdn), deshalb von Hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

#: Anteil am Gesamtfluss, unter dem ein Posten in "übrige" aufgeht.
KLEIN = 0.015
BREITE = 1100
KNOTEN = 14          # Breite eines Knotenbalkens
LUECKE = 13          # Abstand zwischen zwei Knoten: eine Zeile Beschriftung
X_QUELLE, X_MITTE, X_SENKE = 330, 548, 766
#: Laenger passt eine Beschriftung samt Betrag nicht neben die Spalte.
LABEL_MAX = 30


@dataclass(slots=True)
class Posten:
    label: str
    cents: int                     # immer positiv: der Betrag des Bandes
    href: str = ""
    art: str = ""                  # "", "rest" (gespart/Rücklage), "uebrig"
    teile: list[str] = field(default_factory=list)
    #: Die Kategorien dahinter. Aus ihnen baut `link` die Adresse der Buchungen
    #: -- auch fuer "übrige", das die Kategorien seiner Mitglieder erbt.
    kategorien: list[str] = field(default_factory=list)


def kurz(text: str, laenge: int = LABEL_MAX) -> str:
    return text if len(text) <= laenge else text[:laenge - 1].rstrip() + "…"


def euro_rund(cents: int) -> str:
    """46.122 € -- ganze Euro, im Diagramm sind Cent nur Rauschen."""
    return f"{round(cents / 100):,}".replace(",", ".") + " €"


def _buendeln(posten: list[Posten], gesamt: int, name: str) -> list[Posten]:
    # Ein Rest-Knoten (ins Depot, aus Rücklagen) ist keine Kategorie, sondern
    # die Antwort auf "wo ist der Rest hin" -- er bleibt stehen, auch klein.
    reste = [p for p in posten if p.art == "rest"]
    posten = [p for p in posten if p.art != "rest"]
    gross = [p for p in posten if p.cents >= gesamt * KLEIN]
    klein = [p for p in posten if p.cents < gesamt * KLEIN]
    if len(klein) < 2:
        return sorted(posten, key=lambda p: -p.cents) + reste
    # Die Kategorien erbt "übrige" nur, wenn JEDER Teil welche hat: sonst
    # zeigte der Link nur einen Teil dessen, worauf man geklickt hat.
    alle = all(p.kategorien for p in klein)
    # Fuehren alle Teile auf dieselbe Seite (etwa lauter Planzeilen), fuehrt
    # "übrige" dorthin -- ohne den Anker des einzelnen Teils.
    seiten = {p.href.split("#")[0].split("?")[0] for p in klein}
    gemeinsam = next(iter(seiten)) if len(seiten) == 1 and not alle else ""
    rest = Posten(name, sum(p.cents for p in klein), art="uebrig", href=gemeinsam,
                  teile=[f"{p.label} {euro_rund(p.cents)}" for p in
                         sorted(klein, key=lambda p: -p.cents)],
                  kategorien=[k for p in klein for k in p.kategorien] if alle else [])
    return [*sorted(gross, key=lambda p: -p.cents), rest, *reste]


def _spalte(posten: list[Posten], x: int, k: float, hoehe: float, gesamt: int) -> list[dict]:
    belegt = sum(p.cents for p in posten) * k + LUECKE * (len(posten) - 1)
    y = 30 + (hoehe - 30 - belegt) / 2
    out = []
    for p in posten:
        h = max(p.cents * k, 1.5)
        out.append({"x": x, "y": round(y, 1), "h": round(h, 1), "label": p.label,
                    "kurz": kurz(p.label),
                    "cents": p.cents, "href": p.href, "art": p.art,
                    "pct": round(100 * p.cents / gesamt) if gesamt else 0,
                    "text": euro_rund(p.cents), "teile": p.teile,
                    "mitte_y": round(y + h / 2, 1)})
        y += h + LUECKE
    return out


def _band(x0: float, y0: float, x1: float, y1: float, dicke: float) -> str:
    """Ein Band als geschlossene Flaeche aus zwei Bezierkurven."""
    xm = (x0 + x1) / 2
    return (f"M{x0:.1f},{y0:.1f} C{xm:.1f},{y0:.1f} {xm:.1f},{y1:.1f} {x1:.1f},{y1:.1f} "
            f"L{x1:.1f},{y1 + dicke:.1f} C{xm:.1f},{y1 + dicke:.1f} {xm:.1f},{y0 + dicke:.1f} "
            f"{x0:.1f},{y0 + dicke:.1f} Z")


def layout(quellen: list[Posten], senken: list[Posten], *,
           rest_rein: str = "aus Rücklagen", rest_raus: str = "gespart",
           rest_href: str = "", rest_teile: list[str] | None = None,
           uebrig_href: str = "",
           link: Callable[[list[str]], str] | None = None) -> dict:
    """Knoten und Baender fuer die Vorlage. Leer, wenn nichts fliesst.

    `link` macht aus den Kategorien eines Knotens die Adresse seiner
    Buchungen; ein Knoten mit eigener Adresse behaelt sie.
    """
    rein = sum(p.cents for p in quellen)
    raus = sum(p.cents for p in senken)
    if not rein and not raus:
        return {}
    gesamt = max(rein, raus)
    links = _buendeln(quellen, gesamt, "übrige Einnahmen")
    rechts = _buendeln(senken, gesamt, "übrige Ausgaben")
    if rein > raus:
        rechts.append(Posten(rest_raus, rein - raus, art="rest", href=rest_href,
                             teile=list(rest_teile or [])))
    elif raus > rein:
        links.append(Posten(rest_rein, raus - rein, art="rest", href=rest_href,
                            teile=list(rest_teile or [])))
    for p in links + rechts:
        if link and not p.href and p.kategorien:
            p.href = link(p.kategorien)
        if not p.href and p.art == "uebrig":
            p.href = uebrig_href

    zeilen = max(len(links), len(rechts))
    hoehe = max(380, zeilen * 30 + 70)
    # Oben 30 Pixel frei fuer die Spaltenkoepfe "rein" und "raus".
    k = (hoehe - 70 - LUECKE * (zeilen - 1)) / gesamt
    knoten_l = _spalte(links, X_QUELLE, k, hoehe, gesamt)
    knoten_r = _spalte(rechts, X_SENKE, k, hoehe, gesamt)
    mitte = _spalte([Posten("", gesamt)], X_MITTE, k, hoehe, gesamt)[0]

    baender = []
    y_mitte = mitte["y"]
    for n in knoten_l:
        baender.append({"d": _band(n["x"] + KNOTEN, n["y"], X_MITTE, y_mitte, n["h"]),
                        "seite": "rein", "art": n["art"], "href": n["href"],
                        "titel": f"{n['label']}: {n['text']} ({n['pct']} %)"})
        y_mitte += n["h"]
    y_mitte = mitte["y"]
    for n in knoten_r:
        baender.append({"d": _band(X_MITTE + KNOTEN, y_mitte, n["x"], n["y"], n["h"]),
                        "seite": "raus", "art": n["art"], "href": n["href"],
                        "titel": f"{n['label']}: {n['text']} ({n['pct']} %)"})
        y_mitte += n["h"]
    return {"breite": BREITE, "hoehe": hoehe, "knoten": KNOTEN,
            "links": knoten_l, "rechts": knoten_r, "mitte": mitte,
            "baender": baender, "rein": rein, "raus": raus,
            "rein_text": euro_rund(rein), "raus_text": euro_rund(raus)}
