# Status: test-engineer — Welle 3

`tests/factories.py`, `tests/test_integration.py`, `tests/test_performance.py`
und die Ergänzungen in `tests/conftest.py`. Dazu `docs/performance.md` (von der
Definition of Done verlangt) und ein angehängter Abschnitt in
`handoff/blockers.md`. **Kein Produktivcode geändert, kein Commit, keine fremde
Testdatei angefasst.**

Ergebnis in einer Zeile: **42 neue Tests, 40 grün, 2 als `xfail(strict=True)` —
das sind zwei echte Fehler, die keine Einzelsuite finden konnte.** Dazu ein
dritter Fund über die Query-Zahlen bei 100.000 Positionen.

## Erledigt

### 1. Faktorien (`tests/factories.py`, ~950 Zeilen)

Zwei getrennte Aufgaben, bewusst nicht vermischt:

**`build_reference_world(event)`** — ein kleines, **von Hand geschriebenes**
Event, dessen jede Zahl mit Bleistift nachrechenbar ist. Der Modul-Docstring
enthält das Hauptbuch als Tabelle:

| Code | Status | Total | Positionen | Zahlungen | Erstattungen |
| --- | --- | --- | --- | --- | --- |
| PAID1 | paid | 33,00 | 23,00 + 10,00 live, 10,00 storniert | 33,00 confirmed | — |
| PART2 | pending | 46,00 | 23,00 + 23,00 | 20,00 confirmed, **5,00 pending** | — |
| PEND3 | pending | 23,00 | 23,00 (mit Gutschein) | — | — |
| EXPI4 | expired | 15,00 | 15,00 | — | — |
| CANC5 | canceled | 23,00 | 23,00 storniert | 23,00 confirmed | 23,00 done |
| OVER6 | paid | 23,00 | 23,00 | **30,00** confirmed | — |
| TEST7 | paid, **Testmodus** | 7,00 | 7,00 | 7,00 confirmed | — |

Jede Zeile ist eine Falle für eine Abkürzung im Produktivcode:

- PART2s `pending`-Zahlung trennt „Betrag bezahlt" von „Summe aller Zahlungen".
  Wer die Zustandsmenge aus `orders.py:486-492` nicht benutzt, bekommt 25,00
  statt 20,00.
- CANC5 ist storniert **und** voll erstattet. Der offene Betrag ist damit 0,00;
  wer `total - bezahlt + erstattet` ohne die Storno-Sonderbehandlung rechnet,
  bekommt 23,00.
- OVER6 ist überzahlt — der einzige Weg, den vierten Zahlungszustand zu sehen.
- PAID1s stornierte Position hat die T-Shirt-Frage mit `S` beantwortet. Damit
  wird `include_canceled_positions` in **vier** Zellen gleichzeitig sichtbar.

Dazu: eine Frage **jedes** `Question.type` (12 Stück, inkl. beider Choice-Typen
mit `QuestionOption`-Zeilen und festen Option-Identifiern), Produkte mit
Varianten und ein Produkt **ohne** Kategorie (leere Relation ≠ leerer String),
Check-ins inklusive eines Exit-Scans und eines fehlgeschlagenen Scans
(`Checkin.all` statt `Checkin.objects`), Gutschein, Rechnungsadresse,
Subevent-Serie auf Abruf.

**Die erwarteten Werte stehen bewusst *nicht* in der Faktorei**, sondern noch
einmal von Hand in `tests/test_integration.py`. Eine aus der Faktorei importierte
Erwartung würde nur beweisen, dass die Faktorei mit sich selbst übereinstimmt.

**`build_bulk(event, ...)`** — synthetische Massendaten über `bulk_create` mit
festem Seed. 100.000 Positionen in 22 s. `bulk_create` ruft `Model.save()` nicht
auf, also werden `secret`, `pseudonymization_id`, `organizer_id` und die
Steuerfelder von `OrderPosition` hier explizit und **deterministisch aus einem
Zähler** gesetzt — ein zufälliges Secret würde zwei Läufe desselben Lasttests
verschiedene Daten messen lassen.

Determinismus insgesamt: kein `now()`, kein `uuid4()`, kein ungeseedetes
`random`. Alle Zeitstempel sind Offsets von `EPOCH = 2026-05-04 09:00 UTC`,
alle Zufallsentscheidungen aus `random.Random(20260801)`.

### 2. Durchstich (`tests/test_integration.py`, Abschnitt 1)

**`test_the_whole_path_from_the_editor_to_another_events_export`** — ein Report,
einmal rundherum, an **acht** Stationen geprüft, davon fünf über HTTP durch den
echten URL-Resolver, die echte Control-Middleware und die echten
Permission-Dekoratoren:

1. Editor-Seite öffnet (`editor.new`, 200)
2. `POST api/validate/` → kanonisches Dokument, keine Registry-Warnungen
3. `POST api/preview/` → die sechs Bestellungen dieses Events in der richtigen
   Reihenfolge
4. `POST event.reports.add` (CRUD-Formular) → Zeile in der DB, `definition`
   byte-gleich mit der kanonischen Fassung, stabiler Identifier
5. Export über `init_event_exporter` + `ListExporter.render` → **exakte
   Kopfzeile und exakte sechs Zeilen**
6. `GET event.reports.export` → JSON-Datei, gegen
   `validate_portable_document` geprüft, `source == "dummy/main"`,
   `meta.references` enthält den Fragen-Identifier, keine Primärschlüssel
7. Import in ein **zweites Event** über die zweistufige View (Vorschau, dann
   `action=confirm`) → neuer Report, Identifier **überlebt**
8. Export dort → **die Daten des zweiten Events**, nicht die des ersten

Der Punkt von Schritt 8: das zweite Event hat eigene Bestellungen und eigene
Fragen mit denselben Identifiern. Die Definition muss reisen, die Zeilen nicht.

Weiter in Abschnitt 1:

- **`test_a_template_reaches_an_event_whose_question_is_spelled_differently`** —
  Organizer-Vorlage → Event, in dem die Frage `tshirt_size` (Unterstrich) heißt
  und `diet` gar nicht existiert. Erwartet und geprüft: ein Key `found`, einer
  `mapped`, einer `missing`, `abort` verweigert, `skip` erzeugt den reduzierten
  Report — und die **Spalte ist wirklich weg**, nicht still leer (geprüft an der
  CSV: zwei Spalten, nicht drei).
