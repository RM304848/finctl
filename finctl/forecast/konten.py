"""Die Kontoprognose: Tiefpunkt je Konto und Monat, Monat fuer Monat.

Stand bis 26.09.2026 in finctl/ops.py, zwischen Sicherung und Einlesen -- die
App-Schicht trug damit eine Rechnung, die der Prognose gehoert, und die
Prognose musste die App importieren, um mit derselben Basis zu rechnen
(tests/test_module.py, OFFEN). Hier steht sie bei den anderen Rechnungen;
`ops` reicht die Namen weiter, damit Aufrufer nichts merken.
"""

from __future__ import annotations

from finctl.forecast.stichtag import saldo_zum as _saldo_zum
from finctl.forecast.stichtag import stichtag as _stichtag
from finctl.pfade import CONFIG_DIR


def _sweep_ceiling(account: str, fcfg: dict) -> int | None:
    """The ceiling an account is actually emptied down to, if it declares one.

    Only where `sweep_to` names a destination. A ceiling without a sweep is an
    observation; a ceiling with one is a monthly instruction, and the forecast
    should show the world where the instruction is followed.
    """
    role = (fcfg.get("account_roles") or {}).get(account) or {}
    return role.get("ceiling_cents") if role.get("sweep_to") else None



# _monatsschnitt_ops ist am 21.09.2026 entfallen. Es mass eine Planzeile ueber
# den Schnitt ihrer GANZEN Kategorie, waehrend die Jahresrechnung dieselbe
# Zeile ueber ihre zugeordneten Buchungen mass -- zwei Antworten auf eine
# Frage, die im Monat spuerbar auseinanderlagen. Beide Rechnungen nehmen jetzt
# `szenarien.messung`.


def _income_overrides(fcfg: dict) -> dict[str, int]:
    """Feste Werte, die einen gemessenen Median ersetzen.

    Der Gehaltsfloor kommt aus assumptions.yaml und wird hier eingesetzt, statt
    in forecast.yaml wiederholt zu werden. Er stand vorher an drei Stellen --
    forecast.yaml, lebensplan.yaml und der settings-Tabelle -- und nichts hielt
    sie zusammen ausser einer eigenen Pruefung, die nur meldete, wenn sie
    bereits auseinandergelaufen waren.
    """
    from finctl import assumptions as _ann

    out = {k: int(v) for k, v in (fcfg.get("overrides") or {}).items()}
    out.setdefault("einkommen/gehalt", _ann.salary_floor_cents())
    return out


def _as_month(value):
    """YYYY-MM oder ein YAML-Datum, auf den Monatsersten normalisiert."""
    from datetime import date

    if isinstance(value, date):
        return value.replace(day=1)
    text = str(value)
    return date.fromisoformat(text + "-01" if len(text) == 7 else text).replace(day=1)


def _operating_account(fcfg: dict) -> str:
    """Where an unassigned obligation lands.

    Declared rather than guessed: the roles in forecast.yaml already say which
    account runs salary and fixed costs, and that is the one a new rate would
    be debited from.
    """
    for account, role in (fcfg.get("account_roles") or {}).items():
        if (role or {}).get("role") == "operating":
            return account
    return "dkb-giro"


def _rate_floor(loan_id: str, month=None) -> int | None:
    """Eine Monatsrate des genannten Darlehens, im genannten Monat.

    Fuer ein Konto, das nichts tut ausser eine Rate auszufuehren, IST die Rate
    der richtige Floor: genug, dass die Lastschrift nie platzt, und keinen
    Euro mehr, denn das Geld verzinst sich anderswo besser.

    Abgeleitet und nicht eingetragen, weil die Rate sich aendert. Eine Rate,
    die nach der Zinsbindung um 40 % steigt, macht jeden festen Floor danach
    zu niedrig -- oder, haette man ihn vorsorglich auf den hoeheren Wert
    gesetzt, vorher um denselben Betrag zu hoch.
    """

    from finctl.realestate.loan import amortise, lade_kredite, opening_balance_cents, segments_from

    if not (CONFIG_DIR / "loans.yaml").exists():
        return None
    loan = next((x for x in lade_kredite(CONFIG_DIR)
                 if str(x.get("id")) == str(loan_id)), None)
    if loan is None:
        return None
    try:
        sched = amortise(loan["id"], opening_balance_cents(loan),
                         segments_from(loan))
    except Exception:
        return None
    if not sched.payments:
        return None
    if month is None:
        return sched.payments[0].payment_cents
    passend = [p for p in sched.payments
               if (p.month.year, p.month.month) <= (month.year, month.month)]
    return (passend[-1] if passend else sched.payments[0]).payment_cents


