# registry-dev → query-dev: drei Dinge, die die echte Registry anders macht als der Stub

**Welle:** 1 → 1/2 (spätestens beim ersten Merge)
**Quelle:** `docs/adr/0002-registry.md` Abschnitte 3, 4, 5
**Warum du:** `pretix_custom_reports/query/**` gehört dir. Ich habe dort nichts
angefasst. Du hast gegen `contracts.stubs.StubFieldRegistry` entwickelt, und der
Stub liefert für alle Annotationen `{alias: None}` — er kann diese drei Punkte
also strukturell nicht zeigen.

Der Contract ist unverändert. Es geht nur um Verhalten der echten
Implementierung, das der Stub nicht abbildet.

---

## 1. Alle Annotationen der benutzten Felder in **ein** `annotate()`

`ReportField.annotation(ctx)` liefert ein `Mapping[str, expression]` mit **einem
oder mehreren** Aliasen. Zwei Eigenschaften, die zusammengehören:

**a) Verschiedene Felder teilen sich Aliase.** `order.pending_sum` und
`computed.payment_state` liefern beide `pcr_pending_sum` mit demselben Ausdruck.
`computed.payment_state` liefert zusätzlich `pcr_payment_sum` (weil sein `Case`
dagegen vergleicht) und `pcr_payment_state`.

**b) Die Reihenfolge im Mapping ist bedeutungstragend.** `pcr_payment_state`
referenziert `pcr_pending_sum` per `Q(pcr_pending_sum__lt=0)`. Django löst das
auf, weil `annotate(**mapping)` die Aliase in Dict-Reihenfolge hinzufügt.

Daraus folgt:

```python
merged = {}
for field in used_fields:                      # in Spalten-/Filter-/Sortierreihenfolge
    if field.annotation is not None:
        merged.update(field.annotation(ctx))   # dict.update, Reihenfolge bleibt
if merged:
    queryset = queryset.annotate(**merged)     # genau EIN Aufruf
```

**Nicht** pro Feld einzeln `annotate()` aufrufen: zwei Aufrufe mit demselben
Alias wirft Django ab (`ValueError: The annotation 'pcr_pending_sum' conflicts
with a field …`). `dict.update` mit identischem Alias ist dagegen harmlos, weil
der Ausdruck derselbe ist, und die Position des Alias bleibt die der ersten
Einfügung — die Abhängigkeit steht damit immer vorne.

Abgesichert durch
`tests/test_registry.py::test_annotations_of_all_fields_merge_into_one_mapping`
und `::test_all_annotations_execute` (baut ein Queryset mit *allen* annotierten
Feldern und wertet es aus).

Alle Aliase beginnen mit `pcr_`
(`tests/test_registry.py::test_every_annotation_alias_is_namespaced`). Du darfst
also davon ausgehen, dass sie nicht mit `Order.annotate_overpayments`
(`payment_sum`, `refund_sum`, `pending_sum_t`) oder `pcnt` kollidieren.

`ctx` ist `registry.context(event, base)` bzw. ein
`FieldContext(event=event, base=base)`. Die Callables prüfen, dass Event und
Basis zu dem passen, wofür das Feld gebaut wurde, und werfen sonst
`FieldContractError` — falls dir das begegnet, hast du einen Context recycelt.

---

## 2. Aggregate über `all_positions` brauchen ein `filter=` aus der Registry

Auf Basis `order` ist `position.price` der ORM-Pfad
`all_positions__price`. `all_positions` ist der echte `related_name` und enthält
**stornierte** Positionen (`Order.positions` ist nur eine Python-Property über
den gefilterten Manager, `docs/pretix-api-notes.md` 6.2 Fallstrick 1). Eine
Antwort-Spalte läuft über `all_positions__answers__answer` und muss zusätzlich
auf ihre Frage eingeschränkt werden, sonst aggregiert sie die Antworten aller
Fragen des Events.

Beides kennt nur die Registry. Es liegt als JSON-fähige Primitive in
`field.extra` und wird über zwei Funktionen nutzbar:

```python
from pretix_custom_reports.registry import hints

relation = hints.aggregate_relation(field)      # "all_positions" | "all_positions__answers" | None
condition = hints.aggregate_filter(             # Q(...) | None
    field,
    include_canceled_positions=definition.options.include_canceled_positions,
)
expression = Sum(field.orm_path, filter=condition)   # filter=None ist für Django ok
```

`hints.aggregate_filter` liefert:

| Feld | `include_canceled_positions=False` | `=True` |
| --- | --- | --- |
| `position.price` (Basis `order`) | `Q(all_positions__canceled=False)` | `None` |
| `answer.tshirt-size` (Basis `order`) | `Q(all_positions__canceled=False) & Q(all_positions__answers__question=<pk>)` | `Q(all_positions__answers__question=<pk>)` |
| `order.code`, `payment.sum_confirmed`, … | `None` | `None` |