- **`test_an_event_copy_carries_its_reports_and_runs_them_in_the_copy`** —
  `Event.copy_data_from` **wirklich aufgerufen** (das hat `portability-dev` sich
  gewünscht), danach `copy_reports_to_event`. Der kopierte Report liefert erst
  eine leere Datei mit Kopfzeile und, nachdem in der Kopie eine Bestellung
  angelegt wurde, deren Zeile.
- **`test_a_multi_event_export_labels_every_row_with_its_event`** — der
  Organizer-Export über zwei Events mit demselben Identifier: zwei führende
  Spalten, sieben Zeilen, keine Verschränkung, keine Dopplung.

### 3. Korrektheit mit von Hand gerechneten Werten (Abschnitt 2)

Zwölf Tests, alle mit ausgeschriebenen Erwartungswerten. Die wichtigsten:

- **`test_the_money_columns_of_an_order_report_match_the_ledger`** — acht Spalten
  × sechs Zeilen als eine Literaltabelle: Total, bezahlt, erstattet, offen,
  Zahlungszustand, Positionszahl, Check-in-Zahl. Deckt alle vier Zustände von
  `computed.payment_state` ab.
- **`test_four_aggregates_over_three_relations_do_not_multiply_each_other`** —
  Positionen, Antworten und Zahlungen in einem Report, PAID1 muss 33,00 / 2 / 2 /
  `"L, XL"` / 33,00 liefern.
- **`test_the_naive_join_really_does_produce_the_wrong_number`** — die
  Gegenprobe, damit der Test darüber nicht leer ist: dieselben Daten mit zwei
  gewöhnlichen `annotate(Sum/Count)` über denselben gejointen Pfad ergeben
  **286,00** statt 33,00 und **13** statt 2. Damit ist die
  Subquery-Entscheidung aus `query/relations.py` gemessen und nicht nur
  beschrieben.
- **`test_include_canceled_positions_changes_exactly_four_cells`** — inklusive
  der Stelle, an der sich der Schalter *nicht* auswirken darf
  (`order.position_count` zählt laut Registry immer nur lebende Positionen; eine
  Änderung dort wäre ein Widerspruch zwischen zwei Spalten desselben Reports).
- **`test_a_position_report_renders_an_answer_of_every_question_type`** — alle
  zwölf Fragetypen in einer Zeile, mit den tatsächlichen Zeichenketten. Das
  legt fest, wie ein Boolean und eine Mehrfachauswahl im Export aussehen; eine
  Änderung daran muss eine Entscheidung sein, kein Nebeneffekt.
- **`test_age_at_the_event_date_is_computed_in_the_database`** — Ada, geboren
  1990-06-15, ist am Eventtag 2026-06-03 **35**, nicht 36. Wer nur Jahre
  subtrahiert, bekommt 36 und 16.
- **`test_a_hidden_column_filters_and_sorts_but_does_not_appear`** — inklusive
  des `pk`-Tiebreakers: PAID1 und CANC5 stehen beide bei 0,00 und müssen in
  dieser Reihenfolge kommen. Ohne den Tiebreaker wäre die Zeile flaky — was
  genau der Grund ist, dass sie so dasteht.

### 4. Grenzfälle (Abschnitt 3)

Alle sechs aus dem Auftrag, plus zwei weitere Grenzen und zwei
Nullwert-Gegenproben:

| Fall | Test | Ergebnis |
| --- | --- | --- |
| Event ohne Bestellungen | `…exports_a_header_and_nothing_else` | Kopfzeile, null Zeilen, keine Exception |
| Bestellung ohne Positionen | `test_an_order_without_positions` | Basis `order`: Zeile mit `0` und `None`; Basis `orderposition`: keine Zeile |
| gelöschte Frage bei bestehendem Report | `…leaves_the_report_openable_and_names_the_key` | Zeile überlebt, Editor öffnet, `api/validate` warnt bei `columns[1]`, Export nennt den Key im `ExportError` |
| Subevent-Spalte auf Event ohne Serie | `test_a_subevent_column_on_an_event_without_a_series` | leere Zellen; **Gegenprobe im selben Test**: eine echte Serie füllt dieselbe Spalte |
| stornierte Bestellung | `test_a_canceled_order_is_a_row_that_owes_nothing` | Zeile ja, Positionen 0, offener Betrag 0,00 |
| Bestellung mit 200 Positionen | `test_an_order_with_two_hundred_positions` | 200 Zeilen bzw. eine Zeile, Summe 20.100,00 (arithmetische Reihe) |
| `row_limit` | `test_a_report_whose_row_limit_caps_the_result` | Zeilen **und** `count()` gekappt |
| fremdes Event | `…is_not_reachable_through_this_one` | 404 auf Export- und Editor-URL |

Zwei Nullwert-Gegenproben stehen aus Zusammenhangsgründen in Abschnitt 2
(Korrektheit): Varianten und die **fehlende** Produktkategorie
(`test_a_variation_and_an_empty_category_render_as_themselves`) sowie Gutschein
und Rechnungsadresse (`test_the_voucher_and_the_invoice_address_reach_their_columns`).
Beide prüfen konkrete Werte neben den `None`-Zellen — ein Test, in dem eine
Spalte durchgehend leer ist, würde auch dann bestehen, wenn sie nie funktioniert
hätte.

### 5. Zeitabhängigkeit (Abschnitt 4)

Über das hinaus, was `exporter-dev` mit `freeze_time` für den einfachen Fall
schon geprüft hatte:

- **`…follows_the_event_timezone_across_the_date_line`** — Auckland-Event,
  UTC-Server, eingefrorener Zeitpunkt 2026-06-30 13:00 UTC. Für den
  Veranstalter ist das der **1. Juli**, für den Server der 30. Juni. Drei
  Bestellungen, zwei davon „heute". Wer in Serverzeit auflöst, bekommt die
  andere Zweiermenge.
