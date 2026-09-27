"""finctl -- local financial ledger, forecast and decision engine.

Every command accepts --json so results can be consumed programmatically
rather than screen-scraped. That is deliberate: Claude Code is expected to
drive this tool when working through financial decisions, and it should use
the engine's own arithmetic rather than re-deriving it from the database.

No command in this file calls a language model. AI is used to *author* rules
during development; the compiled rules then execute deterministically.
"""

from __future__ import annotations

import json as jsonlib
import sqlite3
import sys
from pathlib import Path

import typer
import yaml

from finctl import konten as _konten
from finctl import ops as _ops
from finctl import pfade as _p
from finctl.ledger import db as ledger
from finctl.pfade import CONFIG_DIR

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local financial ledger, forecast and decision engine.",
)

DATA_DIR = _p.DATA_DIR
DB_PATH = DATA_DIR / "finance.db"


def _emit(payload: dict, as_json: bool, render) -> None:
    if as_json:
        typer.echo(jsonlib.dumps(payload, indent=2, ensure_ascii=False))
    else:
        render(payload)


def _require_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        # Es gibt vier Regeln dafuer, welcher Ordner der Datenordner ist
        # (`finctl/pfade.py`). Wer hier landet, hat den leeren erwischt -- und
        # die einzige nuetzliche Auskunft ist, WELCHE Regel gegriffen hat.
        # Blosses "run finctl init" legte ein zweites, leeres Hauptbuch an,
        # und das ist schlimmer als das urspruengliche Problem.
        if not CONFIG_DIR.exists():
            typer.secho(
                f"Kein config/ unter {CONFIG_DIR.parent}.\n"
                f"  `{_p.aus_umgebung('finctl')} ort` zeigt, wo gesucht wird "
                f"und warum dort.\n"
                f"  Liegen die Daten woanders: "
                f"`{_p.aus_umgebung('finctl')} ort --setzen <ordner>`.",
                fg=typer.colors.RED, err=True)
        else:
            typer.secho("No database yet. Run `finctl init` first.",
                        fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return ledger.connect(DB_PATH)


# ----------------------------------------------------------------- init


def _init_bericht(p: dict) -> None:
    """Was `finctl init` getan hat, in Worten."""
    typer.secho(f"Initialised {p['database']}", fg=typer.colors.GREEN)
    for name in p["startdateien_angelegt"]:
        typer.echo(f"  config/{name} angelegt -- Startwerte, bitte anpassen")
    if p["accounts_config_missing"]:
        typer.secho("  Noch kein Konto. Anlegen auf /einrichtung "
                    "oder in config/accounts.yaml.", fg=typer.colors.YELLOW)
    else:
        typer.echo(f"  {p['accounts_loaded']} accounts, "
                   f"{p['properties_loaded']} properties, "
                   f"{p['loans_loaded']} loans registered")



@app.command()
def init(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """Create the database and load the account register."""
    ledger.init_db(DB_PATH)
    # Was fehlt, wird angelegt -- ohne das sah ein neuer Nutzer nach `init`
    # als Erstes einen 500er auf der Startseite.
    from finctl import vorgaben as _vorgaben

    angelegt = _vorgaben.sicherstellen(CONFIG_DIR)
    # Eine neue Einrichtung beginnt mit der Basis; die Module schaltet man
    # in der Einrichtung dazu. Eine bestehende behaelt alles (finctl/module.py).
    from finctl import module as _module

    if _module.erststart(CONFIG_DIR):
        angelegt.append(_module.DATEI)
    conn = ledger.connect(DB_PATH)
    loaded, skipped, properties_loaded, loans_loaded = 0, 0, 0, 0
    try:
        # Register und Upsert stehen in finctl/konten.py, weil die Seite
        # /konten dieselbe Tabelle schreibt. Zweimal geschrieben hiesse: der
        # Editor kennt ein Feld, das init beim naechsten Lauf ueberbuegelt.
        register = _konten.laden(CONFIG_DIR)
        if register:
            for acc in register:
                loaded += _konten.in_db(conn, acc)
        else:
            skipped += 1

        # Ueber das Register, nicht an der Basisdatei vorbei: sonst kaeme
        # ein auf /immobilien angelegtes oder entferntes Objekt hier nie an.
        from finctl import objekte as _objekte

        objekte = _objekte.vorhandene(CONFIG_DIR)
        if objekte:
            for prop in objekte.values():
                # The AfA fields were previously writable only from the
                # dashboard, which is why every property showed 0 -- nothing
                # ever put a tax return's figures into the ledger.
                #
                # COALESCE, not plain overwrite: what the YAML declares wins,
                # and where it is silent the value typed into the dashboard
                # survives the next init rather than being blanked.
                conn.execute(
                    """
                    INSERT INTO properties
                        (id, name, address, acquired_on, status, planned_sale_on,
                         afa_rate_pct, afa_base_cents, afa_extra_annual_cents, afa_start,
                         purchase_price_cents, incidental_costs_cents,
                         land_share_pct, equity_cents, sale_price_cents)
                    VALUES (:id, :name, :address, :acquired_on, :status, :planned_sale_on,
                            :afa_rate_pct, :afa_base_cents, :afa_extra_annual_cents, :afa_start,
                            :purchase_price_cents, :incidental_costs_cents,
                            :land_share_pct, :equity_cents, :sale_price_cents)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name, address = excluded.address,
                        acquired_on = excluded.acquired_on, status = excluded.status,
                        planned_sale_on = excluded.planned_sale_on,
                        afa_rate_pct = COALESCE(excluded.afa_rate_pct, properties.afa_rate_pct),
                        afa_base_cents = COALESCE(excluded.afa_base_cents,
                                                  properties.afa_base_cents),
                        afa_extra_annual_cents = COALESCE(excluded.afa_extra_annual_cents,
                                                          properties.afa_extra_annual_cents),
                        afa_start = COALESCE(excluded.afa_start, properties.afa_start),
                        purchase_price_cents = COALESCE(excluded.purchase_price_cents,
                                                        properties.purchase_price_cents),
                        incidental_costs_cents = COALESCE(excluded.incidental_costs_cents,
                                                          properties.incidental_costs_cents),
                        land_share_pct = COALESCE(excluded.land_share_pct,
                                                  properties.land_share_pct),
                        equity_cents = COALESCE(excluded.equity_cents, properties.equity_cents),
                        -- Wie die uebrigen Zahlen: die YAML gewinnt, wo sie
                        -- etwas sagt, und ein im Frontend gesetzter Wert
                        -- ueberlebt das naechste init, wo sie schweigt.
                        sale_price_cents = COALESCE(excluded.sale_price_cents,
                                                    properties.sale_price_cents)
                    """,
                    {
                        "id": prop["id"],
                        "name": prop["name"],
                        "address": prop.get("address"),
                        "acquired_on": (str(prop["acquired_on"])
                                        if prop.get("acquired_on") else None),
                        "status": prop.get("status"),
                        "planned_sale_on": str(prop["planned_sale_on"])
                                           if prop.get("planned_sale_on") else None,
                        "afa_rate_pct": prop.get("afa_rate_pct"),
                        "afa_base_cents": prop.get("afa_base_cents"),
                        "afa_extra_annual_cents": prop.get("afa_extra_annual_cents"),
                        "afa_start": str(prop["afa_start"]) if prop.get("afa_start") else None,
                        "purchase_price_cents": prop.get("purchase_price_cents"),
                        "incidental_costs_cents": prop.get("incidental_costs_cents"),
                        "land_share_pct": prop.get("land_share_pct"),
                        "sale_price_cents": prop.get("sale_price_cents"),
                        "equity_cents": prop.get("equity_cents"),
                    },
                )
                properties_loaded += 1

            # Ein entferntes Objekt muss auch aus der Datenbank, sonst stuende
            # es weiter in der Vermoegensrechnung -- und wer dieses Werkzeug
            # uebernimmt, erbte fremde Wohnungen.
            #
            # NUR, WENN NICHTS DARAUF GEBUCHT IST. Eine Zeile zu loeschen, auf
            # die Splits zeigen, risse das Hauptbuch auseinander; solange
            # Buchungen daran haengen, ist das Objekt Teil der Geschichte und
            # nicht wegzuklicken.
            platzhalter = ",".join("?" * len(objekte))
            conn.execute(
                f"DELETE FROM properties WHERE id NOT IN ({platzhalter}) "
                f"AND NOT EXISTS (SELECT 1 FROM splits s "
                f"WHERE s.property_id = properties.id)",
                list(objekte))

        loans_file = CONFIG_DIR / "loans.yaml"
        if loans_file.exists():
            spec = yaml.safe_load(loans_file.read_text(encoding="utf-8")) or {}
            for loan in spec.get("loans", []):
                conn.execute(
                    """
                    INSERT INTO loans (id, name, lender, property_id,
                                       servicing_account_id, principal_cents,
                                       start_date, active)
                    VALUES (:id, :name, :lender, :property_id,
                            :servicing_account_id, :principal_cents,
                            :start_date, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name, lender = excluded.lender,
                        property_id = excluded.property_id,
                        servicing_account_id = excluded.servicing_account_id,
                        principal_cents = excluded.principal_cents,
                        start_date = excluded.start_date
                    """,
                    {
                        "id": str(loan["id"]),
                        "name": loan["name"],
                        "lender": loan.get("lender"),
                        "property_id": loan.get("property_id"),
                        "servicing_account_id": loan.get("servicing_account_id"),
                        "principal_cents": loan.get("principal_cents") or 0,
                        "start_date": str(loan.get("start_date") or "1970-01-01"),
                    },
                )
                loans_loaded += 1

        balances_file = CONFIG_DIR / "balances.yaml"
        if balances_file.exists():
            spec = yaml.safe_load(balances_file.read_text(encoding="utf-8")) or {}
            as_of = str(spec.get("as_of") or "")
            known = {r["id"] for r in conn.execute("SELECT id FROM accounts")}
            # Config owns this date's manual snapshots: clear them first, so an
            # entry removed from the file does not linger in the database.
            if as_of:
                conn.execute(
                    "DELETE FROM balance_snapshots WHERE as_of = ? AND source = 'manual'",
                    (as_of,),
                )
            for bal in spec.get("balances", []):
                acct = bal.get("account_id")
                if not acct or acct not in known or not as_of:
                    continue
                # Ein bewusst offener Wert ist keine Null und kein Fehler.
                # balances.yaml fuehrt einen Bestand mit `cents: null` und der
                # Begruendung "OFFEN: Wert zum Verkaufszeitpunkt" -- und daran
                # brach `finctl init` mit einem TypeError ab, mitten im Lauf,
                # nachdem Konten und Objekte schon geschrieben waren. Ein
                # dokumentiertes Unbekanntes darf den Import nicht anhalten.
                if bal.get("cents") is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO balance_snapshots (account_id, as_of, balance_cents, source)
                    VALUES (?,?,?,'manual')
                    ON CONFLICT(account_id, as_of) DO UPDATE SET
                        balance_cents = excluded.balance_cents
                    """,
                    (acct, as_of, int(bal["cents"])),
                )

        # Die Overlays zuletzt: sie sollen gewinnen, was immer die
        # Basisdateien davor gesagt haben.
        from finctl import overlays as _ov
        _ov.anwenden(conn)

        from finctl.tax import taxonomy as _tx
        if (CONFIG_DIR / "taxonomy.yaml").exists():
            _tx.load_into_db(conn)
            moved = _tx.apply_migrations(conn)
            hidden = _tx.deactivate_missing(conn)
            for label, n in moved.items():
                typer.secho(f"  remapped {n} splits: {label}", fg=typer.colors.BLUE)
            if hidden:
                typer.secho(f"  {hidden} retired categories hidden", fg=typer.colors.BLUE)
        conn.commit()
    finally:
        conn.close()

    payload = {
        "database": str(DB_PATH),
        "accounts_loaded": loaded,
        "accounts_config_missing": bool(skipped),
        "properties_loaded": properties_loaded,
        "loans_loaded": loans_loaded,
        "startdateien_angelegt": angelegt,
    }

    _emit(payload, json, _init_bericht)


# --------------------------------------------------------------- status


@app.command()
def abgleich(
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Beschreibt die Prognose dieselbe Welt wie das Ledger?

    Vergleicht Block fuer Block, was der Plan sagt, gegen das, was gebucht
    wurde. Eine Abweichung bedeutet eins von beidem: eine Regel fehlt, oder
    eine Annahme ist falsch. Beides will man wissen, bevor man zwanzig Jahre
    darauf rechnet.
    """
    from finctl.forecast import abgleich as ag

    conn = _require_db()
    try:
        result = ag.build(conn, ag.planned(conn))
    finally:
        conn.close()

    payload = {
        "von": result.von,
        "bis": result.bis,
        "months": result.months,
        "unassigned_cents": result.unassigned_cents,
        "rows": [
            {"block": r.block.id, "label": r.block.label,
             "planned_cents": r.planned_cents,
             "measured_cents": r.measured_cents,
             "delta_cents": r.delta_cents,
             "window_months": r.window_months, "note": r.note}
            for r in result.rows
        ],
    }

    def render(p: dict) -> None:
        typer.secho(f"Abgleich {p['von'][:7]} bis {p['bis'][:7]} — "
                    f"letzte {p['months']} vollständige Monate", bold=True)
        typer.echo(f"{'':26}{'geplant':>13}{'gemessen':>13}{'Δ':>13}")
        for row in p["rows"]:
            if not (row["planned_cents"] or row["measured_cents"]):
                continue
            colour = (typer.colors.RED if abs(row["delta_cents"]) > 5000
                      else typer.colors.GREEN)
            typer.secho(
                f"{row['label']:26}"
                f"{ledger.format_eur(row['planned_cents']):>13}"
                f"{ledger.format_eur(row['measured_cents']):>13}"
                f"{ledger.format_eur(row['delta_cents']):>13}", fg=colour)
            if row["note"]:
                typer.secho(f"  {row['note']}", fg=typer.colors.YELLOW)
        if p["unassigned_cents"]:
            typer.secho(
                f"  {ledger.format_eur(p['unassigned_cents'])} keinem Block "
                f"zugeordnet — das ist immer ein Fehler in der Aufteilung, "
                f"nie eine Eigenschaft der Daten.", fg=typer.colors.RED)

    _emit(payload, json, render)


@app.command()
def treffer(
    monate: int = typer.Option(6, "--monate", help="Wie viele vollständige Monate zurück."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Wie gut hätte die Prognose getroffen?

    Für jeden der letzten vollständigen Monate: die Prognose, wie sie am
    Monatsende davor ausgesehen hätte, gegen das Ist. Aus dem Ledger
    nachgerechnet, nichts gespeichert. Δ = Ist minus Prognose.
    """
    from finctl.forecast import treffer as tr

    conn = _require_db()
    try:
        r = tr.pruefen(conn, monate)
    finally:
        conn.close()

    payload = {
        "jahresbasis": [
            {"monat": j.monat.isoformat()[:7],
             "soll": j.soll, "ist": j.ist,
             "delta": {b: j.delta(b) for b in r["bloecke"]},
             "saldo_soll_cents": j.sparrate_soll, "saldo_ist_cents": j.sparrate_ist,
             "nicht_vorhersehbar_cents": j.unvorhersehbar_cents}
            for j in r["jahres"]],
        "konten": [
            {"monat": k.monat.isoformat()[:7], "konto": k.konto,
             "soll_cents": k.soll_cents, "ist_cents": k.ist_cents,
             "delta_cents": k.delta_cents, "ohne_kredit": k.ohne_kredit}
            for k in r["konten"]],
        "mittel": r.get("mittel"), "konten_mittel": r.get("konten_mittel"),
    }

    def render(p: dict) -> None:
        eur = ledger.format_eur
        typer.secho("Jahresbasis — Δ je Block (Ist minus Prognose)", bold=True)
        bloecke = list(r["bloecke"])
        typer.echo(f"{'Monat':9}" + "".join(f"{b[:10]:>12}" for b in bloecke)
                   + f"{'Saldo Δ':>14}{'unvorh.':>14}")
        for j in p["jahresbasis"]:
            zellen = "".join(f"{('—' if j['delta'][b] is None else eur(j['delta'][b])):>12}"
                             for b in bloecke)
            ds = j["saldo_ist_cents"] - j["saldo_soll_cents"]
            typer.secho(f"{j['monat']:9}{zellen}{eur(ds):>14}"
                        f"{eur(j['nicht_vorhersehbar_cents']):>14}",
                        fg=typer.colors.RED if abs(ds) > 50000 else None)
        if p["mittel"]:
            typer.echo(f"{'Ø':9}" + "".join(
                f"{('—' if p['mittel'][b] is None else eur(p['mittel'][b])):>12}"
                for b in bloecke) + f"{eur(p['mittel']['sparrate']):>14}")
        typer.secho("\nKonten — Prognose, Ist, Δ", bold=True)
        for k in p["konten"]:
            typer.echo(f"{k['monat']:9}{k['konto']:16}{eur(k['soll_cents']):>14}"
                       f"{eur(k['ist_cents']):>14}{eur(k['delta_cents']):>14}"
                       + ("  ohne Kreditbuchungen" if k["ohne_kredit"] else ""))
        for konto, w in (p["konten_mittel"] or {}).items():
            typer.echo(f"{'Ø':9}{konto:16}{'':28}{eur(w):>14}")

    _emit(payload, json, render)


@app.command()
def jahre(
    end: int = typer.Option(2045, help="Stichtag."),
    szenario: list[str] = typer.Option(
        None, "--szenario", help="Weitere Annahmedatei, mehrfach moeglich."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Wie viel Prozent von Barista FIRE stehen am Stichtag?

    Ein Szenario ist eine Annahmedatei, kein Feature:

        finctl jahre --szenario config/assumptions.pessimistisch.yaml
    """
    from datetime import date

    import yaml as _y

    from finctl import assumptions as ann
    from finctl.forecast import jahre as jm
    from finctl.forecast import ziele as zm

    def read(name):
        path = CONFIG_DIR / name
        return (_y.safe_load(path.read_text(encoding="utf-8")) or {}) \
            if path.exists() else {}

    goals = zm.merge_edits(read("goals.yaml"), read("ziele_custom.yaml"))
    from finctl import bestaende as _best

    _c = _require_db()
    try:
        balances = _best.zusammenfuehren(
            _c, read("balances.yaml"), read("balances_custom.yaml").get("overrides"))
    finally:
        _c.close()
    opening = zm.liquid_cents(balances)
    target = next((int(g.get("cents") or 0) for g in (goals.get("ziele") or [])
                   if g.get("id") == "barista-fire"), 0)

    # Unter welcher Inflation das Ziel gesetzt wurde. Ein Szenario mit anderer
    # Inflation muss es mitskalieren, sonst gewinnt das pessimistische.
    declared_inflation = next(
        (float(g.get("inflation_pa", 0.02)) for g in (goals.get("ziele") or [])
         if g.get("id") == "barista-fire"), 0.02)

    laeufe = [("Basis", ann.PATH)] + [(Path(s).stem, s) for s in (szenario or [])]
    conn = _require_db()
    try:
        base_year = date.today().year
        runs = []
        for name, path in laeufe:
            scaled = jm.target_under(path, declared_cents=target,
                                     declared_inflation=declared_inflation,
                                     years=end - base_year)
            runs.append((name, jm.project(conn, end_year=end,
                                          opening_cents=opening,
                                          toepfe=zm.toepfe(balances),
                                          puffer_cents=zm.puffer_cents(goals),
                                          policen_je_konto=zm.policen_je_konto(balances),
                                          target_cents=scaled,
                                          assumptions=path)))
    finally:
        conn.close()

    payload = {
        "opening_cents": opening, "target_cents": target, "end_year": end,
        "runs": [{"name": n, "final_cents": r.final_cents,
                  "frei_cents": r.years[-1].frei_cents if r.years else 0,
                  "target_cents": r.target_cents,
                  "pct_of_target": round(r.pct_of_target, 1),
                  "years": [{"year": y.year, "saving_cents": y.saving_cents,
                             "closing_cents": y.closing_cents,
                             "tagesgeld_cents": y.tagesgeld_cents,
                             "depot_cents": y.depot_cents,
                             "policen_cents": y.policen_cents,
                             "puffer_grenze_cents": y.puffer_grenze_cents,
                             "blocks": y.blocks} for y in r.years]}
                 for n, r in runs],
    }

    def render(p: dict) -> None:
        typer.secho(f"Ausgangsbestand {ledger.format_eur(p['opening_cents'])}"
                    f"  ·  Ziel {ledger.format_eur(p['target_cents'])}"
                    f"  ·  Stichtag {p['end_year']}", bold=True)
        typer.echo()
        # Ohne den Notgroschen: er ist Grundstock, kein Zielkapital.
        typer.echo(f"{'Szenario':22}{'Zielkapital':>16}{'Ziel':>16}{'%':>8}")
        for run in p["runs"]:
            colour = (typer.colors.GREEN if run["pct_of_target"] >= 100
                      else typer.colors.YELLOW if run["pct_of_target"] >= 50
                      else typer.colors.RED)
            typer.secho(f"{run['name']:22}"
                        f"{ledger.format_eur(run['frei_cents']):>16}"
                        f"{ledger.format_eur(run['target_cents']):>16}"
                        f"{run['pct_of_target']:>7.0f}%", fg=colour)

    _emit(payload, json, render)


group_app = typer.Typer(help="Vorgänge: was zu einer Sache gehört.")
app.add_typer(group_app, name="group")


@group_app.command("apply")
def group_apply(
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Nur zeigen, nichts schreiben."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """config/groups.yaml anwenden und transactions.group_id neu setzen.

    Setzt JEDE Zuordnung zurück, nicht nur die neu getroffenen -- sonst bliebe
    eine Transaktion in einer Gruppe, deren Regel inzwischen entfernt wurde.
    """
    from finctl.ledger import gruppen as gr

    conn = _require_db()
    try:
        gruppen = gr.load()
        treffer = gr.apply(conn, gruppen, dry_run=dry_run)
        rows = [] if dry_run else gr.summary(conn)
        ohne = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE group_id IS NULL").fetchone()[0]
    finally:
        conn.close()

    payload = {"regeln": len(gruppen), "treffer": treffer,
               "vorgaenge": rows, "ohne_gruppe": ohne, "dry_run": dry_run}

    def render(p: dict) -> None:
        typer.secho(f"{p['regeln']} Regeln"
                    + (" (dry run, nichts geschrieben)" if p["dry_run"] else ""),
                    bold=True)
        for row in p["vorgaenge"]:
            typer.echo(
                f"  {row['id']:16}{row['n']:5} Tx"
                f"{ledger.format_eur(row['cents']):>16}"
                f"   {row['von']} .. {row['bis']}"
                f"   {row['kategorien']} Kategorien")
        if p["dry_run"]:
            for key, n in p["treffer"].items():
                typer.echo(f"  {key:16}{n:5} Tx")
        typer.secho(f"  {p['ohne_gruppe']} Transaktionen ohne Vorgang",
                    fg=typer.colors.YELLOW)

    _emit(payload, json, render)


@app.command()
def status(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """Show what the ledger currently holds."""
    conn = _require_db()
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "accounts", "statements", "transactions", "splits",
                "properties", "loans", "rules",
            )
        }
        parsed = conn.execute(
            "SELECT id, display_name FROM accounts WHERE ingest_mode='parsed' AND active=1"
        ).fetchall()
        coverage = conn.execute(
            """
            SELECT account_id, MIN(period_start) AS first, MAX(period_end) AS last,
                   COUNT(*) AS n
            FROM   statements WHERE status='imported'
            GROUP  BY account_id ORDER BY account_id
            """
        ).fetchall()
        # Where a manual snapshot is newer than the last statement, the
        # difference is real activity no statement covers yet -- not an error.
        unstatemented = [dict(r) for r in conn.execute(
            """
            SELECT b.account_id, b.as_of, b.balance_cents,
                   s.period_end, s.balance_end_cents,
                   b.balance_cents - s.balance_end_cents AS delta_cents
            FROM   balance_snapshots b
            JOIN   (SELECT account_id, MAX(period_end) AS period_end
                    FROM statements WHERE status='imported'
                    GROUP BY account_id) last
                   ON last.account_id = b.account_id
            JOIN   statements s ON s.account_id = b.account_id
                   AND s.period_end = last.period_end AND s.status='imported'
            WHERE  b.as_of > s.period_end
            """
        ).fetchall()]
        queue = conn.execute("SELECT COUNT(*) FROM v_review_queue").fetchone()[0]
        imbalances = len(ledger.split_imbalances(conn))
        gaps = ledger.statement_chain_gaps(conn)
    finally:
        conn.close()

    payload = {
        "counts": counts,
        "parsed_accounts": [dict(r) for r in parsed],
        "coverage": [dict(r) for r in coverage],
        "review_queue": queue,
        "unstatemented": unstatemented,
        "split_imbalances": imbalances,
        "statement_gaps": gaps,
    }

    def render(p: dict) -> None:
        typer.secho("Ledger", bold=True)
        for table, n in p["counts"].items():
            typer.echo(f"  {table:<20} {n:>8}")
        typer.echo()
        typer.secho("Statement coverage", bold=True)
        if not p["coverage"]:
            typer.echo("  (nothing imported yet)")
        for row in p["coverage"]:
            typer.echo(f"  {row['account_id']:<18} {row['first']} .. {row['last']}"
                       f"  ({row['n']} statements)")
        typer.echo()
        for u in p.get("unstatemented", []):
            typer.secho(
                f"  {u['account_id']}: statement closes {u['period_end']} at "
                f"{ledger.format_eur(u['balance_end_cents'])}, live balance "
                f"{u['as_of']} is {ledger.format_eur(u['balance_cents'])} "
                f"({ledger.format_eur(u['delta_cents'])} not yet statemented)",
                fg=typer.colors.BLUE)
        if p["review_queue"]:
            typer.secho(f"  {p['review_queue']} transactions awaiting review",
                        fg=typer.colors.YELLOW)
        if p["split_imbalances"]:
            typer.secho(f"  {p['split_imbalances']} split imbalances -- this is a bug",
                        fg=typer.colors.RED)
        for gap in p["statement_gaps"]:
            typer.secho(
                f"  gap on {gap['account_id']}: {gap['after']} -> {gap['before']}"
                f" (delta {ledger.format_eur(gap['delta_cents'])}) -- statement missing?",
                fg=typer.colors.RED,
            )
        if not (p["split_imbalances"] or p["statement_gaps"]):
            typer.secho("  integrity checks pass", fg=typer.colors.GREEN)

    _emit(payload, json, render)


# ------------------------------------------------------------- validate


@app.command()
def validate(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """Assert the ledger's invariants. Exits non-zero on any violation."""
    from finctl.rules import categorize as _cz

    conn = _require_db()
    try:
        imbalances = [dict(r) for r in ledger.split_imbalances(conn)]
        orphans = [dict(r) for r in ledger.orphaned_splits(conn)]
        gaps = ledger.statement_chain_gaps(conn)
        unrecorded = _cz.unrecorded_manual_splits(conn)
        rejected = [
            dict(r) for r in conn.execute(
                "SELECT source_name, account_id, reconcile_delta_cents "
                "FROM statements WHERE status='rejected'"
            ).fetchall()
        ]
    finally:
        conn.close()

    ok = not (imbalances or gaps or rejected or orphans or unrecorded)
    payload = {
        "ok": ok,
        "split_imbalances": imbalances,
        "orphaned_splits": orphans,
        "statement_gaps": gaps,
        "unrecorded_manual": unrecorded,
        "rejected_statements": rejected,
    }

    def render(p: dict) -> None:
        if p["ok"]:
            typer.secho("All invariants hold.", fg=typer.colors.GREEN)
            return
        for row in p["split_imbalances"]:
            typer.secho(
                f"  imbalance tx={row['transaction_id']} {row['booking_date']}"
                f" off by {ledger.format_eur(row['delta_cents'])}",
                fg=typer.colors.RED)
        for u in p.get("unrecorded_manual", [])[:10]:
            typer.secho(
                f"  unrecorded manual decision: {u['booking_date']} "
                f"{u['account_id']} {ledger.format_eur(u['amount_cents'])} "
                f"-- lost on a rebuild", fg=typer.colors.RED)
        for o in p.get("orphaned_splits", [])[:10]:
            typer.secho(
                f"  orphan: {o['booking_date']} {o['account_id']} "
                f"{ledger.format_eur(o['amount_cents'])} -> retired category "
                f"'{o['mgmt_category_id']}'", fg=typer.colors.RED)
        if p.get("orphaned_splits"):
            typer.secho(
                f"  {len(p['orphaned_splits'])} splits point at a retired category. "
                f"They still count in the ledger but vanish from every report.",
                fg=typer.colors.RED)
        for gap in p["statement_gaps"]:
            typer.secho(f"  gap on {gap['account_id']}: {gap['after']} -> {gap['before']}",
                        fg=typer.colors.RED)
        for row in p["rejected_statements"]:
            typer.secho(f"  rejected {row['source_name']} "
                        f"(delta {ledger.format_eur(row['reconcile_delta_cents'])})",
                        fg=typer.colors.RED)

    _emit(payload, json, render)
    if not ok:
        raise typer.Exit(1)


# --------------------------------------------------------------- backup


@app.command()
def ort(
    setzen: Path = typer.Option(None, "--setzen",
                                help="Diesen Ordner als Datenordner eintragen."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Zeigen, wo der Datenordner liegt -- und warum dort.

    Die Auskunft, die man braucht, wenn das Werkzeug das falsche oder ein
    leeres Hauptbuch aufmacht: nicht nur WO gesucht wurde, sondern welche der
    vier Regeln aus `finctl/pfade.py` gegriffen hat.

    `--setzen` traegt einen Ordner ein und VERSCHIEBT NICHTS. Wer Daten
    mitnehmen will, kopiert sie selbst -- ein Befehl, der beides taete, waere
    einer, nach dem man nicht mehr weiss, wo die Daten sind, wenn er in der
    Mitte abbricht.
    """
    if setzen is not None:
        _p.ort_setzen(setzen)

    ordner, grund = _p.wurzel_mit_grund()
    payload = {
        "datenordner": str(ordner),
        "grund": grund,
        "zeiger": str(_p.zeiger()),
        "eingetragen": str(_p.ort()) if _p.ort() else None,
        "vorgabe": str(_p.standard()),
        "eingerichtet": (ordner / "config").is_dir(),
        "gilt_ab": "beim naechsten Aufruf",
    }

    def render(p: dict) -> None:
        typer.secho(f"Datenordner: {p['datenordner']}", fg=typer.colors.GREEN)
        typer.echo(f"  weil: {p['grund']}")
        typer.echo(f"  Zeiger: {p['zeiger']}"
                   + ("" if p["eingetragen"] else " (nicht vorhanden)"))
        typer.echo(f"  Vorgabe dieses Systems: {p['vorgabe']}")
        if not p["eingerichtet"]:
            typer.secho("  Dort liegt noch kein config/ -- `finctl init` legt es an.",
                        fg=typer.colors.YELLOW)
        if setzen is not None:
            typer.echo("  Eingetragen. Vorhandene Daten werden nicht "
                       "mitgenommen; die kopierst du selbst.")

    _emit(payload, json, render)


@app.command()
def backup(
    to: Path = typer.Option(None, "--to",
                            help="Target directory. Defaults to backup.yaml."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """Write a self-contained, compressed snapshot.

    Contains the database as a SQL dump, the YAML config and rules, and a
    source snapshot -- enough to rebuild on a replacement machine without git
    access. Statement PDFs are excluded; they are already in the cloud folder,
    and copying them would multiply the archive for no recovery benefit.
    """
    from finctl import ops

    try:
        payload = ops.write_backup(to)
    except ops.KeinBackupZielError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except FileNotFoundError:
        typer.secho("No database to back up.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


    def render(p: dict) -> None:
        typer.secho(f"Backup written: {p['archive']}", fg=typer.colors.GREEN)
        typer.echo(f"  {p['bytes'] / 1024:.1f} KiB, {p['kept']} snapshots kept")
        if p["pruned"]:
            typer.echo(f"  pruned {len(p['pruned'])} older")

    _emit(payload, json, render)


@app.command()
def passwort(
    entfernen: bool = typer.Option(False, "--entfernen",
                                   help="Anmeldung wieder abschalten."),
) -> None:
    """Das Passwort fuer den Netzzugang setzen.

    Gespeichert wird ein scrypt-Hash mit Salz, nicht das Passwort. Eine Datei
    im Projektordner, die es lesbar enthaelt, waere schlimmer als keine
    Anmeldung, weil sie Sicherheit behauptet.

    Ein neues Passwort meldet alle Geraete ab. Das ist gewollt.
    """
    from finctl.web import auth as _auth

    if entfernen:
        _auth.passwort_setzen("")
        typer.secho("Anmeldung abgeschaltet. Nur noch auf 127.0.0.1 vertretbar.",
                    fg=typer.colors.YELLOW)
        return
    eingabe = typer.prompt("Passwort", hide_input=True, confirmation_prompt=True)
    if len(eingabe) < 8:
        typer.secho("Mindestens acht Zeichen.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    _auth.passwort_setzen(eingabe)
    host, _ = _auth.bindung()
    typer.secho("Gesetzt. Alle angemeldeten Geraete muessen sich neu anmelden.",
                fg=typer.colors.GREEN)
    if host in ("127.0.0.1", "localhost"):
        typer.echo(f"  config/server.yaml steht auf host: {host} -- fuer den "
                   f"Zugriff aus dem Netz dort die LAN-Adresse eintragen.")


@app.command()
def restore(
    archive: Path = typer.Argument(..., help="Das tar.gz aus dem Backup-Ordner."),
    to: Path = typer.Option(..., "--to", help="Leeres Zielverzeichnis."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Ein Backup auspacken und die Datenbank daraus bauen.

    Der Weg, den man im Ernstfall geht -- als Befehl, damit er im Ernstfall
    nicht erfunden werden muss. Schreibt nie in eine bestehende Installation.

    Was entsteht, ist ein DATENORDNER, keine Installation: das Archiv enthaelt
    seit dem 23.09.2026 keinen Quelltext mehr. Das Programm kommt aus der
    Installation, der Ordner wird ihm genannt:

        finctl ort --setzen <ziel>
        finctl validate
    """
    from finctl import ops

    try:
        payload = ops.restore(archive, to)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None

    def render(p: dict) -> None:
        typer.secho(f"Wiederhergestellt nach {p['ziel']}", fg=typer.colors.GREEN)
        typer.echo(f"  {p['transaktionen']} Transaktionen in data/finance.db")
        for name, da in p["vorhanden"].items():
            typer.echo(f"  {'✓' if da else '✗'} {name}")
        if p["fehlend"]:
            typer.secho(f"  fehlt: {', '.join(p['fehlend'])}", fg=typer.colors.YELLOW)
        if p["werkzeug"]:
            typer.echo(f"  geschrieben mit finctl {p['werkzeug']}")
        typer.echo(f"  {p['hinweis']}")
        typer.echo(f"\n  Naechster Schritt: "
                   f"`{_p.aus_umgebung('finctl')} ort --setzen {p['ziel']}`, "
                   f"dann `{_p.aus_umgebung('finctl')} validate`.")

    _emit(payload, json, render)


@app.command()
def paypal(
    export: Path = typer.Argument(..., help="PayPal CSV export."),
    year: str = typer.Option(None, "--year", help="Limit to one year."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Resolve unlabelled PayPal debits against a PayPal CSV export.

    A PayPal charge reaches the bank as "PayPal Europe S.a.r.l." and nothing
    else, which is why these stay unlabelled longest. The export has the
    counterparty.

    PROPOSES ONLY. What a payment to a friend was for is in neither file -- the
    same person is Gastronomie one month and Geschenke the next -- so the
    category comes from your own earlier decisions about that person and is
    never written without you.
    """
    from finctl.ingest import paypal as pp

    conn = _require_db()
    try:
        rows = pp.match(conn, export, year=year)
    finally:
        conn.close()

    payload = {"open": len(rows),
               "resolved": sum(1 for r in rows if r["counterparty"]),
               "rows": rows}

    def render(p: dict) -> None:
        typer.secho(f"{p['resolved']} of {p['open']} resolved", bold=True)
        for r in p["rows"]:
            who = r["counterparty"] or "-- not found in the export"
            hint = ""
            if r["suggested_category"]:
                hint = f"  -> {r['suggested_category']} ({r['suggested_from']}x before)"
            typer.echo(f"  tx {r['transaction_id']:<5} {r['booking_date']} "
                       f"{ledger.format_eur(r['amount_cents']):>10}  {who}{hint}")
        if p["rows"]:
            typer.secho("\n  Nothing was written. Assign them in the dashboard, "
                        "or with `finctl split`.", fg=typer.colors.BLUE)

    _emit(payload, json, render)


# ------------------------------------------------------------- accounts


@app.command()
def accounts(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """List registered accounts."""
    conn = _require_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, display_name, institution, account_type, ingest_mode, "
            "parser_profile, dispo_threshold_cents FROM accounts WHERE active=1 "
            "ORDER BY ingest_mode DESC, id"
        ).fetchall()]
    finally:
        conn.close()

    def render(p: dict) -> None:
        for row in p["accounts"]:
            marker = "*" if row["ingest_mode"] == "parsed" else " "
            typer.echo(f" {marker} {row['id']:<18} {row['display_name']:<22} "
                       f"{row['account_type']:<12} {row['ingest_mode']}")
        typer.echo("\n * = transaction-level import")

    _emit({"accounts": rows}, json, render)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())


# --------------------------------------------------------------- ingest

ingest_app = typer.Typer(no_args_is_help=True, help="Import bank statements.")
app.add_typer(ingest_app, name="ingest")


@ingest_app.command("probe")
def ingest_probe(
    path: Path = typer.Argument(..., help="A PDF whose layout should be dumped."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Dump an unknown statement's layout so a parser profile can be written."""
    from finctl.ingest import importer

    payload = importer.probe(path)

    def render(p: dict) -> None:
        typer.secho(f"{p['file']}  ({p['pages']} pages)", bold=True)
        typer.echo(f"detected profile: {p['detected_profile'] or '(none)'}\n")
        for i, line in enumerate(p["first_page_lines"], 1):
            typer.echo(f"{i:3d}| {line}")

    _emit(payload, json, render)


@app.command()
def anonymisieren(
    datei: Path = typer.Argument(..., help="CSV-Export oder PDF-Auszug."),
    ziel: Path = typer.Option(None, "--ziel", help="Wohin die Kopie kommt."),
    name: list[str] = typer.Option([], "--name", help="Eigener Name, überall ersetzt; mehrfach."),
    faktor: int = typer.Option(1, "--faktor", help="Beträge mit dieser ganzen Zahl verfremden."),
) -> None:
    """Eine weitergebbare Kopie eines Auszugs: ohne Namen, IBANs und Nummern.

    Für ein Parserbeispiel. Aufbau, Datum und Beträge bleiben, damit der
    Parser daran prüfbar bleibt. Ein PDF wird als Seitentext (.txt) kopiert.
    Vor dem Weitergeben ansehen: was nicht als Name erkennbar ist, bleibt.
    """
    from finctl.ingest import anonym

    try:
        kopie, zaehlung = anonym.verfremden(datei, ziel, namen=name, faktor=faktor)
    except (ValueError, OSError) as exc:
        typer.secho(f"Nicht verfremdet: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.secho(f"Kopie: {kopie}", fg=typer.colors.GREEN)
    typer.echo(f"  ersetzt: {zaehlung['iban']} IBAN, {zaehlung['nummer']} Nummern, "
               f"{zaehlung['text']} Texte, {zaehlung['name']} Namen")
    typer.secho("  Vor dem Weitergeben ansehen.", fg=typer.colors.YELLOW)


@ingest_app.command("run")
def ingest_run(
    account: str = typer.Option(None, "--account", help="Limit to one account id."),
    force: bool = typer.Option(False, "--force", help="Re-import files already seen."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Import every statement under data/statements/<account>/."""
    from finctl import ops

    conn = _require_db()
    try:
        results = ops.ingest_all(conn, account=account, force=force)
    finally:
        conn.close()

    payload = _ops.ingest_summary(results)

    def render(p: dict) -> None:
        colours = {"imported": typer.colors.GREEN, "skipped": typer.colors.BLUE,
                   "rejected": typer.colors.RED, "error": typer.colors.RED,
                   "unknown_layout": typer.colors.YELLOW}
        for r in p["results"]:
            detail = ""
            if r["status"] == "imported":
                detail = f"{r['transactions']:>4} txns  {r['period'][0]}..{r['period'][1]}"
            elif r.get("delta_cents"):
                detail = f"off by {ledger.format_eur(r['delta_cents'])}"
            typer.secho(f"  {r['status']:<15}", fg=colours.get(r["status"]), nl=False)
            typer.echo(f"{r['file'][:52]:<54}{detail}")
            if r.get("message") and r["status"] != "skipped":
                typer.secho(f"      {r['message']}", fg=typer.colors.YELLOW)
        typer.echo()
        typer.echo(f"  imported {p['imported']}  rejected {p['rejected']}  skipped {p['skipped']}")

    _emit(payload, json, render)


# ------------------------------------------------------------- taxonomy

taxonomy_app = typer.Typer(no_args_is_help=True, help="Category taxonomies.")
app.add_typer(taxonomy_app, name="taxonomy")


@taxonomy_app.command("load")
def taxonomy_load(json: bool = typer.Option(False, "--json")) -> None:
    """Load config/taxonomy.yaml into the database. Idempotent."""
    from finctl.tax import taxonomy as tx

    conn = _require_db()
    try:
        counts = tx.load_into_db(conn)
    finally:
        conn.close()

    _emit(
        {"loaded": counts},
        json,
        lambda p: typer.secho(
            f"Loaded {p['loaded']['mgmt']} management and {p['loaded']['tax']} "
            f"tax categories.", fg=typer.colors.GREEN),
    )


@taxonomy_app.command("show")
def taxonomy_show(
    axis: str = typer.Argument("tax", help="'tax' or 'mgmt'."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Print a taxonomy tree."""
    conn = _require_db()
    table = "tax_categories" if axis == "tax" else "mgmt_categories"
    try:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE active=1 ORDER BY sort_order, id"
        ).fetchall()]
        properties = [dict(r) for r in conn.execute(
            "SELECT id, name FROM properties ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()

    def render(p: dict) -> None:
        roots = [r for r in p["rows"] if r["parent_id"] is None]
        for root in roots:
            anlage = f"  [{root['anlage']}]" if root.get("anlage") else ""
            typer.secho(f"{root['name']}{anlage}", bold=True)
            for child in [r for r in p["rows"] if r["parent_id"] == root["id"]]:
                if child.get("requires_property"):
                    names = [q["name"] for q in p["properties"]] or ["<no properties yet>"]
                    typer.echo(f"  {child['name']}")
                    for name in names:
                        typer.secho(f"      -> {name} - {child['name']}",
                                    fg=typer.colors.BRIGHT_BLACK)
                else:
                    typer.echo(f"  {child['name']}")
            typer.echo()

    _emit({"rows": rows, "properties": properties}, json, render)


# ---------------------------------------------------------------- rules

rules_app = typer.Typer(no_args_is_help=True, help="Categorization rules.")
app.add_typer(rules_app, name="rules")


@app.command()
def categorize(
    recompute: bool = typer.Option(True, "--recompute/--incremental",
                                   help="Rebuild rule-derived splits from scratch."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Apply the rulebook. Manual splits are never touched."""
    from finctl.rules import categorize as cat

    conn = _require_db()
    try:
        result = cat.categorize(conn, recompute=recompute)
        imbalances = len(ledger.split_imbalances(conn))
    finally:
        conn.close()

    total = result.matched + result.unmatched + result.flagged
    payload = {
        "matched": result.matched,
        "unmatched": result.unmatched,
        "flagged_for_split": result.flagged,
        "manual_preserved": result.manual_preserved,
        "replayed": result.replayed,
        "coverage_pct": round(100 * result.matched / total, 1) if total else 0.0,
        "split_imbalances": imbalances,
        "by_rule": dict(sorted(result.by_rule.items(), key=lambda kv: -kv[1])),
    }

    def render(p: dict) -> None:
        # One denominator, the same one the percentage uses. It printed
        # "matched of matched+unmatched" against a percentage computed over
        # matched+unmatched+flagged, so a clean ledger read "1631 of 1631
        # (98,7%)" -- two numbers that cannot both be right.
        typer.secho(f"Matched {p['matched']} of "
                    f"{p['matched'] + p['unmatched'] + p['flagged_for_split']} "
                    f"({p['coverage_pct']}%)", fg=typer.colors.GREEN)
        if p["manual_preserved"]:
            typer.echo(f"  {p['manual_preserved']} manual overrides preserved")
        if p.get("replayed"):
            typer.secho(f"  {p['replayed']} manual decisions replayed from config",
                        fg=typer.colors.BLUE)
        if p["unmatched"]:
            typer.secho(f"  {p['unmatched']} in the review queue",
                        fg=typer.colors.YELLOW)
        if p.get("flagged_for_split"):
            typer.secho(f"  {p['flagged_for_split']} flagged for manual breakdown "
                        f"(finctl split flagged)", fg=typer.colors.BLUE)
        if p["split_imbalances"]:
            typer.secho(f"  {p['split_imbalances']} split imbalances -- bug",
                        fg=typer.colors.RED)

    _emit(payload, json, render)


@rules_app.command("preview")
def rules_preview(
    rule_id: str = typer.Argument(..., help="Rule id to preview."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Show what a rule would catch, before committing to it."""
    from finctl.rules import categorize as cat

    conn = _require_db()
    try:
        payload = cat.preview(conn, rule_id)
    finally:
        conn.close()

    def render(p: dict) -> None:
        typer.secho(f"{p['rule']} -- {p['name']}", bold=True)
        typer.echo(f"  matches {p['matched']} transactions, "
                   f"{ledger.format_eur(p['total_cents'])} total")
        if p["shadowed_by_higher_priority"]:
            typer.secho(f"  {p['shadowed_by_higher_priority']} already claimed by "
                        f"a higher-priority rule", fg=typer.colors.YELLOW)
        for s in p["sample"]:
            typer.echo(f"    {s['date']}  {ledger.format_eur(s['amount_cents']):>12}  "
                       f"{s['text'][:64]}")

    _emit(payload, json, render)


@rules_app.command("queue")
def rules_queue(
    limit: int = typer.Option(30, "--limit"),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Uncategorized transactions, largest first."""
    conn = _require_db()
    try:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT t.id, t.booking_date, t.account_id, t.amount_cents, t.raw_text
            FROM   v_review_queue t
            ORDER  BY ABS(t.amount_cents) DESC LIMIT ?
            """, (limit,)).fetchall()]
        total = conn.execute("SELECT COUNT(*) FROM v_review_queue").fetchone()[0]
    finally:
        conn.close()

    def render(p: dict) -> None:
        typer.secho(f"{p['total']} transactions awaiting review", bold=True)
        for r in p["rows"]:
            typer.echo(f"  {r['booking_date']}  {r['account_id']:<15}"
                       f"{ledger.format_eur(r['amount_cents']):>13}  {r['raw_text'][:60]}")

    _emit({"total": total, "rows": rows}, json, render)


# --------------------------------------------------------------- reports

@app.command()
def transfers(
    window: int = typer.Option(5, "--window", help="Days allowed between the two legs."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Verify that transfers between your own accounts actually pair up."""
    from finctl import reports

    conn = _require_db()
    try:
        payload = reports.transfer_pairs(conn, window_days=window)
    finally:
        conn.close()

    def render(p: dict) -> None:
        typer.secho(f"{p['matched_count']} transfer pairs matched "
                    f"({ledger.format_eur(p['matched_cents'])} moved)",
                    fg=typer.colors.GREEN)
        for m in p["matched_pairs"][:12]:
            typer.echo(f"    {m['sent']}  {m['from']:<15} -> {m['to']:<15}"
                       f"{ledger.format_eur(m['amount_cents']):>12}")
        if p["leaves_parsed_set"]:
            typer.echo()
            typer.secho(f"  {len(p['leaves_parsed_set'])} legs leave the parsed "
                        f"accounts (expected -- no second leg exists)",
                        fg=typer.colors.BLUE)
        if p["unpaired"]:
            typer.echo()
            typer.secho(f"  {len(p['unpaired'])} unpaired legs -- check these",
                        fg=typer.colors.YELLOW)
            for u in p["unpaired"][:10]:
                typer.echo(f"    {u['date']}  {u['account']:<15}"
                           f"{ledger.format_eur(u['amount_cents']):>12}  {u['text'][:44]}")
        typer.echo()
        typer.echo(f"  net across all transfer legs: {ledger.format_eur(p['net_cents'])}")

    _emit(payload, json, render)


@app.command()
def spend(
    year: str = typer.Option("2026", "--year"),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Spending by category, transfers excluded."""
    from finctl import reports

    conn = _require_db()
    try:
        payload = reports.spend_by_category(conn, year)
    finally:
        conn.close()

    def render(p: dict) -> None:
        typer.secho(f"Spend by category, {p['year']} (transfers excluded)", bold=True)
        for r in p["rows"]:
            label = (f"{r['kategorie']} / {r['subkategorie']}"
                     if r["subkategorie"] and r["kategorie"] != r["subkategorie"]
                     else (r["kategorie"] or "(uncategorized)"))
            typer.echo(f"  {ledger.format_eur(r['cents']):>14}  {r['n']:>4}x  {label}")

    _emit(payload, json, render)


# ----------------------------------------------------------------- loans

@app.command()
def loans(
    loan_id: str = typer.Option(None, "--loan", help="Limit to one loan id."),
    years: int = typer.Option(3, "--years", help="How far ahead to show."),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Amortisation schedules, driven by the loans.yaml segments."""
    from datetime import date as _date

    from finctl.realestate.loan import amortise, lade_kredite, opening_balance_cents, segments_from

    horizon = _date.today().replace(year=_date.today().year + years)
    out = []

    for loan in lade_kredite(CONFIG_DIR):
        if loan_id and str(loan["id"]) != loan_id:
            continue
        segs = loan.get("segments")
        if not segs:
            out.append({"id": str(loan["id"]), "name": loan["name"],
                        "error": "no segments declared yet"})
            continue
        opening = opening_balance_cents(loan)
        schedule = amortise(str(loan["id"]), opening, segments_from(loan))
        window = [p for p in schedule.payments if p.month <= horizon]
        out.append({
            "id": str(loan["id"]), "name": loan["name"],
            "opening_cents": opening,
            "total_interest_cents": schedule.total_interest_cents(),
            "payoff": schedule.payoff_month().isoformat() if schedule.payoff_month() else None,
            "payments": [
                {"month": p.month.isoformat(), "payment_cents": p.payment_cents,
                 "interest_cents": p.interest_cents, "principal_cents": p.principal_cents,
                 "balance_cents": p.balance_cents, "basis": p.basis}
                for p in window
            ],
        })

    def render(p: dict) -> None:
        for loan in p["loans"]:
            typer.secho(f"{loan['name']}", bold=True)
            if loan.get("error"):
                typer.secho(f"  {loan['error']}", fg=typer.colors.YELLOW)
                continue
            typer.echo(f"  opening {ledger.format_eur(loan['opening_cents'])}   "
                       f"total interest {ledger.format_eur(loan['total_interest_cents'])}   "
                       f"paid off {loan['payoff']}")
            last_basis = None
            for pay in loan["payments"]:
                if pay["basis"] != last_basis:
                    typer.secho(f"    -- {pay['basis']} --", fg=typer.colors.BRIGHT_BLACK)
                    last_basis = pay["basis"]
                typer.echo(f"    {pay['month']}  {ledger.format_eur(pay['payment_cents']):>10}"
                           f"  = Zins {ledger.format_eur(pay['interest_cents']):>9}"
                           f" + Tilg {ledger.format_eur(pay['principal_cents']):>9}"
                           f"   Rest {ledger.format_eur(pay['balance_cents']):>12}")
            typer.echo()

    _emit({"loans": out}, json, render)


# -------------------------------------------------------------- forecast

@app.command()
def forecast(
    account: str = typer.Option("dkb-giro", "--account"),
    months: int = typer.Option(12, "--months"),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Project cash flow, reporting the intra-month trough."""
    from finctl import ops

    conn = _require_db()
    try:
        payload = ops.account_forecast(conn, account, months=months)
    finally:
        conn.close()

    def render(p: dict) -> None:
        typer.secho(p['account'], bold=True)
        typer.echo(f"  opening {ledger.format_eur(p['opening_cents'])} "
                   f"({p['opening_source']}, {p['opening_as_of']}), "
                   f"Dispo floor {ledger.format_eur(p['dispo_threshold_cents'])}\n")
        typer.echo(f"  {'month':<10}{'vor Gehalt':>14}{'Kosten':>14}"
                   f"{'nach Kosten':>15}{'Einnahmen':>14}{'nach Gehalt':>15}")
        for r in p["rows"]:
            line = (f"  {r['month'][:7]:<10}{ledger.format_eur(r['opening_cents']):>14}"
                    f"{ledger.format_eur(r['costs_cents']):>14}"
                    f"{ledger.format_eur(r['trough_cents']):>15}"
                    f"{ledger.format_eur(r['income_cents']):>14}"
                    f"{ledger.format_eur(r['closing_cents']):>15}")
            typer.secho(line, fg=typer.colors.RED if r["breaches_dispo"] else None)
        typer.echo()
        if p["breach_months"]:
            typer.secho(f"  Dispo breached in: {', '.join(m[:7] for m in p['breach_months'])}",
                        fg=typer.colors.RED)
        typer.echo(f"  worst trough {ledger.format_eur(p['worst_trough_cents'])} "
                   f"in {p['worst_month'][:7]}")

    _emit(payload, json, render)


# ----------------------------------------------------------------- split

split_app = typer.Typer(no_args_is_help=True,
                        help="Break one transaction into several categorized parts.")
app.add_typer(split_app, name="split")


def _parse_part(spec: str) -> dict:
    """`amount:mgmt[:note]`, e.g. `-30.00:abo/geteilt:Office annual`."""
    bits = spec.split(":", 2)
    if len(bits) < 2:
        raise typer.BadParameter(f"expected amount:category[:note], got {spec!r}")
    try:
        cents = int(round(float(bits[0].replace(",", ".")) * 100))
    except ValueError as exc:
        raise typer.BadParameter(f"bad amount in {spec!r}") from exc
    return {"amount_cents": cents, "mgmt": bits[1] or None,
            "note": bits[2] if len(bits) > 2 else None}


@split_app.command("show")
def split_show(transaction_id: int = typer.Argument(...),
               json: bool = typer.Option(False, "--json")) -> None:
    """Show a transaction and how it is currently split."""
    conn = _require_db()
    try:
        tx = conn.execute(
            "SELECT id, account_id, booking_date, amount_cents, raw_text "
            "FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if tx is None:
            typer.secho(f"no transaction {transaction_id}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        parts = [dict(r) for r in conn.execute(
            "SELECT seq, amount_cents, mgmt_category_id, tax_category_id, "
            "property_id, note, source FROM splits WHERE transaction_id = ? "
            "ORDER BY seq", (transaction_id,)).fetchall()]
    finally:
        conn.close()

    payload = {"transaction": dict(tx), "splits": parts,
               "sum_cents": sum(p["amount_cents"] for p in parts)}

    def render(p: dict) -> None:
        t = p["transaction"]
        typer.secho(f"#{t['id']}  {t['booking_date']}  {t['account_id']}  "
                    f"{ledger.format_eur(t['amount_cents'])}", bold=True)
        typer.echo(f"  {t['raw_text'][:100]}\n")
        for s in p["splits"]:
            typer.echo(f"  [{s['seq']}] {ledger.format_eur(s['amount_cents']):>11}  "
                       f"{s['mgmt_category_id'] or '(uncategorized)':<28} "
                       f"{s['source']:<8} {s['note'] or ''}")
        typer.echo(f"\n  splits sum to {ledger.format_eur(p['sum_cents'])}"
                   f" of {ledger.format_eur(t['amount_cents'])}")

    _emit(payload, json, render)


@split_app.command("set")
def split_set(
    transaction_id: int = typer.Argument(...),
    part: list[str] = typer.Option(..., "--part", "-p",
                                   help="amount:category[:note], repeatable."),
    tax: str = typer.Option(None, "--tax", help="Tax category for all parts."),
    property_id: str = typer.Option(None, "--property"),
    json: bool = typer.Option(False, "--json"),
) -> None:
    """Replace a transaction's splits with a manual breakdown.

    Manual splits are sticky: `categorize --recompute` rebuilds rule-derived
    splits from scratch and never touches these.
    """
    parts = [_parse_part(p) for p in part]
    total = sum(p["amount_cents"] for p in parts)

    conn = _require_db()
    try:
        tx = conn.execute("SELECT id, amount_cents FROM transactions WHERE id = ?",
                          (transaction_id,)).fetchone()
        if tx is None:
            typer.secho(f"no transaction {transaction_id}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

        # The ledger invariant is not negotiable: parts must sum to the whole.
        if total != tx["amount_cents"]:
            typer.secho(
                f"parts sum to {ledger.format_eur(total)} but the transaction is "
                f"{ledger.format_eur(tx['amount_cents'])} "
                f"(off by {ledger.format_eur(total - tx['amount_cents'])})",
                fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

        known = {r["id"] for r in conn.execute("SELECT id FROM mgmt_categories")}
        for p in parts:
            if p["mgmt"] and p["mgmt"] not in known:
                typer.secho(f"unknown category: {p['mgmt']}", fg=typer.colors.RED, err=True)
                raise typer.Exit(1)

        stamp = ledger.now_iso()
        conn.execute("DELETE FROM splits WHERE transaction_id = ?", (transaction_id,))
        for seq, p in enumerate(parts):
            conn.execute(
                """
                INSERT INTO splits (transaction_id, seq, amount_cents,
                                    mgmt_category_id, tax_category_id, property_id,
                                    note, source, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?, 'manual', ?, ?)
                """,
                (transaction_id, seq, p["amount_cents"], p["mgmt"], tax,
                 property_id, p["note"], stamp, stamp),
            )
        conn.commit()
        from finctl.rules.categorize import record_override
        record_override(conn, transaction_id)
        imbalance = len(ledger.split_imbalances(conn))
    finally:
        conn.close()

    _emit({"transaction_id": transaction_id, "parts": len(parts),
           "sum_cents": total, "imbalances": imbalance}, json,
          lambda p: typer.secho(
              f"Split #{p['transaction_id']} into {p['parts']} manual parts "
              f"totalling {ledger.format_eur(p['sum_cents'])}.",
              fg=typer.colors.GREEN))


@split_app.command("flagged")
def split_flagged(json: bool = typer.Option(False, "--json")) -> None:
    """Transactions a rule flagged for manual breakdown."""
    conn = _require_db()
    try:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT t.id, t.booking_date, t.account_id, t.amount_cents,
                   s.note, t.raw_text
            FROM   splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE  s.source = 'default' AND s.rule_id IS NOT NULL
                   AND s.mgmt_category_id IS NULL
            ORDER  BY ABS(t.amount_cents) DESC
            """).fetchall()]
    finally:
        conn.close()

    def render(p: dict) -> None:
        typer.secho(f"{len(p['rows'])} transactions flagged for manual breakdown",
                    bold=True)
        for r in p["rows"]:
            typer.echo(f"  #{r['id']:<5} {r['booking_date']}  {r['account_id']:<7}"
                       f"{ledger.format_eur(r['amount_cents']):>10}  {r['raw_text'][:56]}")

    _emit({"rows": rows}, json, render)


@app.command()
def serve(
    port: int = typer.Option(None, "--port", help="Sonst aus config/server.yaml."),
    host: str = typer.Option(None, "--host", help="Sonst aus config/server.yaml."),
    reload: bool = typer.Option(True, "--reload/--no-reload",
                                help="Pick up template and code edits without a restart."),
    ohne_passwort: bool = typer.Option(
        False, "--ohne-passwort",
        help="Keine Anmeldung -- nur zusammen mit 127.0.0.1, fuer die Pruefvorschau."),
) -> None:
    """Run the local dashboard."""
    import uvicorn

    if not DB_PATH.exists():
        typer.secho("No database yet. Run `finctl init` first.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    from finctl.web import auth as _auth

    voreinstellung_host, voreinstellung_port = _auth.bindung()
    host = host or voreinstellung_host
    port = port or voreinstellung_port

    # Ohne Passwort nur auf diesem Rechner. Die Vorschau im Desktop-Werkzeug
    # soll pruefen koennen, ohne dass jemand ein Passwort eintippt -- aber
    # eine Abkuerzung, die im Netz funktionierte, waere genau die Luecke, die
    # die Anmeldung schliesst.
    if ohne_passwort:
        if host not in ("127.0.0.1", "localhost", "::1"):
            typer.secho("--ohne-passwort geht nur mit --host 127.0.0.1.",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        import os

        os.environ["FINCTL_OHNE_PASSWORT"] = "1"
    # Im Netz erreichbar UND ohne Passwort gibt es nicht. Das ist die eine
    # Stelle, an der ein vergessener Schalter das ganze Finanzleben offenlegt,
    # und ein Hinweis im Handbuch haette dagegen nichts ausgerichtet.
    if host not in ("127.0.0.1", "localhost", "::1") and not _auth.passwort_gesetzt():
        typer.secho(
            f"host = {host} macht die App im Netz erreichbar, und es ist kein "
            f"Passwort gesetzt.\n"
            f"Jedes Geraet im WLAN koennte dann alles lesen und aendern.\n\n"
            f"  finctl passwort        setzt eines\n"
            f"  --host 127.0.0.1       bleibt lokal",
            fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(f"Finance OS -> http://{host}:{port}"
                + (" (ohne Passwort)" if ohne_passwort else ""), fg=typer.colors.GREEN)
    uvicorn.run("finctl.web.server:app", host=host, port=port,
                reload=reload, log_level="warning")
