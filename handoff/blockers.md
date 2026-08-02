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