- **`…the_current_month_can_be_a_different_month`** — derselbe Trick, aber mit
  `relative_current_month`: Juni auf dem Server, Juli für den Veranstalter. Vier
  Bestellungen an den Monatsgrenzen. Der Operator, bei dem ein Zeitzonenfehler
  am teuersten ist, weil ein Monatsreport genau das ist, was man terminiert.
- **`…the_spring_dst_change_is_twenty_three_hours_long`** und
  **`…the_autumn_dst_change_is_twenty_five_hours_long`** — Europe/Berlin,
  2026-03-29 und 2026-10-25. Im Herbstfall liegen **beide Durchläufe** von 02:30
  (+02:00 und +01:00) im selben Tag.
- **`…daily_windows_stay_gapless_and_disjoint_across_both_dst_changes`** — die
  Eigenschaft, auf die es bei einem täglichen Report ankommt: fünf Tage
  durchlaufen, jede Bestellung erscheint **genau einmal**. Eine verlorene Stunde
  ist eine verlorene Zeile, und niemand gleicht einen täglichen Export gegen den
  Kalender ab.
- **`…relative_last_days_spans_the_switch_without_losing_a_day`** — `last_days: 3`
  über den 25. Oktober ist ein 73-Stunden-Fenster.
- **`…a_scheduled_export_reevaluates_its_relative_filter_across_the_switch`** —
  derselbe `ScheduledEventExport`, zweimal ausgeführt über
  `run_scheduled_exports(None)` (der echte `periodic_task`-Empfänger), an zwei
  Tagen beiderseits der Umstellung. Geprüft wird der **Mailanhang**: Tag 1
  enthält DAY28 und nicht DAY29, Tag 2 umgekehrt.
- **`…since_event_start_uses_the_events_own_start_instant`** — der Operator ist
  ein Zeitpunkt, keine Mitternacht. Eine Bestellung eine Stunde vor Eventbeginn
  am selben Tag muss draußen bleiben.

### 6. Lasttests (`tests/test_performance.py`, 8 Tests, `-m performance`)

Vollständiger Bericht mit allen Zahlen: **`docs/performance.md`**. Kurzfassung
für 100.000 Positionen / 50.000 Bestellungen auf SQLite:

| Report | Zeilen | Queries | Laufzeit | Durchsatz |
| --- | --- | --- | --- | --- |
| schmal, 3 Spalten | 94.666 | **1** | 4,32 s | 21.900 Z/s |
| breit, 22 Spalten, Basis `orderposition` | 94.666 | **1** | 11,10 s | 8.500 Z/s |
| breit, 16 Spalten, Basis `order`, ohne `join` | 49.484 | **1** | 1,93 s | — |
| breit, 16 Spalten, Basis `order`, **mit 2 `join`** | 49.484 | **151** | 14,23 s | — |
| Vorschau (20 von 100.000) | 20 | — | 0,057 s | — |
| `count()` | 49.484 | 1 | 0,003 s | — |
| CSV über den Exporter | 94.666 | — | 4,54 s | 1,5 MiB |

**Beleg „Query-Anzahl wächst nicht mit der Zeilenzahl":** derselbe Report wird
gegen ein Event mit 1.000 und eines mit 100.000 Positionen ausgeführt und die
Zahl mit `django_assert_num_queries(1)` **festgenagelt** — nicht bloß verglichen,
denn „in beiden gleich" gälte auch für ein N+1, das in beiden ein N+1 ist.

**Speicher:** Spitzenverbrauch **6,4 MiB** bei 94.666 Zeilen und 22 Spalten,
gemessen mit `tracemalloc`. Die Streaming-Kette ist nicht nur konstruiert faul,
sondern gemessen faul. Das war die von `exporter-dev` in Welle 2 erbetene
Messung.

## Drei Findings (`handoff/blockers.md`, Abschnitt „test-engineer")

Keines blockiert. Alle drei liegen genau dort, wo eine Suite endet und die
nächste beginnt — jede beteiligte Einzelsuite ist grün und hat recht.

### T-001 (mittel) — `ColumnFormat` wirkt in der Vorschau, nicht im Export

`date_style`, `number_style` und `boolean_style` stehen im eingefrorenen
Contract, der Editor bietet sie an, die Vorschau wendet sie an — und **nichts im
Exportpfad** tut es. `query/columns.py` liest nur `format.separator`,
`exporters.py` enthält bewusst gar keine Renderlogik. Gemessen: `iso` und
`date_only` erzeugen dieselbe CSV-Zeile.

Der Nutzer stellt „nur Datum" ein, sieht es in der Vorschau und bekommt in der
Datei den vollen Zeitstempel. `frontend-dev` hat den Punkt in Welle 1 **und**
Welle 2 als offene Frage gestellt; für `separator` hat der Compiler es
übernommen, für den Rest niemand.

Reproduzierer:
`test_finding_a_column_format_chosen_in_the_editor_reaches_the_export`

### T-002 (mittel) — aggregierte Geldspalten verlieren ihre Nachkommastellen

In einer CSV-Zeile: `order.total` schreibt `23.50`, `payment.sum_confirmed`
schreibt `20.5`, `SUM(position.price)` schreibt `23.5`. Djangos SQLite-Backend
quantisiert einen `DecimalField` nur für ein einfaches `Col`, nicht für
`Subquery`/`Coalesce`/`Sum`. PostgreSQL behält die Skala — **derselbe Report
erzeugt auf zwei Installationen zwei verschiedene Dateien**, dieselbe Klasse von
Abweichung, die `query-dev` bei `nulls_last` bewusst abgefangen hat.

Warum es keine Einzelsuite finden konnte: `Decimal("23.5") == Decimal("23.50")`
ist in Python `True`. Alle bestehenden Tests vergleichen Beträge als `Decimal`
und bleiben grün. Sichtbar wird es erst an den *Zeichen* der Datei.

Reproduzierer:
`test_finding_an_aggregated_money_column_keeps_its_two_decimal_places`

### T-003 (niedrig/mittel) — Query-Zusage für `join`-Spalten gilt nur je Chunk

