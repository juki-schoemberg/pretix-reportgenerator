# Performance: 100.000 Positionen, gemessen

Erstellt vom `test-engineer` in Welle 3, fortgeschrieben am **2026-08-03** nach
der Verifikation von T-001 bis T-003. Quelle aller Zahlen:
`tests/test_performance.py`, ausgeführt mit

```bash
pytest tests/test_performance.py -m performance -q -s
```

Die Tests sind mit `performance` markiert und laufen im Normalfall **nicht** mit
(`pytest -m "not performance"`, siehe `CLAUDE.md`). Die Tabelle unten wird vom
Testmodul selbst am Ende des Laufs gedruckt; sie ist hier unverändert übernommen.

**Was sich am 2026-08-03 geändert hat:** zwei neue Messungen (XLSX in voller
Größe, Abschnitt 3.6b; Kosten der Spaltenformatierung, Abschnitt 3.8), die
korrigierte Query-Formel aus T-003 (Abschnitt 3.3) und ein neuer Befund, der aus
der Messung selbst kommt (**T-005**, Abschnitt 3.8). Die alten Zahlen stehen
unverändert daneben; wo zwei Läufe verglichen werden, ist das ausgewiesen.

---

## 1. Messumgebung

| | |
|---|---|
| CPU | Intel Core i7-1255U (12 logische Kerne) |
| OS | Windows 10 22H2 (10.0.19045) |
| Python | 3.12.6 |
| Django | 5.2.16 |
| pretix | 2026.6.0 |
| Datenbank | **SQLite 3.45.3** (`pretix.testutils.settings`) |
| Testdaten | `tests.factories.build_bulk`, Seed `20260801` |

**Zur SQLite-Einschränkung.** SQLite ist ein guter Stellvertreter für
*Query-Anzahlen* und für *Speicherverhalten* — beides hängt am ORM, nicht am
Backend. Für absolute Laufzeiten ist es ein schlechter: es gibt keinen
Netzwerk-Roundtrip und keinen gemeinsamen Buffer-Cache. Die 151 Queries aus
Abschnitt 3 sind auf SQLite kaum messbar und auf einem PostgreSQL über Netzwerk
151 echte Roundtrips.

`registry-dev`, `query-dev`, `exporter-dev` und `frontend-dev` haben alle vier
eine PostgreSQL-Gegenprobe angefordert. Sie ist **nicht erfolgt**: in dieser
Umgebung gibt es kein PostgreSQL (`pretix/src/pretix.cfg`: `backend=sqlite3`).
Der Punkt bleibt offen und steht im Statusbericht.

## 2. Datensatz

| | groß | klein |
|---|---|---|
| Bestellungen | 50.000 | 500 |
| Positionen | 100.000 | 1.000 |
| Antworten | 50.000 | 500 |
| Zahlungen | 50.000 | 500 |
| Testmodus-Bestellungen | jede 97. | jede 97. |
| stornierte Positionen | jede 23. | jede 23. |

Beide Events gehören demselben Organizer und haben dieselbe Struktur. Das kleine
existiert nur zu einem Zweck: „die Query-Anzahl hängt nicht an der Zeilenzahl"
wird dadurch von einer Beobachtung zu einer **Behauptung mit Gegenprobe**.

Zeilenzahlen, die daraus folgen (jeweils Voreinstellung: kein Testmodus, keine
stornierten Positionen):

- Basis `orderposition`: **94.666** Zeilen (groß), 945 (klein)
- Basis `order`: **49.484** Zeilen (groß), 494 (klein)

Aufbau der 100.000 Positionen: **22,7 s** über `bulk_create`.

## 3. Messwerte

Lauf vom **2026-08-03**, nach den Fixes zu T-001 und T-002:

```
measurement                                        rows  queries   seconds  note
build fixture                                    100000        -    21.862  50000 orders, 50000 answers
narrow (3 columns, base orderposition)            94666        1     4.382  21604 rows/s; small event: 945 rows in 0.045s (x100)
wide (22 columns, base orderposition)             94666        1    11.277  8395 rows/s; small event: 945 rows in 0.112s (x100)
wide (16 columns, base order, 2 join columns)     49484      151    14.054  1 + 3 x ceil(rows/1000)
same report without the two join columns          49484        1     2.116  constant at any size
count() on the wide order report                  49484        1     0.003
preview (20 of 100.000 rows)                         20        -     0.056
full drain under tracemalloc                      94666        1    45.197  peak 6.5 MiB; time inflated ~4x by the tracer
CSV through the exporter                          94666        -     4.449  1.5 MiB of CSV
XLSX through the exporter (22 columns)            94666        -    69.718  8.4 MiB of XLSX; 1358 rows/s
CSV, 22 columns, no column format                 94666        -    11.567  _cell_formats() -> None, rows pass through
CSV, 22 columns, three column formats             94666        -    50.443  x4.36 of the unformatted run
filtered + sorted position report                 59255        1     2.778  gte 25.00, desc
```

Der Lauf aus Welle 3, zum Vergleich (dieselbe Maschine, dieselben Daten):

```
narrow (3 columns, base orderposition)            94666        1     4.323
wide (22 columns, base orderposition)             94666        1    11.099
wide (16 columns, base order, 2 join columns)     49484      151    14.226
same report without the two join columns          49484        1     1.928
full drain under tracemalloc                      94666        1    44.105  peak 6.4 MiB
CSV through the exporter                          94666        -     4.542  1.5 MiB of CSV
```

**Die Fixes zu T-001 und T-002 kosten nichts, solange kein Format gesetzt ist.**
Jede Zeile, die es in beiden Läufen gibt, liegt innerhalb der Messstreuung
(±2 %), und die Query-Anzahlen sind identisch. `MoneyField.from_db_value`
quantisiert je Wert und ist in den 11,3 s der 22 Spalten nicht sichtbar; der
Renderer aus T-001 läuft gar nicht erst an, weil `_cell_formats()` bei einem
Report ohne Stile `None` liefert (Abschnitt 3.8). Was ein *gesetztes* Format
kostet, ist die vorletzte Zeile — und ein eigener Befund.

### 3.1 Schmaler Report

Drei Spalten, alle direkte Felder der Zeile
(`order.code`, `position.positionid`, `position.price`).

| | groß | klein | Faktor |
|---|---|---|---|
| Zeilen | 94.666 | 945 | ×100 |
| Queries | **1** | **1** | ×1 |
| Laufzeit | 4,32 s | 0,045 s | ×96 |

Durchsatz **≈ 21.900 Zeilen/s**. Laufzeit skaliert linear mit der Zeilenzahl,
Query-Anzahl gar nicht.

### 3.2 Breiter Report, Basis `orderposition`

22 Spalten, absichtlich über **alle vier** Strategien aus `query/columns.py`:
direkte Pfade, `select_related` über vier Relationen, vier
Registry-Annotationen mit korrelierten Subqueries (`payment.sum_confirmed`,
`refund.sum_done`, `checkin.count`, `order.pending_sum`), ein `Case` über zwei
andere Annotationen (`computed.payment_state`), eine Antwort-Subquery und zwei
Python-Getter (`position.code`, `position.net_price`).

| | groß | klein | Faktor |
|---|---|---|---|
| Zeilen | 94.666 | 945 | ×100 |
| Queries | **1** | **1** | ×1 |
| Laufzeit | 11,10 s | 0,109 s | ×102 |

Durchsatz **≈ 8.500 Zeilen/s**, also rund 2,6× teurer je Zeile als der schmale
Report bei 7× so vielen Spalten. Der Aufpreis steckt in einer einzigen Query;
**keine** der vier Subquery-Annotationen erzeugt eine zusätzliche.

### 3.3 Breiter Report, Basis `order` — hier wächst die Query-Anzahl doch

16 Spalten, davon zehn Aggregate über drei verschiedene Eins-zu-viele-Relationen
(Positionen, Antworten, Zahlungen) und **zwei `join`-Spalten**.

