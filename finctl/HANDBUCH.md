# Handbuch — Betrieb ohne KI

Finance OS liest deine Kontoauszüge ein und ordnet jede Buchung zu – lokal
auf deinem Rechner, ohne Cloud und ohne Bankzugang. Daraus entsteht jeden
Monat ein Abschluss: wohin das Geld geht, was Fixkosten sind und was übrig
bleibt. Und der Blick nach vorn: Kredite, Immobilien, Anschlussfinanzierungen,
Lebensziele, große Anschaffungen und Urlaube, Rentenlücke – durchgerechnet bis
zum Lebensende, sodass Entscheidungen auf Zahlen beruhen statt auf Gefühl.

Das Werkzeug läuft vollständig ohne Sprachmodell. KI hat die Regeln *geschrieben*;
ausgeführt werden sie von gewöhnlichem Python, deterministisch und offline. Zweimal
laufen lassen ergibt byte-identische Splits — dafür gibt es einen Test.

Dieses Dokument ist die Bedienungsanleitung für den Fall, dass nie wieder ein Modell
beteiligt ist.

Die App rechnet, sie berät nicht. Was sie zu Steuern, Renten und Anlagen sagt, sind
Rechenhilfen ohne Gewähr, keine Steuer- oder Anlageberatung.

---

## 1. Der Monatsablauf

Vier Befehle, in dieser Reihenfolge. Zusammen unter einer Minute.

**Alles hängt am Datenordner.** Darin liegen `config/` und `data/`, die Auszüge
in `data/statements/`, je Konto ein Ordner.
Welcher Ordner das ist, sagt:

```bash
.venv/bin/finctl ort
```

Der Befehl nennt den Ordner **und warum er es ist** — das ist die Auskunft, die man
braucht, wenn plötzlich ein leeres Hauptbuch aufgeht. Es gibt vier Regeln, in dieser
Reihenfolge: die Variable `FINCTL_DATEN`, der eingetragene Ort
(`finctl ort --setzen <ordner>`), ein `config/` im aktuellen Verzeichnis, und sonst
die Vorgabe des Systems (`~/Library/Application Support/Finance OS`).

Ohne eigene Angabe greift die letzte: der Systemordner. Dann arbeitet `finctl`
von jedem Verzeichnis aus mit denselben Daten, und die Beispiele unten laufen
überall. Wer aus einem Quellcode-Ordner arbeitet, schreibt `.venv/bin/finctl`
statt `finctl`.

### Der schnelle Weg: zwei Knöpfe

Läuft das Dashboard (`finctl serve`), reicht die **Monatsabschluss**-Seite:

| Knopf | was er tut |
|---|---|
| **Update** | Neue PDFs einlesen, Regeln anwenden, Vorgänge zuordnen, prüfen — die Schritte 2 bis 4 unten |
| **Sichern** | Einen Stand in den Sicherungsordner schreiben |

Beide melden das Ergebnis darunter, inklusive abgelehnter Auszüge und Lücken in
der Auszugskette. PDFs vorher ablegen (Schritt 1). Die Terminalvariante darunter
tut dasselbe und ist die Rückfallebene, wenn das Dashboard nicht läuft.

**1. Auszüge ablegen.** PDFs in den Ordner des jeweiligen Kontos, Dateiname egal —
identifiziert wird über den SHA-256 des Inhalts, dieselbe Datei zweimal ablegen
schadet also nicht.

```bash
finctl ort        # der Datenordner; die Auszuege liegen darin unter data/statements/
```

| Konto | Ordner |
|---|---|
| DKB Giro | `data/statements/dkb-giro/` |
| Trade Republic | `data/statements/trade-republic/` |
| C24 | `data/statements/c24/` |
| Sparda | `data/statements/sparda/` |
| Scalable Tagesgeld | `data/statements/scalable_tg/` |

**2. Einlesen.**

```bash
finctl ingest run
```

Ein Auszug wird nur übernommen, wenn `Anfangssaldo + Σ Buchungen = Endsaldo`.
Stimmt das nicht, wird er **abgelehnt**, nicht halb importiert. `rejected 0` ist das
Ziel.

**3. Kategorisieren.**

```bash
finctl categorize
```

Wendet den Regelsatz an. Manuelle Entscheidungen werden nie überschrieben.

**4. Prüfen.**

```bash
finctl validate
```

Muss `All invariants hold.` sagen. Meldet es eine Lücke in der Auszugskette, fehlt
ein PDF — die Kette verlangt, dass der Endsaldo eines Auszugs der Anfangssaldo des
nächsten ist.

**5. Nacharbeiten im Dashboard.**

```bash
finctl serve
```

→ <http://127.0.0.1:8765>, Seite **Transaktionen**, Reiter **Offen**. Dort steht, was keine Regel erkannt hat.

Aus dem Paket (`.dmg`, `.exe`) oder mit `finctl app` startet dasselbe Dashboard
auf <http://127.0.0.1:8777>, richtet beim ersten Mal ein und öffnet den Browser.
Beendet wird es dann über **Beenden** oben rechts.

---

## 2. Wo was geändert wird

Alles Dauerhafte steht in `config/` als YAML mit Kommentaren. Bearbeitbar mit jedem
Texteditor; nach jeder Änderung `finctl categorize` (bzw. `finctl init` bei Stammdaten).