def _floor_for(conn, account: str, fcfg: dict, acct, month=None) -> int:
    """The warning floor, in order of how deliberately it was set.

    `floor_from_loan` steht GANZ VORN: ein Konto, das nur eine Rate ausfuehrt,
    soll genau eine Rate halten, und die aendert sich mit dem Tilgungsplan.
    Ein getippter Wert daneben friert die Rate von heute ein -- genau das
    passierte, als die Kontokarte den angezeigten Wert mitspeicherte und das
    Konto ab der naechsten Ratenerhoehung zu duenn gedeckt war.

    Danach der im Dashboard getippte Wert: er ist die juengste Entscheidung
    und lebt in `settings`, das `finctl init` nie ueberschreibt. Dann die
    deklarierte Rolle. Die accounts-Tabelle kommt zuletzt -- init schreibt sie
    bei jedem Lauf aus accounts.yaml neu, und so stand ein morgens gesetzter
    Floor nachmittags wieder auf dem alten Wert.
    """
    role = (fcfg.get("account_roles") or {}).get(account) or {}
    aus_plan = role.get("floor_from_loan")
    if aus_plan:
        rate = _rate_floor(str(aus_plan), month)
        if rate:
            return rate
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       (f"floor_cents:{account}",)).fetchone()
    if row is not None:
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            pass
    declared = role.get("floor_cents")
    if declared is not None:
        return int(declared)
    return acct["dispo_threshold_cents"] or 0


#: Wie weit die Liquiditaetsprognose standardmaessig reicht, und wie weit sie
#: auf Aufforderung reicht.
#:
#: Zwoelf bleibt die Vorgabe, weil die abgeleiteten Positionen nur so weit
#: belastbar sind: ein Median aus neun Monaten, fortgeschrieben mit der
#: Inflation, ist ueber ein Jahr eine gute Naeherung und ueber fuenf eine
#: Behauptung. Sechzig gibt es fuer die Faelle, in denen genau das gefragt ist
#: -- eine Kreditentscheidung ueber fuenf Jahre --, aber als ausdruecklicher
#: Aufruf und nicht als stille Voreinstellung.
HORIZON_DEFAULT = 12
HORIZON_LONG = 60


def _anzeigenamen(conn, rows) -> dict[str, str]:
    """Die Etiketten der Prognose in lesbare Namen uebersetzen.

    Die Engine fuehrt Posten unter ihrer Kennung -- `immobilie/grundsteuer`,
    `loan:<objekt>`. Das ist als Schluessel richtig und als Ueberschrift
    unleserlich, und eine Tabelle, die man erst uebersetzen muss, liest man
    nicht.

    Nur fuer die Anzeige: was nicht aufgeloest werden kann, bleibt stehen, wie
    es ist. Ein unbekanntes Etikett zu verschweigen waere schlimmer als ein
    haessliches -- Szenariozeilen tragen bereits deutschen Klartext.
    """
    etiketten = {e for r in rows for e in r.detail}
    namen: dict[str, str] = {}

    kategorien = {k for k in etiketten if "/" in k and not k.startswith("loan:")}
    if kategorien:
        platzhalter = ",".join("?" * len(kategorien))
        for row in conn.execute(
                f"""SELECT c.id, c.name, p.name AS oberbegriff
                    FROM   mgmt_categories c
                    LEFT   JOIN mgmt_categories p ON p.id = c.parent_id
                    WHERE  c.id IN ({platzhalter})""", sorted(kategorien)):
            # Mit Oberbegriff, weil "Sonstiges" allein nichts sagt und in vier
            # Zweigen gleichzeitig vorkommt.
            namen[row["id"]] = (f"{row['oberbegriff']}: {row['name']}"
                                if row["oberbegriff"] else row["name"])

    from finctl import abos as _ab

    for a in _ab.alle_vertraege():
        if f"abo:{a['id']}" in etiketten:
            namen[f"abo:{a['id']}"] = f"{a['name']} (erklärt)"
        if f"abo-zurueck:{a['id']}" in etiketten:
            namen[f"abo-zurueck:{a['id']}"] = f"{a['name']}: Rückzahlungen offen"

    for e in etiketten:
        if e.startswith("budget:"):
            namen[e] = f"Dauerauftrag an {e[7:]}"
    kredite = {k[5:] for k in etiketten if k.startswith("loan:")}
    if kredite:
        from finctl.realestate.loan import lade_kredite

        for kredit in lade_kredite(CONFIG_DIR):
            if str(kredit["id"]) in kredite:
                namen[f"loan:{kredit['id']}"] = (
                    f"Kreditrate {kredit.get('name') or kredit['id']}")
    return namen


_FREQ = {"monatlich": "monatlich", "quartalsweise": "quartalsweise",
         "jaehrlich": "jährlich", "einmalig": "einmalig"}


