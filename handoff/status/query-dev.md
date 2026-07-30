# Status: query-dev — Welle 1

`pretix_custom_reports/query/**` (2.3 kLOC) und `tests/test_query*.py`
(252 Tests). Contracts unangetastet, kein Commit, keine fremde Datei angefasst.

> **Hinweis zur Sitzung.** Die Arbeit lief in zwei Läufen; der erste wurde mitten
> in einer Selbstkorrektur vom Ausgabenlimit abgebrochen. Der zweite Lauf hat die
> zwei dabei gefundenen Fehler behoben — sie stehen unten in einem eigenen
> Abschnitt, weil einer davon still falsche Zahlen produziert hat und für
> spätere Wellen interessant ist.

## Erledigt

**1. Compiler in zwei Pässen** (`query/plan.py`, `query/report.py`,
`query/compiler.py`).

`ReportQueryCompiler(registry).compile(definition, event, preview=False, now=None)`
erfüllt das `QueryCompiler`-Protokoll; die Zusatzargumente haben Defaults, ein
gegen das Protokoll typisierter Aufrufer merkt nichts davon.

- **Pass 1** (`build_plan`) löst jeden Key über die Registry auf, prüft alles, was
  nur die Registry weiß, und baut einen `QueryPlan`. **Kein Queryset.**
- **Pass 2** (`build_report`) legt den Plan auf Querysets.
- `check_definition(definition, event, registry)` ist die billige Hälfte von
  Pass 1: kein Ausdruck, kein Annotation-Callable, keine DB, kein
  django-scopes-Scope. Das ist der Haken für Editor („ist der Entwurf
  brauchbar?") und Import („läuft die Datei hier?") — und die Stelle, an der die
  Sicherheitseigenschaft **prüfbar** statt nur beabsichtigt wird: der Test
  `test_check_definition_builds_no_queryset` behauptet null Queries.
- Fehlerreihenfolge wie im Protokoll: erst `FieldResolutionError` mit **allen**
  fehlenden Keys auf einmal (ein Importeur muss die ganze Liste zeigen), dann
  ein gesammelter `CompilationError`.

**2. Filter → `Q()`** (`query/filters.py`, `query/values.py`), eine
Verschachtelungsebene UND/ODER.

- Operatoren sind semantisch, nicht ORM-Lookups. Die Suffix-Tabelle
  (`contains → icontains`, …) ist eine geschlossene Tabelle im Code; aus der
  Definition kommt **nie** ein Lookup-Bestandteil.
- Werte werden nach `field.datatype` gecastet (`values.py`), damit `order.total > "9.00"`
  numerisch und nicht lexikografisch vergleicht.