| Datei | Wofür | Auch im Frontend? |
|---|---|---|
| `rules.yaml` | Der Regelsatz | teilweise („Regel ändern") |
| `taxonomy.yaml` | Kategorien, Steuerpositionen, `fix`-Flag | Kategorien: ja |
| `taxonomy_custom.yaml` | Im Dashboard angelegte Kategorien | ja |
| `taxonomy_migrations.yaml` | Umbenennungen und Zusammenlegungen | ja |
| `overrides.yaml` | Deine Handkorrekturen (automatisch geschrieben) | ja |
| `accounts.yaml` | Konten, IBANs, Dispo-Grenzen | Dispo: ja |
| `properties.yaml` | Objekte, AfA, Kaufdaten | teilweise |
| `loans.yaml` | Darlehen und Zinsbindungssegmente | nein |
| `planned.yaml` | Datierte künftige Posten | ja (Reiter Planung) |
| `szenarien.yaml` | Klammern: Verpflichtungen und Pläne | ja (Reiter Planung) |
| **`assumptions.yaml`** | **Alle Annahmen: Inflation, Rendite, Gehaltsfloor** | teilweise |
| `forecast.yaml` | Was fortgeschrieben werden darf | nein |
| `goals.yaml` | Ziele | Zielwerte: ja |
| `scenarios.yaml` | Alternativszenarien | nein |
| `adjustments.yaml` | Korrektur eines falsch gedruckten Saldos | nein |

**`assumptions.yaml` ist die einzige Stelle für eine Annahme.** Alles, was die
Prognose rechnet, kommt von dort: die Inflationsrate von 2 %, die nominale Rendite,
der Gehaltsfloor. Fakten stehen woanders — ein Kreditzins steht in `loans.yaml`, eine
AfA-Basis in `properties.yaml`, ein Policenwert in `lebensplan.yaml`, jeweils neben
seiner Herleitung. Die Trennung ist der Zweck: ein Vertrag ist nicht verhandelbar,
eine Annahme schon.

Ein **Szenario ist eine Kopie dieser Datei** mit anderen Werten. Kein Knopf, keine
Tabelle, keine Oberfläche — eine Datei.

**`overrides.yaml` ist deine Versicherung.** Jede Handkorrektur wird dort über den
`dedup_hash` der Buchung festgehalten, nicht über die ID. Die Datenbank darf also
jederzeit gelöscht und aus den PDFs neu gebaut werden — deine Entscheidungen kommen
zurück. (Genau das ist einmal schiefgegangen, bevor es diese Datei gab.)

---

## 3. Eine Regel selbst schreiben

`config/rules.yaml`, aufsteigende Priorität, **die erste passende Regel gewinnt**.
Textvergleich ignoriert Leerzeichen und Satzzeichen vollständig — `WEG Musterstr`
trifft auch, wenn das PDF `WEG Muster str.` gerendert hat.

```yaml
  - id: mein-fitnessstudio
    priority: 30
    name: Fitnessstudio
    match: {text: ["FitX", "McFit"]}
    set: {mgmt: konsum/fitness, tax: privat/nicht-abzugsfaehig}
```

**Matcher:** `text` (ODER-Liste), `text_all` (UND), `regex`, `counterparty`, `iban`,
`iban_is_own`, `account`, `tx_type`, `sign`, `amount_between`, `amount_abs_between`,
`date_from`, `date_to`, `any_of`, `all_of`, `none_of`

**Aktionen:** `mgmt`, `tax`, `property`, `loan`, `transfer_account`, `note`

### Eine Zahlung aufteilen

```yaml
    split:
      - {amount: 9.00,  mgmt: wohnen/gez}
      - {amount: 27.00, mgmt: wohnen/internet}
      - {mgmt: wohnen/miete}      # letzter Eintrag nimmt den Rest
```

Der letzte Eintrag ohne Betrag bekommt die Differenz, damit die Teile **immer** exakt
auf die Buchung aufgehen. Alternativ `pct:` statt `amount:`.

Ein Split, den du im Dashboard machst, gilt nur für die eine Buchung. Eine Regel mit
`split:` gilt für alle — vergangene wie künftige.

### Zwei Fallen, die schon zugeschlagen haben

**Namen.** `squash()` entfernt Leerzeichen. „Erika Mustermann" ist deshalb **kein**
Teilstring von „Erika Maria Mustermann". Drucken die Auszüge zwei Schreibweisen, müssen
beide in die Liste.

**Präfixe.** Ein Blattknoten wird exakt verglichen, eine Oberkategorie als Präfix.
`einkommen/gehalt` als Präfix hätte auch `einkommen/gehalt-nebentaetig` gefangen.

---

## 3b. PayPal auflösen

Eine PayPal-Zahlung kommt auf dem Kontoauszug als „PayPal Europe S.a.r.l." an —
ohne Händler, ohne Person, ohne Grund. Das ist der Grund, warum diese Buchungen am
längsten unkategorisiert bleiben.

PayPal-Export ziehen (Aktivitäten → Berichte → Kontoauszüge, CSV), dann:

```bash
finctl paypal ~/Downloads/<export>.CSV --year 2025
```

Das nennt zu jeder offenen Buchung die Gegenpartei und schlägt eine Kategorie vor —
aus **deinen eigenen früheren Entscheidungen** zu derselben Person, nicht geraten.

**Es schreibt nichts.** Wofür eine Zahlung an einen Freund war, steht in keiner der
beiden Dateien — dieselbe Person ist einen Monat Gastronomie und den nächsten
Geschenke. Zuordnen im Dashboard oder mit `finctl split`.

---

## 4. Steuer

Reiter **Steuer**. Sortiert nach Anlage → Position → Objekt, also in der Form der
Formulare, nicht des Haushaltsbuchs. Jede Zeile führt über **Belege →** auf die
Buchungen dahinter.

Ablauf für eine Erklärung:

1. Jahr wählen.
2. Die rote Warnung **„n Buchungen ohne Steuerposition"** abarbeiten. Die stehen im
   Ledger, tauchen aber in keiner Anlage auf — die Fehlerart, die am spätesten
   auffällt, weil jede Haushaltssumme trotzdem stimmt.
3. Bei Anlage V prüfen, ob jede Zeile ein Objekt trägt. `⚠ ohne Objekt` ist ein
   Fehler: Anlage V ist ein Formular **pro Objekt**.
4. Beträge nach WISO übertragen.
5. **AfA steht bewusst nicht auf dieser Seite.** Sie kostet kein Geld und steht in
   keinem Kontoauszug. Sie kommt aus `properties.yaml` und ist unter **Immobilien**
   sichtbar.

In der Transaktionstabelle filtert das Feld **Steuer** auf derselben Achse,
mehrfach wählbar. `— ohne Steuerposition —` findet genau die Lücken aus Schritt 2.

---

## 5. Prognose und Allokation

Reiter **Prognose**.

Fortgeschrieben wird jede Kategorie mit dem **Median ihrer beobachteten Monate, flach**
— heutige Preise, keine Indexierung. Jahresbeiträge (KFZ-Steuer, Hausrat, PHV) werden
als Jahressumme im Fälligkeitsmonat geführt, nicht auf Zwölftel geglättet, damit die
Dispo-Warnung die richtige Talsohle sieht.

Nicht fortgeschrieben: Kredite (die kommen aus dem Tilgungsplan in `loans.yaml` und
steigen deshalb korrekt bei Ablauf der Zinsbindung), Projektausgaben und
Vermögensumschichtungen.

Die Spalten **in Puffer** / **investierbar** / **kumuliert** sind **keine**
Vermögensprognose. Sie sagen nur, wie viel freier Cashflow in dem Monat allokierbar
ist, in der Reihenfolge aus `goals.yaml`: erst Tagesgeld bis zum Ziel, dann frei fürs
Depot. Es gibt bewusst keine Sparrate einzutragen — die Rate *ist* der Überschuss.
Was der Broker daraus macht, steht hier nicht.

---

## 6. Sicherung

```bash
finctl backup
```

Oder der Knopf **Sichern** auf der Monatsabschluss-Seite.

Ziel und Anzahl der aufbewahrten Stände stehen in **`config/backup.yaml`**, eingetragen
in der Einrichtung:

```yaml
directory: ~/Sicherung/finance-os      # ein Ordner deiner Wahl, gern in einer Cloud
keep: 12
```

Ältere Stände werden beim Schreiben gelöscht. `--to` überschreibt das Ziel einmalig.

Enthalten ist der **Datenordner, und nur der**: die Datenbank als SQL-Dump, die
komplette `config/`, die Versionen, mit denen es lief (`requirements.lock`), und ein
Blatt `stand.yaml`, das erklärt, was fehlt und wie man zurückkommt. Meist unter
einem Megabyte.

**Nicht enthalten ist der Quellcode** — das Programm kommt aus der Installation.
Das trennt, was du entschieden hast, von dem, womit du es aufgeschrieben hast.

**Nicht enthalten sind die Kontoauszüge.** Sie würden das Archiv vervielfachen;
bewahre sie dort auf, wo du sie ohnehin ablegst. Aus Auszügen und `config/` lässt
sich das Hauptbuch jederzeit neu bauen (unten).

In der App: Einrichtung → **Aus einer Sicherung wiederherstellen**. Die Sicherung
landet in einem neuen Ordner neben dem bisherigen, und **Diesen Stand verwenden**
schaltet ab dem nächsten Öffnen um. Der alte Ordner bleibt unverändert.

Im Terminal sieht die Wiederherstellung so aus — App installieren, Archiv einlesen:

```bash
.venv/bin/finctl restore <archiv>.tar.gz --to ~/finance-wieder
```

Das packt aus, baut `data/finance.db` aus dem Dump und sagt, was drin war. Danach
bekommt das Programm den Ordner genannt, und dann wird geprüft:

```bash
.venv/bin/finctl ort --setzen ~/finance-wieder && .venv/bin/finctl validate
```

`restore` schreibt nie in eine bestehende Installation: das Ziel muss leer sein.
Sonst wäre der erste Versuch einer Wiederherstellung der Moment, in dem der letzte
gute Stand verloren geht.

Oder ohne den Dump, allein aus Auszügen und Konfiguration:

```bash
finctl init && finctl ingest run && finctl categorize && finctl validate
```

---

## 7. Wenn etwas nicht stimmt

| Symptom | Ursache | Abhilfe |
|---|---|---|
| `rejected` beim Einlesen | Saldo geht nicht auf | Der Auszug bleibt draußen. Parser prüfen, oder Sonderfall in `adjustments.yaml`. |
| `gap on <konto>` | Auszug fehlt in der Kette | Fehlendes PDF nachlegen. |
| Viele Buchungen unter Offen | Neuer Zahlungsempfänger | Regel schreiben oder im Dashboard einzeln zuordnen. |
| „Regeln, denen du widersprichst" | Dieselbe Regel oft korrigiert | Die Regel ist falsch. Knopf **Regel ändern** oder `rules.yaml` bearbeiten. |
| Kategorie verschwunden | Umbenannt ohne Migration | Eintrag in `taxonomy_migrations.yaml` unter `remap:`. |
| `command not found: finctl` | Nicht installiert oder nicht im Pfad | `uv tool install` bzw. `pipx install` erneut, oder aus dem Quellcode-Ordner `.venv/bin/finctl`. |
| `Kein config/ unter …` oder ein leeres Hauptbuch | Der falsche Datenordner | `finctl ort` zeigt, welcher es ist und warum. Stimmt er nicht: `finctl ort --setzen <ordner>`. |
| Zahl ändert sich nach `init` | YAML überschreibt | So gewollt: YAML gewinnt, wo es etwas sagt. |

Nichts wird automatisch umgeschrieben. Der Regelsatz ändert sich nur, wenn du ihn
änderst — auch dann nicht, wenn du derselben Regel vierzigmal widersprochen hast. Sie
wird dir gemeldet, nicht heimlich korrigiert.

---

## 8. Tests

```bash
.venv/bin/python -m pytest -q                  # alles, vor jedem Commit
.venv/bin/python -m pytest -q -m "not langsam" # schnelle Runde, Sekunden
```

Alles läuft parallel auf allen Kernen und **in einer Kopie der Daten**: jeder
Testprozess legt `config/` und `data/` in einen Wegwerfordner und arbeitet dort.
Ein abgebrochener Lauf hinterlässt deshalb nichts in den echten Dateien, und ein
laufendes Dashboard sieht keine Testzahlen. Einen einzelnen Test ohne Parallelstart:
`-n 0 tests/test_x.py::test_y`.

Darunter: der Tilgungsplan gegen das Forward-Angebot der Sparda auf den Cent, die
Reconciliation-Schranke, dass zweimaliges Kategorisieren identische Splits ergibt,
und der **Referenzstand** (`tests/referenz.py`): ein Neuaufbau aus `config/` und den
Auszügen muss Hauptbuch und jede Seite Zeile für Zeile so ergeben wie festgehalten.
Rot heißt: nicht committen.

---

## 9. Was die Seiten zeigen

Je Seite: welche Frage sie beantwortet, wie ihre Spalten zu lesen sind und
welche Falle es dort gibt. Der Konfigurationsfuß jeder Seite verlinkt hierher.
Wie gerechnet wird, steht in Abschnitt 5.

### /monatsabschluss

Was am Monatsanfang zu tun ist, in der Reihenfolge, in der es Sinn ergibt — plus die
Kennzahlen des laufenden Jahres.

- **Die Seite hakt sich selbst ab.** Eine Zeile ist erledigt, wenn die Daten es belegen:
  ein Auszug, der den Vormonat abdeckt, oder ein Stand aus diesem Monat. Ein Haken zum
  Anklicken wäre eine Behauptung neben den Daten, die falsch werden kann, ohne aufzufallen.
- **Konten, Depots, Renten stehen je als eine Zeile** mit Haken, Wert und Notiz —
  geprüft und gepflegt an derselben Stelle. Einzelheiten unter
  [Stände](#monatsabschluss-konten-depots-renten).
- **Rot steht nur dort, wo etwas klemmt.** Eine Seite, auf der alles leuchtet, liest man
  nach dem zweiten Monat nicht mehr.
- Das **Backup** kommt zum Schluss: es soll den Stand sichern, den du gerade durchgesehen
  hast, und nicht den davor.

### /monatsabschluss — Konten, Depots, Renten

Je Position eine Zeile. Aktuell ist sie, wenn die Daten es belegen, sonst steht ein
rotes ○ davor und sie zählt oben bei „offen" mit.

| Gruppe | Woher | Aktuell, wenn … |
|---|---|---|
| Konten | aus dem Auszug, nicht änderbar | der Auszug den Vormonat abdeckt |
| Depots (auch Krypto) | getippt | der Stand aus dem laufenden Monat ist |
| Rentenversicherungen | getippt, nach der Standmitteilung | der Stand aus dem laufenden Jahr ist |
| Renten laut Mitteilung | getippt, nach Renteninformation und Standmitteilung | das Schreiben aus dem laufenden Jahr ist |

- **Der Saldo eines Kontos** ist der am letzten Monatsende, das alle Auszüge abdecken —
  derselbe Stichtag, an dem die Kontenvorschau beginnt. Wie weit der neueste Auszug
  reicht, steht als Beleg daneben.
- **Getippte Werte** werden genannt, nicht gemessen, und brauchen genau deshalb diesen
  Ort: ein Depotwert, der nur in einer Datei änderbar ist, ist bald still falsch, während
  der Zielfortschritt darauf weiter präzise aussieht.
- **Unverändert** legt denselben Wert mit heutigem Datum ab, ein Klick. Wer tippt, dessen
  Knopf heißt „Speichern"; ein neuer Wert zieht den Stand auf heute, solange du ihn nicht
  selbst setzt. Der Stand gehört zu jedem Wert: ein alter Bestand, der sagt, wann er alt
  ist, bleibt benutzbar.
- **Die Notiz** ist bei jeder Position änderbar, auch bei Konten: dass ein Teil des
  Saldos jemand anderem gehört, steht in keinem Auszug. Gespeichert wird in
  `balances_custom.yaml`; „eigen" zeigt, was dort von `balances.yaml` abweicht, „zurück"
  nimmt es wieder heraus.
- **Renten laut Mitteilung**: Betrag wie im Schreiben (Monatsrente oder Kapital) und
  **Bezug ab** — leer heißt ab dem Rentenbeginn aus der Einrichtung. Gespeichert in
  `renten_custom.yaml`, die Herleitung steht in `renten.yaml`.
- **Verbindlichkeiten** werden hier nicht gepflegt. Geld, das jemand anderem gehört,
  steht als Verpflichtung in der [Planung](#planung). Was noch in `balances.yaml` steht,
  zieht weiter ab; die Seite nennt es, bis es gestrichen ist.

### /konten

Je Konto der **Tiefpunkt innerhalb des Monats** („nach Kosten"): Kosten kommen am
Monatsanfang, das Gehalt am Ende, und der Monatsendstand verbirgt genau die Delle, um
die es geht. Die Kennzahl oben sagt, ob der Haushalt sich ohne das große Objekt trägt —
ohne Umbuchungen zwischen eigenen Konten, ohne Kaufraten, ohne Investments.

- **Zufluss** ist ein deklarierter Dauerauftrag, kein gemessener Wert: ein Budget ist eine
  Entscheidung, und Historie sieht keine Entscheidung.
- **Deckel** gibt es nur beim Betriebskonto: Guthaben dort wird nicht verzinst, alles
  darüber gehört aufs Tagesgeld. **Abräumen** ist deshalb ein Auftrag, kein Befund.
- Die **Reihenfolge ist die Regel**: erst wird jedes Konto aufgefüllt, das sonst unter
  seine Grenze fiele, dann erst geht der Rest aufs Tagesgeld. Andersherum würde gespart,
  während ein Girokonto im Dispo steht.
- **Bewusst pessimistisch**: ein Posten ohne Tagesangabe zählt als Kosten früh und als
  Einnahme spät, das Gehalt mit seiner Untergrenze. Eine Warnung ins Leere kostet nichts;
  eine übersehene Delle kostet Dispozinsen.
- Eine Zahlung gilt nur als gedeckt, wenn die Umbuchung dorthin datiert dasteht. Sonst
  sieht das Konto den Abfluss ohne den Zufluss.

### /einrichtung

Was gesetzt sein muss, in der Reihenfolge, in der es Sinn ergibt, und welche Module
an sind.
Solange etwas offen ist, weist der [Monatsabschluss](#monatsabschluss) darauf hin —
ohne Umleitung.

- **Erledigt entscheiden die Daten,** nicht ein Haken. Ein Zeiger auf einen
  Datenordner, ein eingetragenes Sicherungsziel, mindestens ein Konto, ein Geburtsdatum.
- **1 Wo die Daten liegen.** Zuerst, weil alles andere dorthin geschrieben wird. Der
  Eintrag **verschiebt nichts** und gilt ab dem nächsten Start; welcher Ordner gilt,
  sagt auch `finctl ort`.
- **2 Wohin gesichert wird.** Vor den Konten, weil ab dem ersten Import etwas da ist,
  das verloren gehen kann. Geprüft wird durch Schreiben.
- **3 Welche Konten es gibt.** Ohne Konto kein Import, ohne Import kein Hauptbuch.
  Über der Maske steht das Register aller Konten, siehe
  [Konten](#einrichtung-konten).
- **4 Wer plant.** Geburtsdatum, Krankenversicherung, Rentenalter. Daraus werden
  Rentenbeginn und, nur bei privater Versicherung, die Frist für die Rückkehr in die
  GKV.
- **5 Was die App zeigen soll.** Die Basis ist immer an, Module schaltet man dazu.
  **Aus heißt ausgeblendet**, nicht gelöscht.
- **6 Eigene Angaben** listet, was du durch deine eigenen Zahlen ersetzt, mit Seite,
  Datei und Stand.

### /einrichtung — Konten

Welche Konten es gibt — die Stammdaten, nicht die Prognose. Getrennt von
[Konten](#konten), weil es zwei Fragen sind: dort der Tiefpunkt im Monat, hier wie
ein Auszug gelesen wird und wo er liegt.

- **Einlesen** entscheidet alles Weitere. `parsed` liest den Auszug Buchung für
  Buchung und braucht ein **Parserprofil**; `summary` tut das nicht — so ein Konto
  wird nur über die Umbuchungen sichtbar, die es von einem geparsten Konto erreichen.
- **Warnen unter** ist die Schwelle, ab der die Delle auf [Konten](#konten) gemeldet
  wird. Für ein Girokonto mit Dispo ist das die wichtigste Zahl der Seite.
- **Auszüge in** ist ein Ordnername unter `data/statements/`: im Datenordner, also im
  Backup und beim Umzug dabei. Ein voller Pfad ist erlaubt, wird aber nicht
  mitgesichert und zeigt auf einem anderen Rechner ins Leere — die Seite schreibt
  beim Tippen mit, wohin die Dateien kommen.
- Angelegt wird in `accounts_custom.yaml`. Die von Hand geschriebene
  `accounts.yaml` bleibt unangetastet, damit ihre Begründungen nicht verloren gehen.
- **Auszug erkennen:** eine Datei aus dem Online-Banking wählen. Die App nennt
  Bank, Konto, Zeitraum und ob der Auszug aufgeht, und füllt damit das Formular für
  ein neues Konto — oder legt die Datei in ein Konto, das dieselbe Bank schon liest.
  Beschrieben sind die CSV-Exporte von ING, comdirect, DKB, Sparkasse, Volks- und
  Raiffeisenbanken, Commerzbank und N26, nach Beschreibung, noch nicht an echten
  Exporten geprüft.
- **Spalten zuordnen:** kennt die App einen CSV-Export nicht, schlägt sie vor, welche
  Spalte Datum, Betrag, Gegenpartei und Saldo ist. Nach der Vorschau wird daraus ein
  eigenes Profil in `config/csv_profile_custom.yaml`; ab dann wird die Bank erkannt.
- **Beispiel für eine fehlende Bank:** `finctl anonymisieren DATEI --name "Vorname Nachname"`
  schreibt eine Kopie ohne Namen, IBANs und Nummern; Datum und Beträge bleiben,
  `--faktor 3` verfremdet auch sie. Ein PDF wird zu Seitentext. Vor dem Weitergeben
  ansehen.

### /ziele

Zwei Balken je Ziel, zwei Fragen: **oben** der Bestand, der heute wirklich da ist, ohne
Renditeannahme; **unten** die Extrapolation auf den Stichtag, wenn Annahmen und
eingeschaltete Pläne eintreten. Nur der erste sagt nicht, ob du ankommst; nur der zweite
lässt ein kaum begonnenes Depot halb fertig aussehen.

- Der **Wendepunkt** oben ist eine andere Frage als die Zielbeträge: ab wann du nicht
  mehr arbeiten *musst*, statt ob du aufhören *kannst*. Gehalt und Einmalposten zählen
  dort nicht — eine große Erstattung ließe ein Jahr sonst wie Unabhängigkeit aussehen.
- Jedes Ziel misst gegen dasselbe liquide Vermögen, außer es trägt eine eigene **Basis**.
  Nichts angehakt heißt: alles, abzüglich Verbindlichkeiten und Notgroschen.
- Die **Rentenlücke** ist fest und gerechnet: laufende Ausgaben im ersten vollen
  Rentenjahr minus Renten und Mieten, entnommen bis zur Lebenserwartung. Gedreht wird
  auf [Annahmen](#annahmen), die Renten im Monatsabschluss.
- Ein Ziel **ohne Stichtag** bekommt keine Extrapolation: es gibt keinen Zeitpunkt, auf
  den man sie beziehen könnte.
- Die **Notiz** ist kein Schmuck. Eine siebenstellige Zahl ohne sie ist in einem Jahr
  nicht mehr nachvollziehbar, und dann korrigiert sie jemand, der die Herleitung nicht
  kennt.

### /planung

Alles, was noch nicht im Ledger steht, als **Klammern** mit beliebig vielen Positionen.

- **Verpflichtungen** sind eingegangen und gelten in jeder Projektion; **Pläne** sind
  Optionen und lassen sich schalten. Der Schalter bewegt Kontenvorschau, Jahresrechnung
  und Ziele gemeinsam.
- Eine Position ist ein **Betrag** oder ein **Wegfall**. Ein neues Auto kostet nicht
  seine Rate, sondern die Rate minus dem, was der alte Wagen kostet.
- **Teilzeit** ist ein Anteil am Gehalt ab einem Monat, nur in der Jahresrechnung.
- Beträge werden **ohne Vorzeichen** eingetragen, die Richtung ist eine Auswahl.
- Gemessen wird über die **zugeordneten Buchungen**, nicht über die Kategorie und nie
  eingetippt. Ein Haken genügt: dazu zählt die Reihe, also derselbe Empfänger in
  derselben Kategorie. Ohne Zuordnung zieht ein Wegfall nichts ab — die Seite sagt das
  oben an.
- Eine Zeile wirkt nur mit dem Teil, der **noch nicht im Ledger steht**. Was schon
  gebucht ist, steckt im Median und wird abgezogen; ein Wegfall schrumpft, sobald das
  Messfenster über ihn hinweggelaufen ist. Beide Rechnungen tun das gleich.
- Projiziert wird **ab dem Monat nach dem letzten Kontoauszug**, damit Eingetretenes
  nicht doppelt zählt.
- Sind zwei Pläne gleichzeitig an, summiert die Prognose sie. Varianten derselben
  Entscheidung gehören einzeln eingeschaltet.

### /annahmen

Oben wird gedreht, darunter steht, was gemessen oder belegt ist und deshalb kein
Eingabefeld hat. Jede Stellschraube nennt, was sie bewegt — nicht jede bewegt beide
Rechnungen.

- Gespeichert wird in `config/settings_custom.yaml`; „zurück" stellt
  `config/assumptions.yaml` wieder her.
- Was die Stellschrauben bewirken, zeigt die [Hochrechnung](#hochrechnung).
- Was ein Objekt abwirft, steht am Objekt ([/immobilie](#immobilie), Prognose).
- **Prognosebasis** (unten, aufklappbar) zeigt je Kategorie, worin sich die beiden
  Rechnungen unterscheiden: **Konten** fragt, ob ein Konto ins Minus rutscht — Median der
  letzten Monate, nur was oft genug vorkam, Gehalt mit Untergrenze; die
  **Jahresrechnung** fragt, was sich anhäuft — Zwölfmonatsschnitt, Gehalt mit Median.
  *unregelmäßig* heißt: in weniger als der Hälfte der Monate gebucht und trotzdem
  fortgeschrieben. Abgewählt gilt für **beide** Rechnungen.

### /hochrechnung

Wohin das Geld in den nächsten Jahren fließt. Gedreht wird auf [Annahmen](#annahmen),
hier steht die Wirkung.

- **Fluss** zeigt ein Jahr nach Grund: Planzeilen und Kredite mit Namen, Gemessenes je
  Block. Ein Klick führt zu den Buchungen dahinter, zur Klammer oder zum Kredit.
- **Gespart** ist der Saldo des Jahres. Er geht aufs Tagesgeld, bis dessen Ziel erreicht
  ist; was darüber liegt, wird ins Depot umgeschichtet („ins Depot" in der Tabelle).
  Rendite kommt obendrauf und steht nicht im Fluss: sie ist kein Geld, das hereinkommt.
- **Im Ruhestand** zeigt jede Rente mit Beginn und dem Betrag, der dann nominal ankommt;
  gepflegt wird sie im [Monatsabschluss](#monatsabschluss-konten-depots-renten).
  Gerechnet wird bis zur Lebenserwartung. Ab Rentenbeginn endet das Gehalt, die Renten
  kommen nach dem Abzug für Steuer und KV, und das Depot füllt das Tagesgeld auf.
  Oben steht, bis wann das Kapital reicht.
- **Jahr für Jahr**: laufende Posten kommen aus den letzten zwölf vollständigen Monaten
  und wachsen ab deren Mitte mit der Inflation. Ein Klick auf das Jahr zeigt jeden
  Posten mit Herleitung; die Posten ergeben genau die Summen der Zeile.
- Das **laufende Jahr** zählt nur seine Restmonate, auch bei der Rendite.

### /rueckblick

Vier Reiter auf dieselben Buchungen.

- **Vorjahr** vergleicht je Kategorie **dieselben Monate** beider Jahre; ein
  angebrochenes Jahr gegen ein volles wiese überall eine Ersparnis aus. Beträge sind
  **netto je Kategorie**: Erstattungen mindern die Position, in der sie anfallen.
- Eine Zeile ohne Buchungen im laufenden Jahr ist eingespart oder vergessen — bei einem
  Abo derselbe Betrag mit sehr verschiedener Bedeutung.
- **Fixkosten** zeigt, was in der Taxonomie als `fix` markiert ist, **Alle Kategorien**
  alles außer Umbuchungen. Das Flag ist eine eigene Achse: KFZ-Steuer ist Fixkosten
  *und* Mobilität. Sitzt es falsch, fällt das im Vergleich beider Reiter auf und wird
  auf [Kategorien](/kategorien) abgeräumt.
- **Letzte Buchung** zeigt, was aufgehört hat zu buchen — beendet oder vergessen.
- **Jahr und Konto** engen Summe *und* Monatsteiler ein; geteilt wird nur durch
  abgeschlossene Monate, der Spaltenkopf nennt ihre Zahl.
- **Fluss** zeigt ein Jahr als Diagramm: links, was netto hereinkam, rechts, wohin
  es ging; ein Klick zeigt die Buchungen. Ohne Kapital bleibt ein **Überschuss**;
  *inkl. Kapital* zeigt, was daraus wurde: ins Depot, in einen Objektkauf, auf den Konten.

### /transactions

Jede Buchung, filterbar — und die Stelle, an der eine falsche Zuordnung korrigiert wird.
Drei Reiter, derselbe Filter darunter:

- **Offen** sind die Buchungen ohne Kategorie, größte zuerst. Kategorie wählen und Enter
  drücken; die Zeile wird blass, die nächste ist dran.
- **Aufzuteilen** sind Buchungen, die eine Regel bewusst offen gelassen hat, weil eine
  Zahlung mehrere Dinge abdeckt — meist Lastschriften eines Zahlungsdienstes.
- Gespeicherte Zeilen gelten als **Handentscheidung**: ein erneutes Zuordnen aller
  Buchungen überschreibt sie nie.
- **Offen** zeigt nur, was *keine* Kategorie hat. Eine Regel, die zu breit greift, ist
  dort unsichtbar: dafür filtert man unter **Alle** nach *Quelle = Regel*.
- Eine Korrektur ist eine Ausnahme; dieselbe Regel wiederholt korrigiert ist eine falsche
  Regel, und sie je Buchung zu korrigieren behebt die nächste nicht.
- Nichts wird automatisch umgeschrieben: eine Regel, die sich aus einer Korrektur selbst
  umschreibt, hängt still eine Regel um, die vierzigmal richtig lag.

### /split

Eine Buchung in ihre Teile zerlegen, wenn eine Zahlung mehrere Dinge abdeckt. Die Summe
der Teile muss den Betrag der Buchung ergeben; jeder Teil trägt Kategorie,
Steuerposition, Objekt und Notiz.

### /kategorien

Der Kategorienbaum: anlegen, umbenennen, stilllegen — und das `fix`-Flag setzen, das
entscheidet, was unter Fixkosten erscheint.

**Stilllegen tut zwei Dinge, die zusammen gehören**: die Buchungen wandern zur
Ersatzkategorie, und jede Regel, die die alte Kennung nennt, muss umgehängt werden. Nur
das Erste zu tun heißt, dass der nächste Lauf alles zurückschiebt. Die Regeln stehen
deshalb je Zeile dabei.

### /regeln

Welche Regel welche Buchung zuordnet.

- Die **kleinere Priorität gewinnt**. Eigene Entscheidungen auf Transaktionen
  stehen über jeder Regel.
- Die **Vorschau** zeigt vor dem Speichern, welche Buchungen eine Regel fängt und welche
  dadurch die Kategorie wechseln — eine zu breite Regel ordnet sonst still das halbe
  Ledger um.
- Gespeichert wird nur, was von `config/rules.yaml` abweicht; die Begründungen dort
  bleiben stehen.

### /vertraege

Verträge mit festem Termin, in zwei Reitern: **Abos** und **Versicherungen**. Die
Prognose erwartet sie am Tag, an dem sie abgehen, statt sie als Zwölftel zu mitteln.

- Einem Vertrag werden **seine Buchungen zugeordnet**. Er ersetzt sie dann in der
  Prognose, statt neben ihnen zu stehen, und die jüngste zeigt, ob der Betrag noch
  stimmt.
- **Ein Haken genügt.** Dazu zählt die Reihe: derselbe Empfänger, dieselbe Kategorie,
  ein Betrag nahe dem Vertragsbetrag. Eine Beitragserhöhung bleibt drin, eine
  Erstattung und ein Einkauf beim selben Empfänger nicht.
- Was in der Kategorie **daneben** liegt, bleibt gemessen. Die Prognosebasis auf /annahmen sagt je
  Kategorie, welcher Fall vorliegt.
- **Gekündigt zum** genügt ebenso: beide Rechnungen hören am Stichtag auf. Eine
  Wegfall-Zeile brauchst du dafür nicht — die bleibt für Dinge ohne Vertrag. Hast du
  beides, zählt es einmal, und versprochen wird nie mehr als die Vertragsrate.
- Unter **Geteilt** steht, wer mitzahlt. Rückzahlungen werden aus den Eingängen
  abgeglichen, nicht abgehakt: ein Haken wäre eine Behauptung neben den Daten.

### /immobilien

Cashflow und Gewinn sind verschiedene Fragen. Ein Objekt kann jeden Monat Geld kosten und
trotzdem rentabel sein — der Normalfall bei einer finanzierten Vermietung.

- **Tilgung** kostet Geld und baut Vermögen auf; **AfA** mindert den Gewinn und kostet
  nichts.
- **Kapital investiert** bleibt aus beiden heraus: Kaufraten sind Investition, keine
  laufende Kosten. Auf ein Jahr hochgerechnet ließen sie ein Objekt im Bau
  katastrophal aussehen.

### /immobilie

Ein Objekt im Einzelnen: Anschaffung, Abschreibung, geplanter Verkauf und alle Buchungen
dazu.

- Der **Grundstücksanteil** gehört gewusst, nicht geschätzt: Grund und Boden werden nicht
  abgeschrieben, der Anteil ändert die AfA-Grundlage direkt. §7 EStG sind in der Regel
  2 % (vor 1925 gebaut: 2,5 %; Neubauten seit 2023 auch degressiv).
- Ein **Verkauf ohne erwarteten Preis** wird nicht gerechnet. Den Kredit enden zu lassen,
  ohne den Erlös zu kennen, hieße eine Rate zu streichen und nichts dafür zu zahlen.
- Steht der Verkauf in einer **Planklammer**, gewinnt die Klammer; Termin und Preis hier
  sind dann nur ein Vorschlag.
- **Prognose**: Miete und Kosten je Monat, dazu wann die Vermietung beginnt und endet.
  Für ein Objekt, dessen Buchungen den Ertrag noch nicht tragen — ein Monat Miete, aufs
  Jahr gemittelt, wäre ein Zwölftel. Mit Prognose rechnet die Hochrechnung das Objekt
  aus ihr statt aus der Messung; leer bleibt es gemessen. Die Vorgabe steht unter
  `prognose:` in `config/properties.yaml`; ein Szenario ändert sie je Objekt unter
  `immobilien.objekte`.
- **Kategorie und Steuer** sind in der Buchungstabelle änderbar: die Anlage-V-Positionen
  sind das, was der Steuerexport liest.

### /kredite

Die Tilgungspläne aus `config/loans.yaml`, je Kredit mit aktueller Kondition und
Anschlussfinanzierung nebeneinander.

- **Neuer Ratenkredit** legt einen Kredit mit einem Segment an: Restschuld, Zins, Rate,
  Laufzeit. Ein Immobiliendarlehen entsteht hier nicht — dessen Zinsbindung und Anschluss
  stehen in `loans.yaml`.
- **Entfernen** blendet einen Kredit aus jeder Prognose aus. Was aus `loans.yaml` kommt,
  wird nur ausgeblendet, damit die Begründung dort bleibt.
- **laut Plan** ist der Tilgungsplan über das ganze Jahr, auch die Monate, die noch
  kommen; **gebucht** kommt aus dem Ledger. Weichen sie ab, ist entweder der Plan falsch
  oder die Buchung — zum Beispiel eine Rate, die vollständig als Tilgung gebucht wurde
  und deren Zinsanteil dadurch in keiner Anlage V auftaucht.
- Die **Anschlussfinanzierung** ist Vorschau, solange kein Angebot vorliegt: was passiert,
  wenn zu den heutigen Konditionen weitergeführt wird. Das ist der Maßstab für alles, was
  stattdessen ausgehandelt wird.
- Die **Rate ist ein Ergebnis**, keine dritte Eingabe: sie folgt aus Zins und anfänglicher
  Tilgung, das Laufzeitende aus beiden.
- **Echte Verträge sind nicht editierbar**, Vorlagen schon. Ein Vertrag ist aus Unterlagen
  rekonstruiert; ihn überschreibbar zu machen hieße, eine Tatsache durch eine Eingabe zu
  ersetzen. Vorlagen speichern nach `config/loans_custom.yaml`.

### /steuer

Die einzige Ansicht, die nach der **Steuerachse** sortiert ist statt nach dem
Kategorienbaum. Anlage V geht je Objekt, Anlage N je Werbungskosten-Position, und keines
von beidem deckt sich mit „wohin ist das Geld geflossen".

- Jede Zeile führt auf die Buchungen dahinter: eine Zahl, die man nicht zurückverfolgen
  kann, kann man dem Finanzamt nicht erklären.
- Eine Zeile mit *⚠ ohne Objekt* ist ein Fehler — sie lässt sich keinem Formular zuordnen.
- **AfA steht bewusst nicht hier**: sie kostet kein Geld und taucht in keinem Auszug auf,
  sondern kommt aus den Objektstammdaten.
- Die Seite ersetzt keine Erklärung; sie liefert die Zahlen, die dort eingetragen werden.


### /energie

Je Zähler und je Vorrat ein Reiter. Zählerstand oder Füllstand eintragen, der Rest
ergibt sich.

- **Zähler** (Strom, Gas, Fernwärme, Wasser): Verbrauch seit Beginn des Zeitraums,
  aufs Ende fortgeschrieben, bepreist und gegen die Abschläge gestellt. Positiv ist
  Guthaben, negativ Nachzahlung; der Rechenweg zeigt jeden Posten.
- **Gas** zählt m³ und rechnet in kWh ab: m³ × Brennwert × Zustandszahl, beide von
  der Rechnung.
- **Verteilung:** gleichmäßig für Strom und Wasser, nach Gradtagen für Heizung. Wer
  im Herbst abliest, hat den Winter noch vor sich. Die **Grundlast** (Warmwasser)
  verteilt sich gleichmäßig.
- **Ein Zeitraum hat kein festes Jahr**, und abgerechnet ist erst, was abgehakt ist.
  Ein neues Abrechnungsjahr geht, sobald das Ende vorbei ist.
- **Vorrat** (Heizöl, Pellets, Flüssiggas): verbraucht ist, was drin war, plus
  Lieferungen, minus was jetzt drin ist. Daraus der Jahresverbrauch, wie lange es bis
  zum Mindestbestand reicht, und was im Monat zurückzulegen ist, zum Preis der
  letzten Lieferung.
- Strom steht in `config/strom.yaml`, alles andere in `config/energie.yaml`.

### /projekte

Ein Projekt sammelt Ausgaben, die zusammengehören: eine Reise, ein Umbau, ein
Geschenk. Kategorie und Steuer bleiben davon unberührt; das Projekt ist eine eigene
Achse daneben.

- **Mit Personen** wird geteilt, **ohne Personen** sammelt es nur die Kosten.
- Eine zugeordnete Ausgabe ist gleich auf alle Personen und dich geteilt, bis du für
  diese eine Buchung etwas anderes einstellst.
- Aufgeklappt zeigt ein Projekt seine Buchungen mit den Anteilen, die Kosten nach
  Kategorie und nach Monat.
- Zugeordnet und geteilt wird unter „Buchungen".

### /salden

Je Person und Projekt: ihr Anteil an den Ausgaben, und was davon offen ist.

- **Rückzahlungen werden nicht verfolgt.** Was andere für dich ausgelegt haben, steht
  in keinem deiner Auszüge; ein Saldo daraus wäre nur halb richtig.
- Stattdessen gibt es je Person und Projekt den Haken **„ausgeglichen"**. Er merkt sich
  den Anteil beim Abhaken. Kommt danach eine Ausgabe dazu, ist genau die Differenz
  wieder offen.
- Eine Rückzahlung gehört im Ledger in dieselbe Kategorie wie die Ausgabe, positiv. So
  zeigt die Kategorie am Ende deinen echten Anteil.

### /geteilt/buchungen

Die schlanke Buchungstabelle nur zum Zuordnen. Die Kategorie ist Anzeige und Filter,
nicht änderbar; dafür gibt es /transactions.

- **Eine Zeile je Aufteilungsteil.** Bei einem aufgeteilten Einkauf lässt sich so nur
  der Teil zuordnen, der zum Projekt gehört.
- **Mehrere auf einmal:** Zeilen markieren, Projekt wählen, „Markierte zuordnen".
- **Teilung** je Buchung: gleich, feste Beträge, Prozent, Gewichte oder nur ich. Die
  Vorschau zeigt die Beträge, bevor gespeichert wird.
- Alles steht in `config/geteilt_custom.yaml`, geschlüsselt über die Buchung selbst,
  und übersteht damit einen Neuaufbau der Datenbank.