def _herkunft_planzeile(row: dict, konto: str) -> dict:
    """Wie eine Planzeile auf diesem Konto ankommt."""
    from finctl.forecast.herkunft import eur_genau as eur
    from finctl.forecast.herkunft import monat

    verweis = f"{row['scenario_id']}:{row['zeile']}"
    m = row.get("gemessen") or {}
    n = len(m.get("buchungen") or [])
    belege = (f"{n} Buchung{'' if n == 1 else 'en'} dieser Reihe in "
              f"{m.get('monate') or 0} Monaten")
    if row.get("kind") == "wegfall":
        text = (f"entfällt ab {monat(row['start'])}: {eur(row['amount_cents'])} "
                f"je Monat, gemessen an {belege} — dieselbe Rechnung wie in "
                "der Jahresrechnung")
    else:
        text = f"{_FREQ.get(row.get('frequency'), row.get('frequency'))} {eur(row['amount_cents'])}"
        if row.get("frequency") != "einmalig" and row.get("start"):
            text += f" ab {monat(row['start'])}"
            if row.get("end"):
                text += f" bis {monat(row['end'])}"
        elif row.get("start"):
            text += f" {monat(row['start'])}"
        if n:
            # Der Rest, nicht der Plan: was schon gebucht ist, steckt im
            # Median und darf nicht ein zweites Mal danebenstehen.
            text += f"; gekürzt um {belege}, die schon in der Messung stehen"
        if not row.get("account_id"):
            text += f"; ohne Konto in der Zeile, deshalb auf {konto}"
    return {"quelle": "plan", "herleitung": text, "verweis": verweis,
            "kategorie": row.get("category_id")}


def _planposten(conn, account: str, fcfg: dict, as_of: str, months: int) -> list:
    """Die eingeschalteten Planzeilen als datierte Posten DIESES Kontos.

    DER GESCHLOSSENE KREIS, WIE IN DER JAHRESRECHNUNG. Eine Planzeile wirkt
    nur mit dem Teil, der noch NICHT in der Messung steckt -- siehe den
    Abschnitt gleichen Namens in szenarien.py. Hier stand stattdessen
    `resolve_measured`: ein Wegfall bekam den Schnitt seiner GANZEN Kategorie,
    neue Kosten gar keinen Abgleich. Damit antworteten Kontoprognose und
    Jahresrechnung verschieden auf dieselbe Zeile, und neue Kosten zaehlten
    doppelt, sobald sie zu buchen anfingen.
    """
    from datetime import date as _date

    import yaml as _yaml

    from finctl.forecast import abgleich as _ag
    from finctl.forecast import engine as fc
    from finctl.forecast import szenarien as _sz

    pfad = CONFIG_DIR / "szenarien.yaml"
    if not pfad.exists():
        return []
    geladen = _sz.load(_yaml.safe_load(pfad.read_text(encoding="utf-8")) or {})
    fenster = _ag.fenster(conn)
    zeilen = {(s.id, i): line for s in _sz.active(geladen)
              for i, line in enumerate(s.lines)}
    gemessen = {k: _sz.messung(conn, line, fenster) for k, line in zeilen.items()}
    horizon = fc.add_months(_date.fromisoformat(as_of).replace(day=1), months + 1)

    out = []
    for row in _sz.dated_amounts(geladen, horizon):
        # A line with no account belongs to the operating account: that is
        # where a rate is debited unless stated otherwise, and dropping it
        # would make a plan look free.
        schluessel = (row["scenario_id"], row["zeile"])
        betrag = _sz.restbetrag_je_termin(zeilen[schluessel], gemessen[schluessel])
        if (row["account_id"] or _operating_account(fcfg)) != account or not betrag:
            continue
        out.append(fc.OneOff(
            label=row["label"], account_id=account, month=row["month"],
            amount_cents=betrag, reduces_cost=row.get("reduces_cost", False),
            herkunft=_herkunft_planzeile({**row, "amount_cents": betrag,
                                          "gemessen": gemessen[schluessel]},
                                         account)))
    return out


def _herkunft_konto(conn, fcfg: dict, recurring, one_offs, schedules,
                    rows) -> dict[str, dict]:
    """Je Etikett der Monatsdetails die Herkunft.

    Ein Etikett ohne Herkunft bekommt eine ehrliche Zeile statt keiner --
    sonst sieht eine Luecke hier aus wie eine Zahl, die sich selbst erklaert.
    """
    from finctl.forecast import jahre as _jm
    from finctl.forecast.herkunft import eur_genau as eur

    out: dict[str, dict] = {}
    for item in recurring:
        if item.herkunft:
            out[item.label] = item.herkunft
    for event in one_offs:
        if getattr(event, "herkunft", None):
            out.setdefault(event.label, event.herkunft)
    for sched in schedules:
        out[f"loan:{sched.loan_id}"] = {
            "quelle": "vertrag", "verweis": str(sched.loan_id),
            "herleitung": "Rate laut Tilgungsplan in loans.yaml, im Monat der "
                          "Fälligkeit, nicht gesteigert"}

    overrides = {k: int(v) for k, v in (fcfg.get("overrides") or {}).items()}
    for label, cents in _income_overrides(fcfg).items():
        if label in overrides:
            out[label] = {"quelle": "regel", "verweis": label,
                          "herleitung": f"fest {eur(cents)} aus forecast.yaml "
                                        "(overrides), ersetzt den Median"}
        elif label == "einkommen/gehalt":
            median = _jm.gehalt_median_cents(conn)
            out[label] = {
                "quelle": "annahme", "verweis": "salary_floor_cents",
                "herleitung": (f"Gehalts-Untergrenze {eur(cents)} aus den Annahmen, "
                               "bewusst vorsichtig für die Dispo-Frage; nicht "
                               "gesteigert"
                               + (f". Die Jahresrechnung nutzt den Median "
                                  f"{eur(median)}" if median else ""))}

    etiketten = {e for r in rows for e in r.detail}
    return {e: out.get(e) or {"quelle": "regel", "verweis": None,
                              "herleitung": "ohne hinterlegte Herleitung"}
            for e in etiketten}


