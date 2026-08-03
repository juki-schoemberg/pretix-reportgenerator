# Blocker

Diese Datei ist für Dinge, die die Arbeit eines Agenten **stoppen**:
Contract-Änderungswünsche nach der Freigabe und kritische Sicherheitsbefunde.
Jeder Eintrag wird angehängt, nie überschrieben.

---

## 2026-08-02 — security-reviewer, Welle 3: kein Blocker

Der adversariale Review (`docs/security-review.md`, `tests/test_security.py`)
hat **keinen kritischen Befund** ergeben. Es gibt in diesem Stand keinen Pfad,
auf dem ein ORM-Pfad, ein Lookup oder ein Operator aus einer Definition, einer
Importdatei oder einem Editor-Request in ein Queryset gelangt; keinen, auf dem
ein Report Daten eines fremden Events oder Organizers liefert; und keinen
Endpunkt ohne Rechteprüfung.

Es sind sechs Befunde offen, drei davon *mittel*, drei *niedrig*. Keiner
blockiert eine laufende Arbeit, deshalb steht hier nur der Verweis:

| Nr. | Schwere | Zuständig | Kurz |
| --- | --- | --- | --- |
| S-001 | mittel | persistence-dev | `views/crud.py` ohne `PluginActiveMixin` |
| S-002 | mittel | exporter-dev | Organizer-Export ignoriert das Plugin-Gate |
| S-003 | mittel | portability-dev + frontend-dev | ungepaarte Surrogate → 500 auf drei Endpunkten, persistierbar über Import |
| S-004 | niedrig | persistence-dev | doppelter Identifier → IntegrityError statt Formularfehler |
| S-005 | niedrig | query-dev | eine Query pro `join`-Spalte in der Vorschau |
| S-006 | niedrig | portability-dev | `strategy=keep` per POST wählbar, überspringt `check_definition` |

Details, Reproduktion und Empfehlung je Befund: `docs/security-review.md`.

**Ausdrücklich kein Contract-Änderungswunsch.** S-003 ließe sich theoretisch in
`contracts.validate_definition` beheben; die Empfehlung geht bewusst an
`portability/payload.py` und die drei `ensure_ascii=False`-Stellen, weil die
Contracts eingefroren sind und die Behebung ohne sie vollständig möglich ist.

Zeitpunkt zum Beheben: **vor** dem Verdrahten von `urls.py`/`signals.py` durch
den `integrator`. Solange die Routen und die Exporter-Receiver nicht gesetzt
sind, sind S-001 und S-002 produktiv nicht erreichbar — danach sofort.

---

## 2026-08-02 — test-engineer, Welle 3: drei Findings, kein Blocker

Aus den Durchstich- und Lasttests (`tests/test_integration.py`,
`tests/test_performance.py`). **Keines blockiert die Welle.** Alle drei sind
Naht-Fehler zwischen zwei Agenten, die in keiner der Einzel-Testsuiten sichtbar
sein *konnten*: sie liegen genau dort, wo eine Suite endet und die nächste
beginnt, und jede beteiligte Suite ist für sich grün und hat recht.

Kein Produktivcode geändert. Zu jedem Finding gibt es einen Test, der
**fehlschlägt**. Die zwei Reproduzierer in `tests/test_integration.py` sind
`@pytest.mark.xfail(strict=True)`: der Fehlschlag ist damit dokumentiert und
jederzeit vorführbar, ohne dass die Suite dauerhaft rot ist und die nächste echte
Regression darin untergeht. Sie schlagen mit **XPASS** um, sobald jemand den
Fehler behebt — das ist der Moment, sie in normale Asserts umzuschreiben.

Fehlschlagen vorführen:

```bash
pytest tests/test_integration.py -k finding --runxfail
```

### T-001 (mittel) — `ColumnFormat` wirkt in der Vorschau, aber nicht im Export

*Betroffen:* `query/columns.py` (query-dev), `exporters.py` (exporter-dev),
`views/api.py` (frontend-dev). Dass niemand eindeutig zuständig ist, **ist** das
Problem.

`ColumnFormat.date_style`, `number_style` und `boolean_style` stehen im
eingefrorenen Contract, der Editor bietet sie je Datentyp an, und die Vorschau
wendet sie an (`views/api.py:1003-1015`, `test_preview_applies_the_column_format`).
Der Exportpfad wendet **keines** davon an: `query/columns.py` liest ausschließlich
`format.separator` (Zeile 395-396), `exporters.py` enthält bewusst gar keine
Renderlogik. Der Nutzer stellt „nur Datum" ein, sieht es in der Vorschau und
bekommt in der Datei den vollen Zeitstempel. Gemessen, zwei Stile, ein Ergebnis:

```
iso       -> ['"Order code","Order date"', '"AAAAA","2026-04-24 09:00:00+00:00"']
date_only -> ['"Order code","Order date"', '"AAAAA","2026-04-24 09:00:00+00:00"']
```

*Test:* `test_finding_a_column_format_chosen_in_the_editor_reaches_the_export`

*Vorgeschichte:* `frontend-dev` hat den Punkt in Welle 1 **und** Welle 2 als offene
Frage an `query-dev`/`exporter-dev` gestellt („Gleiches gilt für
`ColumnFormat.separator`, den heute der Compiler anwendet"). Für `separator` hat
der Compiler es übernommen, für den Rest niemand.

*Zu entscheiden, nicht nur zu fixen:*

1. **Formatierung in den Compiler** — eine Stelle, Vorschau und Export teilen
   sich den Code, `preview` hört auf, eine zweite Implementierung zu sein. Aber
   der Compiler liefert dann Strings statt `Decimal`/`datetime`, und XLSX
   verliert echte Zahlen und Datumswerte; genau davor warnt der Docstring von
   `NumberStyle.RAW`. Müsste also ausgabeformatabhängig sein.
2. **Ein expliziter Renderer im Exporter**, den `views/api.py` ebenfalls aufruft
   — Compiler bleibt typrein, XLSX behält seine Zahlen, Preis ist eine dritte
   Schicht. **Empfehlung.**

Was nicht bleiben darf, ist der Zustand von heute: die Vorschau darf nicht
schöner sein als der Export. Das ist wörtlich `frontend-dev`s eigene Regel.

### T-002 (mittel) — aggregierte Geldspalten verlieren ihre Nachkommastellen

*Betroffen:* `registry/annotations.py` (registry-dev), `query/columns.py`
(query-dev). Auf SQLite sichtbar, auf PostgreSQL vermutlich nicht — und das ist
der eigentliche Punkt.

In einer einzigen CSV-Zeile:

| Spalte | Feld | Wert |
| --- | --- | --- |
| Order total | `order.total` (Modellspalte) | `23.50` |
| Amount paid | `payment.sum_confirmed` (`Coalesce(Subquery(Sum(...)))`) | `20.5` |
| Sum of price | `position.price` mit `sum` | `23.5` |

*Ursache:* Djangos SQLite-Backend quantisiert einen `DecimalField`-Wert auf seine
`decimal_places` nur, wenn der Ausdruck ein `Col` ist; für `Subquery`, `Coalesce`
oder `Sum` reicht es den Rohwert durch
(`django/db/backends/sqlite3/operations.py::get_decimalfield_converter`).
PostgreSQL behält bei `SUM(numeric(13,2))` die Skala. **Derselbe Report erzeugt
auf zwei Installationen zwei verschiedene Dateien** — dieselbe Klasse von
Abweichung, die `query-dev` bei `nulls_last` bewusst abgefangen hat.

*Test:* `test_finding_an_aggregated_money_column_keeps_its_two_decimal_places`

*Warum keine Einzelsuite es finden konnte:* `Decimal("23.5") == Decimal("23.50")`
ist in Python `True`. Jeder Test, der Beträge als `Decimal` vergleicht — und das
tun alle bestehenden — bleibt grün. Sichtbar wird es erst an den *Zeichen* der
Exportdatei.

*Fix (Entscheidung bei registry-dev/query-dev):* entweder die Ausdrücke in
`registry/annotations.py` mit `Cast(..., DecimalField(max_digits=13,
decimal_places=2))` abschließen — ehrlicher, die Datenbank liefert dann, was das
Feld verspricht — oder die Money-Renderer in `query/columns.py` quantisieren, was
alle Wege auf einmal trifft und T-001 berührt.

### T-003 (niedrig/mittel) — die Query-Zusage für `join`-Spalten gilt nur je Chunk

*Betroffen:* Docstring von `query/columns.py` und die Query-Tabelle in
`handoff/status/query-dev.md` (query-dev). Verwandt mit S-005 des
`security-reviewer`, aber anderer Pfad: dort die Vorschau, hier der volle Export.

Zugesagt ist „genau eine Query pro Prefetch-Ebene, unabhängig von der
Zeilenzahl". Gemessen an 100.000 Positionen:

| Report | 494 Zeilen | 49.484 Zeilen |
| --- | --- | --- |
| 16 Spalten, Basis `order`, **mit** 2 `join`-Spalten | 4 | **151** |
| dieselben Spalten **ohne** die zwei `join` | 1 | 1 |

Exakt `1 + 3 × ceil(Zeilen / DEFAULT_CHUNK_SIZE)`.

*Und das ist kein Fehler im Entwurf:* `QuerySet.iterator(chunk_size=1000)` führt
`prefetch_related` je Chunk aus, und genau das hält den Speicher bei 6,4 MiB für
94.666 Zeilen (`docs/performance.md` 3.5). Es ist auch kein N+1 — die Kosten je
Zeile sinken mit der Größe (0,0081 → 0,0031 Queries/Zeile). **Falsch ist nur die
Zusage.** Wer sie liest, plant mit vier Roundtrips und bekommt hundertfünfzig;
auf einem PostgreSQL über Netzwerk ist das spürbar, und `join`-Spalten sind in
einem Teilnehmerreport der Normalfall.

*Test:* `tests/test_performance.py::test_a_join_column_costs_one_prefetch_per_chunk_not_one_per_row`
— **kein** xfail: er prüft das tatsächliche Verhalten als Gleichung, ist grün und
wird rot, sobald sich das Verhalten ändert.

*Empfehlung, aufsteigend nach Aufwand:* (1) Docstring korrigieren und die Formel
hinschreiben, drei Zeilen, Mindest-Fix; (2) im `help_text` der `join`-Spalte auf
die Kosten hinweisen bzw. den Editor warnen lassen, wenn ein Report mit
`join`-Spalten kein `row_limit` hat; (3) `StringAgg` auf PostgreSQL — bringt eine
Query, kostet aber zwei Codepfade und damit die Eigenschaft, dass beide Backends
dieselbe Datei erzeugen. (3) würde ich nicht tun, solange (1) offen ist.

### Offen, kein Finding: PostgreSQL bleibt ungeprüft

`registry-dev`, `query-dev`, `exporter-dev` und `frontend-dev` haben alle vier um
eine PostgreSQL-Gegenprobe gebeten. Sie hat **nicht** stattgefunden: die
Entwicklungsumgebung hat kein PostgreSQL (`pretix/src/pretix.cfg`:
`backend=sqlite3`), und einen Datenbankserver zu installieren liegt außerhalb
meines Dateibereichs und außerhalb dessen, was ein Agent ohne `sudo` tut.

| Stelle | Risiko |
| --- | --- |
| `Coalesce`/`Subquery`-Ausgabetypen bei Geld- und `count`-Aggregaten | Skalenabweichung, siehe T-002 |
| `nulls_last` in beiden Sortierrichtungen | zwei Installationen, zwei Dateien |
| `Cast(answer AS date)` in `computed.age.*` | auf PostgreSQL scheitert die **ganze** Query an einer kaputten Zeile |
| `Case`-Ausdrücke über Annotationsaliase | Ausdrucksauflösung |
| die 151 Roundtrips aus T-003 | Laufzeit statt Korrektheit |

Vorschlag an den Orchestrator: PostgreSQL-Container starten, `pretix.cfg` auf
`backend=postgresql` umstellen und **genau diese** vier Module laufen lassen —
`tests/test_registry.py`, `tests/test_query_compile.py`,
`tests/test_query_registry.py`, `tests/test_integration.py`. Umgebungsentscheidung,
keine Codeänderung.


---

# S-001 und S-002 verifiziert und geschlossen (security-reviewer, 2026-08-02)

Nachtrag zu den beiden Plugin-Gate-Befunden aus dem Wellen-3-Review. **Kein
neuer Blocker** — dieser Abschnitt schließt zwei alte und hält eine Nacharbeit
fest, die vor Welle 4 anfällt.

## Ergebnis

| Befund | Fix von | Datei | Status |
| --- | --- | --- | --- |
| S-001 CRUD-Views ohne Plugin-Gate | persistence-dev | `views/crud.py:98` (`PluginActiveMixin`) | behoben, verifiziert |
| S-002 Organizer-Export ohne Plugin-Gate | exporter-dev | `exporters.py:263` (`_plugin_is_active`), angewandt in `report_choices()` und `_prepare()` | behoben, verifiziert |

Beide Beweistests in `tests/test_security.py` tragen kein `xfail` mehr:

* `test_every_event_view_404s_when_the_plugin_is_off` — erweitert auf alle fünf
  Views inklusive `POST` auf add/edit/duplicate/delete, mit Nachprüfung der
  Tabelle. `ReportDuplicateView` ist POST-only und war deshalb vorher gar nicht
  abgedeckt (der Hinweis von `persistence-dev` war korrekt).
* `test_an_organizer_export_skips_events_with_the_plugin_switched_off` — Aufbau
  umgebaut, beide Events halten jetzt denselben Report und je eine Bestellung
  (Variante 1 aus dem Vorschlag von `exporter-dev`). Vorher hätte der Test nach
  dem Fix an einem `ExportError` grün ausgesehen, ohne das Leck je zu messen.

Neu dazu, weil beim Gegenlesen Lücken sichtbar wurden:
`test_no_crud_view_is_missing_the_plugin_gate` (Klassenhierarchie statt Routen),
`test_the_organizer_export_form_never_offers_a_switched_off_events_report`
(`report_choices()` ist die zweite Facette von S-002),
`test_the_permission_check_runs_before_the_plugin_gate_not_after`.

Gegenprobe für beide: mit zur Laufzeit neutralisiertem Gate (Wegwerf-Plugin,
**kein** Produktivcode angefasst) fallen beide Tests wieder genau am
ursprünglichen Leck — `200` statt `404` bzw. die Zeile
`"plain","Plain Event","OFFEV"` in der CSV.

Suitenstand: `pytest tests/test_security.py -q` → 128 passed, 6 xfailed.
`pytest tests -q -m "not performance"` → 1004 passed, 1 failed; der eine
Fehlschlag ist `test_smoke.py::test_no_migration_created_yet`, das vorbestehende
Welle-0-Gate.

## Eine Korrektur, die vor Welle 4 gehört (kein Sicherheitsproblem)

`handoff/status/persistence-dev.md` begründet den S-001-Fix damit, die MRO
stelle das Plugin-Gate **vor** die Rechteprüfung, es gebe deshalb „404 statt
403". Die MRO-Aussage stimmt; die Schlussfolgerung nicht.
`EventPermissionRequiredMixin` hat **kein** `dispatch`, sondern überschreibt
`as_view()` und wickelt die fertige View in `event_permission_required(...)`
(`pretix/control/permissions.py:81-91`). Der Rechte-Decorator läuft damit
außerhalb der Dispatch-Kette und **vor** jedem Mixin.

Gemessen: ein Nutzer ohne `event.settings.general:write` bekommt auf
`.../plain/customreports/reports/add/` **403**, nicht 404.

Kein Befund — beide Tore weisen ab, und diese Reihenfolge verrät einem
Unberechtigten nichts über den Plugin-Zustand. Aber die falsche Begründung darf
nicht bei der nächsten View wiederverwendet werden, und sie gilt wortgleich für
`views/api.py`, `views/portability.py` und `views/templates.py`. Festgenagelt in
`test_the_permission_check_runs_before_the_plugin_gate_not_after`.

## Nacharbeit an den `integrator` (Welle 4)

Das Plugin-Gate steht jetzt **dreimal wörtlich** im Repo (`views/api.py`,
`views/portability.py`, `views/crud.py`), plus die Organizer-Variante in
`views/templates.py` und `_plugin_is_active` im Exporter. Jede Duplikation war
einzeln begründet (keine Modulabhängigkeit zwischen Agentengebieten), in Summe
ist es eine Stelle, an der eine Divergenz unbemerkt bleiben kann. Vorschlag:
`views/_mixins.py` beim `integrator`. Bis dahin gilt: wer eines ändert, ändert
alle.

## Weiter offen

S-003 (mittel, `portability-dev` + `frontend-dev`), S-004, S-005, S-006
(niedrig) — unverändert, siehe `docs/security-review.md`. Die vier
`xfail(strict=True)`-Tests zu S-003 und je einer zu S-004 und S-006 stehen
weiterhin scharf.


---

# Welle 4 — ein roter Test in fremdem Gebiet (integrator, 2026-08-02)

**Kein Contract-Änderungswunsch, kein Sicherheitsbefund.** Der Eintrag steht
hier, weil er das DoD-Tor „Testsuite grün" blockiert und ich ihn laut Rolle
nicht selbst reparieren darf.

## Blockierend für „grün"

`tests/test_integration.py::test_an_event_copy_carries_its_reports_and_runs_them_in_the_copy`
(Zeilen 485-533, Eigentümer `test-engineer`) schlägt seit dem Verdrahten von
`event_copy_data` fehl:

```
AssertionError: assert 'sizes-2' == 'sizes'
tests\test_integration.py:516
```

Ursache: Der Test kopiert die Reports **zweimal**. Zeile 509
(`copy_event.copy_data_from(world.event)`) löst jetzt den Empfänger
`pretix_custom_reports.signals.copy_reports` aus, Zeile 511 ruft
`copy_reports_to_event(...)` danach noch einmal von Hand auf. Der zweite Lauf
findet den Identifier `sizes` belegt und vergibt `sizes-2`.

Nachgewiesen: mit zur Laufzeit abgehängtem Empfänger (Wegwerf-Plugin, kein
Produktivcode angefasst) läuft derselbe Test grün.

Der Test war korrekt, solange das Signal nicht verdrahtet war — sein Docstring
sagt das ausdrücklich („``event_copy_data`` equivalent"). Genau diesen Umbau
hat `portability-dev` in
`handoff/requests/erledigt/portability-dev-an-integrator-signals.md` Abschnitt 5
angekündigt. Entscheidung und Fix gehören `test-engineer`.

## Nicht blockierend, aber im selben Zug gefunden

`tests/test_exporters.py::registered` (Zeilen 71-96, Eigentümer `exporter-dev`)
verbindet dieselben zwei `dispatch_uid`s, die `signals.py` seit Welle 4
produktiv verbindet, und ruft im Teardown
`register_data_exporters.disconnect(dispatch_uid=...)` auf. Damit ist die
**Produktivverdrahtung** für den Rest der pytest-Session weg. Das fällt heute
nicht auf, weil jeder Test, der den Exporter braucht, die Fixture selbst
benutzt — aber jeder neue Test ohne sie prüft ab da einen abgeschalteten
Exporter. Vorschlag: im Teardown den Zustand wiederherstellen statt zu trennen,
oder eigene `dispatch_uid`s benutzen.

Details, alle weiteren offenen Punkte und die getroffenen Entscheidungen:
`handoff/status/integrator.md`.


---

# T-001 bis T-003 verifiziert und geschlossen, zwei neue Befunde (test-engineer, 2026-08-03)

Gegenprüfung der Fixes, die `exporter-dev`, `frontend-dev`, `registry-dev` und
`query-dev` zu meinen drei Findings aus Welle 3 geliefert haben. **Kein neuer
Blocker.** Dieser Abschnitt schließt drei Befunde und eröffnet zwei kleinere, die
beide beim Gegenprüfen entstanden sind — nicht beim Lesen des Fixes, sondern beim
Messen daneben.

Kein Produktivcode geändert. Geändert: `tests/test_integration.py`,
`tests/test_performance.py`, `docs/performance.md`, diese Datei.

## Ergebnis

| Befund | Fix von | Datei | Status |
| --- | --- | --- | --- |
| T-001 `ColumnFormat` nur in der Vorschau | exporter-dev + frontend-dev | `exporters.py` (`format_cell_value`, `format_export_cell`, `_cell_formats`), `views/api.py` (`get_cell_renderer`) | behoben, verifiziert |
| T-002 aggregierte Geldspalten ohne Skala | registry-dev + query-dev | `registry/annotations.py` (`MoneyField`), `query/relations.py` (`aggregate_expression`) | behoben, verifiziert |
| T-003 Query-Zusage für `join` | query-dev | Docstring `query/columns.py` | behoben, verifiziert |
| T-004 dasselbe wie T-002 für `DataType.DECIMAL` | — | `query/relations.py` | **neu, offen** |
| T-005 `event.timezone` je Zelle statt je Export | — | `exporters.py` | **neu, offen** |

Die beiden Reproduzierer in `tests/test_integration.py` tragen kein `xfail` mehr.
Die Assertions sind **unverändert** geblieben: ein Reproduzierer, von dem
nachgewiesen ist, dass er mit neutralisiertem Fix wieder umfällt, ist der beste
Regressionswächter, den ein Finding hinterlassen kann, und ihn in dem Moment
umzuschreiben, in dem er grün wird, wirft genau das weg.

## Wie geprüft wurde

Drei Stufen je Finding, in dieser Reihenfolge:

1. **Grün ohne `--runxfail`.** Beide meldeten vorher `XPASS(strict)`.
2. **Misst er noch das Ursprungsproblem?** Nicht „ist grün", sondern: derselbe
   Aufbau, dieselben Zellen, und die erwarteten Zeichenketten stehen jetzt
   zusätzlich literal im Test (`"2026-04-24"` statt nur „ungleich").
3. **Neutralisierte Gegenprobe.** Fix zur Laufzeit abgeschaltet (`monkeypatch`,
   kein Produktivcode angefasst), Test muss an *derselben* Stelle wieder fallen.
   Ergebnisse unten.

Dazu, weil ein Fix, der einen Reproduzierer grün macht, nicht dasselbe ist wie ein
Fix, der trägt: sieben neue Tests, die genau dort suchen, wo der jeweilige Fix
noch falsch sein könnte.

## T-001 — behoben

`ColumnFormat.date_style/number_style/boolean_style` wirken jetzt im Export.
Geteilter Renderer in `exporters.py`, den die Vorschau über
`views/api.py::get_cell_renderer()` mitbenutzt.

**Gegenprobe.** Mit `exporters.format_export_cell` zur Laufzeit durch die
Identität ersetzt, fällt der Reproduzierer wieder an derselben Assertion, und
zwar auf genau die zwei Zeilen, die im Wellen-3-Eintrag oben stehen:

```
iso       -> ['PAID1', '2026-04-24 09:00:00+00:00']
date_only -> ['PAID1', '2026-04-24 09:00:00+00:00']
```

**Wo ich gesucht habe, ob der Fix trägt** — fünf neue Tests, alle grün:

* **Alle zwölf Stile, nicht die zwei des Reproduzierers.** Fünf `DateStyle`, drei
  `NumberStyle`, drei `BooleanStyle` in einer Zeile, einmal durch `api/preview/`
  über HTTP und einmal durch `ListExporter` in eine CSV. Verglichen wird beides:
  Vorschau **gegen** Datei *und* beide gegen die ausgeschriebenen
  Erwartungswerte. Nur das erste wäre auch von zwei gleich kaputten Renderern
  erfüllt — das ist die Form, die T-001 hatte.
* **Verdeckte Spalten verschieben die Formate nicht.** `CompiledReport.columns`
  hat verdeckte Spalten schon entfernt, `definition.columns` nicht; eine Paarung
  über den falschen Index würde jedes Format eine Spalte nach links schieben.
  Stiller falscher Wert, kein Fehler. Der Test setzt die verdeckte Spalte
  **zwischen** die beiden sichtbaren und gibt ihr ein eigenes Format, damit ein
  Off-by-one doppelt auffällt.
* **Multi-Event: je Event das eigene Format und die eigene Zeitzone.** Zwei
  Events, derselbe Identifier, zwei Definitionen, Berlin und Auckland, derselbe
  Zeitpunkt. Ein Renderer, der die Serverzone oder das Format des ersten Events
  benutzt, liefert eine Tabelle, in der eine der beiden Zeilen falsch ist und
  nichts in der Datei das sagt.
* **Terminierter Export.** Das Format wird aus dem **Mailanhang** eines
  `ScheduledEventExport` gelesen, über den echten `run_scheduled_exports`. Für
  einen terminierten Report gibt es zum Laufzeitpunkt keine Vorschau, nur die
  Datei, die ankommt — der einzige Weg, auf dem ein Formatierungsfehler
  monatelang unbemerkt bleibt.
* **XLSX, mit und ohne Stil.** Deckt zugleich den Zusatzfund von `exporter-dev`
  ab (siehe unten).

**Zusatzfund von `exporter-dev`, hier auf Exportebene festgenagelt.** Ein
XLSX-Export einer Datumsspalte **ohne** Stil starb vorher an openpyxls Ablehnung
zeitzonenbehafteter `datetime`-Werte — kein `ExportError`, der die Spalte nennt,
sondern `TypeError` im Celery-Task, fünf Retries und „Internal Error", danach
fällt der Zeitplan aus der periodischen Abfrage. `as_spreadsheet_value()` behebt
das. Der Test dazu prüft nicht nur, dass es nicht mehr kracht, sondern **welche
Uhrzeit** in der Zelle steht: Auckland-Event, 09:00 UTC, in der Tabelle muss
21:00 stehen. Eine Tabellenzelle kann nicht sagen, in welcher Zone sie ist — UTC
hineinzuschreiben wäre um zwölf Stunden falsch und völlig unauffällig. Erwähnung
verdient hat der Fund, ja: er war eine Produktionsgefahr auf einem Pfad, den kein
Nutzer besonders konfigurieren muss (Datumsspalte + XLSX + Zeitplan), und er
steht jetzt in `docs/performance.md` 3.6b und hier.

**Bewusste Grenze, kein Restfehler, aber sie gehört aufgeschrieben.** Für Spalten
**ohne** gesetzten Stil ist die Vorschau weiterhin hübscher als die Datei,
gemessen an derselben Zeile:

```
Vorschau: ['CENT1', '€23.50', 'April 24, 2026, 9 a.m.',   'No']
Datei:    ['CENT1', '23.50',  '2026-04-24 09:00:00+00:00', 'False']
```

Das ist die dokumentierte Politik von `format_export_cell` („nur formatieren, was
die Definition ausdrücklich verlangt") und sie hat zwei gute Gründe: eine
XLSX-Zelle soll eine echte Zahl bleiben, und Dateien bestehender Reports sollen
sich nicht ändern. Ich halte das für richtig. Der ursprüngliche Satz „die Vorschau
darf nicht schöner sein als der Export" gilt damit für *gesetzte* Stile und nicht
für die Vorbelegung — wer das später anders entscheidet, soll wissen, dass es
eine Entscheidung war und kein Versehen.

Dasselbe in klein zwischen den Ausgabeformaten: eine Datumsspalte ohne Stil
schreibt in die CSV `2026-04-24 09:00:00+00:00` und in die XLSX
`2026-04-24 21:00` (Auckland). Beides ist vertretbar — die CSV ist eindeutig, die
Tabelle lokal —, aber es sind zwei Wanduhren für dieselbe Zelle desselben
Reports.

## T-002 — behoben

`MoneyField` mit `from_db_value` in `registry/annotations.py`, plus
`aggregate_expression` in `query/relations.py` für das nutzerwählbare Aggregat.

**Gegenprobe, beide Hälften einzeln.** Das ist der Punkt: die zwei Fixes decken
einander nicht ab, jeder fällt auf seinen eigenen Spalten aus.

```
                                       order.  payment.  SUM(position.
                                       total   sum_conf  price)
MoneyField.from_db_value entfernt  ->  23.50   20.5      23.50
aggregate_expression umgangen      ->  23.50   20.50     23.5
```

`order.total` bleibt in beiden Fällen richtig — es ist eine Modellspalte und war
nie betroffen. Genau deshalb steht es im Test daneben: es ist der Maßstab, an dem
sich die anderen *innerhalb derselben Zeile* messen lassen müssen.

**Vollständigkeit statt Stichprobe.** Ein Fix, der gegen seinen eigenen
Reproduzierer geprüft wird, beweist den Reproduzierer. Deshalb einmal die
Aufzählung: **jedes** Feld, für das die Registry `DataType.MONEY` deklariert, in
**jedem** Aggregat, das es zulässt, in einer Zeile — vierzehn Geldzellen plus
`order.total`. Aus der Registry ermittelt, nicht geraten:

| Weg | Spalten | Ergebnis |
| --- | --- | --- |
| Modellspalte | `order.total` | 23.50 |
| Registry-Ausdrücke | `payment.sum_confirmed`, `refund.sum_done`, `order.pending_sum`, `position.net_price` | alle zweistellig |
| Nutzeraggregate | `position.price`, `position.tax_value`, `item.default_price` × `sum`/`min`/`max`/`avg` | alle zwölf zweistellig |

`join` steht nicht in der Tabelle, weil **kein** Geldfeld es anbietet (geprüft,
nicht angenommen: `position.price` mit `join` scheitert schon in der
Kompilierung), und `count`/`count_distinct` nicht, weil eine Anzahl keine Skala
verlieren kann. Auf Basis `orderposition` ist kein Geldfeld aggregierbar. Damit
ist die Aufzählung geschlossen.

**Zwei Ränder, die dabei mitgeprüft sind:**

* `AVG` wird auf zwei Stellen quantisiert — die bewusste Entscheidung von
  `query-dev`. Mit umgangener Quantisierung liefert dieselbe Zelle
  `Decimal("14.3333333333333")`, dreizehn Stellen aus SQLites Float-Pfad, die
  PostgreSQL anders beantworten würde. Das ist das Argument, und jetzt ist es
  gemessen: die ungerundete Zahl ist nicht genauer, nur weniger vergleichbar.
  Eigener Test, damit die Entscheidung nicht in einer Tabelle untergeht.
* `SUM`/`MIN`/`MAX`/`AVG` über **keine** Zeile bleiben `None` und werden nicht zu
  `0.00`. Ein Konverter, der auf jedem Wert läuft, ist ein `if` davon entfernt,
  „diese Bestellung hat keine Positionen" in „diese Bestellung ist null Euro wert"
  zu verwandeln. `count` bleibt bewusst `0`.

## T-003 — behoben

Docstring in `query/columns.py` sagt jetzt `1 + Ebenen × ceil(Zeilen /
chunk_size)`. Nachgemessen: die Zahlen sind unverändert (151 bei 49.484 Zeilen, 4
bei 494), und das ist keine Selbstverständlichkeit — der S-005-Fix von `query-dev`
lässt sich Prefetch-Ebenen jetzt zwischen `join`-Spalten teilen, die dieselben
Zeilen holen. Die beiden Spalten im Lasttest sind wirklich verschieden, deshalb
bleibt es bei drei Ebenen. Der Docstring meines Tests zitierte noch die alte
Zusage; das ist nachgeführt, samt der Begründung, warum `Ebenen` nicht dasselbe
ist wie `join`-Spalten.

## T-004 (niedrig) — dasselbe wie T-002, eine Datentypgrenze weiter

*Betroffen:* `query/relations.py::aggregate_expression` (query-dev), mit
`registry-dev`. Gefunden beim Aufzählen der Geldpfade für die T-002-Prüfung.

Der T-002-Fix hängt an `DataType.MONEY`. `DataType.DECIMAL` geht durch dieselben
`Sum`/`Min`/`Max`/`Avg` mit dem einfachen `DecimalField` des Modells als
`output_field` — also genau in dem Zustand, in dem Geld vorher war.
`position.tax_rate` ist ein Kernfeld der Registry, der Editor bietet es mit allen
sechs Aggregaten an, und pretix deklariert es als
`DecimalField(max_digits=7, decimal_places=2)`
(`pretix/base/models/orders.py:2558`). Gemessen, eine Bestellung mit 19,00 % und
7,00 %:

```
Basis orderposition, Modellspalte  -> "19.00", "7.00"
Basis order, min/max/sum/avg       -> "7", "19", "26", "13"
```

Beide Symptome von T-002: eine Datei, die sich selbst widerspricht
(`Tax rate` = `19.00`, `Highest tax rate` = `19`), und zwei Installationen, die
sich widersprechen, weil PostgreSQL die Skala von `numeric(7,2)` durch `SUM`
behält.

*Test:* `test_finding_an_aggregated_decimal_column_keeps_its_scale`,
`xfail(strict=True)`.

*Warum nicht derselbe Einzeiler:* `MoneyField` darf zwei Nachkommastellen
festverdrahten, weil jede Geldspalte in pretix zwei hat. `DataType.DECIMAL`
umfasst Felder verschiedener Skalen, und die Registry deklariert heute keine. Zu
entscheiden ist also zwischen „Skala in `ReportField` mitführen" und „auf die
`decimal_places` des Modellfeldes quantisieren" — eine Entscheidung, kein Fix.

*Schwere niedrig:* ein Steuersatz ist kein Betrag, den jemand aufaddiert.

## T-005 (niedrig) — der Export löst `event.timezone` je Zelle auf

*Betroffen:* `exporters.py` (`_format_temporal`, `as_spreadsheet_value`),
exporter-dev. Entstanden **mit** dem T-001-Fix; keine Einzelsuite konnte es
sehen, weil es erst bei fünfstelligen Zeilenzahlen wehtut.

`Event.timezone` ist kein Attribut, sondern
`pytz_deprecation_shim.timezone(self.settings.timezone)`
(`pretix/base/models/event.py:233-235`) — ein hierarkey-Lookup über Event →
Organizer → globale Defaults. Der Renderer ruft ihn für **jede** Zelle auf, die er
anfasst.

Gezählt statt gestoppt, weil eine Zählung deterministisch ist:

| Report | 1 Zeile | 6 Zeilen |
| --- | --- | --- |
| Datumsspalte ohne Stil | 22 Auflösungen | 22 |
| dieselbe Spalte mit `date_only` | 23 | **28** |

Grundlast konstant, Aufschlag genau eine Auflösung je formatierter Datumszelle.
In Zahlen, auf 94.666 Zeilen × 22 Spalten: CSV **11,6 s → 50,4 s (×4,4)**, sobald
drei Spalten einen Stil tragen; rund 17 der 70 XLSX-Sekunden sind derselbe Lookup
in `as_spreadsheet_value`. Eine Auflösung kostet hier 178–345 µs, die Umrechnung,
die sie ermöglicht, 1,8 µs.

*Test:* `test_finding_the_export_resolves_the_event_timezone_once_not_once_per_row`,
`xfail(strict=True)`. Alle Zahlen in `docs/performance.md` 3.8.

*Fix:* die Zone einmal je Event auflösen — dort, wo `_cell_formats()` ohnehin
schon einmal je Event gebaut wird, und aus demselben Grund — und durchreichen.

*Kein Blocker:* jeder Wert ist richtig. Ein terminierter Export, der 50 statt 12
Sekunden braucht, kommt an. Es ist eine Handvoll Zeilen in fremdem Gebiet, deshalb
Finding und kein stiller Fix.

*Einschränkung:* der Absolutwert hängt am Cache-Backend; pretix'
Testeinstellungen benutzen `DummyCache`, produktiv steht dort Redis, und ein
Redis-Roundtrip ist nicht offensichtlich billiger. Konfigurationsunabhängig ist
die Form: ein Lookup je Zelle statt einem je Export.

## Weiter offen

* **PostgreSQL-Gegenprobe.** Unverändert, Umgebungsentscheidung. Für T-002 ist die
  Frage kleiner geworden (der Fix wirkt auf der Python-Seite und damit
  backend-unabhängig), für T-004 gilt sie voll.
* **T-004 und T-005**, beide mit `xfail(strict=True)`-Reproduzierer.
* `tests/test_smoke.py::test_no_migration_created_yet` — weiterhin der einzige
  rote Test des Repos, gehört dem `integrator`.
