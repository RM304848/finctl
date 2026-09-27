"""Kalenderrechnung, die mehrere Module brauchen.

Liegt im Kern, weil Vertraege (`abos.py`) und Energie (`strom.py`) sie beide
brauchen und keins der beiden das andere voraussetzen darf
(`finctl/module.py`).
"""

from __future__ import annotations

from datetime import date


def plus_monate(d: date, n: int) -> date:
    index = d.year * 12 + d.month - 1 + n
    jahr, monat = divmod(index, 12)
    monat += 1
    # Der 31. im Februar existiert nicht. Auf den letzten Tag des Monats
    # gekappt statt in den naechsten zu rutschen -- eine Lastschrift zum
    # Monatsende bleibt zum Monatsende.
    schalt = jahr % 4 == 0 and (jahr % 100 != 0 or jahr % 400 == 0)
    letzter = [31, 29 if schalt else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][monat - 1]
    return date(jahr, monat, min(d.day, letzter))