def _erster_offener_monat(conn, account: str | None = None):
    """Der erste Monat nach dem Stichtag -- fuer alle Konten derselbe."""
    from datetime import timedelta

    return _stichtag(conn) + timedelta(days=1)


def date_vor(tag):
    """Der Tag vor `tag`."""
    from datetime import timedelta

    return tag - timedelta(days=1)


def abgeleitete_posten(conn, account: str, fcfg: dict,
                       erklaerte_abos: list | None = None, *,
                       abwahl: bool = True, stichtag=None) -> list:
    """Die aus dem Ledger abgeleiteten Posten eines Kontos.

    Eigene Funktion, weil die Prognosebasis auf /annahmen dieselben Posten zeigt, die die
    Kontoprognose rechnet -- eine zweite Ableitung daneben liefe auseinander.

    `stichtag` (ein Monatserster) rechnet so, als waere heute dieser Tag:
    Fenster davor, keine Buchung ab dem Stichtag. Fuer den Treffer-Check.
    """

    from finctl import abos as _ab
    from finctl.forecast import engine as fc
    from finctl.forecast import prognosebasis as _pb

    # Rolling window, not a fixed start. A pattern that stopped -- C24 went
    # quiet in August when its standing orders moved to Trade Republic -- has
    # to decay out of the baseline on its own, or the forecast keeps charging
    # for spending that no longer happens.
    window = int(fcfg.get("window_months", 0) or 0)
    window_since = "2026-01-01"
    # NUR VOLLSTAENDIGE MONATE. Ab heute zurueckgerechnet lag der angebrochene
    # Monat im Fenster: ein September mit Miete, aber noch ohne Gehalt, ging
    # als eigener Monat in den Median ein. Das Fenster endet deshalb mit dem
    # letzten Monat, den der Auszug dieses Kontos ganz abdeckt.
    heute = stichtag or _erster_offener_monat(conn, account)
    if window:
        cutoff = fc.add_months(heute, -window)
        window_since = cutoff.isoformat()
    # Fixkosten ueber ein eigenes, laengeres Fenster: eine Jahrespraemie aus
    # dem Januar muss im September noch sichtbar sein. Siehe derive_recurring.
    fenster_fix = int(fcfg.get("window_months_fixkosten", 0) or 0)
    since_fix = (fc.add_months(heute, -fenster_fix).isoformat()
                 if fenster_fix else None)
    bis = date_vor(heute).isoformat()

    if erklaerte_abos is None:
        erklaerte_abos = _ab.alle_vertraege()

    # Konten, die nichts halten. Geld dorthin ist ausgegeben, nicht geparkt --
    # und auf ihnen selbst ist die Deckung ein echter Zufluss.
    durchlaufend = {k for k, v in (fcfg.get("account_roles") or {}).items()
                    if (v or {}).get("role") == "durchlaufend"}

    return fc.derive_recurring(
        conn, account,
        durchlaufend=durchlaufend,
        include_transfers=account in durchlaufend,
        min_occurrences=int(fcfg.get("min_occurrences", 5)),
        # kredit/* comes from the amortisation schedules below, never from
        # history. Sparda carried BOTH: -539,61 inferred from its own payments
        # plus the full 540 from the schedule, so an account that is neutral by
        # construction -- 540 in by standing order, 540 out as the rate --
        # projected to -2.664. The household forecast fixed this long ago; the
        # per-account one never did.
        # Things that have been STOPPED by a decision. The rolling window
        # would eventually forget them, but "eventually" is six months of
        # warning about spending that is not going to happen, and a forecast
        # that keeps charging for a habit you have ended is one you stop
        # believing.
        exclude=set(fcfg.get("exclude_from_recurring", []) or []) | {
            d["category"] for d in (fcfg.get("discontinued") or [])
            if d.get("account") in (None, account)} | {
            r["id"] for r in conn.execute(
                "SELECT id FROM mgmt_categories WHERE parent_id = 'kredit'")} | (
            # Unter Prognosebasis abgewaehlt: gilt fuer BEIDE Rechnungen.
            _pb.nicht_fortschreiben() if abwahl else set()),
        # Transfers stay OUT of the inferred baseline even here. Inferring them
        # looked right for C24, whose 100 a month is a standing allocation, and
        # was nonsense for Scalable, where they are lumpy one-off moves --
        # +94.985 in March against -22.400 in July. A median of that is not a
        # run rate. An account's allocation is DECLARED below instead.
        since=window_since,
        # Bekannte Enden, wo eines belegt ist. Ueber zwoelf Monate
        # entbehrlich, ueber sechzig nicht.
        ends={cat: _as_month(when)
              for cat, when in (fcfg.get("enden") or {}).items()},
        # Was in `abos.yaml` mit Termin erklaert ist, wird nicht zusaetzlich
        # gemessen -- sonst stuende derselbe Betrag zweimal da.
        ohne=_ab.ausschluesse(conn, erklaerte_abos),
        until=bis,
        # Variable Kosten als Schnitt ueber das Fenster, siehe derive_recurring.
        variabel_als_schnitt=str(fcfg.get("variable_kosten") or "") == "schnitt",
        since_fixkosten=since_fix,
    )



