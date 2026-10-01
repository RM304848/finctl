"""Das Diagramm-Modul (docs/design_conventions.yaml, diagramme).

Erfundene, runde Werte: das Modul kennt keine Konten, nur Monate und Cent.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from finctl.web import diagramm as dg


def _monate(n: int, start: dt.date = dt.date(2026, 1, 1)) -> list[dt.date]:
    return [dt.date(start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1, 1)
            for i in range(n)]


def _tips(html: str) -> list:
    roh = re.search(r'data-tips="([^"]*)"', html).group(1)
    return json.loads(roh.replace("&quot;", '"').replace("&amp;", "&"))


def _zwei_linien(**extra) -> str:
    m = _monate(6)
    return str(dg.linien("Verlauf", m, [
        dg.Linie("nach Kosten", "--accent", [100_000, 50_000, -20_000, 0, 30_000, 80_000],
                 art="beides", auffaellig=frozenset({2})),
        dg.Linie("nach Gehalt", "--muted", [300_000] * 6)], **extra))


def test_every_chart_carries_its_table_with_one_row_per_month():
    html = _zwei_linien()
    koerper = re.search(r"<tbody>(.*)</tbody>", html, re.S).group(1)
    assert koerper.count("<tr>") == 6
    assert "<summary>Tabelle</summary>" in html
    # Neuester Monat zuerst, wie jede Tabelle der App.
    assert koerper.index("2026-06") < koerper.index("2026-01")


def test_the_table_colours_a_direction_and_the_tooltip_does_not():
    html = _zwei_linien()
    assert 'class="num neg">-200,00 €' in html
    assert 'class="num pos">1.000,00 €' in html
    assert all("neg" not in str(z) for _, zeilen in _tips(html) for z in zeilen)


def test_a_legend_appears_from_two_entries_on():
    assert 'class="legende"' in _zwei_linien()
    eine = str(dg.linien("Eins", _monate(3), [dg.Linie("a", "--accent", [1, 2, 3])]))
    assert 'class="legende"' not in eine


def test_red_points_get_a_legend_entry_only_when_asked_and_present():
    assert "unter Grenze" in _zwei_linien(auffaellig_name="unter Grenze")
    ohne = str(dg.linien("x", _monate(2), [dg.Linie("a", "--accent", [1, 2], art="beides")],
                         grenzen=[dg.Grenze("Untergrenze", 0, "--bad")],
                         auffaellig_name="unter Grenze"))
    assert "unter Grenze" not in ohne


def test_a_limit_names_its_value_in_the_legend():
    html = _zwei_linien(grenzen=[dg.Grenze("Untergrenze", 100_000, "--bad", flaeche=True)])
    assert "Untergrenze 1.000,00 €" in html
    assert "opacity:.07" in html, "die Flaeche unter der Untergrenze fehlt"


def test_marks_follow_the_rules():
    html = _zwei_linien(grenzen=[dg.Grenze("Deckel", 400_000, "--warn")])
    assert "stroke-dasharray" not in html, "nichts gestrichelt"
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", html), "Farben nur als Variable"
    assert not re.search(r"<text[^>]*style=", html), "Text nie in Serienfarbe"
    assert html.count('class="linie"') >= 3


def test_the_axis_shows_whole_euros_and_short_months():
    html = _zwei_linien()
    achse = re.findall(r'<text class="tm"[^>]*>([^<]*)</text>', html)
    assert "26-01" in achse
    assert all("," not in a for a in achse), achse


def test_month_labels_stay_few_and_fall_on_round_months():
    html = str(dg.linien("x", _monate(24), [dg.Linie("a", "--accent", list(range(24)))]))
    monate = re.findall(r'>(\d\d-\d\d)</text>', html)
    assert monate == ["26-01", "26-07", "27-01", "27-07"]


def test_an_outlier_cuts_the_axis_and_says_so():
    werte = [100_000, 120_000, 110_000, 2_000_000]
    html = str(dg.linien("x", _monate(4), [dg.Linie("a", "--accent", werte)], kappen=True))
    achse = [int(a.replace(".", "")) for a in
             re.findall(r'text-anchor="end">(-?[\d.]+)</text>', html)]
    assert max(achse) < 20_000, "die Achse endet beim zweithoechsten Wert"
    assert "↑ 20.000" in html
    # Ohne Ausreisser bleibt alles, wie es ist.
    ruhig = str(dg.linien("x", _monate(3), [dg.Linie("a", "--accent", werte[:3])], kappen=True))
    assert "↑" not in ruhig


def test_bars_grow_from_zero_both_ways():
    balken = [dg.Balken(50_000, "--accent", "gemessen"), dg.Balken(-30_000, "--bad", "gemessen"),
              dg.Balken(20_000, "--muted", "vorausgerechnet")]
    html = str(dg.saeulen("Cashflow", _monate(3), "Cashflow", balken,
                          legende=[("gemessen", "--accent"), ("vorausgerechnet", "--muted")],
                          zusatz=[dg.Linie("Nebenkonto", "--muted", [-1_000, -2_000, -3_000])]))
    assert html.count('class="treffer"') == 3, "jede Saeule ist ein Ziel fuer den Tooltip"
    assert 'tabindex="0"' in html, "und per Tastatur erreichbar"
    kopf = [k for k, _ in _tips(html)]
    assert kopf == ["2026-01 · gemessen", "2026-02 · gemessen", "2026-03 · vorausgerechnet"]
    assert "Nebenkonto" in html and "-30,00 €" in html


def test_no_data_gives_an_empty_figure_not_an_error():
    assert "Keine Daten" in str(dg.linien("x", [], []))
    assert "Keine Daten" in str(dg.linien("x", _monate(2), [dg.Linie("a", "--accent",
                                                                     [None, None])]))
    assert "Keine Daten" in str(dg.saeulen("x", [], "x", [], legende=[]))


def test_labels_are_escaped():
    html = str(dg.linien("<b>", _monate(2), [dg.Linie("<i>", "--accent", [1, 2]),
                                             dg.Linie("b", "--muted", [1, 2])]))
    assert "<b>" not in html and "<i>" not in html.replace("<i ", "")


def test_round_ticks_cover_the_range():
    for lo, hi in ((0, 7), (-1234, 98765), (3.2, 3.9), (-50, -10)):
        ticks = dg.schoene_ticks(lo, hi)
        assert ticks[0] <= lo and ticks[-1] >= hi
        assert 3 <= len(ticks) <= 6


def test_whole_euros_never_print_minus_zero():
    assert dg.euro_ganz(-40) == "0"
    assert dg.euro_ganz(-4_000_000) == "-40.000"
    assert dg.euro_ganz(123_456_700) == "1.234.567"
