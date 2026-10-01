"""Die Design-Konventionen, geprueft aus docs/design_conventions.yaml.

Die Regeln stehen in der YAML-Datei, mit Grund; hier steht nur, WIE eine Art
von Pruefung ausgefuehrt wird. Eine neue Konvention ist damit ein Eintrag in
der Datei und kein neuer Test, der irgendwo in test_web.py verschwindet --
genau so waren die bisherigen ueber zwei Dateien und zweitausend Zeilen
verstreut.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from finctl.web.server import app

KONVENTIONEN = Path("docs/design_conventions.yaml")
VORLAGEN = Path("finctl/web/templates")
REGELN = yaml.safe_load(KONVENTIONEN.read_text(encoding="utf-8"))["regeln"]
client = TestClient(app)


@functools.cache
def _seite(pfad: str):
    """Jede Seite einmal rendern.

    Mehrere Pruefungen hier lesen dieselben Seiten, und eine Seite zu rendern
    kostet Sekunden. Keine dieser Pruefungen schreibt etwas, also bleibt die
    einmal gerenderte Seite fuer alle gueltig.
    """
    return client.get(pfad)


def _mit(art: str) -> list[dict]:
    return [r for r in REGELN if (r.get("pruefung") or {}).get("art") == art]


def _ids(regel):
    return regel["id"]


def _ohne_kommentare(text: str) -> str:
    # Jinja-Kommentare erklaeren oft, warum es etwas NICHT gibt -- sie selbst
    # sind keine Verwendung.
    return re.sub(r"\{#.*?#\}", " ", text, flags=re.S)


# ------------------------------------------------------------------- Datei

def test_every_rule_says_what_and_why():
    ids = [r["id"] for r in REGELN]
    assert len(ids) == len(set(ids)), "doppelte Kennung"
    for r in REGELN:
        assert r.get("regel") and r.get("warum"), r["id"]


def test_every_check_has_an_implementation():
    bekannt = {"vorlagen_verbieten", "punktdezimalen", "reihenfolge", "datei_enthaelt",
               "ueberschriften", "kein_cdn", "fliesstext", "konfig_fuss", "navigation",
               "zwecktext", "textdubletten", "kommentare", "seitenrhythmus",
               "kontrast", "themengleich", "nur_in"}
    for r in REGELN:
        art = (r.get("pruefung") or {}).get("art")
        assert art is None or art in bekannt, f"{r['id']}: unbekannte Pruefung {art}"


def test_every_exception_is_listed_as_open():
    """Eine Ausnahme ohne `offen` wird zur stillen Erlaubnis."""
    for r in REGELN:
        if (r.get("pruefung") or {}).get("ausnahmen"):
            assert r.get("offen"), r["id"]


# -------------------------------------------------------------- Pruefungen

@pytest.mark.parametrize("regel", _mit("vorlagen_verbieten"), ids=_ids)
def test_templates_avoid_what_the_convention_forbids(regel):
    p = regel["pruefung"]
    funde = []
    for vorlage in sorted(VORLAGEN.glob("*.html")):
        if vorlage.name in (p.get("ausnahmen") or []):
            continue
        text = _ohne_kommentare(vorlage.read_text(encoding="utf-8"))
        for muster in p["muster"]:
            if re.search(muster, text):
                funde.append(f"{vorlage.name}: {muster}")
    assert not funde, f"{regel['id']}: {funde}"


@pytest.mark.parametrize("regel", _mit("vorlagen_verbieten"), ids=_ids)
def test_an_exception_that_is_fixed_is_removed_from_the_list(regel):
    """Sonst bleibt eine behobene Altlast als Erlaubnis stehen."""
    p = regel["pruefung"]
    for name in p.get("ausnahmen") or []:
        text = _ohne_kommentare((VORLAGEN / name).read_text(encoding="utf-8"))
        assert any(re.search(m, text) for m in p["muster"]), (
            f"{regel['id']}: {name} ist behoben -- aus `ausnahmen` und `offen` streichen")


@pytest.mark.parametrize("regel", _mit("nur_in"), ids=_ids)
def test_something_is_done_in_one_place_only(regel):
    """Anders als `ausnahmen` keine Altlast: die genannten Dateien SIND der eine Ort."""
    p = regel["pruefung"]
    funde = []
    for vorlage in sorted(VORLAGEN.glob("*.html")):
        if vorlage.name in p["dateien"]:
            continue
        text = _ohne_kommentare(vorlage.read_text(encoding="utf-8"))
        funde += [f"{vorlage.name}: {m}" for m in p["muster"] if re.search(m, text)]
    assert not funde, f"{regel['id']}: {funde}"


@pytest.mark.parametrize("regel", _mit("punktdezimalen"), ids=_ids)
def test_no_input_field_offers_a_dot_as_decimal_separator(regel):
    for pfad in regel["pruefung"]["seiten"]:
        html = _seite(pfad).text
        schlimm = [v for v in re.findall(r'value="([^"]*)"', html)
                   if re.fullmatch(r"-?\d+\.\d+", v)]
        assert not schlimm, f"{pfad} schreibt Punktdezimalen: {schlimm[:5]}"


@pytest.mark.parametrize("regel", _mit("reihenfolge"), ids=_ids)
def test_order_within_a_file(regel):
    p = regel["pruefung"]
    text = Path(p["datei"]).read_text(encoding="utf-8")
    assert text.index(p["vorher"]) < text.index(p["nachher"]), regel["id"]


@pytest.mark.parametrize("regel", _mit("datei_enthaelt"), ids=_ids)
def test_a_file_carries_what_the_convention_needs(regel):
    p = regel["pruefung"]
    text = Path(p["datei"]).read_text(encoding="utf-8")
    fehlend = [m for m in p["muss"] if m not in text]
    assert not fehlend, f"{regel['id']}: {fehlend}"


@pytest.mark.parametrize("regel", _mit("seitenrhythmus"), ids=_ids)
def test_the_gap_between_two_blocks_comes_from_one_rule(regel):
    """Ein Block direkt unter `main` bringt seinen Abstand nicht selbst mit.

    Geprueft wird die Einrueckung: eine Zeile, die in Spalte null mit einem
    Tag beginnt, ist ein Block der Seite -- alles Eingerueckte gehoert in
    einen Kasten und darf seine eigenen Abstaende setzen.
    """
    p = regel["pruefung"]
    basis = Path(p["datei"]).read_text(encoding="utf-8")
    fehlend = [m for m in p["muss"] if m not in basis]
    assert not fehlend, f"{regel['id']}: base.html ohne {fehlend}"

    funde = []
    for vorlage in sorted(VORLAGEN.glob("*.html")):
        if vorlage.name in (p.get("ausnahmen") or []):
            continue
        text = _ohne_kommentare(vorlage.read_text(encoding="utf-8"))
        if (start := text.find("{% block body %}")) == -1:
            continue
        funde += [f"{vorlage.name}: {z[:60]}" for z in text[start:].splitlines()
                  if z.startswith("<") and re.search(p["muster"], z)]
    assert not funde, f"{regel['id']}: {funde}"


def _leuchtdichte(hexfarbe: str) -> float:
    """Relative Leuchtdichte nach WCAG 2.1."""
    kanaele = [int(hexfarbe[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (k / 12.92 if k <= 0.03928 else ((k + 0.055) / 1.055) ** 2.4
               for k in kanaele)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@pytest.mark.parametrize("regel", _mit("kontrast"), ids=_ids)
def test_contrast_holds_in_both_themes(regel):
    """Gerechnet aus den Tokens selbst, in hellem und dunklem Thema.

    Ein Blick auf den Knopf im eigenen Thema genuegt nicht: der Fehler sass
    im anderen.
    """
    p = regel["pruefung"]
    text = Path(p["datei"]).read_text(encoding="utf-8")
    fehlend = [m for m in p.get("muss", []) if m not in text]
    assert not fehlend, f"{regel['id']}: {fehlend}"

    hell = text[text.index(":root{"):text.index("@media (prefers-color-scheme:dark)")]
    dunkel = text[text.index("@media (prefers-color-scheme:dark)"):]
    dunkel = dunkel[:dunkel.index("}") + 1]
    gruende = p["grund"] if isinstance(p["grund"], list) else [p["grund"]]
    vorne = p["vorn"] if isinstance(p["vorn"], list) else [p["vorn"]]
    for thema, block in (("hell", hell), ("dunkel", dunkel)):
        werte = {}
        for token in (*vorne, *gruende):
            treffer = re.search(re.escape(token) + r":(#[0-9a-fA-F]{3,6})\b", block)
            assert treffer, f"{regel['id']}: {token} fehlt im Thema {thema}"
            farbe = treffer.group(1)
            if len(farbe) == 4:
                farbe = "#" + "".join(c * 2 for c in farbe[1:])
            werte[token] = _leuchtdichte(farbe)
        for vorn in vorne:
            for grund in gruende:
                a, b = werte[vorn], werte[grund]
                verhaeltnis = (max(a, b) + 0.05) / (min(a, b) + 0.05)
                assert verhaeltnis >= p["mindestens"], (
                    f"{regel['id']}: {vorn} {thema} auf {grund} nur {verhaeltnis:.2f}:1")


@pytest.mark.parametrize("regel", _mit("themengleich"), ids=_ids)
def test_the_dark_theme_is_the_same_whether_chosen_or_inherited(regel):
    """Das dunkle Thema steht zweimal in base.html. Weicht eines ab, sieht
    die App mit Schalter anders aus als ohne -- und niemand merkt es, weil
    man selten beides nebeneinander hat."""
    p = regel["pruefung"]
    text = Path(p["datei"]).read_text(encoding="utf-8")

    def tokens(anfang: str) -> dict[str, str]:
        block = text[text.index(anfang):]
        block = block[:block.index("}")]
        return dict(re.findall(r"(--[a-z-]+):(#[0-9a-fA-F]{3,6})\b", block))

    geerbt, gewaehlt = tokens(p["geraet"]), tokens(p["schalter"])
    assert geerbt, f"{p['geraet']} ohne Farben"
    assert geerbt == gewaehlt, (
        f"{regel['id']}: nur in einem Block oder verschieden: "
        f"{sorted(set(geerbt.items()) ^ set(gewaehlt.items()))}")


@pytest.mark.parametrize("regel", _mit("ueberschriften"), ids=_ids)
def test_every_heading_speaks_with_the_same_voice(regel):
    p = regel["pruefung"]
    css = Path(p["datei"]).read_text(encoding="utf-8")
    for css_regel in p["css_regeln"]:
        block = css[css.index(css_regel):]
        block = block[:block.index("}")]
        for muss in p["css_muss"]:
            assert muss in block, f"{css_regel} ohne {muss}"
    for vorlage in VORLAGEN.glob("*.html"):
        text = vorlage.read_text(encoding="utf-8")
        for tag in p["tags"]:
            stelle = 0
            while (stelle := text.find(tag, stelle)) != -1:
                kopf = text[stelle:text.find(">", stelle)]
                assert p["im_tag_verboten"] not in kopf, f"{vorlage.name}: {kopf[:60]}"
                stelle += 1


@pytest.mark.parametrize("regel", _mit("kein_cdn"), ids=_ids)
def test_no_page_loads_a_library_from_the_network(regel):
    p = regel["pruefung"]
    for seite in p["seiten"]:
        body = client.get(seite).text
        for muster in p["muster"]:
            assert muster not in body, f"{seite}: {muster}"


def _fliesstext(pfad: Path) -> str:
    """Nur das, was der Eigentuemer liest: ohne Skripte, Stile, Jinja, Tags."""
    s = pfad.read_text(encoding="utf-8")
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S)
    s = re.sub(r"\{#.*?#\}", " ", s, flags=re.S)
    s = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", s, flags=re.S)
    return re.sub(r"<[^>]+>", " ", s)


@pytest.mark.parametrize("pfad", sorted(VORLAGEN.glob("*.html")), ids=lambda p: p.name)
def test_no_template_states_a_number_that_will_age(pfad):
    [regel] = _mit("fliesstext")
    p = regel["pruefung"]
    erlaubt = re.compile(p["erlaubt"])
    text = _fliesstext(pfad)
    funde = []
    for art, muster in p["verboten"].items():
        for treffer in re.finditer(muster, text):
            umfeld = text[max(0, treffer.start() - 90):treffer.end() + 90]
            if erlaubt.search(umfeld):
                continue
            funde.append(f"{art} „{treffer.group(0)}“ in: …{' '.join(umfeld.split())}…")
    assert not funde, (
        f"{pfad.name} behauptet etwas, das veraltet:\n  " + "\n  ".join(funde)
        + "\n\nDrei Reparaturen: die Zahl live rendern, wenn sie der Punkt ist; "
          "streichen, wenn sie nur Illustration war; datieren, wenn sie ein Fund war.")


# ---------------------------------------------------------------- Textmenge
#
# Die Prosa dieser App lebt in vier Behaeltern -- Zwecksatz, Hinweiskasten,
# Hilfebereich und sonstiger Absatz. Sie werden einzeln gemessen statt die
# ganze Seite zu zaehlen: eine Tabelle mit vierzig Spaltenkoepfen ist kein
# Aufsatz, und ein Aufsatz in einem <td> waere sonst unsichtbar.


def _ohne_code(text: str) -> str:
    """Skripte, Stile und Jinja-Kommentare raus -- die liest niemand auf der Seite."""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    return re.sub(r"\{#.*?#\}", " ", text, flags=re.S)


def _worte(fragment: str) -> list[str]:
    """Was gelesen wird. Ein gerenderter Wert zaehlt als EIN Wort.

    Sonst spraengte eine lange Jinja-Zeile jedes Budget, und der Fuss mit
    seiner Dateiliste -- selbst ein `{{ }}`-Aufruf -- waere ein Aufsatz.
    """
    s = re.sub(r"\{\{.*?\}\}", " Wert ", fragment, flags=re.S)
    s = re.sub(r"\{%.*?%\}", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", " ")
    return [w for w in s.split() if any(c.isalnum() for c in w)]


def _bloecke(text: str, start: str, tag: str) -> list[tuple[int, str, bool]]:
    """Jeden Block `start` … `</tag>`, Verschachtelung mitgezaehlt.

    Drittes Feld: ob der Block sauber geschlossen war. Ein unbalancierter
    Block wird gemeldet, statt still halb gezaehlt zu werden.
    """
    auf, zu = re.compile(rf"<{tag}\b", re.I), re.compile(rf"</{tag}>", re.I)
    out = []
    for treffer in re.finditer(start, text):
        tiefe, pos, ende, heil = 1, treffer.end(), len(text), False
        while tiefe:
            a, z = auf.search(text, pos), zu.search(text, pos)
            if z is None:
                break
            if a is not None and a.start() < z.start():
                tiefe, pos = tiefe + 1, a.end()
                continue
            tiefe, pos = tiefe - 1, z.end()
            if not tiefe:
                ende, heil = z.start(), True
        out.append((treffer.start(), text[treffer.end():ende], heil))
    return out


def _bedingt(text: str, stelle: int) -> bool:
    """Steht die Stelle in einem `{% if %}` oder `{% for %}`?"""
    kopf = text[:stelle]
    auf = len(re.findall(r"\{%-?\s*(?:if|for)\b", kopf))
    zu = len(re.findall(r"\{%-?\s*end(?:if|for)\b", kopf))
    return auf > zu


def _hinweis_funde(roh: str, name: str, p: dict, funde: list[str],
                   sichtbar: list[str]) -> set[int]:
    """Die Hinweiskaesten pruefen; zurueck die Stellen der Begruessungsabsaetze."""
    willkommen: set[int] = set()
    for stelle, inneres, heil in _bloecke(roh, r'<div class="note[^"]*"[^>]*>', "div"):
        if not heil:
            funde.append("Hinweiskasten ohne schliessendes </div> — nicht messbar")
            continue
        n = len(_worte(inneres))
        # Die Begruessung (Regel app-zweck): eigenes Mass, nur wo erlaubt,
        # und ihre Absaetze zaehlen nicht einzeln.
        grenze, begruessung = p["note_woerter_max"], False
        if kopf := re.match(r'<div class="note willkommen"[^>]*>', roh[stelle:]):
            if name not in p["willkommen_nur"]:
                funde.append("Begruessung ausserhalb von " + ", ".join(p["willkommen_nur"]))
            grenze, begruessung = p["willkommen_woerter_max"], True
            ende = stelle + kopf.end() + len(inneres)
            willkommen |= {s for s, _i, _h in _bloecke(roh, r"<p\b[^>]*>", "p")
                           if stelle < s < ende}
        if n > grenze:
            funde.append(f"Hinweis mit {n} Woertern (hoechstens {grenze})")
        # Ein leerer Kasten ist ein Behaelter fuer eine Meldung, die erst beim
        # Speichern entsteht -- kein Text, der immer dasteht.
        if n and not begruessung and not _bedingt(roh, stelle):
            funde.append(f"Hinweis steht immer da: „{' '.join(_worte(inneres)[:8])}…“ — "
                         "ein Hinweis beschreibt einen Zustand und wird bedingt gerendert")
        sichtbar.append(inneres)
    return willkommen


def _zwecktext_funde(pfad: Path, p: dict) -> list[str]:
    roh = _ohne_code(pfad.read_text(encoding="utf-8"))
    teilvorlage = pfad.name.startswith(p["teilvorlagen_praefix"]) or pfad.name == "base.html"
    funde: list[str] = []
    sichtbar: list[str] = []

    zweck = _bloecke(roh, r'<p class="zweck"[^>]*>', "p")
    if teilvorlage:
        if zweck:
            funde.append("Teilvorlage traegt einen Zwecksatz — der gehoert auf die Seite")
    elif len(zweck) > 1:
        funde.append(f"{len(zweck)} Zwecksaetze (`p.zweck`), hoechstens einer gehoert unter die h1")
    for _stelle, inneres, _heil in zweck:
        n = len(_worte(inneres))
        if n > p["zweck_woerter_max"]:
            funde.append(f"Zwecksatz mit {n} Woertern (hoechstens {p['zweck_woerter_max']})")
        if re.search(r"<(br|ul|ol|li)\b", inneres, re.I):
            funde.append("Zwecksatz mit Liste oder Umbruch — dann ist es kein Satz")
        sichtbar.append(inneres)

    willkommen = _hinweis_funde(roh, pfad.name, p, funde, sichtbar)

    hilfe = _bloecke(roh, r'<details class="hilfe"[^>]*>', "details")
    summe = 0
    for _stelle, inneres, _heil in hilfe:
        n = len(_worte(inneres))
        summe += n
        if n > p["hilfe_woerter_max"]:
            funde.append(f"Hilfebereich mit {n} Woertern (hoechstens {p['hilfe_woerter_max']})")
        sichtbar.append(inneres)
    if summe > p["hilfe_summe_max"]:
        funde.append(f"{summe} Woerter Hilfe auf einer Seite (hoechstens {p['hilfe_summe_max']})")

    zweckstellen = {s for s, _i, _h in zweck}
    for stelle, inneres, _heil in _bloecke(roh, r"<p\b[^>]*>", "p"):
        if stelle in zweckstellen or stelle in willkommen:
            continue
        n = len(_worte(inneres))
        if n > p["absatz_woerter_max"]:
            funde.append(f"Absatz mit {n} Woertern: „{' '.join(_worte(inneres)[:8])}…“")
        sichtbar.append(inneres)

    sichtbar += re.findall(r'title="([^"]*)"', roh)
    text = " ".join(sichtbar)
    from test_namen import verbotene_namen

    for name in verbotene_namen():
        if re.search(rf"\b{re.escape(name)}\b", text):
            funde.append(f"nennt „{name}“ — ein Name, den man im Frontend nicht aendern kann")
    return funde


@pytest.mark.parametrize("pfad", sorted(VORLAGEN.glob("*.html")), ids=lambda p: p.name)
def test_a_page_says_its_purpose_in_one_sentence_and_nothing_else(pfad):
    [regel] = _mit("zwecktext")
    p = regel["pruefung"]
    if pfad.name in (p.get("ausnahmen") or []):
        pytest.skip("bekannte Altlast, steht unter `offen`")
    funde = _zwecktext_funde(pfad, p)
    assert not funde, (
        f"{pfad.name} traegt mehr Text als die Seite braucht:\n  " + "\n  ".join(funde)
        + "\n\nVier Ablagen: an das Feld, das es erklaert; in ein `details.hilfe`; "
          "in einen bedingt gerenderten Hinweis; sonst in HANDBUCH.md.")


@pytest.mark.parametrize("pfad", sorted(VORLAGEN.glob("*.html")), ids=lambda p: p.name)
def test_a_page_that_is_tidy_leaves_the_exception_list(pfad):
    """Sonst bleibt eine aufgeraeumte Seite als Erlaubnis stehen."""
    [regel] = _mit("zwecktext")
    p = regel["pruefung"]
    if pfad.name not in (p.get("ausnahmen") or []):
        pytest.skip("keine Ausnahme")
    assert _zwecktext_funde(pfad, p), (
        f"{pfad.name} haelt die Regel ein — aus `ausnahmen` und `offen` streichen")


def _saetze(pfad: Path, mindestlaenge: int) -> list[str]:
    """Jeder Satz der Vorlage, normalisiert -- Wiederholungen bleiben drin."""
    text = " ".join(_worte(_ohne_code(pfad.read_text(encoding="utf-8"))))
    out = []
    for satz in re.split(r"(?<=[.!?:])\s+", text):
        worte = satz.split()
        if len(worte) >= mindestlaenge:
            out.append(" ".join(w.lower() for w in worte))
    return out


@pytest.mark.parametrize("regel", _mit("textdubletten"), ids=_ids)
def test_an_explanation_is_written_in_exactly_one_place(regel):
    """Auch zweimal in DERSELBEN Vorlage ist zweimal: auf /monitor stand ein
    Absatz oben und unten noch einmal."""
    p = regel["pruefung"]
    wo: dict[str, list[str]] = {}
    for pfad in sorted(VORLAGEN.glob("*.html")):
        if pfad.name in (p.get("ausnahmen") or []):
            continue
        for satz in _saetze(pfad, p["mindestlaenge"]):
            wo.setdefault(satz, []).append(pfad.name)
    funde = [f"{' und '.join(stellen)}: „{satz[:70]}…“"
             for satz, stellen in wo.items() if len(stellen) > 1]
    assert not funde, (
        f"{regel['id']}: derselbe Satz steht mehrfach:\n  " + "\n  ".join(funde)
        + "\n\nBeim naechsten Mal wird eine Stelle korrigiert und die andere bleibt falsch.")


def _verboten_in_kommentaren(p: dict) -> dict[str, str]:
    """Die Muster aus den Konventionen, dazu die Namen aus dem Datenordner."""
    from test_namen import verbotene_namen

    muster = dict(p["verboten"])
    if namen := verbotene_namen():
        muster["Name"] = r"\b(" + "|".join(re.escape(n) for n in namen) + r")\b"
    return muster


def _kommentar_funde(pfad: Path, p: dict) -> list[str]:
    text = pfad.read_text(encoding="utf-8")
    funde, summe = [], 0
    for kommentar in re.findall(r"\{#(.*?)#\}", text, flags=re.S):
        worte = _worte(kommentar)
        summe += len(worte)
        if len(worte) > p["woerter_je_kommentar_max"]:
            funde.append(f"Kommentar mit {len(worte)} Woertern: „{' '.join(worte[:8])}…“")
        for art, muster in _verboten_in_kommentaren(p).items():
            treffer = re.search(muster, kommentar)
            if treffer:
                funde.append(f"{art} „{treffer.group(0)}“ im Kommentar — das ist Historie, "
                             "kein Grund")
    if summe > p["woerter_je_vorlage_max"]:
        funde.append(f"{summe} Woerter Kommentar (hoechstens {p['woerter_je_vorlage_max']})")
    return funde


@pytest.mark.parametrize("pfad", sorted(VORLAGEN.glob("*.html")), ids=lambda p: p.name)
def test_comments_give_a_reason_instead_of_telling_the_history(pfad):
    [regel] = _mit("kommentare")
    p = regel["pruefung"]
    if pfad.name in (p.get("ausnahmen") or []):
        pytest.skip("bekannte Altlast, steht unter `offen`")
    funde = _kommentar_funde(pfad, p)
    assert not funde, (
        f"{pfad.name}: Kommentare erzaehlen statt zu begruenden:\n  " + "\n  ".join(funde)
        + "\n\nEin Fund gehoert in notizen/archiv_notizen.yaml im Datenordner, "
          "ein Grund bleibt hier.")


def _navigationslinks() -> list[str]:
    kopf = _seite("/").text
    kopf = kopf[kopf.index("<header>"):kopf.index("</header>")]
    # Der Name im Kopf fuehrt auch auf eine Seite, ist aber kein Menuepunkt.
    return re.findall(r'<a class="(?!marke)[^"]*" href="(/[^"]*)"', kopf)


def test_every_page_says_which_files_feed_it():
    for pfad in _navigationslinks():
        html = _seite(pfad).text
        assert "Konfiguration" in html, pfad
        assert "finctl/web/templates/" in html, pfad
        assert "config/" in html, pfad


def test_the_config_footer_only_names_files_that_exist():
    [regel] = _mit("konfig_fuss")
    darf_fehlen = set(regel["pruefung"]["darf_fehlen"])
    genannt = set()
    for vorlage in VORLAGEN.glob("*.html"):
        text = vorlage.read_text(encoding="utf-8")
        genannt |= set(re.findall(r"config/[a-z_]+\.yaml", text))
        genannt |= set(re.findall(r"finctl/web/templates/[a-z_]+\.html", text))
    assert genannt
    fehlend = [p for p in sorted(genannt)
               if not Path(p).exists() and Path(p).name not in darf_fehlen]
    assert not fehlend, f"Fusszeile nennt nicht vorhandene Dateien: {fehlend}"


def test_every_page_links_to_a_handbook_section_that_exists():
    """Sonst verlagern wir die Erklaerungen hinter Links, die niemand prueft."""
    from finctl.web import handbuch as _hb

    vorhanden = _hb.anker_liste()
    tot = []
    for vorlage in sorted(VORLAGEN.glob("*.html")):
        text = vorlage.read_text(encoding="utf-8")
        for anker in re.findall(r"handbuch=['\"]([a-z0-9-]+)['\"]", text):
            if anker not in vorhanden:
                tot.append(f"{vorlage.name} → HANDBUCH.md#{anker}")
    assert not tot, f"Fusszeile zeigt ins Leere: {tot}"


def test_a_handbook_section_stays_shorter_than_the_page_it_explains():
    """Sonst wird das Handbuch in zwei Monaten das, was die Vorlagen waren."""
    from finctl.web import handbuch as _hb

    [regel] = _mit("konfig_fuss")
    grenze = regel["pruefung"]["handbuch_woerter_max"]
    # Nur die Abschnitte, auf die eine Seite zeigt: dorthin wandert die Prosa,
    # und nur dort droht die Halde. Der Monatsablauf darf ausfuehrlich sein.
    verlinkt = {a for vorlage in VORLAGEN.glob("*.html")
                for a in re.findall(r"handbuch=['\"]([a-z0-9-]+)['\"]",
                                    vorlage.read_text(encoding="utf-8"))}
    lang = {anker: n for anker, zeilen in _hb.abschnitte().items()
            if anker in verlinkt and (n := len(" ".join(zeilen).split())) > grenze}
    assert not lang, (f"zu lange Handbuchabschnitte (hoechstens {grenze} Woerter): {lang}")


def test_every_page_appears_in_exactly_one_navigation_group():
    [regel] = _mit("navigation")
    links = _navigationslinks()
    assert len(links) == len(set(links)), "ein Ziel steht doppelt in der Navigation"
    kopf = _seite("/").text
    kopf = kopf[kopf.index("<header>"):kopf.index("</header>")]
    assert re.findall(r'<span class="navlabel">(.*?)</span>', kopf) == regel["pruefung"]["gruppen"]
    for link in links:
        assert _seite(link).status_code == 200, f"{link} ist tot"