`query/columns.py` sagt zu: „eine Query pro Prefetch-Ebene, unabhängig von der
Zeilenzahl". Tatsächlich `1 + 3 × ceil(Zeilen / 1000)` — 4 Queries bei 494
Zeilen, **151** bei 49.484. Kein N+1 (die Kosten je Zeile sinken), kein Fehler im
Entwurf (der Prefetch je Chunk ist genau das, was den Speicher bei 6,4 MiB hält)
— aber die Zusage stimmt nicht, und auf einem PostgreSQL über Netzwerk sind 151
Roundtrips etwas anderes als vier. Mindest-Fix: drei Zeilen Docstring.

Gemessen in `test_a_join_column_costs_one_prefetch_per_chunk_not_one_per_row`
(kein xfail — der Test prüft das *tatsächliche* Verhalten als Gleichung und wird
rot, wenn es sich ändert). Verwandt mit S-005 des `security-reviewer`, aber
anderer Pfad: dort die Vorschau, hier der volle Export.

### Zur Form der Reproduzierer

T-001 und T-002 sind `@pytest.mark.xfail(strict=True)`. Die Rollenbeschreibung
verlangt „Finding mit fehlschlagendem Test" — ein strikter xfail **ist** dieser
Test: er läuft, er schlägt fehl, der Fehlschlag ist protokolliert. Gleichzeitig
bleibt die Suite grün, damit die nächste echte Regression nicht unter zwei
bekannten begraben wird, und `strict=True` sorgt dafür, dass er mit **XPASS**
umfällt, sobald jemand den Fehler behebt — das ist der Moment, ihn in einen
normalen Assert umzuschreiben. Vorführen:

```bash
pytest tests/test_integration.py -k finding --runxfail
```

## Nicht erledigt (und warum)

- **PostgreSQL bleibt ungeprüft.** Vier Agenten haben darum gebeten; es gibt in
  dieser Umgebung kein PostgreSQL (`pretix/src/pretix.cfg`: `backend=sqlite3`),
  und einen Datenbankserver zu installieren liegt außerhalb meines
  Dateibereichs und außerhalb dessen, was ein Agent ohne `sudo` tun soll. Die
  betroffenen fünf Stellen und ein konkreter Vorschlag stehen in
  `handoff/blockers.md`. **T-002 ist der bisher beste Grund, diesen Lauf zu
  machen.**
- **XLSX-Bytepfad unter Linux.** `_render_xlsx` ohne `output_file` öffnet unter
  Windows eine `NamedTemporaryFile` ein zweites Mal über ihren Namen und wirft
  `PermissionError` — eine Plattformgrenze von pretix. Ich laufe unter Windows,
  also unverändert offen (`handoff/status/exporter-dev.md`). Die
  XLSX-Durchsatzzahl für 100.000 Zeilen fehlt deshalb in
  `docs/performance.md`.
- **`tests/test_smoke.py::test_no_migration_created_yet`** ist weiterhin der
  einzige rote Test des Repos. Er gehört dem `integrator` (Welle-0-Gate,
  Ersatzvorschlag liegt in
  `handoff/requests/persistence-dev-an-integrator-urls.md` Abschnitt 2). Er
  steht seit Welle 1 in vier Statusberichten; ich habe ihn **nicht** angefasst,
  weil `tests/test_smoke.py` nicht mein Bereich ist.
- **Kein Browsertest.** `frontend-dev` hat sechs Playwright-Tests; sie skippen
  ohne Browser, und ich habe nichts hinzugefügt. Der Hinweis von `frontend-dev`
  bleibt gültig: auf einem CI ohne Browser fällt die Abdeckung von Drag & Drop,
  select2 und der Verlassen-Nachfrage **still** weg.
- **Multi-Event-Export in relevanter Größe** nicht gemessen, nur fachlich
  getestet. Er kompiliert je Event einmal, die Kosten sind additiv; eine eigene
  Messung hätte nichts Neues gesagt.
- **Kein Test gegen Nebenläufigkeit.** Alle Messungen sind Einzelläufe.

## Getroffene Entscheidungen

Keine neue ADR — nichts davon ist eine Architekturentscheidung. Vier Punkte, die
eine Begründung brauchen und im Code kommentiert sind:

1. **Erwartungswerte doppelt geschrieben.** Das Hauptbuch steht im Docstring von
   `tests/factories.py`, die erwarteten Zellen noch einmal literal in
   `tests/test_integration.py`. Eine importierte Erwartung würde nur beweisen,
   dass die Faktorei mit sich selbst übereinstimmt.
2. **`xfail(strict=True)` statt roter Tests** für die zwei Findings, Begründung
   oben.
3. **Geldzellen im Durchstichtest als `Decimal` verglichen, nicht als String.**
   Sonst wäre der Durchstichtest an T-002 gescheitert und hätte damit über
   etwas anderes berichtet, als sein Name sagt. Die Stelle trägt einen Kommentar
   mit Verweis auf den Finding-Test.
4. **`wired_urls` in `conftest.py`, modul-scoped, opt-in.** `test_permissions.py`
   und `test_portability.py` hängen sich je ihre Teilmenge der Routen ein; der
   Durchstich braucht **alle** gleichzeitig, weil er durch die Views von vier
   Agenten läuft. Bewusst **kein** autouse-Fixture: eine Ergänzung in
   `conftest.py` darf nicht ändern, was ein anderes Modul tut. Der Shim kann in
   Welle 4 ersatzlos entfallen, sobald `urls.py` verdrahtet ist; die Tests
   laufen dann unverändert weiter.

## Contract-Abweichungen

**KEINE.** `pretix_custom_reports/contracts/` und
`tests/fixtures/definitions/` sind unangetastet. Benutzt werden
`validate_definition`, `validate_portable_document`, `SCHEMA_VERSION`,
`EXPORT_FORM_REPORT_KEY`, `PREVIEW_ROW_LIMIT`, `DEFAULT_CHUNK_SIZE` — alle wie
deklariert.

T-001 berührt den Contract *inhaltlich* (`ColumnFormat` verspricht etwas, das
niemand einlöst), ist aber **kein Änderungswunsch**: der Contract ist richtig,
die Implementierung unvollständig. Deshalb Finding, nicht Blocker.

