"""HANDBUCH.md im Dashboard lesbar machen.

Die Seiten tragen kuenftig einen Satz und einen Link; was eine Zahl bedeutet
und wie sie zustande kommt, steht im Handbuch. Ein Link auf einen Dateinamen
waere dafuer nutzlos -- er muesste erst gefunden und in einem Editor geoeffnet
werden, und genau das tut niemand mitten in einer Frage.

Handgeschrieben statt Bibliothek, wie die Diagramme: das Werkzeug laeuft ohne
Netz, und eine Abhaengigkeit fuer sechs Markdown-Formen waere teurer als die
sechs Formen. Unterstuetzt wird, was HANDBUCH.md wirklich benutzt --
Ueberschriften, Absaetze, Listen, Tabellen, Codebloecke, Trennlinien, fett und
`code`. Alles andere erscheint als Absatz, nicht als Fehler.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

#: Das Handbuch gehoert zum CODE, nicht zu den Daten -- deshalb neben dem
#: Paket gesucht und nicht im Arbeitsverzeichnis. Als `Path("HANDBUCH.md")`
#: fand es sich nur von der Projektwurzel aus; eine installierte App haette
#: eine leere Seite gezeigt und jeden Fusszeilenlink ins Leere.
#:
#: ZWEI ORTE, EINE DATEI. Im Repository liegt sie an der Wurzel, wo man sie
#: bearbeitet; ins Paket geht eine Kopie unter `finctl/`, weil ein Rad nur
#: mitnimmt, was innerhalb eines Pakets liegt. `tests/test_paket.py` haelt
#: beide byteweise gleich -- eine Kopie ohne Test waere eine zweite Wahrheit.
_IM_REPO = Path(__file__).resolve().parent.parent.parent / "HANDBUCH.md"
_IM_PAKET = Path(__file__).resolve().parent.parent / "HANDBUCH.md"
PFAD = next((p for p in (_IM_REPO, _IM_PAKET) if p.exists()), Path("HANDBUCH.md"))

#: Die Ueberschriften, auf die die Seiten verlinken: `### /konten` -> `konten`.
_ANKER_EBENE = 3


def anker(ueberschrift: str) -> str:
    """Aus `### /konten` wird `konten` -- der Anker, den `kf.fuss` ansteuert."""
    # Umlaute ausgeschrieben statt verschluckt: aus "Bestände" wird sonst
    # "best-nde", und einen solchen Anker schreibt niemand von Hand richtig.
    text = ueberschrift.strip().lower()
    for umlaut, ersatz in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, ersatz)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def anker_liste(pfad: Path | str = PFAD) -> set[str]:
    """Welche Anker es gibt. Die Fusspruefung haelt die Links damit sauber."""
    text = Path(pfad).read_text(encoding="utf-8") if Path(pfad).exists() else ""
    return {anker(z.lstrip("#").strip()) for z in text.splitlines()
            if z.startswith("#" * _ANKER_EBENE + " ")}


def abschnitte(pfad: Path | str = PFAD) -> dict[str, list[str]]:
    """Je Anker der Ebene drei seine Zeilen -- fuer die Wortpruefung."""
    out: dict[str, list[str]] = {}
    laufend = None
    for zeile in (Path(pfad).read_text(encoding="utf-8").splitlines()
                  if Path(pfad).exists() else []):
        if zeile.startswith("#" * _ANKER_EBENE + " "):
            laufend = anker(zeile.lstrip("#"))
            out[laufend] = []
        elif zeile.startswith("#"):
            laufend = None
        elif laufend:
            out[laufend].append(zeile)
    return out


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    # Nur Ziele innerhalb des Werkzeugs; eine Adresse im Netz waere genau die
    # Abhaengigkeit, die dieses Werkzeug nicht hat.
    return re.sub(r"\[([^\]]+)\]\((/[^)]*|#[^)]*)\)", r'<a class="plain" href="\2">\1</a>',
                  text)


def _tabelle(zeilen: list[str]) -> str:
    aus = ['<div class="wrap"><table>']
    for i, zeile in enumerate(zeilen):
        if set(zeile.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        tag = "th" if i == 0 else "td"
        zellen = [z.strip() for z in zeile.strip().strip("|").split("|")]
        aus.append("<tr>" + "".join(f"<{tag}>{_inline(z)}</{tag}>" for z in zellen) + "</tr>")
    return "\n".join(aus) + "</table></div>"


def als_html(pfad: Path | str = PFAD) -> str:
    quelle = Path(pfad)
    if not quelle.exists():
        return "<p>HANDBUCH.md fehlt.</p>"
    aus: list[str] = []
    absatz: list[str] = []
    liste: list[str] = []
    tabelle: list[str] = []
    code: list[str] | None = None

    def absatz_schliessen() -> None:
        nonlocal absatz, liste, tabelle
        if absatz:
            aus.append("<p>" + _inline(" ".join(absatz)) + "</p>")
            absatz = []
        if liste:
            aus.append("<ul>" + "".join(f"<li>{_inline(p)}</li>" for p in liste) + "</ul>")
            liste = []
        if tabelle:
            aus.append(_tabelle(tabelle))
            tabelle = []

    for zeile in quelle.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("```"):
            if code is None:
                absatz_schliessen()
                code = []
            else:
                aus.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            continue
        if code is not None:
            code.append(zeile)
            continue
        if not zeile.strip():
            absatz_schliessen()
            continue
        if zeile.startswith("#"):
            absatz_schliessen()
            ebene = len(zeile) - len(zeile.lstrip("#"))
            text = zeile.lstrip("#").strip()
            stufe = min(ebene, 3)
            kennung = f' id="{anker(text)}"' if ebene == _ANKER_EBENE else ""
            aus.append(f"<h{stufe}{kennung}>{_inline(text)}</h{stufe}>")
            continue
        if zeile.startswith("---"):
            absatz_schliessen()
            aus.append("<hr>")
            continue
        if zeile.lstrip().startswith("|"):
            if absatz or liste:
                absatz_schliessen()
            tabelle.append(zeile)
            continue
        punkt = re.match(r"\s*(?:[-*]|\d+\.)\s+(.*)", zeile)
        if punkt:
            if absatz or tabelle:
                absatz_schliessen()
            liste.append(punkt.group(1))
            continue
        if liste:
            # Fortsetzungszeile eines Listenpunkts, nicht ein neuer Absatz.
            liste[-1] += " " + zeile.strip()
            continue
        if tabelle:
            absatz_schliessen()
        absatz.append(zeile.strip())
    absatz_schliessen()
    return "\n".join(aus)
