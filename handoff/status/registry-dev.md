# Status: registry-dev — Welle 1

## Erledigt

**1. Handgepflegte Kernfeldtabelle** (`pretix_custom_reports/registry/core.py`,
`computed.py`). Keine `Model._meta`-Introspektion, keine Ausschlussliste — jedes
Feld ist eine Zeile, die jemand geschrieben hat.

- 80 Felder auf Basis `order`, 83 auf Basis `orderposition`
- Quellen laut `SPEC.md` F4 vollständig: `Order` (21), `InvoiceAddress` (12),
  `OrderPosition` (19/21), `Item`+`ItemCategory` (5), `ItemVariation` (1),
  `SubEvent` (5), `Seat` (4/5), `Voucher` (3), `Discount` (1),
  `OrderPayment`/`OrderRefund` aggregiert (4), `Checkin` aggregiert (3),
  `computed.*` (2)
- Alle **48** `required_field_keys.core` aus
  `tests/fixtures/definitions/_index.json` sind auf **beiden** Basen vorhanden
  (Test `test_required_core_keys_resolve`)
- Nicht freigegeben, mit Begründung in ADR 0002 Abschnitt 1: Secrets/Nonces,
  `meta_info` (JSON in `TextField`), die redundanten `organizer`-FKs, alle
  `*_includes_rounding_correction`-Spalten. Negativ getestet
  (`test_internal_columns_are_not_exposed`).

**2. Dynamische Fragen-Felder** (`registry/questions.py`), adressiert über
`Question.identifier`.

- Typabbildung aus `Question.type` inkl. der zwei bewusst „falschen" Fälle
  (Zahl und Datetime als `string`, Begründung ADR 0002 Abschnitt 2.1)
- Booleans werden in SQL zu echten Booleans normalisiert, damit
  `{"operator": "exact", "value": true}` aus `orderposition_questions.json`
  überhaupt matchen kann (Test `test_boolean_answer_is_a_real_boolean`)
- Choice-Fragen liefern ihre Optionen **lazy** über `choices`, mit
  `ValueScope.EVENT` (Namensauflösung beim Import)
- **Fallback-Strategie** für Fragen ohne verwendbaren Identifier:
  überspringen + melden, kein Umschreiben. Realistischer Fall ist `__` im
  Identifier. `registry.diagnostics(event, base)` nennt Key, Quelle, Grundcode
  und Detailtext (Test
  `test_question_with_double_underscore_is_skipped_and_reported`)
- `resolve()` ist für `answer.*` case-insensitiv (ADR 0001 Abschnitt 3.2), für
  Kern-Namespaces exakt
- Umbenennen einer Frage verschiebt den Key sofort und korrekt
  (`test_renaming_a_question_moves_its_key`)

**3. Berechnete Felder über Annotationen** (`registry/annotations.py`) — alle
fünf verlangten, alle als korrelierte `Subquery` nach dem Vorbild von
`Order.annotate_overpayments`, kein Aggregat über gejointe Pfade:

| verlangt | Key |
| --- | --- |
| offener Betrag | `order.pending_sum` |
| Zahlungsstatus im Klartext | `computed.payment_state` (+ `computed.order_status_label`) |
| Anzahl Positionen | `order.position_count` |
| erster/letzter Check-in | `checkin.first_datetime` / `checkin.last_datetime` (+ `checkin.count`) |
| Alter zum Veranstaltungsdatum | `computed.age.<identifier>` je Datums-Frage |

Zusätzlich `payment.sum_confirmed`, `refund.sum_done`, `payment.last_datetime`,
`position.net_price`, `meta.event.<name>`. Alle Aliase mit `pcr_`-Präfix, damit
sie nicht mit `payment_sum`/`pending_sum_t`/`pcnt` von pretix kollidieren. Die
Zustandsmengen für Geldsummen sind wörtlich die von pretix.

