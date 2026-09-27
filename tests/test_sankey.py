"""Das Flussdiagramm geht auf: was links hineingeht, geht rechts hinaus."""

from finctl.web.sankey import Posten, euro_rund, layout


def _summe(knoten):
    return sum(n["cents"] for n in knoten)


def test_a_surplus_becomes_a_sink_and_both_sides_balance():
    f = layout([Posten("Gehalt", 300_000), Posten("Miete", 100_000)],
               [Posten("Wohnen", 250_000), Posten("Kredit", 100_000)])
    assert _summe(f["links"]) == _summe(f["rechts"]) == 400_000
    assert f["rechts"][-1]["label"] == "gespart"
    assert f["rechts"][-1]["cents"] == 50_000
    # Je Knoten ein Band, links hinein und rechts hinaus.
    assert len(f["baender"]) == len(f["links"]) + len(f["rechts"])


def test_a_deficit_becomes_a_source():
    f = layout([Posten("Gehalt", 100_000)], [Posten("Auto", 150_000)])
    assert f["links"][-1]["label"] == "aus Rücklagen"
    assert f["links"][-1]["cents"] == 50_000
    assert _summe(f["links"]) == _summe(f["rechts"])


def test_small_items_fold_into_one_and_keep_their_names():
    klein = [Posten(f"Klein {i}", 100) for i in range(5)]
    f = layout([Posten("Gehalt", 1_000_000)], [Posten("Wohnen", 900_000), *klein])
    uebrig = [n for n in f["rechts"] if n["art"] == "uebrig"]
    assert len(uebrig) == 1 and len(uebrig[0]["teile"]) == 5
    assert uebrig[0]["cents"] == 500


def test_nothing_flowing_draws_nothing():
    assert layout([], []) == {}


def test_nodes_stay_inside_the_picture():
    f = layout([Posten(str(i), 10_000 * (i + 1)) for i in range(12)],
               [Posten(str(i), 9_000 * (i + 1)) for i in range(12)])
    for n in f["links"] + f["rechts"]:
        assert n["y"] >= 0 and n["y"] + n["h"] <= f["hoehe"]


def test_euro_rounded_to_whole_euros_with_german_grouping():
    assert euro_rund(4_612_230) == "46.122 €"


def test_folded_items_keep_their_categories_and_get_one_link():
    klein = [Posten(f"Klein {i}", 100, kategorien=[f"k/{i}"]) for i in range(3)]
    f = layout([Posten("Gehalt", 1_000_000, kategorien=["einkommen/gehalt"])],
               [Posten("Wohnen", 900_000, kategorien=["wohnen/miete"]), *klein],
               link=lambda ks: "/transactions?" + "&".join(f"category={k}" for k in ks))
    uebrig = next(n for n in f["rechts"] if n["art"] == "uebrig")
    assert all(f"category=k/{i}" in uebrig["href"] for i in range(3))
    assert f["links"][0]["href"].endswith("category=einkommen/gehalt")


def test_a_rest_node_is_never_folded_away():
    f = layout([Posten("Gehalt", 1_000_000), Posten("aus dem Depot", 500, art="rest"),
                Posten("A", 400), Posten("B", 300)], [Posten("Wohnen", 900_000)])
    assert any(n["label"] == "aus dem Depot" for n in f["links"])


def test_uebrige_links_to_all_its_blocks_or_to_a_shared_page():
    """Gemessene Bloecke: ein Link auf alle ihre Buchungen. Lauter
    Planzeilen: die Planung. Gemischt: kein Link, der nur einen Teil zeigt."""
    gross = Posten("Gehalt", 1_000_000)

    def uebrig(*teile):
        f = layout([gross], [Posten("Miete", 500_000), *teile],
                          uebrig_href="#rechenweg",
                          link=lambda k: "/transactions?" + "&".join(f"block={b}" for b in k))
        return next(n for n in f["rechts"] if n["art"] == "uebrig")["href"]

    assert uebrig(Posten("a", 100, kategorien=["steuern"]),
                  Posten("b", 100, kategorien=["kredit"])) \
        == "/transactions?block=steuern&block=kredit"
    assert uebrig(Posten("a", 100, href="/planung#klammer-x"),
                  Posten("b", 100, href="/planung#klammer-y")) == "/planung"
    assert uebrig(Posten("a", 100, href="/planung#klammer-x"),
                  Posten("b", 100, kategorien=["steuern"])) == "#rechenweg"
