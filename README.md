# Finance OS

Finance OS liest deine Kontoauszüge ein und ordnet jede Buchung zu – lokal
auf deinem Rechner, ohne Cloud und ohne Bankzugang. Daraus entsteht jeden
Monat ein Abschluss: wohin das Geld geht, was Fixkosten sind und was übrig
bleibt. Und der Blick nach vorn: Kredite, Immobilien, Anschlussfinanzierungen,
Lebensziele, große Anschaffungen und Urlaube, Rentenlücke – durchgerechnet bis
zum Lebensende, sodass Entscheidungen auf Zahlen beruhen statt auf Gefühl.

**Installieren:** [docs/installation.md](docs/installation.md) – die fertigen
Pakete für Mac (`.dmg`) und Windows (`.exe`) stehen unter
[Releases](https://github.com/RM304848/finctl/releases).

**Rückmeldungen:** über [Issues](https://github.com/RM304848/finctl/issues),
bitte ohne echte Beträge, Namen oder Auszüge.

## Keine Beratung

Die App rechnet, sie berät nicht. Hinweise zu Steuern (etwa Anlage V,
Vorabpauschale), Renten und Anlagen sind Rechenhilfen ohne Gewähr und ersetzen
keine Steuer-, Rechts- oder Anlageberatung.

## Lizenz

[PolyForm Strict 1.0.0](LICENSE.md): Nutzen für private und andere
nicht-kommerzielle Zwecke ist erlaubt. Nicht erlaubt sind kommerzielle
Nutzung, Änderungen, darauf aufbauende Werke und die Weitergabe. Wer mehr
möchte, fragt.

Die mitgelieferten Bibliotheken stehen unter ihren eigenen Lizenzen (MIT, BSD,
Apache 2.0 und andere); in den Paketen liegen sie als `DRITTLIZENZEN.txt` bei.

## Entwickeln

Python 3.12, SQLite, FastAPI. `CLAUDE.md` beschreibt die Regeln des Codes,
`HANDBUCH.md` die Bedienung, `CHANGELOG.md` die Versionen.