Verifiziert nicht nur strukturell: `test_all_annotations_execute` baut je Basis
ein Queryset mit **allen** annotierten Feldern, wertet es aus und prüft die
Werte. Alter, Zahlungsstatus (alle vier Zustände), Boolean-Antworten und
Datumsvergleiche haben eigene SQL-Tests.

**4. Fremdplugin-Signal** `register_report_fields` als `EventPluginSignal`
(`registry/signals.py`).

- Namespace: ausschließlich `plugin.<django_app_label>.<name>`
- Kollisionsregel: Kern gewinnt; zwischen zwei Plugins gewinnt das erste
  (reproduzierbar, weil pretix Empfänger deterministisch sortiert)
- Sechs Ablehnungsgründe mit stabilen Codes, jeweils Log-Warnung **und**
  Diagnostics-Eintrag: `reserved_namespace`, `wrong_provider`,
  `unsupported_base`, `duplicate_key`, `not_a_field`, `receiver_failed`
- `send_robust`: ein kaputtes Fremdplugin nimmt den Editor nicht mit
- **Mit Beispielplugin getestet** (`tests/test_registry_signal.py`, 21 Tests):
  vollständiges Plugin mit echter, ausgeführter Annotation. Es liefert
  `plugin.pretix_demo.demo_value` und schließt damit die Golden Fixture
  `plugin_and_meta_fields.json` (ohne Plugin fehlt genau dieser eine Key, mit
  Plugin keiner). Alle 15 Kern-Namespaces sind einzeln negativ getestet.

**5. Caching pro Event mit Invalidierung** (`registry/cache.py`), Strategie in
`docs/adr/0002-registry.md` Abschnitt 7.

- Prozesslokales LRU-Dict (max. 128 Einträge) für die Felder — muss lokal sein,
  weil `ReportField` Closures enthält und die nicht picklebar sind
- Gültigkeitstoken in `django.core.cache` aus Event-Token, Organizer-Token und
  `event.plugins`; Invalidieren = Token löschen, damit alle Prozesse es merken
- `post_save`/`post_delete`-Empfänger auf `Question` (strukturrelevant) sowie
  `Item`, `ItemCategory`, `SubEvent`, `Discount` (Vorsorge) und
  `EventMetaProperty` (organizerweit)
- Entwurfsauflage, auf der die Strategie ruht: alles Volatile liegt hinter
  `choices`/`annotation`-Callables, nicht in der Feldstruktur — deshalb muss ein
  Produkt-Edit nichts invalidieren (`test_choices_are_not_cached`)
- Beide degradierten Backends sind getestet: DummyCache (pretix-Testdefault)
  baut immer neu und ist dabei korrekt; `MAX_AGE = 120` begrenzt ein nicht
  geteiltes Backend

**6. Sonstiges**

- `registry/hints.py`: die Naht zu `query-dev` für Aggregat-Bedingungen
  (`all_positions` enthält stornierte Positionen; eine Antwortspalte muss auf
  ihre Frage eingeschränkt werden). JSON-fähige Primitive in `extra` plus eine
  Funktion, die daraus ein `Q` baut — `extra` bleibt `json.dumps`-fähig, weil die
  Editor-API Felder serialisiert (`test_aggregate_hints_are_json_safe`).
- `registry/diagnostics.py`: `RegistryDiagnostics` für die Debug-Ansicht aus
  `SPEC.md` P2.
- `registry/groups.py`: 13 Gruppenlabels plus eine Anzeigereihenfolge, damit
  `frontend-dev` sie nicht erfinden muss.
- `registry/__init__.py` bleibt importfrei (ADR 0000 Abschnitt 9 Punkt 3). Der
  Einstiegspunkt ist
  `from pretix_custom_reports.registry.library import field_registry`.

## Nicht erledigt (und warum)

- **`meta.subevent.*`, `meta.item.*`, `meta.variation.*`** — nicht implementiert.
  Nur `meta.event.*` ist konstant je Report und damit ohne Join und ohne die
  Default-Asymmetrie abbildbar (ADR 0002 Abschnitt 3.3).
  `required_field_keys` verlangt sie nicht. Nachrüstbar ohne Contract-Änderung.