## Offene Anforderungen an andere

Keine eigene Datei in `handoff/requests/` — meine drei Punkte sind Findings, kein
Änderungswunsch in fremdem Gebiet, und stehen deshalb vollständig in
`handoff/blockers.md`:

| An | Was |
| --- | --- |
| query-dev + exporter-dev + frontend-dev | T-001: **eine** Stelle für die Formatierung festlegen; Empfehlung im Blocker |
| registry-dev + query-dev | T-002: Skala der Geldausdrücke festnageln |
| query-dev | T-003: Docstring korrigieren (drei Zeilen) |
| Orchestrator | PostgreSQL-Lauf, vier benannte Testmodule |
| integrator | `test_no_migration_created_yet` ersetzen |

## Tests

```
pytest tests/test_integration.py -q      -> 32 passed, 2 xfailed
pytest tests/test_performance.py -m performance -q -s
                                         ->  8 passed  (~110 s)
pytest tests/ -m "not performance" -q    -> 992 passed, 1 failed, 10 xfailed
```

Der eine Fehlschlag ist `test_no_migration_created_yet` (siehe oben).
Gegenprobe ohne meine zwei Module (`--ignore=tests/test_integration.py
--ignore=tests/test_performance.py`): **960 passed, 1 failed, 8 xfailed** — darin
enthalten die 131 Tests des `security-reviewer`, der in derselben Welle parallel
gearbeitet hat. Ich füge 32 grüne und 2 xfailed hinzu und breche nichts,
insbesondere nicht durch die Ergänzung in `tests/conftest.py`.

| Datei | Tests | Inhalt |
| --- | --- | --- |
| `tests/test_integration.py` | 34 | Durchstich (4), Korrektheit (12), Findings (2 xfail), Grenzfälle (8), Zeit (8) |
| `tests/test_performance.py` | 8 | Query-Zahlen, Speicher, Vorschau, `count()`, CSV, Filter/Sortierung |
| `tests/factories.py` | — | Faktorien |
| `tests/conftest.py` | — | `wired_urls` + `reload_urlconf` ergänzt, Bestehendes unverändert |

Mehrfach mit zufälliger und mit fester Reihenfolge gelaufen, gleiches Ergebnis.
Die Lasttests sind mit `performance` markiert und werden von
`pytest -m "not performance"` sauber abgewählt (8 deselected).

Lint über die eigenen vier Dateien:

```
flake8 tests/conftest.py tests/factories.py tests/test_integration.py tests/test_performance.py   -> rc 0
isort -c   (dieselben)                                                                            -> rc 0
black --check (dieselben)                                            -> 4 files unchanged
```

Kein `black .` / `isort .` über das Repo, kein `git commit`, keine Datei außerhalb
von `tests/conftest.py`, `tests/factories.py`, `tests/test_integration.py`,
`tests/test_performance.py`, `docs/performance.md` und `handoff/**` angefasst.

> **Hinweis zu `docs/performance.md`:** die Datei ist in der Ownership-Tabelle
> nicht aufgeführt; die Definition of Done verlangt sie ausdrücklich von mir. Sie
> ist neu, kollidiert mit nichts und gehört ab jetzt zu diesem Bereich. Falls die
> Tabelle ergänzt werden soll: neben `docs/adr/**`.

## Nächster Schritt

1. **Orchestrator:** T-001 entscheiden (wo wird formatiert?) — das ist die
   einzige der drei Sachen, die eine *Entscheidung* braucht und nicht nur einen
   Fix. Solange sie offen ist, verspricht der Editor etwas, das die Datei nicht
   hält.
2. **Orchestrator:** den PostgreSQL-Lauf einplanen. Vier Agenten haben darum
   gebeten, T-002 zeigt jetzt konkret, wonach zu suchen ist, und es sind vier
   Testmodule.
3. **integrator (Welle 4):** wenn `urls.py` verdrahtet ist, kann `wired_urls` in
   `tests/conftest.py` ersatzlos verschwinden — genauso wie die Shims in
   `tests/test_permissions.py`, `tests/test_portability.py` und
   `tests/test_editor_api.py`. Vier Fixtures, ein Handgriff, und die Tests laufen
   unverändert weiter.
4. **Wer T-001 oder T-002 behebt:** die zwei `xfail`-Tests fallen dann mit
   **XPASS** um. Das ist Absicht. Marker entfernen, Assertion stehen lassen.

---

## Nachtrag — Welle 4: `event_copy_data` ist jetzt verdrahtet

**Anlass.** Der `integrator` hat den Empfänger von `portability-dev` in
`pretix_custom_reports/signals.py` verbunden. Damit ruft jedes
`Event.copy_data_from(...)` selbst `copy_reports_to_event(...)` auf. In Welle 3
gab es das Signal noch nicht, deshalb rief
`test_an_event_copy_carries_its_reports_and_runs_them_in_the_copy` die
Kopierfunktion von Hand nach — jetzt lief sie zweimal, der zweite Aufruf traf
auf den vergebenen Identifier und legte `sizes-2` an:

```
tests/test_integration.py:516: AssertionError: assert 'sizes-2' == 'sizes'
```

Kein Produktivfehler, sondern ein Test, der eine Simulation prüfte, wo es
inzwischen den echten Weg gibt.

**Geändert (nur `tests/test_integration.py`):**

1. `test_an_event_copy_carries_its_reports_and_runs_them_in_the_copy` ruft
   `copy_reports_to_event` nicht mehr auf. Der Test macht nur noch
   `copy_event.copy_data_from(world.event)` und liest das Ergebnis über
   `copy_event.custom_reports`. Statt `result.count`/`result.failed`/
   `result.copied[0].report` steht dort jetzt
   `assert [r.identifier for r in copies] == ["sizes"]` — eine Liste, kein
   `get()`: ein doppelt feuernder Empfänger hinterlässt `sizes-2`, und genau das
   fällt so auf. Der Rest (Export leer, dann Bestellung in der Kopie, dann eine
   Zeile) ist unverändert.
