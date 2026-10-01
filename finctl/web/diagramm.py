"""SVG-Diagramme ohne Bibliothek und ohne Netz (docs/design_conventions.yaml, diagramme).

Uebernommen aus healthctl/web/diagramm.py und auf Monate und Cent umgestellt.
Jede Funktion liefert ein fertiges `<figure class="chart">`: Titel, Legende (ab
zwei Eintraegen immer), das SVG und darunter die Tabelle mit denselben Werten.
Die Tabelle ist der barrierefreie Zwilling -- der Tooltip ergaenzt, er ist nie
der einzige Weg zu einem Wert.

Die Regeln, hier als Code:

- Linien 2 px, runde Enden, `vector-effect:non-scaling-stroke`: das SVG
  schrumpft mit der Seite, die Linie nicht. Punkte mit 2-px-Ring in der
  Flaechenfarbe; ueber 40 Monaten kleiner.
- Gitter und Achsen sind durchgezogene Haarlinien, nie gestrichelt. Eine Grenze
  ist eine volle Linie in ihrer Farbe, unterschieden ueber die Legende.
- Saeulen hoechstens 24 Einheiten dick, am Ende weg von der Null 4 Einheiten
  abgerundet, an der Null gerade.
- Text traegt nie die Serienfarbe: Werte und Beschriftungen nehmen `.t` und `.tm`.
- Keine zwei y-Achsen. Zwei Groessen verschiedener Art sind zwei Diagramme.
- Farben stehen nur als `var(--...)` im SVG, nie als Hex: beide Themen folgen
  von selbst. Serien uebergeben den NAMEN der CSS-Variable (`--accent`).

Alle Beschriftungen laufen durch `html.escape`; die Tooltip-Daten gehen als JSON
in ein Attribut und von dort nur ueber `textContent` ins DOM (_diagramm.html).
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from markupsafe import Markup

from finctl.ledger.db import format_eur

BREITE = 800
RECHTS, OBEN = 24, 12
#: Unter der Grundlinie: Abstand plus eine Zeile Telefonschrift (24 Einheiten).
UNTEN = 36
#: Breite einer Ziffer in Telefonschrift; daraus der linke Rand (_links).
ZIFFER = 13
PUNKT_DICHT = 40           # ab so vielen Monaten werden die Punkte kleiner
SAEULE_MAX = 24            # Dicke einer Saeule in Einheiten, hoechstens
LUECKE = 2                 # Ring um einen Punkt, in der Flaechenfarbe
#: Ist der hoechste Wert mehr als so oft der zweithoechste, endet die Achse beim
#: zweithoechsten (`kappen`): ein einzelner Ausreisser presste sonst alle anderen
#: Monate in ein paar Pixel, genau dort, wo die Grenzlinie liegt.
KAPPE_FAKTOR = 3


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _f(wert: float) -> str:
    return f"{wert:.1f}".rstrip("0").rstrip(".")


def euro_ganz(cents: float) -> str:
    """Ganze Euro ohne Zeichen, fuer Achsen: "40.000", nie "-0"."""
    euro = round(cents / 100)
    return f"{'-' if euro < 0 else ''}{abs(euro):,}".replace(",", ".")


def _euro(cents: int | None) -> str:
    return "–" if cents is None else format_eur(cents)


def _vz(cents: int | None) -> str:
    """Wie der Filter `|vz`: rot raus, gruen rein, null ohne Farbe."""
    if not cents:
        return ""
    return "neg" if cents < 0 else "pos"


# ------------------------------------------------------------------ Skalen und Ticks

def schoene_ticks(niedrig: float, hoch: float, anzahl: int = 5) -> list[float]:
    """Runde Achsenwerte, die `niedrig` bis `hoch` ueberdecken, in hoechstens `anzahl` Abschnitten.

    Gewaehlt wird der KLEINSTE runde Schritt (1, 2, 5 je Zehnerpotenz, 2,5 ab der
    Zehn), der das schafft -- so bekommt eine Achse 5 bis 6 Werte statt 3.
    "40.000" erkennt man im Vorbeigehen, "43.725" zwingt zum Lesen.
    """
    if hoch <= niedrig:
        hoch = niedrig + 1
    kleinste = 10 ** (math.floor(math.log10(hoch - niedrig)) - 2)
    for potenz in range(8):
        groesse = kleinste * 10 ** potenz
        for faktor in (1, 2, 2.5, 5) if groesse >= 10 else (1, 2, 5):
            schritt = groesse * faktor
            erster = math.floor(niedrig / schritt + 1e-9) * schritt
            letzter = math.ceil(hoch / schritt - 1e-9) * schritt
            if round((letzter - erster) / schritt) <= anzahl:
                return [round(erster + i * schritt, 10)
                        for i in range(round((letzter - erster) / schritt) + 1)]
    return [niedrig, hoch]


def _links(beschriftungen: list[str]) -> int:
    """Linker Rand so breit wie der laengste Achsenwert in Telefonschrift."""
    return max(48, ZIFFER * max((len(b) for b in beschriftungen), default=0) + 12)


def _skala(d0: float, d1: float, r0: float, r1: float) -> Callable[[float], float]:
    spanne = d1 - d0 or 1.0
    return lambda v: r0 + (v - d0) / spanne * (r1 - r0)


@dataclass
class _Rahmen:
    hoehe: int
    links: int
    teile: list[str] = field(default_factory=list)

    @property
    def rechte_kante(self) -> int:
        return BREITE - RECHTS

    @property
    def untere_kante(self) -> int:
        return self.hoehe - UNTEN

    def x_monat(self, n: int) -> Callable[[int], float]:
        """Position des i-ten Monats; ein einzelner steht in der Mitte."""
        if n == 1:
            return lambda i: (self.links + self.rechte_kante) / 2
        return _skala(0, n - 1, self.links, self.rechte_kante)


# ------------------------------------------------------------------ Datenklassen

@dataclass(frozen=True)
class Linie:
    """Eine Monatsreihe. `cents` hat je Monat einen Wert oder None (Luecke)."""
    name: str
    farbe: str                          # Name einer CSS-Variable, z. B. "--accent"
    cents: list[int | None]
    art: str = "linie"                  # linie | beides (Linie mit Punkten)
    #: Das Vorzeichen ist eine Richtung (Kontostand, Cashflow): die Tabelle
    #: faerbt rot und gruen (vorzeichenfarbe). Eine Grenze hat keine.
    richtung: bool = True
    #: Monate (Index), deren Punkt in --bad steht, etwa unter der Untergrenze.
    auffaellig: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Grenze:
    """Ein fester Wert quer durchs Diagramm: Untergrenze, Deckel, Ziel."""
    name: str
    cents: int
    farbe: str
    #: Die Flaeche darunter leicht getoent: dort faengt das Problem an.
    flaeche: bool = False


@dataclass(frozen=True)
class Balken:
    cents: int
    farbe: str
    #: Steht in Tooltip und Tabelle, z. B. "gemessen" oder "vorausgerechnet".
    merkmal: str = ""


# ------------------------------------------------------------------ Bausteine

def _achse_y(g: _Rahmen, ticks: list[float], y: Callable[[float], float]) -> None:
    for t in ticks:
        g.teile.append(f'<line class="gitter" x1="{g.links}" x2="{g.rechte_kante}" '
                       f'y1="{_f(y(t))}" y2="{_f(y(t))}"/>')
        g.teile.append(f'<text class="tm" x="{g.links - 6}" y="{_f(y(t))}" dy=".35em" '
                       f'text-anchor="end">{_e(euro_ganz(t))}</text>')
    g.teile.append(f'<line class="achse" x1="{g.links}" x2="{g.rechte_kante}" '
                   f'y1="{g.untere_kante}" y2="{g.untere_kante}"/>')


def _monatsschritt(monate: Sequence[dt.date]) -> int:
    """Jeder wievielte Monat beschriftet wird: hoechstens sieben, auf Quartal oder Jahr."""
    for schritt in (1, 2, 3, 6, 12, 24):
        if math.ceil(len(monate) / schritt) <= 7:
            return schritt
    return 60


def _achse_x_monate(g: _Rahmen, monate: Sequence[dt.date], x: Callable[[int], float]) -> None:
    """Monate als `26-09` (datum-iso: nur Achsen kuerzen), ausgerichtet auf Quartale und Jahre."""
    schritt = _monatsschritt(monate)
    for i, m in enumerate(monate):
        if (m.year * 12 + m.month - 1) % schritt:
            continue
        g.teile.append(f'<text class="tm" x="{_f(x(i))}" y="{g.untere_kante + 6}" dy="1em" '
                       f'text-anchor="middle">{m:%y-%m}</text>')


def _legende(eintraege: list[tuple[str, str, str]]) -> str:
    """(Name, CSS-Variable, `linie` | `flaeche` | `punkt`). Ab zwei Eintraegen immer, bei einem nie.

    Der Schluessel spiegelt die Marke im Diagramm: Linie, Flaeche oder Punkt.
    """
    if len(eintraege) < 2:
        return ""
    teile = []
    for name, farbe, art in eintraege:
        klasse = {"flaeche": ' class="fl"', "punkt": ' class="pt"'}.get(art, "")
        stil = (f"background:var({farbe})" if art in ("flaeche", "punkt")
                else f"border-color:var({farbe})")
        teile.append(f'<span><i{klasse} style="{stil}"></i>{_e(name)}</span>')
    return f'<div class="legende">{"".join(teile)}</div>'


def _tabelle(kopf: list[str], zeilen: list[list[tuple[str, str]]]) -> str:
    """Zeilen aus (Text, Klasse). Die erste Spalte ist der Monat, alle anderen Zahlen."""
    th = "".join(f"<th{' class=\"num\"' if i else ''}>{_e(k)}</th>" for i, k in enumerate(kopf))
    tr = "".join("<tr>" + "".join(
        f'<td class="{"num " if i else "nobr "}{klasse}">{_e(text)}</td>'
        for i, (text, klasse) in enumerate(z)) + "</tr>" for z in zeilen)
    return (f'<details><summary>Tabelle</summary><div class="wrap"><table class="klein">'
            f'<thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div></details>')


def _figur(titel: str, untertitel: str, legende: str, svg: str, tabelle: str,
           anker: str) -> Markup:
    sub = f'<div class="sub">{_e(untertitel)}</div>' if untertitel else ""
    kennung = f' id="{_e(anker)}"' if anker else ""
    return Markup(f'<figure class="chart"{kennung}><figcaption>{_e(titel)}</figcaption>{sub}'
                  f'{legende}{svg}{tabelle}</figure>')


def leer(titel: str, text: str = "Keine Daten für diese Auswahl.", anker: str = "") -> Markup:
    kennung = f' id="{_e(anker)}"' if anker else ""
    return Markup(f'<figure class="chart"{kennung}><figcaption>{_e(titel)}</figcaption>'
                  f'<div class="sub">{_e(text)}</div></figure>')


def _svg(g: _Rahmen, beschreibung: str, tips: list, xs: list[float] | None = None) -> str:
    daten = f' data-tips="{_e(json.dumps(tips, ensure_ascii=False))}"'
    if xs is not None:
        daten += f' data-xs="{_e(json.dumps([round(v, 1) for v in xs]))}"'
    return (f'<svg viewBox="0 0 {BREITE} {g.hoehe}" role="img" aria-label="{_e(beschreibung)}"'
            f'{daten}>{"".join(g.teile)}</svg>')


def _grenzen(g: _Rahmen, grenzen: Sequence[Grenze], y: Callable[[float], float]) -> None:
    for gr in grenzen:
        hoehe = g.untere_kante - y(gr.cents)
        if gr.flaeche and hoehe > 0:
            g.teile.append(f'<rect x="{g.links}" y="{_f(y(gr.cents))}" '
                           f'width="{g.rechte_kante - g.links}" height="{_f(hoehe)}" '
                           f'style="fill:var({gr.farbe});opacity:.07"/>')
        g.teile.append(f'<line class="linie" x1="{g.links}" x2="{g.rechte_kante}" '
                       f'y1="{_f(y(gr.cents))}" y2="{_f(y(gr.cents))}" '
                       f'style="stroke:var({gr.farbe})"/>')


def _grenzen_legende(grenzen: Sequence[Grenze]) -> list[tuple[str, str, str]]:
    """Die Grenze mit ihrem Wert: er steht sonst nirgends als Text."""
    return [(f"{gr.name} {format_eur(gr.cents)}", gr.farbe, "linie") for gr in grenzen]


# ------------------------------------------------------------------ Linien

def _pfade(punkte: list[tuple[float, float | None]]) -> list[list[tuple[float, float]]]:
    """Zusammenhaengende Laeufe; ein fehlender Wert unterbricht die Linie."""
    laeufe: list[list[tuple[float, float]]] = []
    lauf: list[tuple[float, float]] = []
    for px, py in punkte:
        if py is None:
            if lauf:
                laeufe.append(lauf)
            lauf = []
        else:
            lauf.append((px, py))
    if lauf:
        laeufe.append(lauf)
    return laeufe


def _kappe(serien: Sequence[Linie]) -> int | None:
    """Der zweithoechste Wert der ersten Reihe, wenn der hoechste ein Ausreisser ist."""
    werte = sorted((v for v in serien[0].cents if v is not None), reverse=True)
    if len(werte) > 1 and werte[1] > 0 and werte[0] > werte[1] * KAPPE_FAKTOR:
        return werte[1]
    return None


def _y_bereich(serien: Sequence[Linie], grenzen: Sequence[Grenze], mit_null: bool,
               kappe: int | None) -> tuple[float, float]:
    werte = [v for s in serien for v in s.cents if v is not None]
    werte = [min(v, kappe) for v in werte] if kappe is not None else werte
    werte += [gr.cents for gr in grenzen] + ([0] if mit_null else [])
    return min(werte), max(werte)


def _linie_zeichnen(g: _Rahmen, s: Linie, x: Callable[[int], float],
                    y: Callable[[float], float], kappe: int | None) -> None:
    oben = y(kappe) if kappe is not None else None     # darueber: am oberen Rand
    punkte = [(x(i), None if v is None else (y(v) if oben is None else max(y(v), oben)))
              for i, v in enumerate(s.cents)]
    for lauf in _pfade(punkte):
        pfad = " ".join(f"{'M' if i == 0 else 'L'}{_f(a)},{_f(b)}" for i, (a, b) in enumerate(lauf))
        g.teile.append(f'<path class="linie" d="{pfad}" style="stroke:var({s.farbe})"/>')
    if s.art != "beides":
        return
    radius = 4 if len(s.cents) <= PUNKT_DICHT else 2.5
    for i, (px, py) in enumerate(punkte):
        if py is None:
            continue
        farbe, r = ("--bad", radius + 1.5) if i in s.auffaellig else (s.farbe, radius)
        g.teile.append(f'<circle cx="{_f(px)}" cy="{_f(py)}" r="{r}" '
                       f'style="fill:var({farbe});stroke:var(--panel);stroke-width:{LUECKE}"/>')


def _kappe_beschriften(g: _Rahmen, s: Linie, x: Callable[[int], float], kappe: int) -> None:
    """Die Linie laeuft sichtbar aus dem Bild; ihr Wert steht am oberen Rand."""
    for i, v in enumerate(s.cents):
        if v is not None and v > kappe:
            g.teile.append(f'<text class="tm" x="{_f(x(i) + 6)}" y="{OBEN}" dy="1em">'
                           f'↑ {_e(euro_ganz(v))}</text>')


def _tips_linien(monate: Sequence[dt.date], serien: Sequence[Linie],
                 grenzen: Sequence[Grenze]) -> list:
    return [[f"{m:%Y-%m}",
             [[s.name, _euro(s.cents[i]), s.farbe] for s in serien if s.cents[i] is not None]
             + [[gr.name, _euro(gr.cents), gr.farbe] for gr in grenzen]]
            for i, m in enumerate(monate)]


def _tabelle_linien(monate: Sequence[dt.date], serien: Sequence[Linie]) -> str:
    zeilen = [[(f"{m:%Y-%m}", "")] + [(_euro(s.cents[i]), _vz(s.cents[i]) if s.richtung else "")
                                       for s in serien]
              for i, m in enumerate(monate)]
    return _tabelle(["Monat"] + [s.name for s in serien], zeilen[::-1])


def linien(titel: str, monate: Sequence[dt.date], serien: Sequence[Linie], *,
           grenzen: Sequence[Grenze] = (), untertitel: str = "", hoehe: int = 260,
           mit_null: bool = False, kappen: bool = False, auffaellig_name: str = "",
           beschreibung: str = "", anker: str = "") -> Markup:
    """Monatsreihen als Linien, mit Grenzen, Fadenkreuz und Tabelle.

    `mit_null`: die Null gehoert in die Skala (ein Kontostand, der ins Minus
    kann). `kappen`: ein Ausreisser in der ersten Reihe schneidet die Achse ab
    (KAPPE_FAKTOR). `auffaellig_name`: Legende fuer die roten Punkte.
    """
    if not monate or not any(v is not None for s in serien for v in s.cents):
        return leer(titel, anker=anker)
    kappe = _kappe(serien) if kappen else None
    ticks = schoene_ticks(*_y_bereich(serien, grenzen, mit_null, kappe))
    g = _Rahmen(hoehe, links=_links([euro_ganz(t) for t in ticks]))
    y = _skala(ticks[0], ticks[-1], g.untere_kante, OBEN)
    x = g.x_monat(len(monate))
    _achse_y(g, ticks, y)
    _achse_x_monate(g, monate, x)
    _grenzen(g, grenzen, y)
    for s in serien:
        _linie_zeichnen(g, s, x, y, kappe)
    if kappe is not None:
        _kappe_beschriften(g, serien[0], x, kappe)
    g.teile.append(f'<line class="kreuz" x1="0" x2="0" y1="{OBEN}" y2="{g.untere_kante}"/>')
    eintraege = [(s.name, s.farbe, "linie") for s in serien] + _grenzen_legende(grenzen)
    if auffaellig_name and any(s.auffaellig for s in serien):
        eintraege.append((auffaellig_name, "--bad", "punkt"))
    svg = _svg(g, beschreibung or f"{titel}: {', '.join(s.name for s in serien)}",
               _tips_linien(monate, serien, grenzen), [x(i) for i in range(len(monate))])
    return _figur(titel, untertitel, _legende(eintraege), svg,
                  _tabelle_linien(monate, serien), anker)


# ------------------------------------------------------------------ Saeulen

def _saeule_pfad(x0: float, y_null: float, y_wert: float, breite: float) -> str:
    """Eine Saeule von der Null bis zum Wert, am Wertende 4 Einheiten abgerundet."""
    oben, unten = min(y_null, y_wert), max(y_null, y_wert)
    if unten - oben < 0.5:
        unten = oben + 0.5
    r = min(4.0, breite / 2, unten - oben)
    x1 = x0 + breite
    if y_wert <= y_null:            # positiv: rund oben
        return (f"M{_f(x0)},{_f(unten)} L{_f(x0)},{_f(oben + r)} "
                f"Q{_f(x0)},{_f(oben)} {_f(x0 + r)},{_f(oben)} L{_f(x1 - r)},{_f(oben)} "
                f"Q{_f(x1)},{_f(oben)} {_f(x1)},{_f(oben + r)} L{_f(x1)},{_f(unten)} Z")
    return (f"M{_f(x0)},{_f(oben)} L{_f(x0)},{_f(unten - r)} "     # negativ: rund unten
            f"Q{_f(x0)},{_f(unten)} {_f(x0 + r)},{_f(unten)} L{_f(x1 - r)},{_f(unten)} "
            f"Q{_f(x1)},{_f(unten)} {_f(x1)},{_f(unten - r)} L{_f(x1)},{_f(oben)} Z")


def _tips_saeulen(monate: Sequence[dt.date], name: str, balken: Sequence[Balken],
                  zusatz: Sequence[Linie], grenzen: Sequence[Grenze]) -> list:
    tips = []
    for i, (m, b) in enumerate(zip(monate, balken, strict=True)):
        kopf = f"{m:%Y-%m}" + (f" · {b.merkmal}" if b.merkmal else "")
        zeilen = [[name, _euro(b.cents), b.farbe]]
        zeilen += [[z.name, _euro(z.cents[i]), None] for z in zusatz]
        zeilen += [[gr.name, _euro(gr.cents), gr.farbe] for gr in grenzen]
        tips.append([kopf, zeilen])
    return tips


def _tabelle_saeulen(monate: Sequence[dt.date], name: str, balken: Sequence[Balken],
                     zusatz: Sequence[Linie]) -> str:
    merkmal = any(b.merkmal for b in balken)
    kopf = ["Monat"] + ([""] if merkmal else []) + [name] + [z.name for z in zusatz]
    zeilen = []
    for i, (m, b) in enumerate(zip(monate, balken, strict=True)):
        zeile = [(f"{m:%Y-%m}", "")] + ([(b.merkmal, "muted")] if merkmal else [])
        zeile.append((_euro(b.cents), _vz(b.cents)))
        zeile += [(_euro(z.cents[i]), _vz(z.cents[i]) if z.richtung else "") for z in zusatz]
        zeilen.append(zeile)
    return _tabelle(kopf, zeilen[::-1])


def saeulen(titel: str, monate: Sequence[dt.date], name: str, balken: Sequence[Balken], *,
            legende: Sequence[tuple[str, str]], grenzen: Sequence[Grenze] = (),
            zusatz: Sequence[Linie] = (), untertitel: str = "", hoehe: int = 240,
            beschreibung: str = "", anker: str = "") -> Markup:
    """Eine Saeule je Monat, positiv wie negativ von der Null aus. Die Saeule ist das Ziel.

    Jede Saeule bringt ihre Farbe mit (gemessen, vorausgerechnet, unter Ziel);
    `legende` nennt diese Farben als (Name, CSS-Variable). `zusatz`: Reihen, die
    nur in Tooltip und Tabelle stehen.
    """
    if not monate:
        return leer(titel, anker=anker)
    werte = [b.cents for b in balken] + [gr.cents for gr in grenzen] + [0]
    ticks = schoene_ticks(min(werte), max(werte))
    g = _Rahmen(hoehe, links=_links([euro_ganz(t) for t in ticks]))
    y = _skala(ticks[0], ticks[-1], g.untere_kante, OBEN)
    _achse_y(g, ticks, y)
    band = (g.rechte_kante - g.links) / len(monate)
    dicke = min(SAEULE_MAX, band * 0.7)
    _achse_x_monate(g, monate, lambda i: g.links + band * (i + .5))
    for i, b in enumerate(balken):
        pfad = _saeule_pfad(g.links + band * i + (band - dicke) / 2, y(0), y(b.cents), dicke)
        g.teile.append(f'<path d="{pfad}" style="fill:var({b.farbe})"/>')
    g.teile.append(f'<line class="achse" x1="{g.links}" x2="{g.rechte_kante}" '
                   f'y1="{_f(y(0))}" y2="{_f(y(0))}"/>')
    _grenzen(g, grenzen, y)
    for i, m in enumerate(monate):     # Trefferflaechen zuletzt: liegen ueber allem
        g.teile.append(f'<rect class="treffer" data-i="{i}" tabindex="0" '
                       f'x="{_f(g.links + band * i)}" y="{OBEN}" width="{_f(band)}" '
                       f'height="{g.untere_kante - OBEN}" aria-label="{m:%Y-%m}"/>')
    eintraege = [(n, f, "flaeche") for n, f in legende] + _grenzen_legende(grenzen)
    svg = _svg(g, beschreibung or titel, _tips_saeulen(monate, name, balken, zusatz, grenzen))
    return _figur(titel, untertitel, _legende(eintraege), svg,
                  _tabelle_saeulen(monate, name, balken, zusatz), anker)