def forecast_config() -> dict:
    """config/forecast.yaml, mit den Kontenregeln von der Kontenseite darueber.

    Eine Stelle, weil die Regeln an sechs Punkten gelesen werden -- Rolle,
    Untergrenze, Deckel, Abraeumen, Auffuellen, Budget. Ein Overlay, das nur
    an einem davon ankommt, aendert die Anzeige, ohne die Rechnung zu aendern.
    """
    import yaml as _y

    from finctl import kontenregeln as _kr

    pfad = CONFIG_DIR / "forecast.yaml"
    basis = (_y.safe_load(pfad.read_text(encoding="utf-8")) or {}) if pfad.exists() else {}
    return _kr.zusammenfuehren(basis)


def account_forecast(conn, account: str, *, months: int = HORIZON_DEFAULT,
                     extra_one_offs: list | None = None,
                     allow_sweep: bool = True) -> dict:
    """Per-account projection with the intra-month trough.

    Lived inside the Typer command, so the only way to see whether the Giro
    dips below its floor was to open a terminal -- which is the same reason
    the monthly import never got run. Same treatment as ingest: the work
    moves here and both callers stay thin.
    """
    from datetime import date as _date

    from finctl.forecast import engine as fc
    from finctl.realestate.loan import amortise, opening_balance_cents, segments_from

    acct = conn.execute(
        "SELECT id, dispo_threshold_cents FROM accounts WHERE id = ?", (account,)
    ).fetchone()
    if acct is None:
        raise ValueError(f"unknown account: {account}")

    # Start am STICHTAG, dem Ende des letzten Monats, den alle Konten ganz
    # abdecken. Ein neuerer Snapshot oder ein Auszug bis zum 4. zaehlt nicht:
    # der erste Prognosemonat ist immer ein ganzer, und was in ihm schon
    # gebucht ist, rechnet die Prognose selbst -- sonst stuende es doppelt
    # oder gar nicht da.
    stichtag = _stichtag(conn)
    as_of = stichtag.isoformat()
    saldo = _saldo_zum(conn, account, stichtag)
    if saldo is not None:
        opening, source = saldo, "statement close"
    else:
        snap = conn.execute(
            "SELECT balance_cents FROM balance_snapshots WHERE account_id=? "
            "AND as_of <= ? ORDER BY as_of DESC LIMIT 1", (account, as_of)).fetchone()
        opening, source = ((snap["balance_cents"], "live snapshot") if snap
                           else (0, "assumed zero"))

    fcfg = forecast_config()
    from finctl import abos as _ab

    erklaerte_abos = _ab.alle_vertraege()
    recurring = abgeleitete_posten(conn, account, fcfg, erklaerte_abos)

    # Budgets, declared rather than derived. This is the model the spreadsheet
    # ran: a fixed allocation in, observed spending out. It has to be declared
    # because history cannot see a decision -- the C24 allocation was cut from
    # 100 to 50 in August, and no median over the preceding months knows that.
    for account_id, cents in (fcfg.get("account_budgets") or {}).items():
        # DIE GEGENSEITE. Die Zuweisung kommt per Dauerauftrag vom
        # Betriebskonto, und dort ist sie ein Abgang. Ohne diese Zeile zaehlte
        # die Prognose das Geld auf dem Zielkonto als Zufluss und liess es auf
        # DKB trotzdem liegen -- 1.440 im Monat, die der Treffer-Check als
        # systematisch zu optimistische DKB-Prognose zeigte.
        if cents and account == _operating_account(fcfg) and account_id != account:
            recurring.append(fc.RecurringItem(
                label=f"budget:{account_id}", account_id=account,
                amount_cents=-int(cents), category="transfer/eigenkonto",
                herkunft={"quelle": "regel", "verweis": "account_budgets",
                          "herleitung": f"Dauerauftrag an {account_id} laut "
                                        "forecast.yaml (account_budgets)"}))
        if account_id != account or not cents:
            continue
        recurring.append(fc.RecurringItem(
            label="budget/zuweisung", account_id=account,
            amount_cents=int(cents), category="transfer/eigenkonto",
            herkunft={"quelle": "regel", "verweis": "account_budgets",
                      "herleitung": "feste Zuweisung je Monat aus forecast.yaml "
                                    "(account_budgets), nicht gemessen"}))

    from finctl.forecast import szenarien as _szk
    from finctl.realestate.loan import lade_kredite as _lade
    from finctl.realestate.loan import nur_wirksame as _wirksam

    schedules = []
    # Nur wirksame Kredite: eine Vorlage wie das Eigenheim rechnet nur mit,
    # solange ihre Klammer an ist -- wie in der Jahresrechnung.
    for loan in _wirksam(_lade(CONFIG_DIR), _szk.aktive_ids()):
        if not loan.get("segments") or loan.get("servicing_account_id") != account:
            continue
        schedules.append(amortise(
            str(loan["id"]), opening_balance_cents(loan), segments_from(loan)))


    one_offs = []

    # config/planned.yaml ist am 13.09.2026 entfallen. Sie trug datierte
    # Verpflichtungen, und alle sind in Klammern gewandert -- dort stehen sie
    # neben dem Vorgang, zu dem sie gehoeren, statt in einer flachen Liste.
    #
    # WAS DAMIT WEGFAELLT und im Zeilenmodell fehlt, falls es wieder gebraucht
    # wird: Fremdwaehrung (amount_foreign/fx_rate, der Eurobetrag abgeleitet)
    # und zweiseitige Deckung (`funding`: Geld verlaesst Scalable am 07.10.
    # und kommt bei DKB an). Eine Klammerzeile gehoert zu EINEM Konto.
    #
    # engine.planned_amount_cents und engine.funding_legs bleiben stehen und
    # getestet -- sie sind die Bausteine dafuer, und sie wegzuwerfen hiesse,
    # sie beim naechsten Auslandsposten neu zu schreiben.

    # Toggleable what-if plans from the dashboard. These apply ON TOP of
    # the base forecast and independently of each other: any
    # number can be switched on at once, because the question is usually
    # "can I do both".
    #
    # They live here rather than in the web layer so that every forecast sees
    # them -- the CLI included. Wiring them into one page was how the toggle
    # ended up affecting the goals but not the projection.

    one_offs.extend(_planposten(conn, account, fcfg, as_of, months))

    # Die erklaerten Abos, mit ihrem TERMIN statt als Zwoelftel. Genau das
    # unterscheidet diese Ansicht von einem Median: die 84,00 gehen an einem
    # Tag ab und nicht mit 10 im Monat, und die Senke liest den Tag.
    horizont_abos = fc.add_months(_date.fromisoformat(as_of).replace(day=1),
                                  months + 1)
    for termin in _ab.posten(erklaerte_abos, account,
                             ab=_date.fromisoformat(as_of), bis=horizont_abos):
        one_offs.append(fc.OneOff(
            label=f"abo:{termin['abo']['id']}", account_id=account,
            month=termin["faellig"], amount_cents=termin["betrag_cents"],
            day=termin["faellig"].day,
            herkunft={"quelle": "vertrag", "verweis": f"abo:{termin['abo']['id']}",
                      "herleitung": f"Termin und Betrag aus "
                                    f"{_ab.ARTEN[termin['abo'].get('art', 'abo')][0]}; die "
                                    "zugeordneten Buchungen zählen dafür nicht "
                                    "mehr im Median"}))

    # Und was Mitzahler noch schulden -- nur der offene Teil, im Einsammel-
    # Monat. Ist das Geld da, faellt der Posten von selbst heraus.
    for rueck in _ab.rueckzahlungen(conn, erklaerte_abos, account,
                                    ab=_date.fromisoformat(as_of),
                                    bis=horizont_abos):
        one_offs.append(fc.OneOff(
            label=f"abo-zurueck:{rueck['abo']['id']}", account_id=account,
            month=rueck["monat"], amount_cents=rueck["cents"],
            herkunft={"quelle": "regel", "verweis": f"abo:{rueck['abo']['id']}",
                      "herleitung": "offener Anteil der Mitzahler laut "
                                    f"{_ab.ARTEN[rueck['abo'].get('art', 'abo')][0]}, "
                                    "im Einsammel-Monat"}))

    # Money swept in from another account is real income here. Without it the
    # savings account shows the target but never the deposits that reach it.
    one_offs.extend(extra_one_offs or [])

    start = _date.fromisoformat(as_of)
    proj = fc.project(
        account_id=account, opening_cents=opening,
        start=fc.add_months(start.replace(day=1), 1), months=months,
        recurring=recurring, one_offs=one_offs, loan_schedules=schedules,
        dispo_threshold_cents=_floor_for(conn, account, fcfg, acct),
        sweep_above_cents=_sweep_ceiling(account, fcfg) if allow_sweep else None,
        income_overrides=_income_overrides(fcfg),
    )


    herkunft = _herkunft_konto(conn, fcfg, recurring, one_offs, schedules,
                               proj.rows)

    # An account can be wrong in two directions. A Giro is capped because its
    # only job is avoiding the Dispo and it pays no interest -- anything above
    # the cap is money sitting idle that belongs on the Tagesgeld. Warning only
    # downwards would treat 40.000 parked on a current account as a success.
    role = (fcfg.get("account_roles") or {}).get(account) or {}
    ceiling = role.get("ceiling_cents")
    if not ceiling and role.get("ceiling_months"):
        typical = sorted(-r.costs_cents for r in proj.rows)
        if typical:
            typical = typical[len(typical) // 2]     # median month, not the worst
            ceiling = int(round(typical * float(role["ceiling_months"])))
    over = [r for r in proj.rows if ceiling and r.closing_cents > ceiling]

    return {
        "account": account,
        "opening_cents": opening, "opening_as_of": as_of, "opening_source": source,
        "dispo_threshold_cents": proj.dispo_threshold_cents,
        "role": role.get("role"), "role_note": role.get("note"),
        "budget_cents": (fcfg.get("account_budgets") or {}).get(account),
        "ceiling_cents": ceiling,
        "over_ceiling_months": [r.month.isoformat() for r in over],
        "sweep_total_cents": sum(r.swept_cents for r in proj.rows),
        "sweeps_to": ((fcfg.get("account_roles") or {}).get(account) or {}).get("sweep_to"),
        "idle_cents": max((r.closing_cents - ceiling for r in over), default=0),
        # WOFUER, nicht nur WIE VIEL. `project` baut das Detail je Monat
        # ohnehin; es hier wegzuwerfen war der Grund, warum ein teurer Monat
        # auf `/konten` teuer aussah, ohne zu sagen, was ihn teuer macht --
        # genau die Frage, die die Tabellenkalkulation vorher beantwortet hat.
        "labels": _anzeigenamen(conn, proj.rows),
        # Je Etikett, woher der Betrag kommt. Dieselben Quellen wie im
        # Rechenweg der Jahresrechnung, damit beide Seiten vergleichbar sind.
        "herkunft": herkunft,
        "rows": [
            {"month": r.month.isoformat(), "opening_cents": r.opening_cents,
             "costs_cents": r.costs_cents, "trough_cents": r.trough_cents,
             "income_cents": r.income_cents, "closing_cents": r.closing_cents,
             "breaches_dispo": r.breaches_dispo, "swept_cents": r.swept_cents,
             "detail": dict(sorted(r.detail.items(), key=lambda kv: kv[1]))}
            for r in proj.rows
        ],
        "worst_trough_cents": proj.worst.trough_cents if proj.worst else None,
        "worst_month": proj.worst.month.isoformat() if proj.worst else None,
        "breach_months": [r.month.isoformat() for r in proj.breaches],
    }


def household_accounts(conn, *, months: int = HORIZON_DEFAULT) -> list[dict]:
    """Every account, with the transfers between them resolved.

    Three passes, and the order is the policy:

      1. Project each account alone. Where one falls below its floor, that is
         a shortfall the operating account has to cover.
      2. Top those up from the operating account, dated to the 1st so the
         money is there BEFORE the costs that would have breached the floor.
         Not for accounts with `auffuellen: false` in forecast.yaml -- there
         only the declared standing orders count.
      3. Only what is left above the ceiling is swept to savings.

    NO OTHER ACCOUNT MAY EVER GO NEGATIVE. Sweeping first and topping up
    afterwards would move money to a savings account while a Giro sits in
    Dispo -- paying overdraft interest to earn deposit interest, which is
    the exact trade this arrangement exists to avoid.
    """
    from datetime import date as _d

    from finctl.forecast.engine import OneOff


    accounts = [dict(r) for r in conn.execute(
        "SELECT id, display_name, account_type, dispo_threshold_cents FROM accounts "
        "WHERE ingest_mode='parsed' AND active=1 ORDER BY id")]
    # Durchlaufende Konten haben keinen Bestand, den man projizieren koennte.
    # PayPal deckt jede Zahlung im selben Moment; projiziert man es wie ein
    # Girokonto, stehen die Ausgaben ohne ihre Deckung da, das Konto rutscht
    # rechnerisch ins Minus, und die Auffuellung vom Betriebskonto zaehlt
    # denselben Betrag ein zweites Mal.
    # Einmal gelesen statt je Monat: die Datei aendert sich waehrend eines
    # Laufs nicht, und sie je Zeile neu zu parsen waere sechzig Mal dieselbe
    # Arbeit.
    fcfg_cache = forecast_config()
    durchlaufend = {k for k, v in (fcfg_cache.get("account_roles") or {}).items()
                    if (v or {}).get("role") == "durchlaufend"}
    accounts = [a for a in accounts if a["id"] not in durchlaufend]
    acct_cache = {a["id"]: a for a in accounts}

    def run(account_id, extra=None, allow_sweep=True):
        return account_forecast(conn, account_id, months=months,
                                extra_one_offs=extra, allow_sweep=allow_sweep)

    base, errors = {}, {}
    for a in accounts:
        try:
            base[a["id"]] = run(a["id"], allow_sweep=False)
        except Exception as exc:
            errors[a["id"]] = f"{type(exc).__name__}: {exc}"

    # Das Konto, das die anderen versorgt, ist das mit der ROLLE `operating`
    # -- nicht einfach das erste mit einem Abraeum-Ziel. Sobald ein zweites
    # Konto eines deklariert (auf /konten ein Klick), gewann sonst das
    # alphabetisch erste: das Betriebskonto raeumte nicht mehr ab, und sein
    # Guthaben lief auf einem unverzinsten Konto auf, ohne dass sich an der
    # Regel etwas geaendert haette.
    operator = _operating_account(fcfg_cache)
    if operator not in base:
        operator = next((k for k, v in base.items() if v.get("sweeps_to")), None)

    topups: dict[str, list] = {}
    operator_cost: list = []
    for account_id, f in base.items():
        if account_id == operator:
            continue
        # Je Monat gelesen, nicht einmal fuer die ganze Projektion: ein Floor,
        # der aus einer Kreditrate stammt, aendert sich mit ihr. Sparda
        # braucht ab April 2027 757 statt 540, und ein einmal gelesener Wert
        # fuellte danach zu wenig auf.
        rolle = (fcfg_cache.get("account_roles") or {}).get(account_id) or {}
        # Abgeschaltet je Konto: dann zaehlen nur die Dauerauftraege, und
        # eine Unterschreitung bleibt sichtbar statt still gedeckt.
        if rolle.get("auffuellen") is False:
            continue
        variabel = bool(rolle.get("floor_from_loan"))
        floor = f["dispo_threshold_cents"]
        # KUMULIERT MITFUEHREN, sonst wird jeden Monat dieselbe Luecke erneut
        # gefuellt. Die ungedeckte Projektion zeigt die Luecke als WACHSEND --
        # Sparda faellt ab 04/2027 um 217 im Monat, also 217, dann 434, dann
        # 651 --, aber nach der ersten Auffuellung ist der Rueckstand bereits
        # bezahlt und nur der Monatsbetrag kommt neu hinzu.
        #
        # Jede Monatsluecke einzeln zu ueberweisen summiert sich quadratisch:
        # nach dreizehn Monaten 19.747 statt 2.821, Faktor sieben. Auf Sparda
        # standen dadurch in der Prognose 19.850 auf einem Konto, das 540
        # halten soll -- und DKB zahlte es.
        gedeckt = 0
        for row in f["rows"]:
            wann = _d.fromisoformat(row["month"])
            if variabel:
                floor = _floor_for(conn, account_id, fcfg_cache,
                                   acct_cache[account_id], month=wann)
            short = floor - row["trough_cents"] - gedeckt
            if short <= 0:
                continue
            gedeckt += short
            when = wann
            label = f"Auffüllung {account_id}"
            topups.setdefault(account_id, []).append(
                OneOff(label=label, account_id=account_id, month=when,
                       amount_cents=short, day=1,
                       herkunft={"quelle": "regel", "verweis": operator,
                                 "herleitung": f"Tiefpunkt läge unter dem Floor; "
                                               f"{operator} füllt am 1. auf"}))
            operator_cost.append(
                OneOff(label=label, account_id=operator, month=when,
                       amount_cents=-short, day=1,
                       herkunft={"quelle": "regel", "verweis": account_id,
                                 "herleitung": f"an {account_id}, damit dessen "
                                               "Floor hält — vor dem Abräumen"}))

    out = []
    op_view = None
    if operator:
        op_view = run(operator, extra=operator_cost)
    sweeps: dict[str, list] = {}
    if op_view:
        for row in op_view["rows"]:
            if row["swept_cents"]:
                sweeps.setdefault(op_view["sweeps_to"], []).append(OneOff(
                    label=f"Übertrag von {operator}", account_id=op_view["sweeps_to"],
                    month=_d.fromisoformat(row["month"]),
                    amount_cents=row["swept_cents"], day=28,
                    herkunft={"quelle": "regel", "verweis": operator,
                              "herleitung": f"was auf {operator} über dem Deckel "
                                            "liegt, am Monatsende abgeräumt"}))

    for a in accounts:
        if a["id"] in errors:
            out.append({**a, "error": errors[a["id"]]})
            continue
        if a["id"] == operator:
            out.append({**a, **op_view, "topped_up_cents":
                        -sum(o.amount_cents for o in operator_cost)})
            continue
        extra = (topups.get(a["id"]) or []) + (sweeps.get(a["id"]) or [])
        # ABRAEUMEN NUR VOM BETRIEBSKONTO. Nur fuer dessen Uebertrag gibt es
        # die Gegenbuchung auf dem Zielkonto; bei jedem anderen verschwaende
        # die Prognose Geld, das nirgends ankommt. Ohne diese Zeile hing es
        # davon ab, ob das Konto zufaellig eine Auffuellung bekommt: mit
        # Auffuellung wurde abgeraeumt, ohne nicht.
        view = (run(a["id"], extra=extra, allow_sweep=False) if extra
                else base[a["id"]])
        # Und deshalb auch kein Ziel in der Sicht: die Seite zeigte sonst
        # „0 € → Ziel" als Regel, die nie etwas bewegt.
        out.append({**a, **view, "sweeps_to": None,
                    "topped_up_cents": sum(o.amount_cents for o in topups.get(a["id"], []))})
    return out