- **`payment.providers` als DB-Ausdruck** — bleibt eine Python-Spalte, weil
  `StringAgg` PostgreSQL-only ist und pretix auch auf SQLite läuft. Damit nicht
  filter-/sortierbar, konsistent mit `_index.json` (`not_sortable`).
- **`docs/extending.md`** — nicht geschrieben, gehört dem `integrator`. Der
  fertige, gegen den Test abgeglichene Text liegt kopierfertig in
  `handoff/requests/registry-dev-an-integrator-signals.md` Abschnitt 3.
- **Debug-View für die Feldliste** (`SPEC.md` P2) — `views/` gehört mir nicht.
  Die Datenbasis dafür ist fertig: `registry.keys()`, `registry.diagnostics()`,
  `groups.GROUP_ORDERING`.
- **PostgreSQL-Verifikation** — die Testumgebung läuft auf SQLite
  (`pretix.testutils.settings`). Zwei Stellen sind deshalb *nicht* gegen
  PostgreSQL bewiesen: der `Cast(answer AS date)` in `computed.age.*` (durch
  einen Regex-Filter abgesichert, Restrisiko in ADR 0002 Abschnitt 3.2 benannt)
  und die `Case`-Ausdrücke mit `Q()` auf Annotationsaliasen. **Bitte in Welle 3
  gegen die echte Dev-Datenbank laufen lassen** — `test_all_annotations_execute`
  und `test_age_at_event_date_is_computed_in_the_database` sind die relevanten
  Tests.

## Getroffene Entscheidungen

Alle in **`docs/adr/0002-registry.md`** (neue Datei, Nummer 0002 beansprucht —
falls ein paralleler Agent dieselbe Nummer gewählt hat, muss der `integrator`
beim Merge umnummerieren):

| Abschnitt | Entscheidung |
| --- | --- |
| 1 | handgepflegte Tabelle statt Introspektion; welche Felder bewusst fehlen |
| 2 | Fragen über `identifier`; Fallback = überspringen + melden, kein Mangling; Case-Insensitivität nur für `answer.*` |
| 2.1 | Datentyp-Kompromiss bei Antworten (Zahl/Datetime als `string`); keine Antwortfilter auf Basis `order` |
| 3 | `pcr_`-Aliasnamensraum; ein gemischtes `annotate()`; Reihenfolge bedeutungstragend |
| 3.1 | `payment_state` liefert Codes, `order_status_label` Wörter |
| 3.2 | Alter zum Eventdatum in SQL; PostgreSQL-Cast-Risiko und Absicherung |
| 3.3 | Meta-Properties als Konstante |
| 4 | `payment.providers` als Python-Spalte |
| 5 | Aggregat-Hinweise JSON-safe in `extra` + `hints`-Funktion für das `Q` |
| 6 | Signal, Namespace, Kollisionsregel, `send_robust` |
| 7 | Cache-Invalidierung (Zwei-Schichten-Token), inkl. vier verworfener Alternativen |
| 8 | kein Registry-Aufbau ohne Event, kein `scopes_disabled()` in der Registry |

## Contract-Abweichungen

