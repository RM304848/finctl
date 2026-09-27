"""Einen Auszug waehlen und erkennen lassen -- fuer die Einrichtung.

Wer das Werkzeug zum ersten Mal oeffnet, weiss, bei welcher Bank er ist, aber
nicht, was ein Parserprofil ist. Deshalb geht es andersherum: eine Datei aus
dem Online-Banking waehlen, die App sagt, was sie erkennt -- Bank, Konto,
Zeitraum, ob der Auszug aufgeht -- und fuellt damit das Kontoformular. Kennt
sie den Export nicht, ordnet man die Spalten selbst zu.

Die Datei wird zum Erkennen nur gelesen, in einem Wegwerfordner. Abgelegt
wird sie erst, wenn ein Konto sie aufnimmt, in dessen Auszugsordner.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter()

#: Groesser ist kein Kontoauszug, sondern ein Irrtum -- und ein Speicherproblem.
GROESSE_MAX = 20 * 1024 * 1024
ENDUNGEN = (".csv", ".pdf", ".txt")


def _fehler(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


async def _inhalt(datei: UploadFile) -> tuple[str, bytes]:
    name = Path(datei.filename or "auszug").name
    if Path(name).suffix.lower() not in ENDUNGEN:
        raise ValueError("Erwartet wird eine CSV-, PDF- oder Textdatei aus dem Online-Banking.")
    daten = await datei.read(GROESSE_MAX + 1)
    if len(daten) > GROESSE_MAX:
        raise ValueError("Die Datei ist größer als 20 MB -- das ist kein Kontoauszug.")
    return name, daten


def _zusammenfassung(parser, ergebnis) -> dict:
    from finctl.ingest.importer import reconcile

    bank = getattr(getattr(parser, "profil", None), "bank", None) or parser.profile_id
    kopf = ergebnis.header
    return {"profil": parser.profile_id, "bank": bank,
            "konto": kopf.account_hint if kopf.account_hint != bank else None,
            "von": kopf.period_start, "bis": kopf.period_end,
            "umsaetze": len(ergebnis.transactions),
            "anfang_cents": kopf.balance_start_cents, "ende_cents": kopf.balance_end_cents,
            "abgestimmt": reconcile(ergebnis).ok, "warnungen": ergebnis.warnings,
            "beispiele": [{"datum": t.booking_date, "cents": t.amount_cents,
                           "text": (t.counterparty or t.raw_text or "")[:60]}
                          for t in ergebnis.transactions[-5:]]}


@router.post("/api/einlesen/erkennen")
async def api_erkennen(datei: UploadFile = File(...)):
    """Welche Bank, welches Konto, ob der Auszug aufgeht -- oder die Spalten."""
    from finctl.ingest import importer, zuordnung

    try:
        name, daten = await _inhalt(datei)
    except ValueError as exc:
        return _fehler(str(exc))
    with tempfile.TemporaryDirectory(prefix="finctl-erkennen-") as tmp:
        pfad = Path(tmp) / name
        pfad.write_bytes(daten)
        try:
            seiten = importer.extract_pages(pfad)
        except Exception as exc:   # ein kaputtes PDF soll eine Antwort geben, keinen 500er
            return _fehler(f"Die Datei lässt sich nicht lesen: {exc}")
        parser = importer.detect(seiten, pfad)
        if parser is not None:
            try:
                return {"erkannt": _zusammenfassung(parser, parser.parse(seiten, pfad)),
                        "konten": _konten_mit(parser.profile_id)}
            except ValueError as exc:
                return _fehler(f"Erkannt als {parser.profile_id}, aber nicht lesbar: {exc}")
        if pfad.suffix.lower() == ".pdf":
            return _fehler("Diesen PDF-Auszug kennt die App noch nicht. Viele Banken bieten "
                           "im Online-Banking einen CSV-Export an -- der lässt sich zuordnen.")
        try:
            return {"analyse": zuordnung.analysieren("\n".join(seiten))}
        except ValueError as exc:
            return _fehler(str(exc))


def _konten_mit(profil: str) -> list[str]:
    """Konten, die dieses Profil schon lesen -- dorthin kann die Datei direkt."""
    from finctl import konten as _kn

    return [k["id"] for k in _kn.laden() if k.get("parser_profile") == profil]


def _zuordnung_aus(roh: str) -> tuple[dict, list[str]]:
    try:
        daten = json.loads(roh)
    except ValueError as exc:
        raise ValueError("Zuordnung ist kein JSON") from exc
    return dict(daten.get("zuordnung") or {}), [str(s) for s in daten.get("spalten") or []]


@router.post("/api/einlesen/vorschau")
async def api_vorschau(datei: UploadFile = File(...), zuordnung: str = Form(...)):
    """Mit einer Zuordnung lesen, ohne sie zu speichern -- geht der Auszug auf?"""
    from finctl.ingest import importer
    from finctl.ingest import zuordnung as _zo
    from finctl.ingest.csvbank import CsvBankParser

    try:
        name, daten = await _inhalt(datei)
        werte, spalten = _zuordnung_aus(zuordnung)
        parser = CsvBankParser(_zo.profil_aus_zuordnung("vorschau", werte, spalten))
        with tempfile.TemporaryDirectory(prefix="finctl-vorschau-") as tmp:
            pfad = Path(tmp) / name
            pfad.write_bytes(daten)
            seiten = importer.extract_pages(pfad)
            if not parser.matches("\n".join(seiten), pfad):
                raise ValueError("Die Kopfzeile passt nicht zu den gewählten Spalten.")
            return {"vorschau": _zusammenfassung(parser, parser.parse(seiten, pfad))}
    except ValueError as exc:
        return _fehler(str(exc))


@router.post("/api/einlesen/profil")
async def api_profil(request_body: dict):
    """Eine bestaetigte Zuordnung als eigenes Profil in config/ speichern."""
    from finctl.ingest import zuordnung as _zo

    try:
        kennung = str(request_body.get("kennung") or "").strip()
        pid = _zo.speichern(kennung, dict(request_body.get("zuordnung") or {}),
                            [str(s) for s in request_body.get("spalten") or []])
    except (ValueError, TypeError) as exc:
        return _fehler(str(exc))
    return {"ok": True, "profil": pid}


@router.post("/api/einlesen/ablegen")
async def api_ablegen(datei: UploadFile = File(...), konto: str = Form(...)):
    """Die Datei in den Auszugsordner eines Kontos legen. Ueberschreibt nie.

    Liegt dort schon eine gleichnamige Datei mit anderem Inhalt, bekommt die
    neue eine Nummer -- zwei Exporte desselben Monats heissen bei manchen
    Banken gleich.
    """
    from finctl import konten as _kn

    try:
        name, daten = await _inhalt(datei)
    except ValueError as exc:
        return _fehler(str(exc))
    k = next((x for x in _kn.laden() if x["id"] == konto), None)
    if k is None:
        return _fehler(f"Unbekanntes Konto {konto}.")
    ordner = _kn.auszugsordner(k)
    ordner.mkdir(parents=True, exist_ok=True)
    ziel, n = ordner / name, 2
    while ziel.exists() and ziel.read_bytes() != daten:
        ziel, n = ordner / f"{Path(name).stem}-{n}{Path(name).suffix}", n + 1
    if not ziel.exists():
        ziel.write_bytes(daten)
    return {"ok": True, "pfad": str(ziel)}