| | groß (49.484 Zeilen) | klein (494 Zeilen) |
|---|---|---|
| Queries **mit** den zwei `join`-Spalten | **151** | **4** |
| Queries **ohne** sie | **1** | **1** |
| Laufzeit mit | 14,05 s | — |
| Laufzeit ohne | 2,12 s | — |

Die Formel ist exakt

```
Queries = 1 + Ebenen × ceil(Zeilen / DEFAULT_CHUNK_SIZE)
        = 1 + 3 × ceil(49484 / 1000)
        = 151
```

und wird im Test als Gleichung geprüft, nicht als Größenordnung.

**Ebenen ≠ `join`-Spalten** (Nachtrag 2026-08-03). Seit dem S-005-Fix in
`query/relations.py::join_leaf_to_attr` leitet sich der `to_attr` des Blattes
aus dem ab, was das Queryset *tut* — Relation, Bedingung, Storno-Regel, inneres
`select_related` — statt daraus, welche Spalte zuerst gefragt hat. Damit teilen
sich `join`-Spalten, die dieselben Zeilen holen, **eine** Ebene: zwanzig
identische kosten so viel wie eine (gemessen von `query-dev`: 2 statt 20). Die
beiden hier sind wirklich verschieden (`item.name` braucht ein
`select_related("item")` auf den geholten Positionen, `answer.bulk-question`
trägt eine Fragenbedingung), deshalb steht in der Formel weiterhin 3 und die
151 sind unverändert.

**Das ist kein N+1** — die Kosten je Zeile *sinken*, wenn der Report wächst:
4/494 = 0,0081 Queries je Zeile beim kleinen Event, 151/49.484 = 0,0031 beim
großen. Der Test prüft beides, `big_queries/big_rows < small_queries/small_rows`
und `big_queries < big_rows/100`. Es ist die bewusste Bauweise: eine `join`-Spalte
ist `prefetch_related` plus `str.join` in Python, weil Django 5.2 keine
backend-unabhängige String-Aggregation hat (`StringAgg` ist PostgreSQL-only), und
`QuerySet.iterator(chunk_size=1000)` führt Prefetches **je Chunk** aus. Genau das
kauft die konstante Speichergrenze aus Abschnitt 3.5.