**KEINE.** `pretix_custom_reports/contracts/` ist unangetastet. Die Registry ist
an drei Stellen **enger** als der Contract erlaubt hätte — das ist keine
Abweichung (`filter_operators` ist laut Contract „the single source of truth at
run time"), aber es sind Entscheidungen, die jemand kennen muss:

1. Antwort-Felder haben auf Basis `order` `filter_operators=()`.
2. `computed.order_status_label` hat `filter_operators=()`.
3. Antwort-Felder benutzen nicht `DEFAULT_OPERATORS[datatype]`, sondern eine
   eigene Tabelle ohne die relativen Datumsoperatoren.

Alle drei mit Begründung in ADR 0002 Abschnitte 2.1 und 3.1.

Eine Ungenauigkeit in fremdem Gebiet, nur zur Kenntnis: der Platzhalterkommentar
in `pretix_custom_reports/signals.py` Zeile 60-61 sagt, das Signal sei „declared
in contracts/". `contracts/protocols.py` deklariert nur den **Namen**
(`REGISTER_FIELDS_SIGNAL_NAME`); das Signalobjekt liegt in
`registry/signals.py`. Ein Test bindet beide aneinander. Korrektur des Kommentars
steht im Request an den `integrator`.

## Offene Anforderungen an andere

1. **`handoff/requests/registry-dev-an-integrator-signals.md`** —
   *eine Import-Zeile ist Pflicht.* `pretix_custom_reports/signals.py` (oder
   `apps.py:ready()`) muss `registry.cache` importieren, sonst sind die
   Invalidierungs-Empfänger erst verbunden, wenn irgendwer die Registry berührt.
   Eine vorher umbenannte Frage invalidiert dann nichts. Das ist der einzige
   Weg, auf dem der Cache falsche Daten liefern kann. Enthält außerdem den
   fertigen Text für `docs/extending.md` und die Liste der neuen Strings für den
   `de`-Katalog.

2. **`handoff/requests/registry-dev-an-query-dev-annotationen-und-aggregate.md`**
   — drei Verhaltensweisen, die der Stub aus Welle 0c strukturell nicht zeigen
   kann (er liefert `{alias: None}`):
   - alle Annotationen in **ein** `annotate()` mischen, Reihenfolge erhalten
   - `hints.aggregate_filter(field, include_canceled_positions=...)` als
     `filter=` an jedes Aggregat über `all_positions`. **Ohne das ist das
     Ergebnis keine Exception, sondern eine falsche Zahl** (stornierte
     Positionen mitgezählt, Antwortspalten mischen alle Fragen).
   - vier Python-Spalten brauchen `prefetch_related` **und** ein `chunk_size` in
     `iterator()`

Keine Blocker. `handoff/blockers.md` wurde nicht angelegt.

## Tests

`pytest tests/test_registry*.py -q` → **127 passed, 0 failed** (mehrfach mit
verschiedenen Random-Seeds von `pytest-randomly`).

| Datei | Tests | Inhalt |
| --- | --- | --- |
| `tests/test_registry.py` | 90 | Vollständigkeit (alle Golden Fixtures, alle 48 Keys), Dichtheit (keine internen Spalten, keine geschmuggelten ORM-Pfade), Contract-Invarianten über *jedes* Feld, Fragen/Meta/Computed, SQL-Ausführung |
| `tests/test_registry_signal.py` | 21 | Beispielplugin, Namespace, Kollisionsregel, sechs Ablehnungsgründe |
| `tests/test_registry_cache.py` | 16 | Treffer/Fehltreffer, sechs Invalidierungspfade, Grenzen, degradierte Backends |
| `tests/test_registry_support.py` | 0 | geteilte Fixtures und Helfer |

Explizit die von der Definition of Done verlangten:

- **Alle Golden Fixtures lösen auf:** `test_valid_fixture_resolves_completely`
  (parametrisiert über alle 10 gültigen Fixtures) und
  `test_valid_fixture_usage_is_permitted` (prüft zusätzlich Aggregat-Pflicht,
  erlaubte Aggregate, erlaubte Operatoren, Sortierbarkeit — also die Stufe-2-
  Prüfungen, damit eine Fixture nicht auflösen und dann trotzdem an der Registry
  scheitern kann).
- **Ein `invalid/`-Key ist nicht auflösbar:**
  `test_key_from_invalid_fixture_does_not_resolve` gegen
  `invalid/unknown_field_key.json` — beide Keys (`order.does_not_exist`,
  `answer.question-that-was-renamed`) sind strukturell einwandfrei und werden
  nur von der Registry abgelehnt. Zusätzlich
  `test_smuggled_orm_path_does_not_resolve` gegen die Keys aus
  `invalid/smuggled_orm_path.json`.
- **Signal-Erweiterung mit Beispielplugin:** die 21 Tests in
  `test_registry_signal.py`, inkl. `test_plugin_annotation_runs` (die Annotation
  des Beispielplugins wird gegen die DB ausgeführt).

Gesamtsuite `pytest tests/ -q`: **562 passed, 1 failed**. Der Fehlschlag ist
`tests/test_smoke.py::test_no_migration_created_yet` und gehört nicht zu mir —
er bricht, weil `persistence-dev` parallel `migrations/0001_initial.py` angelegt
hat. Der Smoke-Test aus Welle 0a muss vom `integrator` oder `test-engineer`
angepasst werden.

Lint über die eigenen Dateien, alle drei sauber:

```
flake8 pretix_custom_reports/registry tests/test_registry*.py   -> rc 0
isort -c  (dieselben Pfade)                                     -> rc 0
black --check (dieselben Pfade)                                 -> 17 files unchanged
```

Kein `black .` / `isort .` über das Repo, kein `git commit`, keine Datei außerhalb
von `pretix_custom_reports/registry/**`, `tests/test_registry*.py`,
`docs/adr/0002-registry.md` und `handoff/**` angefasst.

## Nächster Schritt

1. **Orchestrator:** den Pflicht-Import aus
   `handoff/requests/registry-dev-an-integrator-signals.md` einplanen — er ist
   die einzige Zeile, ohne die der Cache falsch liegen kann.
2. **query-dev:** die drei Punkte aus dem Request an ihn abgleichen; Punkt 2
   (Aggregat-`filter=`) ist der, der stille Zahlenfehler verursacht.
3. **Welle 3 (`test-engineer`/`security-reviewer`):** die Registry-Tests gegen
   PostgreSQL laufen lassen (SQLite hat den `Cast`-Pfad nicht abgedeckt) und
   `registry/core.py` gegen die Feldlisten in `docs/pretix-api-notes.md` 6.1–6.11
   Zeile für Zeile prüfen — genau dort würde ein zu weit geöffnetes Feld sitzen.
4. **frontend-dev:** `groups.GROUP_ORDERING`/`GROUP_LABELS` und
   `registry.diagnostics()` sind da; eine Debug-Ansicht der Feldbibliothek
   (`SPEC.md` P2) braucht nur noch die View.

---

# Nachtrag: T-002 — aggregierte Geldspalten verlieren ihre Nachkommastellen

Auftrag des Orchestrators nach dem Review, Nacharbeitsrunde nach Welle 4.
Geändert wurden genau zwei Dateien: `pretix_custom_reports/registry/annotations.py`
und die neue `tests/test_registry_money.py`. Dazu ein Request an query-dev.

## Befund und Abweichung von der vorgeschlagenen Lösung

Die Entscheidung des Orchestrators — Korrektur in `registry/annotations.py` statt
in `query/columns.py` — habe ich übernommen. Den vorgeschlagenen *Mechanismus*
nicht, weil er nachweislich nicht wirkt.

`Cast(ausdruck, output_field=DecimalField(max_digits=13, decimal_places=2))`
behebt das Problem nicht. Gemessen mit einer Wegwerf-Sonde gegen SQLite,
Django 5.2.16:

```
PROBE a_plain    = Decimal('20.5')   str='20.5'
PROBE a_cast     = Decimal('20.5')   str='20.5'    <- Cast um das Coalesce
PROBE a_cast_sub = Decimal('20.5')   str='20.5'    <- Cast um das Subquery
```

Zwei unabhängige Gründe:

1. `DecimalField.cast_db_type()` liefert auf SQLite `decimal`. Das ergibt
   NUMERIC-Affinität, und die wirft die nachlaufende Null genauso weg.
2. Wichtiger: die Skala geht gar nicht im SQL verloren, sondern im
   *Converter*. `get_decimalfield_converter` quantisiert nur, wenn
   `isinstance(expression, Col)`. Ein `Cast` ist ein `Func`, kein `Col`, landet
   also im selben `else`-Zweig wie das `Subquery` vorher. Ein `Round(x, 2)` hätte
   dasselbe Schicksal.

## Was stattdessen gebaut wurde

`annotations.MoneyField`, eine `DecimalField`-Unterklasse mit `from_db_value`,
die auf `decimal_places` quantisiert. Die Kette dahinter, im Django-Source
verifiziert statt erinnert:

* `Field.get_db_converters` (`django/db/models/fields/__init__.py:919`) gibt
  `[self.from_db_value]` zurück, sobald ein Feld die Methode definiert.
* `BaseExpression.get_db_converters` (`django/db/models/expressions.py:202`)
  hängt die Converter seines `output_field` an die des Backends an.

Damit quantisiert *jeder* Ausdruck, der dieses `output_field` trägt, auf jedem
Backend — ohne eine einzige Vendor-Abfrage im Plugin-Code. Das ist der Punkt: die
Zusage gilt durch Konstruktion, nicht dadurch, dass jemand an SQLite gedacht hat.
Auf PostgreSQL, wo der Wert schon stimmt, ist die Quantisierung ein No-op.

Der Ausreißer `InvalidOperation` (Summe breiter als `max_digits`) wird in einem
passend dimensionierten Context erneut quantisiert, statt die Query zu sprengen —
sonst wäre das Ergebnis wieder backend-abhängig, nur auf andere Art.

## Geänderte Annotationen

| Annotation | Feld-Key | Was ergänzt wurde |
| --- | --- | --- |
| `payment_sum_annotation` | `payment.sum_confirmed` | `output_field=_money()` am `Coalesce`, jetzt über `_payment_sum_coalesced()` |
| `refund_sum_annotation` | `refund.sum_done` | `output_field=_money()` am `Coalesce` |
| `_pending_sum_expression` | `order.pending_sum` | `_as_money(...)` um die Kombination, `output_field` an beiden inneren `Coalesce` |
| `payment_state_annotation` | `computed.payment_state` | benutzt jetzt dasselbe `_payment_sum_coalesced()` und dasselbe `_pending_sum_expression()` |
| `net_price_annotation` | `position.net_price` | `_as_money(F("price") - F("tax_value"))` |

`_payment_sum_coalesced()` ist neu und existiert nur, damit die beiden Aufrufer
denselben Ausdruck bauen — die Merge-Zusage aus dem Modul-Docstring wäre sonst
davon abhängig gewesen, dass jemand zwei Codestellen gleich pflegt.

`net_price_annotation` war im Review nicht genannt und hat dasselbe Problem:
`F() - F()` ist eine `CombinedExpression` und damit ebenfalls kein `Col`.
`23.50 - 3.50` kam als `20` heraus.

**Nicht angefasst**, weil ohne Skalenproblem: `position_count_annotation` und
`checkin_count_annotation` (`Count`), `payment_last_annotation`,
`checkin_first_annotation`, `checkin_last_annotation` (Datum/Zeit),
`answer_annotation`, `age_at_event_annotation`, `meta_annotation`,
`status_label_annotation`.

## `max_digits` / `decimal_places` gegen pretix verifiziert

Nicht aus dem Gedächtnis, sondern Feld für Feld im installierten Source
(`../pretix/src/pretix/base/models/orders.py`, pretix 2026.6.0):

| Feld | Zeile | Deklaration |
| --- | --- | --- |
| `Order.total` | 266 | `decimal_places=2, max_digits=13` |
| `OrderPayment.amount` | 1764 | `decimal_places=2, max_digits=13` |
| `OrderRefund.amount` | 2182 | `decimal_places=2, max_digits=13` |
| `AbstractPosition.price` | 1525 | `decimal_places=2, max_digits=13` |
| `OrderPosition.tax_value` | 2571 | `max_digits=13, decimal_places=2` |

Alle fünf identisch. Die Werte liegen jetzt als `MONEY_MAX_DIGITS` /
`MONEY_DECIMAL_PLACES` an einer Stelle, und
`test_the_money_scale_matches_the_pretix_model_fields` liest sie zur Laufzeit aus
`Model._meta` zurück. Sollte pretix je auf vier Nachkommastellen gehen, fällt das
auf, bevor ein Report anfängt, fremdes Geld zu runden.

## Tests

Neu: `tests/test_registry_money.py`, 12 Tests, alle grün. Jede Zusicherung geht
über die *Zeichen* des Werts (`str(value)` bzw. `-as_tuple().exponent`), nie über
den Zahlenwert — `Decimal("23.5") == Decimal("23.50")` ist in Python `True` und
ist genau der Grund, warum die bestehende Suite das Problem ein ganzes Projekt
lang übersehen hat.

* jede aggregierte Geldspalte einzeln, parametrisiert über beide Basen
* eine Zeile im Vergleich: `order.total` (Spalte) und die vier annotierten
  Beträge müssen dasselbe Format drucken, inklusive `20.00` als
  Doppelnull-Ernstfall bei `position.net_price`
* der Nullfall (`Coalesce(..., 0.00)` muss `0.00` sein, nicht `0`)
* `computed.payment_state` emittiert seine beiden Geld-Aliase mit Skala — der
  Test, der anschlägt, wenn nur eine der zwei Aufrufstellen gefixt wäre
* der gemergte Mapping-Fall, damit die Alias-Kollaps-Zusage weiter hält
* `MoneyField` als Unit-Test ohne Datenbank, inklusive `float`-Eingabe,
  Negativwert und dem zu breiten Wert

Gegenprobe, dass die Tests nicht leer laufen: mit `git stash` auf der alten
`annotations.py` fallen alle 12 um.

```
pytest tests/test_registry_money.py -q          -> 12 passed
pytest -m "not performance" -q                  -> 1092 passed, 4 xfailed, 6 failed
```

Die sechs Fehlschläge sind fremd und gleichzeitig entstanden: fünf
`XPASS(strict)` auf S-003, S-004, S-006 und T-001 (andere Agenten haben ihre
Findings behoben, die Marker sind noch drin) plus
`test_a_report_full_of_join_columns_costs_one_query_per_column` (T-003,
query-dev). Keiner davon liegt in meinem Gebiet, keiner hängt an dieser Änderung.

## Rest von T-002 liegt bei query-dev

`tests/test_integration.py::test_finding_an_aggregated_money_column_keeps_its_two_decimal_places`
bleibt xfail — jetzt aber nur noch wegen einer Spalte:

```
AssertionError: ['23.50', '20.50', '23.5'] == ['23.50', '20.50', '23.50']
```

Spalte 1 (`order.total`) war immer richtig, Spalte 2 (`payment.sum_confirmed`) ist
durch diese Änderung richtig geworden. Spalte 3 ist `("position.price", "sum")`,
also das vom Nutzer gewählte Aggregat aus `AGGREGATE_FUNCTIONS` in
`query/relations.py`. Das ist query-devs Datei; Details, Nachweis und ein
konkreter Vorschlag liegen in
`handoff/requests/registry-dev-an-query-dev-t002-restspalte.md`. `MoneyField` und
die beiden Konstanten sind dafür aus `annotations.__all__` exportiert.

## Lint

Nur über die eigenen zwei Dateien, kein `black .` / `isort .` über das Repo:

```
black    pretix_custom_reports/registry/annotations.py tests/test_registry_money.py -> reformatiert, danach clean
isort    (dieselben Pfade)                                                          -> rc 0
flake8   (dieselben Pfade)                                                          -> rc 0
```

Kein `git commit`. Außerhalb von `pretix_custom_reports/registry/**`,
`tests/test_registry*.py` und `handoff/**` wurde nichts angefasst.
