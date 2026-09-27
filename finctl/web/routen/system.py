"""Zwei Knoepfe, die den Bestand als Ganzes betreffen: Neuberechnen und Sicherung."""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from finctl.web.basis import (
    conn,
)

router = APIRouter()


@router.post("/api/update")
async def api_update():
    """Import new statements, re-apply the rulebook, verify -- in one click.

    The same three commands the handbook lists, because dropping PDFs in a
    folder and then switching to a terminal is the step at which a monthly
    routine stops being monthly.

    Synchronous on purpose. It takes seconds, and a background job would need
    somewhere to report failure to; here the caller simply waits and is told
    what happened, including when a statement was rejected.
    """
    from finctl import ops

    c = conn()
    try:
        return ops.update(c)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    finally:
        c.close()


@router.post("/api/backup")
async def api_backup(request: Request):
    """Write a snapshot to the folder named in backup.yaml.

    No target has to be given here: having to type the full path every time is
    most of the reason the backup had never actually been run. Where it goes
    is answered once, in the configuration -- and later in the setup wizard.
    """
    from finctl import ops

    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()
    try:
        return ops.write_backup(Path(body["to"]) if body.get("to") else None)
    except ops.KeinBackupZielError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "no database to back up"}, status_code=400)
    except OSError as exc:
        # The usual cause is the cloud folder not being mounted, and
        # "permission denied on <path>" says that far better than a generic
        # failure would.
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


@router.get("/api/backup/ziel")
async def api_backup_ziel_lesen():
    """Wohin gesichert wird, ob es erreichbar ist und wann zuletzt."""
    from finctl import ops

    return ops.sicherungsstand()


@router.post("/api/backup/ziel")
async def api_backup_ziel_setzen(request: Request):
    """Das Backup-Ziel setzen -- erst pruefen, dann speichern.

    GEPRUEFT WIRD VOR DEM SPEICHERN, und zwar durch einen Schreibversuch.
    Ein Ziel, das sich nicht beschreiben laesst, ins Dashboard einzutragen
    hiesse, den Nutzer in dem Glauben zu lassen, er sei gesichert -- und das
    faellt erst in dem Moment auf, in dem es darauf ankommt.

    Geschrieben wird nach backup_custom.yaml, nicht nach backup.yaml: dort
    steht, was das Archiv enthaelt und warum zwoelf Staende bleiben.
    """
    from finctl import ops, overlays

    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()

    ordner = str(body.get("directory") or "").strip()
    if not ordner:
        return JSONResponse({"error": "Kein Ordner angegeben."}, status_code=400)
    if fehler := ops.ziel_pruefen(ordner):
        return JSONResponse({"error": fehler}, status_code=400)

    felder: dict = {"directory": ordner}
    if body.get("keep") not in (None, ""):
        try:
            anzahl = int(body["keep"])
        except (TypeError, ValueError):
            return JSONResponse({"error": "Anzahl ist keine Zahl."},
                                status_code=400)
        if anzahl < 1:
            return JSONResponse(
                {"error": "Mindestens ein Stand muss bleiben — sonst loescht "
                          "das Sichern den letzten, bevor es den neuen hat."},
                status_code=400)
        felder["keep"] = anzahl

    overlays.sicherung_setzen(felder)
    return ops.sicherungsstand()