Es war aber auch **nicht das, was `query/columns.py` zusagte** („Kostet genau eine
Query pro Prefetch-Ebene, unabhängig von der Zeilenzahl"). Die Zusage stimmte für
einen Chunk. Als T-003 in `handoff/blockers.md` festgehalten und dort am
2026-08-03 geschlossen: `query-dev` hat den Docstring auf die Formel oben
korrigiert. Der Test hatte nie ein `xfail` — er prüft das *tatsächliche*
Verhalten als Gleichung und wird rot, wenn sich das Verhalten ändert, nicht wenn
sich die Dokumentation ändert.

**Praktische Folge:** wer einen sechsstelligen Report mit `join`-Spalten
terminiert, sollte wissen, dass daraus dreistellig viele Roundtrips werden. Ohne
`join`-Spalten ist derselbe Report eine Query — und `join` ist die einzige
Spaltenart mit diesem Verhalten.

### 3.4 Vorschau und `count()`

| Messung | Zeilen | Queries | Laufzeit |
|---|---|---|---|
| Vorschau (20 von 100.000) | 20 | — | **0,057 s** |
| `count()` auf dem breiten Order-Report | 49.484 | 1 | **0,003 s** |

Beides sind Aussagen über den Editor, nicht über den Export:

- Die Vorschau schneidet in **SQL** (`LIMIT` steht im Statement, im Test geprüft),
  nicht in Python. Ein Python-`break` hätte hier die vollen 11 s gebraucht,
  bevor 20 Zeilen sichtbar werden.
- `count()` benutzt das separate, absichtlich billige Zähl-Queryset ohne die
  Spalten-Annotationen. `.count()` auf dem Anzeige-Queryset hätte jede
  Subquery für jede der 49.484 Zeilen gerechnet und das Ergebnis weggeworfen.

Der Editor bleibt damit auf einem sechsstelligen Event bedienbar — das war die
offene Frage aus `SPEC.md` Abschnitt 4.

### 3.5 Speicher

Voller Durchlauf des breiten Positions-Reports (94.666 Zeilen, 22 Spalten) unter
`tracemalloc`:

| | |
|---|---|
| Spitzenverbrauch | **6,4 MiB** |
| Zeilen | 94.666 |
| Zeilen je MiB | ≈ 14.800 |

Die Streaming-Kette ist durchgehend faul: `QuerySet.iterator(chunk_size=1000)` →
Generator → `csv.writer`. Der Spitzenwert hängt an der **Chunk-Größe**, nicht an
der Zeilenzahl. Der Test setzt die Schranke bei 64 MiB — großzügig mit Absicht,
er ist ein Wächter gegen „jemand hat ein `list()` eingebaut", kein Budget.

Das war die Messung, um die `exporter-dev` in Welle 2 gebeten hat
(`handoff/status/exporter-dev.md`, „Nächster Schritt" 4). Ergebnis: die Kette ist
nicht nur konstruiert faul, sondern gemessen faul.

Die 44 s in der Tabelle sind **nicht** die echte Laufzeit — `tracemalloc`
verlangsamt den Durchlauf um etwa das Vierfache. Der Vergleichswert ohne Tracer
steht in Abschnitt 3.2 (11,1 s).

### 3.6 Export als CSV

Voller Weg durch `CustomReportExporter.iterate_list` (Kopfzeile, Fortschrittszahl,
Kompilierung je Event, Log-Eintrag) mit demselben `csv.writer`, den
`ListExporter` benutzt:

| | |
|---|---|
| Zeilen | 94.666 + 1 Kopfzeile |
| Laufzeit | **4,54 s** |
| Dateigröße | 1,5 MiB |
| Durchsatz | ≈ 20.800 Zeilen/s |

Die 20-MB-Grenze der pretix-Exportmail wird von einem schmalen Report mit
100.000 Zeilen **nicht** erreicht (1,5 MiB). Ein breiter Report mit 22 Spalten
liegt hochgerechnet bei etwa 15–20 MiB und damit an der Grenze — dafür gibt es
das `row_limit`-Feld des Exporters.

### 3.6b Export als XLSX (neu, 2026-08-03)

In Welle 3 stand hier „nicht messbar". Das war zu kurz gegriffen: nicht die
Plattform kann kein XLSX, sondern `ListExporter._render_xlsx` **ohne**
`output_file` — es speichert in eine `NamedTemporaryFile` und öffnet sie danach
ein zweites Mal über ihren Namen, was unter Windows `PermissionError` gibt. Mit
`output_file`, also so wie pretix' Exportdienst jede Datei schreibt, läuft der
Pfad hier genauso.

| | |
|---|---|
| Zeilen | 94.666, 22 Spalten |
| Laufzeit | **69,7 s** |
| Dateigröße | 8,4 MiB |
| Durchsatz | ≈ 1.360 Zeilen/s |

**Rund 15-mal langsamer als dieselbe Ausgabe als CSV** (4,4 s). Zwei Ursachen,
beide außerhalb dieses Plugins und beide unvermeidbar an dieser Stelle:
`openpyxl` baut je Zelle ein XML-Element (`SafeWorkbook(write_only=True)` hält
dabei immerhin den Speicher flach), und je Datumszelle läuft
`as_spreadsheet_value()` — das sind rund 17 der 70 Sekunden, siehe 3.8 und T-005.

Praktische Folge für die Terminierung: ein sechsstelliger Report als XLSX liegt
über der Minute und damit im Bereich, in dem ein Celery-`soft_time_limit`
interessant wird. 8,4 MiB bleiben unter der 20-MB-Mailgrenze, ein breiterer
Report nicht mehr sicher.

### 3.7 Filtern und Sortieren

| | |
|---|---|
| Filter | `position.price >= 25.00` |
| Sortierung | Preis absteigend, dann Bestellcode aufsteigend |
| Treffer | 59.255 von 94.666 |
| Queries | **1** |
| Laufzeit | 2,83 s |

Sortiert wird in der Datenbank. Der Test prüft zusätzlich, dass das Ergebnis
tatsächlich sortiert ist — bei 59.255 Zeilen mit sehr vielen Gleichständen, also
genau dort, wo ein fehlender `pk`-Tiebreaker über `LIMIT`/`OFFSET`-Seiten Zeilen
doppelt und Zeilen gar nicht liefern würde.

### 3.8 Was die Spaltenformatierung kostet (neu, 2026-08-03) — Befund T-005

Der T-001-Fix hat einen Renderer je Zelle zwischen Compiler und Datei gesetzt.
Derselbe Report, dieselben 94.666 Zeilen, dieselben 22 Spalten, einmal ohne und
einmal mit drei gesetzten Stilen (`date_only` auf `order.datetime`, `currency`
auf `order.total`, `localized` auf `position.price`):

| | Laufzeit | Faktor |
|---|---|---|
| kein Format gesetzt | **11,6 s** | — |
| drei Spalten mit Format | **50,4 s** | **×4,36** |

Ohne gesetztes Format ist es exakt der alte Weg: `_cell_formats()` liefert `None`
und die Zeilen gehen unverändert durch. Das ist im Test als Eigenschaft geprüft,
nicht als Laufzeit — eine Uhr kann „gar keine Arbeit" nicht von „ein bisschen
Arbeit" unterscheiden.

**Der Faktor 4,36 steckt fast vollständig in einer Zeile.** `_format_temporal()`
löst `event.timezone` je Zelle auf, und `Event.timezone` ist kein Attribut,
sondern `pytz_deprecation_shim.timezone(self.settings.timezone)`
(`pretix/base/models/event.py:233-235`) — ein hierarkey-Settings-Lookup über
Event → Organizer → globale Defaults. Einzeln gemessen:

| Aufruf | Zeit |
|---|---|
| `event.timezone` (Settings unangetastet) | **178 µs** |
| `event.timezone` (Settings in derselben Transaktion geschrieben) | **345 µs** |
| `pytz_deprecation_shim.timezone("Europe/Berlin")` allein | 0,08 µs |
| `timezone.localtime(wert, tz)` mit hochgezogener Zone | **1,8 µs** |
| `timezone.localtime(wert, event.timezone)` | 344 µs |
| `format_cell_value` einer Geldzelle (`currency`) | 0,34 µs |
| `format_cell_value` einer Datumszelle (`date_only`) | 401 µs |

Die Umrechnung selbst kostet 1,8 µs, das Beschaffen der Zeitzone das
Zweihundertfache. Eine Geldzelle ist von alledem nicht betroffen, weil
`event.currency` eine Modellspalte ist (0,02 µs) — es geht ausschließlich um
Datumsspalten. `as_spreadsheet_value()` macht dasselbe auf dem XLSX-Weg und
erklärt etwa 17 der 70 Sekunden aus 3.6b.

Gezählt statt gestoppt, weil eine Zählung deterministisch ist
(`tests/test_integration.py::test_finding_the_export_resolves_the_event_timezone_once_not_once_per_row`):

| Report | 1 Zeile | 6 Zeilen |
|---|---|---|
| Datumsspalte ohne Stil | 22 Auflösungen | 22 Auflösungen |
| dieselbe Spalte mit `date_only` | 23 | 28 |

Die Grundlast von 22 ist konstant und gehört nicht uns (Compiler, Exporter,
`init_event_exporter`). Der Aufschlag ist **genau eine Auflösung je formatierter
Datumszelle**.

**Kein Korrektheitsproblem und kein Blocker** — jeder Wert ist richtig, und ein
terminierter Export, der 50 statt 12 Sekunden braucht, kommt trotzdem an. Aber es
ist ein paar Zeilen wert: die Zone einmal je Event auflösen, dort wo
`_cell_formats()` ohnehin schon einmal je Event gebaut wird, und durchreichen.
Als **T-005** in `handoff/blockers.md`, Zuständigkeit `exporter-dev`.

Einschränkung, die dazugehört: der Absolutwert hängt am Cache-Backend. pretix'
Testeinstellungen benutzen `DummyCache` (`pretix/testutils/settings.py:74-78`),
produktiv steht dort Redis. Ein Redis-Roundtrip ist nicht offensichtlich billiger
als der DB-Treffer hier. Was **nicht** von der Konfiguration abhängt, ist die
Form: ein Lookup je Zelle statt einem je Export.

## 4. Beleg: die Query-Anzahl wächst nicht mit der Zeilenzahl

Zusammengefasst, weil das die eigentliche Forderung war:

| Report | 1.000 Positionen | 100.000 Positionen | wächst mit Zeilen? |
|---|---|---|---|
| schmal, 3 Spalten | 1 | 1 | **nein** |
| breit, 22 Spalten, Basis `orderposition` | 1 | 1 | **nein** |
| breit, 16 Spalten, Basis `order`, ohne `join` | 1 | 1 | **nein** |
| breit, 16 Spalten, Basis `order`, mit 2 `join` | 4 | 151 | **ja, je Chunk** |

Die ersten drei Zeilen sind mit `django_assert_num_queries(1)` festgenagelt; die
vierte mit der exakten Formel. Die Gegenprobe „derselbe Report ohne die zwei
`join`-Spalten" steht im selben Test, damit der Unterschied dem `join`
zugeschrieben wird und nicht der Breite des Reports.

## 5. Was nicht gemessen wurde

- **PostgreSQL.** Kein Backend verfügbar, siehe Abschnitt 1. Betroffen wären vor
  allem: die 151 Roundtrips aus 3.3 (dort teurer), die `Coalesce`/`Subquery`-
  Ausgabetypen, `nulls_last` und der `Cast(answer AS date)` aus
  `computed.age.*`. Für **T-002** ist die Frage inzwischen kleiner geworden, aber
  nicht weg: der Fix hängt an `MoneyField.from_db_value` und wirkt damit
  backend-unabhängig auf der Python-Seite — genau deshalb wäre eine Gegenprobe
  auf PostgreSQL billig und aussagekräftig. **T-004** (Abschnitt „Befunde") ist
  weiterhin voll backend-abhängig.
- **XLSX ohne `output_file`.** Der Bytepfad (`render()` ohne Dateihandle) bleibt
  unter Windows unerreichbar, siehe 3.6b. Der Dateipfad ist seit 2026-08-03
  gemessen; offen ist nur noch die Variante, die pretix für den Download im
  Browser benutzt.
- **Nebenläufigkeit.** Alle Messungen sind Einzelläufe ohne Last daneben.
- **Der Multi-Event-Export über viele Events.** Fachlich in
  `tests/test_integration.py` abgedeckt, aber nicht in relevanter Größe gemessen;
  er kompiliert je Event einmal, die Kosten sind additiv.

## 6. Reproduzieren

```bash
pytest tests/test_performance.py -m performance -q -s
```

Dauer im Ganzen etwa **270 s**, davon 22 s Datenaufbau (Welle 3: 110 s — der
Zuwachs sind die zwei neuen Messungen aus 3.6b und 3.8, die zusammen gut zwei
Minuten brauchen). Die Testdaten sind über `tests.factories.SEED` festgelegt;
zwei Läufe messen dieselbe Arbeit. Der Datensatz lebt in einer Transaktion, die
am Modulende zurückgerollt wird, also bleibt nichts in der Testdatenbank zurück.

Größe verstellen: `BIG_ORDERS` / `POSITIONS_PER_ORDER` oben in
`tests/test_performance.py`.

## 7. Befunde aus diesem Dokument

| Nr. | Stand | Kurz |
|---|---|---|
| T-001 | **behoben** 2026-08-03 | `ColumnFormat` wirkte nur in der Vorschau |
| T-002 | **behoben** 2026-08-03 | aggregierte Geldspalten ohne Nachkommastellen |
| T-003 | **behoben** 2026-08-03 | Query-Zusage für `join` galt nur je Chunk (3.3) |
| T-004 | offen | dasselbe wie T-002 für `DataType.DECIMAL` (`position.tax_rate`) |
| T-005 | offen | `event.timezone` je Zelle statt je Export (3.8) |

Volltext, Reproduktion und Zuständigkeit: `handoff/blockers.md`.
