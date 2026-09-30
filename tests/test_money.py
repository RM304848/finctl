"""German amount parsing.

A misparsed amount is the most dangerous silent failure in the system: it
would pass the reconciliation gate only by coincidence, and quietly corrupt
every downstream total.
"""

import pytest

from finctl.ledger.db import format_eur, parse_de_amount


@pytest.mark.parametrize(
    "raw,cents",
    [
        ("1.234,56", 123456),
        ("-1.234,56", -123456),
        ("1234,56", 123456),
        ("0,01", 1),
        ("1.234.567,89", 123456789),
        ("1.234,56 EUR", 123456),
        ("1.234,56 €", 123456),
        ("  1.234,56  ", 123456),
        # Trailing sign and Haben/Soll suffixes, both used by German banks.
        ("1.234,56-", -123456),
        ("1.234,56 S", -123456),
        ("1.234,56 H", 123456),
        ("+1.234,56", 123456),
        # No comma: a lone dot is a decimal point, since no German amount
        # groups thousands into two digits.
        ("1234.56", 123456),
        # Ganze Euro ohne Komma, wie der DKB-Export sie schreibt: ein Punkt
        # vor genau drei Ziffern trennt Tausender, er ist kein Komma.
        ("2.000", 200000),
        ("-12.500", -1250000),
        ("950", 95000),
    ],
)
def test_parse(raw, cents):
    assert parse_de_amount(raw) == cents


@pytest.mark.parametrize("raw", ["", "abc", None, "1,2,3,4"])
def test_rejects_garbage(raw):
    """Raise rather than guess -- a wrong amount is worse than a failed import."""
    with pytest.raises(ValueError):
        parse_de_amount(raw)


def test_format_round_trip():
    for cents in (0, 1, -1, 123456, -123456, 123456789):
        assert parse_de_amount(format_eur(cents).replace("€", "").strip()) == cents
