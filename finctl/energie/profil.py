"""Wie sich ein Jahresverbrauch ueber die Tage verteilt.

Strom und Wasser verbrauchen sich ueber das Jahr ungefaehr gleichmaessig:
jeder Tag zaehlt gleich (`linear`). Heizenergie nicht -- im Januar geht ein
Vielfaches dessen durch, was im Juli gebraucht wird. Wer im Maerz abliest und
linear hochrechnet, unterschaetzt den Winter, der noch kommt, oder ueberschaetzt
ihn im Oktober um das Doppelte.

`heizung` verteilt deshalb nach Gradtagzahlen: die Monatsanteile der
Heizarbeit eines Normjahres, wie sie in Heizkostenabrechnungen bei einem
Nutzerwechsel angesetzt werden (Promilletabelle nach VDI 2067). Ein Teil des
Verbrauchs haengt nicht am Wetter -- Warmwasser, Kochen --, der
`grundlast`-Anteil verteilt sich gleichmaessig.

Die Gewichte eines ganzen Jahres ergeben zusammen genau eins. Damit ist
`verbrauch / gewicht(zeitraum)` der Jahresverbrauch, egal wie lang und in
welcher Jahreszeit der Zeitraum liegt.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

#: Monatsanteile der Heizarbeit im Normjahr, in Promille (Summe 1000).
GRADTAGE_PROMILLE = {1: 170, 2: 150, 3: 130, 4: 80, 5: 40, 6: 40 / 3, 7: 40 / 3,
                     8: 40 / 3, 9: 30, 10: 80, 11: 120, 12: 160}

PROFILE = ("linear", "heizung")


def tagesgewicht(tag: date, profil: str, grundlast: float = 0.0) -> float:
    """Der Anteil dieses Tages an einem Normjahr."""
    if profil == "linear":
        return 1 / 365
    if profil != "heizung":
        raise ValueError(f"Unbekanntes Profil {profil!r}, erwartet: {', '.join(PROFILE)}")
    tage_im_monat = calendar.monthrange(tag.year, tag.month)[1]
    wetter = GRADTAGE_PROMILLE[tag.month] / 1000 / tage_im_monat
    return grundlast / 365 + (1 - grundlast) * wetter


def gewicht(von: date, tage: int, profil: str, grundlast: float = 0.0) -> float:
    """Der Anteil der `tage` Tage ab `von` (einschliesslich) an einem Normjahr."""
    if profil == "linear":
        return max(tage, 0) / 365
    return sum(tagesgewicht(von + timedelta(days=i), profil, grundlast)
               for i in range(max(tage, 0)))


def grundlast_pruefen(grundlast: float) -> None:
    if not 0 <= grundlast <= 1:
        raise ValueError("Der Grundlastanteil liegt zwischen 0 und 100 %.")
