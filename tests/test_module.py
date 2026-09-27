"""Die Module halten sich an finctl/module.py.

Was dort je Modul steht -- Seiten, Dateien, was es braucht und nutzt -- ist
nur so viel wert, wie der Code sich daran haelt. Diese Pruefungen lesen die
Importe aller Dateien und die Seiten der App und vergleichen.

`OFFEN` ist eine Ratsche wie `ZU_VERZWEIGT` in test_lint.py: die bekannten
Verstoesse, jeder mit dem Grund. Die Liste darf kuerzer werden und nichts
dazubekommen -- und ein behobener Verstoss muss hier gestrichen werden,
sonst faellt die Pruefung ebenfalls um.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from finctl import module

PAKET = Path(module.__file__).resolve().parent
WURZEL = PAKET.parent

#: (von, nach) -> warum es noch so ist.
OFFEN = {
}


def besitzer(datei: str) -> str:
    """Wem eine Datei unter finctl/ gehoert: 'kern', 'app' oder ein Modul.

    Der laengste passende Eintrag gewinnt -- `forecast/ziele.py` gehoert den
    Zielen, der Rest von `forecast/` der Prognose.
    """
    kandidaten = [(p, "kern") for p in module.KERN] + [(p, "app") for p in module.APP]
    kandidaten += [(p, m.id) for m in module.MODULE for p in m.dateien]
    passend = [(len(p), wer) for p, wer in kandidaten
               if datei == p or (p.endswith("/") and datei.startswith(p))]
    return max(passend)[1] if passend else "?"


def _dateien() -> list[str]:
    return sorted(p.relative_to(WURZEL).as_posix() for p in PAKET.rglob("*.py"))


_IMPORT = re.compile(r"^\s*from\s+(finctl(?:\.\w+)*)\s+import\s+\(?([\w ,\n]+?)\)?\s*$"
                     r"|^\s*import\s+(finctl(?:\.\w+)+)", re.M)


def _ziel(modulname: str) -> str | None:
    pfad = WURZEL / modulname.replace(".", "/")
    if pfad.with_suffix(".py").exists():
        return pfad.with_suffix(".py").relative_to(WURZEL).as_posix()
    if (pfad / "__init__.py").exists():
        return (pfad / "__init__.py").relative_to(WURZEL).as_posix()
    return None


def importe(datei: str) -> set[str]:
    """Welche Dateien unter finctl/ eine Datei importiert."""
    text = (WURZEL / datei).read_text(encoding="utf-8")
    ziele: set[str] = set()
    for von, namen, direkt in _IMPORT.findall(text):
        kandidaten = [direkt] if direkt else [von]
        if von:
            kandidaten += [f"{von}.{n.strip().split(' as ')[0]}"
                           for n in namen.split(",") if n.strip()]
        for k in kandidaten:
            if (z := _ziel(k)) and z != datei:
                ziele.add(z)
    return ziele


def erlaubt(von: str, nach: str) -> bool:
    if von == nach or nach == "kern" or von == "app":
        return True
    if von == "kern" or nach == "app":
        return False
    ziel = module.NACH_ID[nach]
    quelle = module.NACH_ID[von]
    if ziel.basis:
        return True
    if quelle.basis:
        return False
    return nach in quelle.benoetigt or nach in quelle.nutzt


def verstoesse() -> dict[tuple[str, str], list[str]]:
    funde: dict[tuple[str, str], list[str]] = {}
    for datei in _dateien():
        von = besitzer(datei)
        for ziel in importe(datei):
            nach = besitzer(ziel)
            if not erlaubt(von, nach):
                funde.setdefault((von, nach), []).append(f"{datei} -> {ziel}")
    return funde


# ------------------------------------------------------------------ Aufbau

def test_jede_datei_gehoert_genau_einem():
    ohne = [d for d in _dateien() if besitzer(d) == "?"]
    assert not ohne, f"Dateien ohne Modul -- in finctl/module.py eintragen: {ohne}"


def test_benoetigt_und_nutzt_nennen_vorhandene_module():
    for m in module.MODULE:
        for anderes in (*m.benoetigt, *m.nutzt):
            assert anderes in module.NACH_ID, f"{m.id} nennt unbekanntes Modul {anderes}"
            assert not module.NACH_ID[anderes].basis, (
                f"{m.id} nennt die Basis ({anderes}) -- die ist immer da")
        if m.basis:
            assert not m.benoetigt and not m.nutzt, f"Basis {m.id} haengt an einem Modul"


def test_kein_modul_benoetigt_sich_im_kreis():
    for m in module.MODULE:
        assert m.id not in module.abhaengige(m.id), f"{m.id} benoetigt sich selbst"


def test_importe_halten_sich_an_die_module():
    funde = verstoesse()
    neu = {k: v for k, v in funde.items() if k not in OFFEN}
    assert not neu, "Neue Abhaengigkeiten gegen finctl/module.py:\n" + "\n".join(
        f"  {a} -> {b}: {', '.join(v)}" for (a, b), v in neu.items())
    behoben = [k for k in OFFEN if k not in funde]
    assert not behoben, f"Behoben -- aus OFFEN streichen: {behoben}"


def test_die_pruefung_erkennt_einen_verstoss():
    """Die Regeln selbst: Basis darf nicht an ein Modul, ein Modul nur an Genanntes."""
    assert not erlaubt("regeln", "kredite")
    assert not erlaubt("kern", "regeln")
    assert erlaubt("immobilien", "kredite")
    assert not erlaubt("kredite", "immobilien")
    assert erlaubt("ziele", "prognose")
    assert erlaubt("app", "ziele")


def _get_seiten(routen, praefix: str = "") -> set[str]:
    aus: set[str] = set()
    for r in routen:
        unter = getattr(r, "original_router", None)
        if unter is not None:
            kontext = getattr(r, "include_context", None)
            aus |= _get_seiten(unter.routes, praefix + (getattr(kontext, "prefix", "") or ""))
        elif "GET" in (getattr(r, "methods", None) or set()):
            aus.add(praefix + r.path)
    return aus


def test_jede_seite_gehoert_einem_modul_oder_der_app():
    from finctl.web.server import app

    seiten = {s for s in _get_seiten(app.routes)
              if not s.startswith("/api/") and s not in ("/openapi.json",)}
    ohne = sorted(s for s in seiten
                  if s not in module.APP_SEITEN
                  and module.modul_der_seite(s.split("{")[0].rstrip("/")) is None)
    assert not ohne, f"Seiten ohne Modul -- in finctl/module.py eintragen: {ohne}"


# ------------------------------------------------------------------ Schalter

@pytest.fixture
def leer(tmp_path, monkeypatch):
    monkeypatch.setattr(module.overlays, "CONFIG_DIR", tmp_path)
    return tmp_path


def test_ohne_datei_ist_alles_an(leer):
    assert module.aktive() == {m.id for m in module.MODULE}


def test_einschalten_nimmt_mit_was_gebraucht_wird(leer):
    module.erststart(leer)
    assert module.aktive() == {m.id for m in module.MODULE if m.basis}
    an = module.setzen({"ziele": True})
    assert {"ziele", "prognose"} <= an


def test_ausschalten_nimmt_mit_was_davon_abhaengt(leer):
    an = module.setzen({"prognose": False})
    assert "prognose" not in an and "ziele" not in an
    assert "kredite" in an, "nur Nutzen ist kein Brauchen"


def test_die_basis_laesst_sich_nicht_ausschalten(leer):
    an = module.setzen({"regeln": False, "einlesen": False})
    assert {"regeln", "einlesen"} <= an


def test_erststart_nur_ohne_konten(leer):
    (leer / "accounts.yaml").write_text("accounts: []\n", encoding="utf-8")
    assert module.erststart(leer) is False
    assert not (leer / module.DATEI).exists()


# ------------------------------------------------------------------ Oberflaeche

@pytest.fixture
def nur_basis():
    """Die Schalterdatei in der Testkopie, danach wieder weg."""
    datei = module.overlays.CONFIG_DIR / module.DATEI
    vorher = datei.read_text(encoding="utf-8") if datei.exists() else None
    module.overlays.schreiben(module.DATEI, module.SCHLUESSEL,
                              {m.id: False for m in module.MODULE if not m.basis}, "")
    try:
        yield
    finally:
        if vorher is None:
            datei.unlink()
        else:
            datei.write_text(vorher, encoding="utf-8")


def test_ausgeschaltete_seiten_verschwinden_aus_der_navigation(nur_basis):
    from fastapi.testclient import TestClient

    from finctl.web.server import app

    client = TestClient(app)
    kopf = client.get("/einrichtung").text
    kopf = kopf[kopf.index("<header>"):kopf.index("</header>")]
    assert 'href="/rueckblick"' in kopf and 'href="/regeln"' in kopf
    for weg in ("/abos", "/ziele", "/kredite", "/strom", "/projekte"):
        assert f'href="{weg}"' not in kopf, weg
    assert "Vorausschau" not in kopf, "eine leere Gruppe zeigt keine Ueberschrift"

    antwort = client.get("/abos")
    assert antwort.status_code == 404 and "ausgeschaltet" in antwort.text


@pytest.mark.langsam
def test_mit_der_basis_allein_antwortet_jede_seite(nur_basis):
    from fastapi.testclient import TestClient

    from finctl.web.server import app

    client = TestClient(app)
    kopf = client.get("/").text
    kopf = kopf[kopf.index("<header>"):kopf.index("</header>")]
    links = sorted(set(re.findall(r'href="(/[^"]*)"', kopf)))
    assert links
    tot = [link for link in links if client.get(link).status_code != 200]
    assert not tot, f"Mit der Basis allein kaputt: {tot}"
