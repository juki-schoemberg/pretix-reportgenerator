# Performance: 100.000 Positionen, gemessen

Erstellt vom `test-engineer` in Welle 3. Quelle aller Zahlen:
`tests/test_performance.py`, ausgeführt mit

```bash
pytest tests/test_performance.py -m performance -q -s
```

Die Tests sind mit `performance` markiert und laufen im Normalfall **nicht** mit
(`pytest -m "not performance"`, siehe `CLAUDE.md`). Die Tabelle unten wird vom
Testmodul selbst am Ende des Laufs gedruckt; sie ist hier unverändert übernommen.

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

```
measurement                                        rows  queries   seconds  note
build fixture                                    100000        -    22.710  50000 orders, 50000 answers
narrow (3 columns, base orderposition)            94666        1     4.323  21899 rows/s; small event: 945 rows in 0.045s (x100)
wide (22 columns, base orderposition)             94666        1    11.099  8529 rows/s; small event: 945 rows in 0.109s (x100)
wide (16 columns, base order, 2 join columns)     49484      151    14.226  1 + 3 x ceil(rows/1000)
same report without the two join columns          49484        1     1.928  constant at any size
count() on the wide order report                  49484        1     0.003
preview (20 of 100.000 rows)                         20        -     0.057
full drain under tracemalloc                      94666        1    44.105  peak 6.4 MiB; time inflated ~4x by the tracer
CSV through the exporter                          94666        -     4.542  1.5 MiB of CSV
filtered + sorted position report                 59255        1     2.825  gte 25.00, desc
```

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
| Laufzeit mit | 14,23 s | — |
| Laufzeit ohne | 1,93 s | — |

Die Formel ist exakt

```
Queries = 1 + (Prefetch-Ebenen) × ceil(Zeilen / DEFAULT_CHUNK_SIZE)
        = 1 + 3 × ceil(49484 / 1000)
        = 151
```

und wird im Test als Gleichung geprüft, nicht als Größenordnung.

**Das ist kein N+1** — die Kosten je Zeile *sinken*, wenn der Report wächst:
4/494 = 0,0081 Queries je Zeile beim kleinen Event, 151/49.484 = 0,0031 beim
großen. Der Test prüft beides, `big_queries/big_rows < small_queries/small_rows`
und `big_queries < big_rows/100`. Es ist die bewusste Bauweise: eine `join`-Spalte
ist `prefetch_related` plus `str.join` in Python, weil Django 5.2 keine
backend-unabhängige String-Aggregation hat (`StringAgg` ist PostgreSQL-only), und
`QuerySet.iterator(chunk_size=1000)` führt Prefetches **je Chunk** aus. Genau das
kauft die konstante Speichergrenze aus Abschnitt 3.5.

Es ist aber auch **nicht das, was `query/columns.py` zusagt** („Kostet genau eine
Query pro Prefetch-Ebene, unabhängig von der Zeilenzahl"). Die Zusage stimmt für
einen Chunk. Als Finding 3 in `handoff/blockers.md` festgehalten; Kosten und
Gegenmittel dort.

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
  `computed.age.*`. Auch Finding 2 in `handoff/blockers.md` ist backend-abhängig.
- **XLSX.** `_render_xlsx` ohne `output_file` öffnet unter Windows eine
  `NamedTemporaryFile` ein zweites Mal über ihren Namen und wirft
  `PermissionError` — eine Plattformgrenze von pretix, kein Fehler des Plugins
  (`handoff/status/exporter-dev.md`). Die XLSX-Bytes für 100.000 Zeilen sind
  daher hier nicht messbar; unter Linux nachzuholen.
- **Nebenläufigkeit.** Alle Messungen sind Einzelläufe ohne Last daneben.
- **Der Multi-Event-Export über viele Events.** Fachlich in
  `tests/test_integration.py` abgedeckt, aber nicht in relevanter Größe gemessen;
  er kompiliert je Event einmal, die Kosten sind additiv.

## 6. Reproduzieren

```bash
pytest tests/test_performance.py -m performance -q -s
```

Dauer im Ganzen etwa 110 s, davon 23 s Datenaufbau. Die Testdaten sind über
`tests.factories.SEED` festgelegt; zwei Läufe messen dieselbe Arbeit. Der
Datensatz lebt in einer Transaktion, die am Modulende zurückgerollt wird, also
bleibt nichts in der Testdatenbank zurück.

Größe verstellen: `BIG_ORDERS` / `POSITIONS_PER_ORDER` oben in
`tests/test_performance.py`.