- `is_empty`/`is_not_empty` je Datentyp (NULL vs. leerer String vs. beides).
- **Negation über eine Eins-zu-viele-Relation** ist die einzige Stelle, an der
  ADR 0001 offen lässt, was gemeint ist. Entschieden: negierter Operator →
  `NOT EXISTS(positive Bedingung)` („keine Position ist X"), Ausnahme
  `is_not_empty` (Präsenztest, „mindestens eine"). Die Tabelle `_NEGATION_OF`
  ist ausgeschrieben statt hergeleitet, damit man sie reviewen kann. Begründung
  im Moduldocstring: die andere Lesart macht `not_in`/`not_contains` bei jeder
  Bestellung mit zwei Produkten nutzlos.

**3. Relative Datumsfilter in der Event-Zeitzone** (`query/dates.py`, 37 Tests).

Alle sechs relativen Operatoren des eingefrorenen Contracts, aufgelöst zu
konkreten Grenzen **bevor** sie das ORM sehen:

| Operator | Fenster |
| --- | --- |
| `relative_today` | der Tag, wie ihn der Veranstalter nennt |
| `relative_last_days` (N) | N Tage einschließlich heute |
| `relative_next_days` (N) | ab morgen, N Tage |
| `relative_current_month` / `relative_current_year` | laufender Monat/Jahr |
| `relative_since_event_start` | ab `Event.date_from` |

- Zeitzone kommt aus `event.timezone`, **nie** aus `settings.TIME_ZONE`
  (`test_server_timezone_is_not_consulted` dreht die Serverzeitzone auf
  `Pacific/Auckland` und prüft, dass sich nichts ändert).
- `datetime`-Fenster sind halboffen (`>= start`, `< ende`), `date`-Fenster
  geschlossen. `test_datetime_windows_are_half_open_and_therefore_gapless`
  belegt, dass zwischen zwei aufeinanderfolgenden Fenstern keine Sekunde
  verlorengeht — das wäre bei einem terminierten Tagesreport ein täglich
  fehlender Datensatz.
- `now` ist injizierbar (`compile(..., now=...)`), damit ein Test „heute"
  festnageln kann, ohne die Prozessuhr einzufrieren.
  `test_relative_filter_reevaluates_on_every_compile` kompiliert dieselbe
  Definition zweimal mit drei Tagen Abstand und bekommt verschiedene Ergebnisse
  — genau der Punkt, an dem ein fester Datumsbereich ab dem zweiten Lauf lügt.

**4. Mehrstufige Sortierung** (`plan._build_ordering`).

Nur über `sortable`-Felder (sonst `CompilationError`), Reihenfolge der
Definition, danach **immer** `pk` als Tiebreaker. Ohne den kommen zwei Zeilen mit
gleichem Sortierwert bei zwei `LIMIT`/`OFFSET`-Seiten derselben Query in
unterschiedlicher Reihenfolge zurück — eine Zeile erscheint doppelt, eine gar
nicht. NULLs stehen in **beiden** Richtungen hinten (`nulls_last=True`), weil
PostgreSQL und SQLite sich sonst widersprechen und dieselbe Definition auf zwei
Installationen zwei verschiedene Dateien erzeugt. Sortierausdrücke sind `F()`,
nirgends `"-" + pfad`.

**5. Basis-Umschaltung `order` / `orderposition`** (`query/relations.py`).

- Einzige Stelle, die konkrete pretix-Modelle nennt, und einzige Stelle mit dem
  Event-Filter (`base_queryset`) — eine Zeile zum Auditieren, für beide Basen
  und zusätzlich für die Count-Query getestet.
- `OrderPosition` hat kein `event`-Feld; der Lookup ist `order__event`.
- **Positionsfelder auf Basis `order` (ADR 0001 Abschnitt 7):** als Spalte
  aggregatpflichtig, als Filter ein `EXISTS` ohne Aggregat. Ein
  `Order.objects.filter(all_positions__item=...)` würde die Bestellung einmal je
  passender Position liefern; `EXISTS` liefert sie einmal und braucht kein
  `distinct()`, das mit Sortierung und `iterator()` schlecht zusammenspielt.
- Aggregate sind **korrelierte Subqueries**, keine `annotate(Sum(...))` über
  gejointe Pfade — Vorbild `Order.annotate_overpayments`
  (`pretix/base/models/orders.py:510-575`). Zwei Aggregate über zwei Relationen
  bilden sonst ein Kreuzprodukt und kommen **beide** multipliziert heraus.
  `test_aggregates_over_two_relations_do_not_multiply_each_other` ist die Falle
  in einem Test: drei Positionen (43,00) und zwei Zahlungen (43,00) in einer
  Bestellung, gejoint käme 86,00 und 129,00 heraus.
- Zweistufige Ketten (`all_positions__answers__answer`) funktionieren mit
  derselben Mechanik wie einstufige; `RelationChain` kennt den Rückweg vom
  Blattmodell zur Zeile (`orderposition__order`).
- `include_canceled_positions` gilt an **allen** drei Stellen: Basis-Queryset,
  jede Subquery, jeder Prefetch. Sonst widersprechen sich zwei Spalten desselben
  Reports.
- `join` ist ein `Prefetch` plus `str.join` in Python, kein SQL: Django 5.2 hat
  keine backend-unabhängige String-Aggregation (`StringAgg` ist
  PostgreSQL-only), und das Plugin muss auch auf SQLite laufen. Kostet genau
  eine Query pro Prefetch-Ebene, unabhängig von der Zeilenzahl.

**6. Queryset-Optimierung nach den *tatsächlich gewählten* Spalten**
(`query/columns.py`).

Vier Wege zum Zellwert, pro Spalte entschieden:

| Feld deklariert | Strategie | Extra-Queries |
| --- | --- | --- |
| `value_getter` | Callable auf dem Zeilenobjekt + `prefetch_related` des Feldes | 0 |
| `annotation` | Alias lesen | 0 |
| einfacher `orm_path` | `select_related` + Attributzugriff | 0 |
| mehrwertig + `aggregate` | korrelierte Subquery, bei `join` ein Prefetch | 0 / 1 je Ebene |

- Nur benutzte Felder werden annotiert; alle Annotationen landen in **einem**
  `annotate()`-Aufruf, Reihenfolge erhalten (Punkt 1 des Requests von
  `registry-dev`: `pcr_payment_state` referenziert `pcr_pending_sum`).
- Registry-Aliase werden geprüft: ein Alias in Compiler-Form (`pcr_c<n>`) ist ein
  `FieldContractError`, sonst würde eine Spalte still den Wert einer anderen
  zeigen.
- Antworten auf Fragen ohne N+1: auf Basis `orderposition` als korrelierte
  Subquery der Registry (ein SELECT für drei Fragen-Spalten), auf Basis `order`
  als Aggregat-Subquery bzw. Prefetch.
- `iter_rows()` läuft über `QuerySet.iterator(chunk_size=DEFAULT_CHUNK_SIZE)`
  (1000). Das `chunk_size` ist Pflicht, nicht Kosmetik: `iterator()`
  berücksichtigt `prefetch_related` nur mit gesetztem `chunk_size`.
- **Query-Zahlen sind zugesichert, nicht gehofft:**

  | Report | Queries |
  | --- | --- |
  | `wide_order.json`, 30 sichtbare Spalten, voll iteriert | 2 |
  | dasselbe mit 20 zusätzlichen Bestellungen | 2 |
  | `orderposition_basic.json` (20 Spalten, 6 Relationen) | 1 |
  | `orderposition_questions.json` (3 Fragen-Spalten) | 1 |
  | `count()` | 1 |
  | echte Registry, 10 Spalten, 2 `join`-Spalten, 17 Bestellungen | 4 |

**7. Vorschaumodus** (`report.py`).

`compile(..., preview=True)` schneidet hart in SQL auf `PREVIEW_ROW_LIMIT` (20)
— kein Python-`break`, die Datenbank darf das volle Ergebnis nie
materialisieren. Die Zählung ist eine **eigene**, absichtlich billige Query:
gleiche Filter, gleiche Annotationen-für-die-Filter, aber ohne Spalten-
Annotationen, ohne Joins und ohne `ORDER BY`. `.count()` auf dem Anzeige-Queryset
würde jede Subquery für jede Zeile rechnen und das Ergebnis wegwerfen.
`options.row_limit` deckelt auch die Zahl, sonst stünden 20 Zeilen neben einer
Gesamtsumme, die der Report nie liefert.

## Zwei behobene Fehler aus der abgebrochenen Sitzung

### Fehler 1 — Aggregat-Bedingungen kamen nie bei der echten Registry an

`query/columns.py:relation_filter()` las die Bedingung für eine aggregierte
Relation aus `field.extra["relation_filter"]` — einer Konvention, die **nur der
eigene Test-Double** (`ReferenceRegistry` in `tests/test_query_support.py`)
bediente. Die echte Registry stellt dieselbe Information über
`registry.hints.aggregate_filter()` bereit (Request von `registry-dev`,
Abschnitt 2); der Compiler hat diese Funktion nie aufgerufen.

**Wirkung:** kein Fehler, sondern eine falsche Zahl. Eine Spalte
`answer.<identifier>` auf Basis `order` verlor gegen die echte Registry ihre
`question=<pk>`-Bedingung und warf die Antworten **aller** Fragen des Events in
eine Zelle. Dasselbe galt für den Storno-Ausschluss, soweit er aus der Registry
kam (strukturell hat der Compiler ihn ohnehin angewandt, deshalb wäre nur die
Fragen-Einschränkung praktisch aufgefallen).

**Warum es niemand gemerkt hat:** es gab keinen einzigen Test, der Compiler und
echte Registry zusammen ausführt. Alle Compiler-Tests liefen gegen den Stub aus
`contracts/` oder gegen unseren eigenen Double — und der Double bediente die
falsche Konvention mit.

**Behoben:**

1. `relation_filter(field, chain, include_canceled)` ruft jetzt
   `hints.aggregate_filter(field, include_canceled_positions=...)`.
2. Neu `relations.rebase_condition()`: `hints` antwortet aus Sicht des
   **Basismodells** (`Q(all_positions__canceled=False)`), weil sein dokumentierter
   Einsatz `Sum(pfad, filter=...)` ist. Unsere Subqueries laufen über das
   **Blattmodell**, wo dieselben Zeilen `canceled=False` (OrderPosition) bzw.
   `orderposition__canceled=False` (QuestionAnswer) heißen. Die Übersetzung
   nutzt die Accessor-Pfade der Relationskette, längster Präfix zuerst. Ein
   Lookup, der durch die Kette gar nicht läuft, ist ein `FieldContractError` —
   **niemals ein stilles Weglassen**, denn genau das war der Fehler.
3. **Doppelte Storno-Bedingung:** bewusst stehen gelassen. `include_canceled`
   wirkt strukturell (Basis-Queryset, `leaf_queryset`, jede Prefetch-Ebene
   inklusive der Zwischenebenen, die nie ein `Q` von hier sehen), `hints`
   liefert sie zusätzlich als `canceled_flag`. Zweimal dasselbe `AND` ist
   idempotent und kostet nichts: das Duplikat ist entweder eine lokale Spalte
   des Blattes oder ein Lookup über eine einwertige Relation, für den Django den
   vorhandenen Join wiederverwendet. Die Alternative — `hints` mit
   `include_canceled_positions=True` nach der Bedingung fragen, um die Hälfte zu
   unterdrücken — liest sich als Lüge und würde jede *künftige* Bedingung, die
   die Registry aus diesem Flag ableitet, still verschlucken. Steht so im
   Docstring von `relation_filter`.
4. `relation_source()`: der tote Override-Pfad `extra["relation_source"]` ist
   entfernt. `hints.aggregate_relation()` ist **kein** Ersatz dafür — es liefert
   das Relations-*Präfix* (`all_positions__answers`), der Compiler braucht den
   vollen Pfad, weil der Rest hinter der Relation das Aggregationsziel ist; ein
   `sum` über das Präfix würde Primärschlüssel summieren. Stattdessen wird das
   Präfix jetzt als **Gegenprobe** benutzt: deklarierte Relation und `orm_path`
   müssen dieselbe Relation beschreiben, sonst `FieldContractError`.
5. `extra["relation_filter"]` bleibt als **Notausgang** erhalten, dokumentiert
   und nachrangig: `hints` kennt genau zwei Bedingungen, beide
   pretix-Kern-spezifisch. Ein Feld aus einem Fremdplugin über eine eigene
   Eins-zu-viele-Relation (SPEC.md F5) könnte sonst gar keine Einschränkung
   ausdrücken — etwa eine Check-in-Relation auf `successful=True`, was
   `Checkin.objects` über einen Relations-Lookup nicht tut
   (`docs/pretix-api-notes.md` 6.10). Beide Quellen werden UND-verknüpft.

**Neue Tests:** `tests/test_query_registry.py` (12 Tests) kompiliert und
**führt aus** gegen `registry.library.field_registry` — nicht gegen den Stub,
nicht gegen den Double. Kern ist ein Event mit zwei Fragen, deren Antworten auf
denselben Positionen hängen, plus eine stornierte Position, die beide Fragen
beantwortet hat. Abgedeckt: Fragen-Trennung über `join`/`count`/`count_distinct`,
`include_canceled_positions` True/False über drei Codepfade (Prefetch, Count,
Sum) in einem Test, die Rückübersetzung des `Q` isoliert, ein Report auf Basis
`orderposition` mit den geteilten Annotationsaliasen aus Punkt 1 des Requests,
und eine Query-Zahl. Gegenprobe: schaltet man den `hints`-Aufruf ab, fallen
**8 von 12** Tests um.

Zusätzlich deklariert der `ReferenceRegistry` jetzt dieselben `extra`-Keys wie
die echte Registry (`aggregate_relation`, `canceled_flag`), damit auch die
bestehende Suite den `hints`-Pfad benutzt. Den Fragen-Primärschlüssel kann er
nicht nachbilden — er wird ohne Datenbank gebaut — und benutzt dafür den
Notausgang; das ist im Moduldocstring vermerkt.

### Fehler 2 — `test_an_unresolvable_path_from_the_registry_fails_loudly` schlug fehl

Der Test erwartete einen `FieldError` beim Bauen der SQL, wenn die Registry einen
Pfad liefert, den kein Modell kennt. Für eine **einsegmentige** Spalte ohne
Filter und ohne Sortierung kann das nicht eintreten: der Pfad landet nie in der
SQL, er wird als Attribut von der Zeile gelesen.

Zwei Möglichkeiten geprüft:

- *Pfade in Pass 1 gegen `Model._meta` verifizieren.* **Verworfen** — und das ist
  die interessante Erkenntnis: `contracts/stubs.py` ist eingefroren und
  deklariert absichtlich erfundene Pfade (`pcnt`, `payment_sum`,
  `checkin_count`) **ohne** Annotation dahinter. Ein Compiler, der unauflösbare
  Pfade zur Planzeit ablehnt, würde die Stub-Registry des Contracts ablehnen.
  Nachgemessen: die echte Registry hätte die Prüfung auf beiden Basen bestanden,
  der Stub 16-mal nicht.
- *Den Test auf das tatsächliche, bewusste Verhalten festnageln.* **Gewählt.**

Der Test prüft jetzt alle drei Strategien einzeln, jede scheitert laut:
gefiltert/sortiert → `FieldError` schon beim Bauen des Querysets; mehrsegmentige
Spalte (`F()`-Annotation) → `FieldError`; einsegmentige Spalte → `AttributeError`
im Renderer, **kein** leerer Wert. Der letzte Punkt hängt an einer bestehenden
Design-Entscheidung in `_attribute_renderer`: gefangen wird nur
`ObjectDoesNotExist` (fehlende Relation → `None` ist richtig), nie
`AttributeError`. Ein `except AttributeError` dort würde eine falsch deklarierte
Registry in eine Spalte voller Leerzeichen verwandeln — die einzige Fehlerart,
die niemandem auffällt.

Die falsche Behauptung „`build_plan` verifiziert deklarierte Pfade vorab" im
Docstring von `query/columns.py` ist korrigiert und trägt jetzt die Begründung
mitsamt Verweis auf den Test.

## Nicht erledigt (und warum)

- **„Vorheriger Monat" und „bis Event-Ende"** aus meiner Rollenbeschreibung gibt
  es nicht: der eingefrorene `Operator`-Enum kennt sechs relative Operatoren,
  diese zwei sind nicht darunter (`contracts/fields.py:240-245`). Contracts sind
  eingefroren, also kein Alleingang. Beide wären in `dates.py` je drei Zeilen —
  wenn sie gebraucht werden, ist das ein Contract-Änderungswunsch und gehört in
  `handoff/blockers.md`, nicht in meinen Code.
- **Sortierung nach einem Aggregat** ist abgelehnt, nicht vergessen (ADR 0001
  Abschnitt 7b: out of scope für v1). Der Compiler prüft es doppelt, weil eine
  durchgerutschte Aggregat-Sortierung die Zeilen über den nötigen Join
  vervielfacht.
- **Keine eigene ADR-Datei.** Die Entscheidungen stehen ausführlich in den
  Moduldocstrings (`relations.py` erklärt Subquery-statt-Join, `filters.py` die
  Negationssemantik, `dates.py` die Zeitzonenfrage, `columns.py` die vier
  Strategien und den `join`-Kompromiss). Wenn der `integrator` sie als
  `docs/adr/0003-query.md` haben will, ist der Text vorhanden und muss nur
  zusammengezogen werden.
- **PostgreSQL nicht verifiziert.** Die Testumgebung ist SQLite
  (`pretix.testutils.settings`). Betroffen sind vor allem die
  `Coalesce`/`Subquery`-Ausgabetypen bei `count`-Aggregaten und `nulls_last`.
  Gleicher Punkt wie bei `registry-dev` — bitte in Welle 3 gegen die echte
  Dev-Datenbank laufen lassen.

## Contract-Abweichungen

**KEINE.** `pretix_custom_reports/contracts/` ist unangetastet.

Eine Konvention, die der Contract nicht kennt und auch nicht kennen muss:
`ReportField.extra["relation_filter"]` (siehe Fehler 1, Punkt 5). `extra` ist
laut Contract frei und wird von den Contracts nie interpretiert; die Konvention
ist opt-in, steht ausschließlich in Registry-Code und nie in JSON, und die
Kern-Registry benutzt sie nicht — nichts im Compiler hängt davon ab.

## Offene Anforderungen an andere

Keine. `handoff/requests/query-dev-an-*.md` ist leer geblieben, es gibt keine
Blocker.

Zwei Hinweise ohne Handlungsbedarf für mich:

1. `tests/test_query_support.py` verweist im Docstring auf
   `handoff/requests/query-dev-an-registry-dev-aggregat-konventionen.md` — die
   Datei existiert nicht (vermutlich im abgebrochenen Lauf nie geschrieben). Der
   Verweis ist entfernt; die Naht ist jetzt `registry/hints.py` und braucht
   keinen Request mehr.
2. `tests/test_smoke.py::test_no_migration_created_yet` schlägt in der
   Gesamtsuite fehl, weil `persistence-dev` `migrations/0001_initial.py`
   angelegt hat. Fremde Datei, fremder Test — gehört `integrator` oder
   `test-engineer`. Dasselbe steht schon im Status von `registry-dev`.

## Tests

```
pytest tests/test_query_dates.py tests/test_query_filters.py \
       tests/test_query_plan.py tests/test_query_orm_path.py \
       tests/test_query_support.py tests/test_query_compile.py \
       tests/test_query_registry.py -q
-> 252 passed
```

| Datei | Tests | Inhalt |
| --- | --- | --- |
| `test_query_plan.py` | 63 | Pass 1: Auflösung, Registry-Prüfungen, Planform, alle Golden Fixtures, alle `invalid/`-Fixtures |
| `test_query_compile.py` | 62 | Pass 2 gegen die DB: Zeilen, Werte, Query-Zahlen, Vorschau |
| `test_query_filters.py` | 49 | Operator × Datentyp → `Q`, `EXISTS`, Negation, Gruppen |
| `test_query_dates.py` | 37 | relative Fenster, Zeitzonen, Lückenlosigkeit |
| `test_query_orm_path.py` | 29 | Herkunft der ORM-Pfade (siehe unten) |
| `test_query_registry.py` | 12 | **neu:** Compiler gegen die echte Registry |
| `test_query_support.py` | 0 | geteilte Fixtures, `ReferenceRegistry` |

Explizit die aus der Definition of Done verlangten:

- **Alle Golden Fixtures kompilieren:**
  `test_golden_fixture_plans_against_real_orm_paths` (Pass 1) und
  `test_golden_fixture_compiles_and_executes` (Pass 2, gegen die DB, prüft
  zusätzlich Spaltenzahl je Zeile und `count()`), beide parametrisiert über alle
  zehn Fixtures.
- **Alle `invalid/`-Fixtures werfen den erwarteten Fehlertyp:**
  `test_registry_stage_fixture_raises_the_expected_error` liest die erwarteten
  Typen aus `invalid/_expectations.json` statt sie zu wiederholen;
  `test_structural_stage_fixtures_never_reach_the_registry` prüft die andere
  Hälfte.
- **`assertNumQueries` für einen breiten Report:** `test_wide_report_is_two_queries`
  (30 Spalten, 2 Queries) plus `test_wide_report_query_count_does_not_grow_with_rows`
  als N+1-Wächter, plus dieselbe Zusicherung gegen die echte Registry.
- **Manipulierter ORM-Pfad bleibt wirkungslos:** `tests/test_query_orm_path.py`
  greift das von drei Seiten an — dieselbe Definition gegen zwei Registries, die
  sich über den `orm_path` von `order.code` uneinig sind, ergibt zwei
  verschiedene SQL-Statements (`"code"` vs. `"email"`); die Fixture
  `invalid/smuggled_orm_path.json` scheitert strukturell, bevor überhaupt eine
  Registry gefragt wird; und handgebaute Definitionen, die den JSON-Validator
  komplett umgehen, scheitern an der Registry-Allowlist — mit null Queries,
  behauptet statt argumentiert. Dazu `test_no_module_in_the_query_package_uses_eval_or_raw_sql`
  (kein `eval(`, `exec(`, `.raw(`, `RawSQL`, `.extra(` in `query/`) und
  `test_the_event_filter_is_present_in_every_compiled_queryset` für beide Basen
  **und** die Count-Query.

Gesamtsuite `pytest tests/ -q`: **665 passed, 1 failed** — der Fehlschlag ist
`test_no_migration_created_yet`, siehe oben, nicht meiner.

Lint über die eigenen Dateien, alle drei sauber:

```
flake8 pretix_custom_reports/query tests/test_query*.py   -> rc 0
isort -c  (dieselben Pfade)                               -> rc 0
black --check (dieselben Pfade)                           -> 16 files unchanged
```

Anmerkung dazu: `black` und `flake8` streiten sich über Slices mit Ausdruck
(`x[i + 1 :]` → E203). An drei Stellen steht deshalb eine benannte Variable
statt des Ausdrucks im Slice, jeweils mit Kommentar — sonst kann nicht beides
grün sein.

Kein `black .` / `isort .` über das Repo, kein `git commit`, keine Datei
außerhalb von `pretix_custom_reports/query/**`, `tests/test_query*.py` und
`handoff/status/query-dev.md` angefasst.

## Nächster Schritt

1. **`exporter-dev` (Welle 2):** `report.iter_rows(chunk_size=...)` und
   `report.headers()` sind die ganze Schnittstelle; `compile()` wirft
   ausschließlich `ContractError`-Unterklassen, die laut ADR 0001 Abschnitt 5.2
   in `ExportError` übersetzt werden. Für einen Multi-Event-Export einmal pro
   Event kompilieren — ein `CompiledReport` gehört zu genau einem Event.
2. **`frontend-dev`:** `compiler.plan(definition, event)` bzw.
   `check_definition(...)` beantworten „ist dieser Entwurf gültig?" ohne
   Datenbank und ohne Scope; `compile(..., preview=True)` liefert 20 Zeilen und
   eine separate, billige `count()`.
3. **Welle 3 (`security-reviewer`):** der interessanteste Angriffspunkt ist
   nicht der Compiler, sondern die Registry-Naht. Ab jetzt gilt: wer eine neue
   Bedingung für eine aggregierte Relation braucht, erweitert `registry/hints.py`
   — und `tests/test_query_registry.py` ist der Test, der merkt, wenn der
   Compiler sie nicht liest. Genau das ist zwischen Welle 1 und 1 einmal
   schiefgegangen.
4. **Welle 3 (`test-engineer`):** Query-Zahlen und `nulls_last` gegen PostgreSQL
   nachfahren.
