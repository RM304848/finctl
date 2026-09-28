"""Die App und die Tuer davor.

Bound to 127.0.0.1 only, and the data never leaves the machine. There is no
CDN dependency either: the page ships its own CSS and a little vanilla
JavaScript, so the dashboard works with no network at all.

Anything saved here is written with source='manual', so it is sticky:
`categorize --recompute` rebuilds every rule-derived split and never touches
what you decided by hand.

Die Seiten selbst stehen in `finctl/web/routen/`, je Seitengruppe ein Modul.
Hier stehen nur noch die App, die Anmeldung und das Zusammenhaengen -- diese
Datei war 3.985 Zeilen lang und enthielt 57 Routen, und eine Seite zu finden
hiess, an allen anderen vorbeizuscrollen.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from finctl import ops as _ops  # noqa: F401 -- schliesst die Module an

# Namen, die ausserhalb an `finctl.web.server` haengen: die Tests holen sie
# hier ab, und `finctl serve` startet `finctl.web.server:app`. Sie stehen
# inzwischen woanders -- hier bleiben sie erreichbar, damit die Aufteilung
# keine Aufruferaenderung erzwingt.
from finctl.web.basis import (  # noqa: F401
    CONFIG_DIR,
    DB_PATH,
    TEMPLATES,
    _bindung_text,
    betrag,
    conn,
    euro,
)
from finctl.web.routen import (
    abos,
    annahmen,
    auswertung,
    buchungen,
    einlesen,
    einrichtung,
    energie,
    geteilt,
    immobilien,
    kategorien,
    konten,
    kredite,
    planung,
    regeln,
    renten,
    start,
    system,
    ziele,
)
from finctl.web.routen.annahmen import _setting_pruefen  # noqa: F401

app = FastAPI(title="Finance OS", docs_url=None, redoc_url=None)

def _anmeldeseite(falsch: bool = False) -> str:
    """Dieselben Farben und Formen wie die App, in beiden Themen.

    Frueher stand die Seite hier als eigenes HTML mit festen Farben: immer
    dunkel, auch wenn der Rechner hell eingestellt war, und bei jeder
    Farbaenderung der App vergessen.
    """
    return TEMPLATES.env.get_template("anmeldung.html").render(falsch=falsch)


def _ohne_anmeldung(request: Request) -> bool:
    """Pruefvorschau: `finctl serve --ohne-passwort`, und nur von diesem Rechner.

    Doppelt gesichert: der Schalter startet nur auf 127.0.0.1, und selbst wenn
    die Umgebungsvariable anders gesetzt waere, gilt sie nur fuer Aufrufe,
    die von der Loopback-Adresse kommen.
    """
    import os

    if os.environ.get("FINCTL_OHNE_PASSWORT") != "1":
        return False
    return bool(request.client and request.client.host in ("127.0.0.1", "::1"))


@app.middleware("http")
async def _modul_aus(request: Request, call_next):
    """Seiten ausgeschalteter Module antworten mit einem Hinweis statt der Seite.

    Aus der Navigation verschwinden sie ohnehin; das hier faengt Lesezeichen
    und alte Links. Nur Seiten: die Schnittstellen darunter bleiben, weil sie
    nichts zeigen und ein Umschalten mitten im Speichern nichts abbrechen soll.
    Steht VOR `_tuer` im Code und laeuft damit NACH der Anmeldung.
    """
    from finctl import module as _module

    pfad = request.url.path
    if request.method == "GET" and not pfad.startswith("/api/"):
        m = _module.modul_der_seite(pfad)
        if m is not None and m.id not in _module.aktive():
            return TEMPLATES.TemplateResponse(request, "modul_aus.html",
                                              {"modul": m}, status_code=404)
    return await call_next(request)


@app.middleware("http")
async def _tuer(request: Request, call_next):
    """Anmeldung und Herkunftspruefung, beide nur wo sie noetig sind.

    Ohne gesetztes Passwort aendert sich gar nichts -- auf 127.0.0.1 waere
    eine Abfrage nur Reibung. Die Herkunftspruefung gilt dagegen IMMER: eine
    fremde Webseite kann auch auf localhost einen POST schicken, und das ist
    heute schon so.
    """
    from finctl.web import auth as _auth

    if (request.method not in ("GET", "HEAD", "OPTIONS")
            and not _auth.herkunft_passt(request.headers.get("origin"),
                                         request.headers.get("host"))):
        return JSONResponse(
            {"error": "Aufruf von einer fremden Seite abgelehnt."},
            status_code=403)

    if (_auth.passwort_gesetzt() and request.url.path != "/login"
            and not _ohne_anmeldung(request)
            and not _auth.token_gilt(request.cookies.get(_auth.COOKIE))):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
        return HTMLResponse(_anmeldeseite(), status_code=401)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_form() -> HTMLResponse:
    return HTMLResponse(_anmeldeseite())


@app.post("/login")
async def login(request: Request):
    from finctl.web import auth as _auth

    formular = await request.form()
    if not _auth.passwort_stimmt(str(formular.get("passwort") or "")):
        return HTMLResponse(_anmeldeseite(falsch=True), status_code=401)
    antwort = RedirectResponse("/monatsabschluss", status_code=303)
    antwort.set_cookie(_auth.COOKIE, _auth.token_bauen(),
                       max_age=_auth.GUELTIG_SEKUNDEN, httponly=True,
                       samesite="lax")
    return antwort

# Die Reihenfolge ist Lesereihenfolge, keine Bedeutung: keine zwei Routen
# teilen sich einen Pfad, also entscheidet sie nichts.
for _teil in (start, buchungen, konten, abos, auswertung, immobilien, kredite,
              planung, ziele, renten, annahmen, regeln, kategorien, energie, geteilt,
              system, einrichtung, einlesen):
    app.include_router(_teil.router)