2. Neu: `test_the_event_copy_signal_hands_the_question_map_to_the_log_entry`.
   Er prüft den Signalweg dort, wo er sich von einem direkten Funktionsaufruf
   unterscheidet — an den **Argumenten, die pretix schickt**. Über den
   `copied_from_event`-Block des `report.added`-Eintrags:
   `questions_mapped == 12` (eine Frage je `Question.type`, `QUESTION_SPECS` in
   `tests/factories.py`), Quell-Organizer/-Event/-Identifier, und
   `counts.found == 2` bei `mapped == missing == 0`. Festgehalten ist außerdem,
   dass der Eintrag **keinen** Benutzer hat: `event_copy_data` führt keinen mit,
   der Empfänger kann keinen erfinden.
3. Import `copy_reports_to_event` entfernt (in dieser Datei nun ungenutzt). Die
   Funktion selbst bleibt in `tests/test_portability.py` Abschnitt 8 mit fünf
   Tests abgedeckt, inklusive Umbenennung, nicht auflösbarem Key und
   organizerübergreifender Kopie. Doppelt musste sie hier nicht sein; der
   Integrationsteil trägt jetzt genau das bei, was die Unit-Tests nicht können.

Damit ist Punkt 5 aus
`handoff/requests/erledigt/portability-dev-an-integrator-signals.md` erledigt
(„ein Test, der das Signal selbst auslöst, gehört nach dem Verdrahten in
tests/test_integration.py").

**Läufe.**

```
pytest tests/test_integration.py -q          -> 33 passed, 2 xfailed
pytest -m "not performance" -q               -> 1016 passed, 8 deselected, 8 xfailed
flake8 / isort -c / black --check tests/test_integration.py -> rc 0
```

Kein Produktivcode angefasst, kein `git commit`. Die drei Findings in
`handoff/blockers.md` (T-001 bis T-003) sind davon unberührt und stehen weiter
offen.

---

## Nachtrag (Welle 4): `registered`-Fixture entschärft

`exporter-dev` hat in `tests/test_exporters.py` einen Fixture-Fehler gefunden und
behoben und denselben Fehlermechanismus in meiner Datei gemeldet. Meine Fixture
`registered` (`tests/test_integration.py`) hatte **nicht** die lauteste Variante
davon, aber die leisere derselben Familie.

**Was war.** Die Fixture verband `register_report_exporter` und
`register_multievent_report_exporter` unter eigenen `dispatch_uid`s
(`pretix_custom_reports_integration_exporter` / `..._integration_multiexporter`).
Weil `signals.py` seit Welle 4 dieselben Funktionen beim Plugin-Import unter
`pretix_custom_reports_exporter` / `..._multiexporter` verbindet, waren das ab
Welle 4 **zwei** Empfänger für dieselbe Funktion. Der Teardown hat sauber die
eigene Verbindung getrennt — die Produktivverdrahtung blieb also unberührt, die
sessionweite Variante des Fehlers (`test_exporters.py`, `test_security.py`) hatte
ich nicht. Dafür lief jeder Test dieser Datei gegen eine Exporterliste, in der
`customreports` **doppelt** stand: `init_event_exporters()` instanziiert einen
Exporter je wahrer Signalantwort und dedupliziert nicht
(`pretix/base/services/export.py:198-222`). Aufgefallen ist es nie, weil
`init_event_exporter()` den ersten Treffer zurückgibt (ebd. 191-195) — sichtbar
geworden wäre es als doppelte Zeile auf der Exportseite.

**Was jetzt ist.** Muster von `exporter-dev` übernommen, Helfer bewusst
dupliziert statt importiert (zwei Testmodule verschiedener Agents sollen sich
nicht gegenseitig importieren):

* `connected_receiver(signal, dispatch_uid)` dereferenziert die `weakref.ref`s
  aus `Signal.receivers`.
* `WIRING` nennt die **produktiven** `dispatch_uid`s, `WIRING_AT_IMPORT` hält den
  Zustand nach `apps.ready()` fest.
* `registered` verbindet nur noch, was fehlt, und trennt nur, was sie selbst
  verbunden hat. Ist eine uid belegt, prüft ein `assert`, dass sie an die
  erwartete Funktion gebunden ist — sonst liefe die ganze Datei still gegen einen
  fremden Empfänger, weil `connect()` bei belegtem Schlüssel wirkungslos ist
  (`django/dispatch/dispatcher.py:113-117`) und `disconnect(dispatch_uid=...)`
  allein über diesen Schlüssel matcht (ebd. 138-153).

**Zwei neue Tests** in neuem Abschnitt 7:

1. `test_the_report_export_is_offered_exactly_once_on_both_levels` — ohne die
   Fixture, weil die Fixture nicht gleichzeitig Zeuge ihrer eigenen Zusage sein
   kann. Prüft über `init_event_exporters` **und** `init_organizer_exporters`,
   dass `customreports` genau einmal in der Liste steht, und dass die beiden
   produktiven uids an unsere Funktionen gebunden sind. Gegenprobe gemacht: mit
   einer zweiten Verbindung unter fremder uid schlägt er fehl
   (`assert ['customreports', 'customreports'] == ['customreports']`).
2. `test_this_module_hands_the_signal_wiring_back_untouched` — steht bewusst als
   letzter Test der Datei. Vergleicht gegen einen modulweiten Snapshot; der
   strenge Teil läuft nur, wenn die Datei wirklich die Produktivverdrahtung
   bekommen hat, damit fremde Lecks nicht unter meinem Testnamen auflaufen.

**Nicht mein Gebiet, nur als Hinweis:** `tests/test_smoke.py:38-53` (integrator)
hält einen Snapshot-Workaround, dessen Kommentar noch auf die alte Fixture von
`exporter-dev` zeigt. `tests/test_security.py` hat `security-reviewer`
parallel auf dasselbe Muster gezogen (`registered_exporter`). Damit ist in allen
drei Modulen die Ursache weg und der Workaround in `test_smoke.py` könnte
zurückgebaut werden — Entscheidung des `integrator`.

**Läufe.**

```
pytest tests/test_integration.py -q                                   -> 35 passed, 2 xfailed
pytest tests/test_integration.py tests/test_exporters.py \
       tests/test_security.py -q                                      -> 206 passed, 8 xfailed
pytest tests/test_security.py tests/test_integration.py \
       tests/test_exporters.py tests/test_smoke.py -q  (Reihenfolge)   -> 228 passed, 8 xfailed
pytest -m "not performance" -q                                        -> 1019 passed, 8 deselected, 8 xfailed
flake8 / isort -c / black --check tests/test_integration.py           -> rc 0
```

Nur `tests/test_integration.py` geändert. Kein Produktivcode, kein `git commit`.

---

## Verifikation T-001 bis T-003 (2026-08-03)

Gegenprüfung der Fixes, die vier Agenten zu meinen drei Findings geliefert haben.
Ergebnis in einer Zeile: **alle drei behoben und verifiziert, beide `xfail`-Marker
entfernt, sieben neue Tests dazu — und zwei neue Befunde, die erst beim
Gegenprüfen sichtbar wurden.**

Geändert: `tests/test_integration.py`, `tests/test_performance.py`,
`docs/performance.md`, `handoff/blockers.md`. **Kein Produktivcode, kein
`git commit`, keine fremde Testdatei angefasst** — insbesondere nicht
`tests/test_security.py`, an der `security-reviewer` parallel gearbeitet hat.

### Vorgehen: drei Stufen, nicht eine

Ein Test, der `XPASS` meldet, ist kein Beweis, dass der Fix wirkt — er ist ein
Beweis, dass der Test grün ist. Deshalb je Finding:

1. **Grün ohne `--runxfail`.** Erledigt, beide.
2. **Misst er noch das Ursprungsproblem?** Die Assertions sind **unverändert
   geblieben** (eigene Vorgabe aus dem Wellen-3-Bericht: „Marker entfernen,
   Assertion stehen lassen"). Dazu kommen jetzt die literalen Erwartungswerte:
   „ungleich" ist eine schwächere Aussage als `"2026-04-24"`, und ein Renderer,
   der beide Stile gleich falsch macht, hätte „ungleich" ebenfalls erfüllt.
3. **Neutralisierte Gegenprobe.** Fix zur Laufzeit abgeschaltet (`monkeypatch`,
   kein Produktivcode angefasst, nichts davon dauerhaft im Repo), Test muss an
   *derselben* Stelle wieder fallen. Alle drei Neutralisierungen fielen auf
   genau die Zeichenketten zurück, die im Wellen-3-Blocker stehen — die Tabelle
   dazu in `handoff/blockers.md`.

Und weil ein Fix, der einen Reproduzierer grün macht, nicht dasselbe ist wie ein
Fix, der trägt: sieben neue Tests, die dort suchen, wo der jeweilige Fix noch
falsch sein könnte, nicht dort, wo er schon geprüft ist.

### T-001 — behoben, fünf neue Tests

Der geteilte Renderer trägt. Was ich zusätzlich geprüft habe und warum:

| Neuer Test | Was er ausschließt |
| --- | --- |
| alle zwölf Stile, Vorschau **und** Datei, gegen literale Erwartungen | ein Renderer, der nur die zwei Stile des Reproduzierers bedient; und zwei gleich kaputte Renderer, die sich einig sind |
| verdeckte Spalte zwischen zwei sichtbaren, mit eigenem Format | Paarung über den Index von `definition.columns` statt über die sichtbaren — jedes Format eine Spalte nach links, stiller falscher Wert |
| Multi-Event, zwei Formate, zwei Zeitzonen | `cell_formats`/`event.timezone` aus dem Schleifenkopf gezogen: eine Datei, in der eine von zwei Zeilen falsch ist |
| terminierter Export, geprüft am **Mailanhang** | der einzige Pfad, auf dem ein Formatfehler monatelang unbemerkt bleibt, weil es keine Vorschau gibt |
| XLSX mit **und** ohne Stil, inkl. Uhrzeit in der Zelle | der Zusatzfund von `exporter-dev`; und dass eine ungeformte Zahl eine Zahl bleibt |

Zum Zusatzfund (`as_spreadsheet_value`): **ja, der verdient die Erwähnung**, und
er hat sie bekommen — `docs/performance.md` 3.6b und ein eigener Absatz im
Blocker. Begründung: der auslösende Zustand ist nichts Exotisches (Datumsspalte +
XLSX + Zeitplan), und das Versagen war ein `TypeError` im Celery-Task, also fünf
Retries, „Internal Error" und danach ein Zeitplan, der aus der periodischen
Abfrage fällt. Mein Test prüft deshalb nicht nur, dass es nicht mehr kracht,
sondern **welche Uhrzeit** in der Zelle steht — Auckland-Event, 09:00 UTC, in der
Tabelle 21:00. Eine Tabellenzelle kann ihre Zone nicht nennen; UTC hineinzu-
schreiben wäre um zwölf Stunden falsch und völlig unauffällig.

**Eine bewusste Grenze, die ich nicht als Fehler führe, aber aufschreibe:** für
Spalten **ohne** gesetzten Stil ist die Vorschau weiterhin hübscher als die Datei
(`€23.50` gegen `23.50`, `No` gegen `False`, lokalisiert gegen ISO+Offset). Das
ist die dokumentierte Politik von `format_export_cell` und sie ist richtig
begründet (XLSX behält echte Zahlen, bestehende Dateien ändern sich nicht). Der
Satz „die Vorschau darf nicht schöner sein als der Export" gilt damit für
*gesetzte* Stile. Wer das später anders entscheidet, soll wissen, dass es eine
Entscheidung war.

### T-002 — behoben, drei neue Tests, Aufzählung statt Stichprobe

Die zwei Hälften des Fixes decken einander **nicht** ab — einzeln neutralisiert
fällt jede auf ihren eigenen Spalten aus, und genau das war die Frage. Statt den
einen Reproduzierer zu wiederholen, habe ich den Raum geschlossen: jedes Feld mit
`DataType.MONEY`, in jedem Aggregat, das es zulässt, in einer Zeile — vierzehn
Geldzellen neben `order.total` als Maßstab. Aus der Registry ermittelt, nicht
geraten; `join` bietet **kein** Geldfeld an (geprüft), `count` hat keine Skala,
auf Basis `orderposition` ist kein Geldfeld aggregierbar. Damit ist die
Aufzählung vollständig, nicht nur lang.

Zwei Ränder haben eigene Tests bekommen: die `AVG`-Rundung von `query-dev` (mit
umgangener Quantisierung: `Decimal("14.3333333333333")` — die Begründung ist
damit gemessen und nicht nur behauptet) und die Gegenrichtung, dass ein Aggregat
über *keine* Zeile `None` bleibt und nicht zu `0.00` wird.

### T-003 — behoben, Docstring nachgeführt

Reine Textnachführung wie angekündigt, plus die Begründung, warum „Ebenen" nicht
dasselbe ist wie „`join`-Spalten" (S-005-Fix). Nachgemessen: 151 bei 49.484
Zeilen, 4 bei 494 — unverändert, und das war nicht selbstverständlich, weil die
neue Dedup-Regel Ebenen zusammenlegen kann. Die beiden Spalten im Lasttest sind
wirklich verschieden.

### Zwei neue Befunde

* **T-004** (niedrig) — T-002 eine Datentypgrenze weiter: `aggregate_expression`
  hängt an `DataType.MONEY`, `DataType.DECIMAL` geht ungeschützt durch. Gemessen
  an `position.tax_rate`: Modellspalte `19.00`, Aggregat `19`. Beide Symptome von
  T-002. Kein Einzeiler, weil `DECIMAL` Felder verschiedener Skalen umfasst und
  die Registry heute keine deklariert — das ist eine Entscheidung für `query-dev`
  und `registry-dev`.
* **T-005** (niedrig) — der Export löst `event.timezone` je Zelle auf. Entstanden
  **mit** dem T-001-Fix. `Event.timezone` ist ein hierarkey-Lookup, kein Attribut.
  Gezählt: Grundlast 22, Aufschlag genau eine Auflösung je formatierter
  Datumszelle. Gemessen: CSV 11,6 s → 50,4 s (×4,4) auf 94.666 Zeilen, sobald
  drei Spalten einen Stil tragen. Fix ist eine Handvoll Zeilen bei `exporter-dev`
  (Zone einmal je Event auflösen, dort wo `_cell_formats()` ohnehin schon einmal
  je Event gebaut wird).

Beide mit `xfail(strict=True)`-Reproduzierer, gleiche Form wie T-001/T-002.

### Neu in `tests/test_performance.py`

* **XLSX in voller Größe** — in Welle 3 stand hier „nicht messbar". Das war zu
  kurz: nicht die Plattform kann kein XLSX, sondern `_render_xlsx` **ohne**
  `output_file`. Mit Dateihandle, also so wie pretix' Exportdienst schreibt,
  läuft es auch unter Windows. 94.666 Zeilen, 22 Spalten: **69,7 s, 8,4 MiB**,
  rund 15× langsamer als dieselbe Ausgabe als CSV. Damit ist eine offene Zeile
  aus `docs/performance.md` Abschnitt 5 geschlossen.
* **Was die Spaltenformatierung kostet** — derselbe Report, einmal ohne und
  einmal mit drei Stilen. Die Messung, aus der T-005 kommt. Zugesichert wird
  darin nur das Deterministische: ohne Stil liefert `_cell_formats()` `None` und
  der alte Weg ist buchstäblich der alte Weg. Die Laufzeit geht in die Tabelle
  und wird **nicht** assertiert — sie soll sich ändern, wenn T-005 behoben wird.

Laufzeit des Lastmoduls damit 110 s → 270 s. Weiterhin `-m performance`, wird von
`pytest -m "not performance"` sauber abgewählt (10 deselected).

### Läufe

```
pytest tests/test_integration.py -q                  -> 45 passed, 2 xfailed
pytest tests/test_integration.py -k finding -q       ->  2 passed, 2 xfailed
pytest tests/test_performance.py -m performance -q -s-> 10 passed (~270 s)
pytest -m "not performance" -q                       -> 1171 passed, 10 deselected,
                                                        2 xfailed
flake8 / isort -c / black --check (eigene vier)      -> rc 0
```

Die volle Suite viermal gelaufen, dreimal in zufälliger und einmal in fester
Reihenfolge, jedes Mal grün. Ein früherer Lauf meldete `1169 passed, 2 failed` —
das war `tests/test_security.py`, an der `security-reviewer` in genau diesem
Moment gearbeitet hat; der nächste Lauf über dieselbe Datei war grün. Nicht meine
Zahl und kein Befund, aber der Vollständigkeit halber notiert.

`tests/test_smoke.py::test_no_migration_created_yet` ist **nicht** mehr rot. Der
Repo-Stand hat damit zum ersten Mal keinen dauerhaft roten Test.

### Was ich bewusst nicht getan habe

* **Die Gegenproben nicht als dauerhafte Tests eingecheckt.** Sie hängen an
  `format_export_cell`, `MoneyField.from_db_value` und `aggregate_expression`,
  also an Interna fremder Module. Ein `monkeypatch` darauf ist als
  Verifikationsschritt richtig und als Dauertest eine Bremse für den nächsten,
  der dort umbaut. Die Ergebnisse stehen stattdessen wörtlich in den Docstrings
  der beiden Reproduzierer, damit ein späterer Fehlschlag als „der Fix wurde
  rückgängig gemacht" gelesen wird und nicht als „irgendein Formattest ist
  kaputt".
* **T-004 und T-005 nicht selbst behoben.** Beides ist Produktivcode in fremdem
  Gebiet.
* **PostgreSQL weiterhin nicht geprüft.** Unverändert Umgebungsentscheidung. Für
  T-002 ist die Frage kleiner geworden (`MoneyField` wirkt auf der Python-Seite
  und damit backend-unabhängig), für T-004 gilt sie voll.

### Nächster Schritt

1. **exporter-dev:** T-005, ein paar Zeilen, misst sich selbst — der Test dazu
   schlägt mit `XPASS` um, sobald es stimmt.
2. **query-dev + registry-dev:** T-004 entscheiden (Skala in `ReportField` oder
   `decimal_places` des Modellfeldes), nicht nur fixen.
3. **Orchestrator:** PostgreSQL-Lauf, vier Testmodule, unverändert offen.