Das `Q` steckt bewusst **nicht** in `extra`: die Editor-API serialisiert Felder
nach JSON, und ein `Q` darin würde `json.dumps` brechen
(`tests/test_registry.py::test_aggregate_hints_are_json_safe`).

`hints.aggregate_relation(field) is not None` ist zusätzlich dein Signal für die
Doppelzählungsfalle: zwei aggregierte Spalten über dieselbe Relation in einer
Abfrage zählen sich gegenseitig hoch, wenn du nicht mit Subqueries oder
`distinct=True` arbeitest (ADR 0001 Abschnitt 11 nennt sie ausdrücklich).
`order_with_aggregates.json` ist genau dieser Fall: `position.price` vier Mal mit
`sum`/`min`/`max`/`avg` plus `position.positionid` mit `count`.

**Wenn du das nicht implementierst,** ist das Ergebnis nicht ein Fehler, sondern
eine falsche Zahl (stornierte Positionen mitgezählt) bzw. eine Antwortspalte, die
alle Antworten der Bestellung zusammenwirft. Deshalb dieses Dokument.

---

## 3. Vier Spalten sind reine Python-Spalten, mit `prefetch_related`

`payment.providers`, `seat.name`, `order.full_code` und `position.code` haben
`orm_path is None` und nur einen `value_getter`. Der Contract verbietet ihnen
Filter, Sortierung und Aggregate; die Registry setzt sie entsprechend leer.

Zwei Punkte für dich:

1. `field.select_related` und `field.prefetch_related` sind bei diesen Feldern
   gefüllt (`payment.providers` braucht `("payments",)` auf Basis `order` bzw.
   `("order__payments",)` auf Basis `orderposition`). Ohne sie macht der Getter
   pro Zeile eine Abfrage.
2. `QuerySet.iterator(chunk_size=...)` unterstützt `prefetch_related` nur mit
   **gesetztem** `chunk_size`. `contracts.DEFAULT_CHUNK_SIZE` ist der Wert.

Der Grund für Python statt SQL steht in ADR 0002 Abschnitt 4: der naheliegende
Ausdruck wäre `StringAgg`, und der ist PostgreSQL-only.

---

## 4. Kleinigkeiten, die dir Zeit sparen können

- **`DataType.I18N` braucht Rendering.** `item.name`, `item.category`,
  `variation.value`, `subevent.name`, `subevent.location` liegen als
  `I18nCharField`/`I18nTextField` in der DB, also je nach Inhalt als
  Klartext-String *oder* als JSON-Dict. Bei Attributzugriff auf ein
  `select_related`-Objekt gibt Djangos Descriptor eine `LazyI18nString` zurück;
  bei `values()`/Annotation-Alias bekommst du den Rohwert. Die Registry
  deklariert dafür keinen `value_getter` (das wäre `orm_path` *und*
  `value_getter`, also die Regel „genau eine Deklaration" gebrochen) —
  `datatype is DataType.I18N` ist das Signal für deinen Renderer.
  `filters_and_or.json` filtert `item.name contains "Ticket"` und
  `item.category exact "Workshops"`; die Registry erlaubt das, weil die Fixture
  es verlangt. Auf einem mehrsprachigen Wert trifft `exact` gegen den JSON-Blob
  nicht — das ist eine bekannte Grenze, keine Aufgabe für dich.
- **Antwort-Felder sind lexikografisch.** `field.extra["lexicographic_comparison"]`
  ist `True` für alles unter `answer.*` außer Booleans. Datums- und
  Zeit-Antworten vergleichen dadurch korrekt (ISO), Zahlen nicht — die Registry
  gibt Zahlen-Fragen deshalb als `DataType.STRING` heraus, du musst nichts tun.
- **`computed.order_status_label` und `answer.*` auf Basis `order` haben
  `filter_operators=()`.** Das ist kein Bug, sondern eine dokumentierte
  Verengung (ADR 0002 Abschnitte 2.1 und 3.1). Ein Filter darauf muss als
  `CompilationError` scheitern, so wie bei jedem anderen nicht erlaubten
  Operator.
- **Die Registry ruft nie `scopes_disabled()`.** Sie braucht einen aktiven
  Scope. Für Celery gilt: `base=EventTask` reicht, das setzt den Scope selbst
  (`docs/pretix-api-notes.md` 7.2).

---

## Was ich von dir brauche

Nichts, was ich nicht selbst tun könnte — aber wenn dir bei 1. oder 2. eine
andere Aufteilung lieber ist (z. B. dass die Registry das `filter=` gleich als
fertigen Ausdruck liefert statt als `Q`), lege eine Anforderung in
`handoff/requests/query-dev-an-registry-dev-*.md` ab. `registry/hints.py` ist
genau dafür da, dass diese Naht an einer Stelle liegt.
