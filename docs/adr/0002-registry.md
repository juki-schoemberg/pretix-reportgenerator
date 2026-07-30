# ADR 0002 — Feld-Registry: Umfang, dynamische Felder, Fremdplugin-Signal, Cache-Invalidierung

- **Status:** vorgeschlagen
- **Datum:** 2026-07-30
- **Autor:** `registry-dev` (Welle 1)
- **Betrifft:** `query-dev` (Welle 1), `frontend-dev` (Welle 1/2), `exporter-dev`
  und `portability-dev` (Welle 2), `integrator` (Verdrahtung)
- **Baut auf:** ADR 0001 (Contracts, eingefroren), `docs/pretix-api-notes.md`
  (verbindliche API-Referenz), `SPEC.md` F4/F5 und Abschnitt 6
- **Nummer 0002 beansprucht von `registry-dev`.** Falls ein paralleler Agent
  dieselbe Nummer gewählt hat: der `integrator` vergibt beim Merge neu.

---

## Kontext

Die Registry ist die einzige Stelle, an der aus einem gespeicherten Feld-Key
etwas Technisches wird — ein ORM-Pfad, eine Annotation, ein Lookup. Alles, was
sie freigibt, ist damit für importiertes JSON erreichbar; alles, was sie nicht
freigibt, ist es nicht. Sie ist deshalb keine Bequemlichkeitsschicht, sondern
eine Allowlist.

ADR 0001 hat die *Form* eines Feldes eingefroren (`ReportField`) und die
Key-Grammatik. Offen geblieben ist bewusst (ADR 0001 Abschnitt 12):

- welche Kernfelder es über die 48 `required_field_keys` hinaus gibt,
- wie die Registry gecacht und invalidiert wird.

Diese ADR entscheidet beides und dokumentiert die Stellen, an denen die Registry
enger ist als der Contract erlaubt hätte.

---

## 1. Entscheidung: handgepflegte Feldtabelle, keine Introspektion

`registry/core.py` ist eine Liste. Kein `Model._meta`, keine Ableitung aus
Feldtypen, keine Ausschlussliste.

**Begründung.** Eine Ausschlussliste ist die falsche Richtung. Sie ist genau
dann vollständig, wenn niemand ein Feld vergessen hat — und der Preis des
Vergessens ist, dass `order.secret`, `order.internal_secret`,
`position.secret`, `position.web_secret` oder ein beliebiger Relationspfad
(`event__organizer__…`) in einer Oberfläche landen, deren Eingabe eine
importierte Datei ist. Eine Positivliste ist genau dann unvollständig, wenn
jemand ein Feld vergessen hat, und der Preis ist eine fehlende Spalte. Die
Fehlerkosten sind asymmetrisch, also ist die Richtung nicht verhandelbar.

**Preis, bewusst getragen:**

- Ein pretix-Upgrade macht neue Spalten nicht automatisch verfügbar.
- Ein in pretix umbenanntes Feld bricht genau einen Eintrag in `core.py`, und
  zwar laut, in `test_all_annotations_execute` bzw. beim ersten Report-Lauf.
  Das ist der gewünschte Ausfallmodus: eine Introspektion hätte stattdessen
  stillschweigend gespeicherte Reports entwertet.

**Umfang:** 80 Kernfelder auf Basis `order`, 83 auf Basis `orderposition`
(inkl. der drei `computed.*`), plus je Event die Fragen- und Meta-Felder.
Abgedeckt sind die von `SPEC.md` F4 verlangten Quellen: `Order`,
`OrderPosition`, `InvoiceAddress`, `Item`, `ItemVariation`, `ItemCategory`,
`SubEvent`, `Seat`, `Voucher`, `Discount`, `OrderPayment`/`OrderRefund`
(aggregiert), `Checkin` (aggregiert), Meta-Properties, Fragen.

**Nicht freigegeben, mit Grund:** alle Secrets und Nonces;
`Order.pseudonymization_id` existiert nicht, `OrderPosition.pseudonymization_id`
ist freigegeben (sie steht auf dem Ticket und ist zum Teilen gedacht);
`meta_info` (JSON in einem `TextField`, ORM-seitig nicht abfragbar,
`docs/pretix-api-notes.md` 6.2 Fallstrick 3); `Order.organizer` und
`OrderPosition.organizer` (redundante nullable FKs, Fallstrick 4); alle
`*_includes_rounding_correction`-Spalten (Fallstrick 8 — sie beschreiben Werte
*vor* Rundung und würden einer Summenspalte widersprechen).

---

## 2. Entscheidung: Fragen über `Question.identifier`, Fallback = überspringen und melden

Verifiziert (`docs/pretix-api-notes.md` 6.4): `identifier` ist pro Event
DB-eindeutig, nie leer, übersteht Event-Kopien — und ist jederzeit vom Nutzer
änderbar.

Die Registry baut `answer.<identifier>`. Wenn daraus kein legaler Key wird,
**wird die Frage übersprungen und in den Diagnostics gemeldet**, nicht
umgeschrieben.

**Warum kein Mangling.** Der einzige realistische Fall ist ein `__` im
Identifier (ADR 0001 Abschnitt 2). Ein automatisch umgeschriebener Key
(`a__b` → `a-b`) wäre:

- nicht stabil zwischen Events, weil er von der Umschreiberegel abhängt statt
  von den Daten,
- kollisionsfähig mit einer real existierenden Frage mit Identifier `a-b`, und
  zwar unbemerkt — dann würden zwei Fragen auf denselben Key zeigen und eine
  gespeicherte Definition bekäme je nach Reihenfolge eine andere Antwort.

Statt dessen sagt `registry.diagnostics(event, base)` genau, welche Frage warum
fehlt (`SkippedField(reason=REASON_INVALID_KEY)`), damit die Debug-Ansicht aus
`SPEC.md` P2 dem Nutzer sagen kann: „Frage X umbenennen". Automatisch generierte
Identifier bestehen aus `ABCDEFGHJKLMNPQRSTUVWXYZ3789` und können den Fall nie
auslösen.

Dieselbe Mechanik greift bei Meta-Properties: pretix erlaubt dort
`^[a-zA-Z0-9_]+$`, also auch `zwei__woerter`.

**Case-Insensitivität.** `resolve()` fällt für `answer.*`-Keys auf einen
Kleinschreibungs-Index zurück. Begründung: pretix prüft die Eindeutigkeit von
`identifier` case-insensitiv (`Question._clean_identifier`), also *kann* es
keine zwei Fragen geben, die sich nur in der Groß-/Kleinschreibung
unterscheiden — die Auflösung ist damit eindeutig, und ein Nutzer, der die
Schreibweise korrigiert, verliert keine Spalte. Für Kern-Namespaces gilt das
**nicht**: dort ist der Key exakt, weil er von uns kommt und keine Toleranz
braucht.

### 2.1 Der Datentyp-Kompromiss bei Antworten

`QuestionAnswer.answer` ist für **jeden** Fragetyp ein `TextField`
(`docs/pretix-api-notes.md` 6.4 Fallstrick 1). Die Registry macht daraus:

| `Question.type` | `DataType` | Vergleichssemantik |
| --- | --- | --- |
| `B` boolean | `boolean` | in SQL zu echtem Boolean normalisiert (`"True"`/`"False"` → `TRUE`/`FALSE`) |
| `D` date | `date` | lexikografisch auf `YYYY-MM-DD` — exakt korrekt |
| `H` time | `time` | lexikografisch auf `HH:MM:SS` — exakt korrekt |
| `C` choice | `choice` | Antworttext = Options-Label, also Namensvergleich |
| `M` multiple choice | `multichoice` | Labels mit `", "` verbunden → **`contains`, nicht `in`** |
| `CC` country | `country` | Zwei-Buchstaben-Code |
| `TEL` phone | `phone` | wie eingegeben |
| `N` number | **`string`** | siehe unten |
| `W` datetime | **`string`** | siehe unten |
| `S`/`T` | `string`/`text` | |
| `F` file | `file` | nur `ist leer`/`ist nicht leer` |

Die zwei Abweichungen sind Absicht:

- **Zahlen-Fragen als `string`.** Ein `Cast(answer AS numeric)` wäre die
  hübschere Lösung, lässt aber auf PostgreSQL **die ganze Abfrage** scheitern,
  sobald eine einzige Zeile keinen gültigen Wert enthält. Ein Report, der für
  ein ganzes Event nicht läuft, ist schlimmer als eine lexikografische
  Sortierung („10" vor „9"). Der Preis steht im `help_text` des Feldes.
- **Datetime-Fragen als `string`.** pretix speichert ISO-8601 *mit Offset*; ein
  Vergleich gegen ein Django-`datetime` würde von Django zu einem anderen
  Stringformat serialisiert und **stillschweigend nichts** matchen. Textoperatoren
  sind hier ehrlicher als ein Datumswidget, das nicht funktioniert.
- **Datums-Fragen behalten `date`**, weil ISO-Datumsstrings lexikografisch
  korrekt vergleichen und Django ein `date` zu genau `YYYY-MM-DD` serialisiert.
  Test: `test_answer_text_is_filterable_and_sortable`.

**Bewusste Verengung gegenüber dem Contract:** Auf Basis `order` haben
Antwort-Felder `filter_operators=()`. „Bestellung hat eine Position, die X
geantwortet hat" und „alle Positionen haben X geantwortet" sind zwei
verschiedene Fragen, der Editor kann nicht ausdrücken welche gemeint ist, und
ein Filter, der still eine der beiden wählt, ist schlimmer als kein Filter. Als
*Spalte* mit Aggregat (`join`, `count`, `count_distinct`) sind sie verfügbar —
das ist der Fall, den `order_with_aggregates.json` benutzt. Für Antwortfilter
ist Basis `orderposition` der richtige Report.

---

## 3. Entscheidung: berechnete Felder über Annotationen, mit einem Namensraum für Aliase

Alle Aliase beginnen mit `pcr_`. Grund: pretix annotiert selbst `payment_sum`,
`refund_sum`, `pending_sum_t`, `pcnt` (`Order.annotate_overpayments`,
`Order.count_positions`). Ein Queryset, das schon durch einen dieser Helfer
gelaufen ist, muss weiter annotierbar sein.

Zwei Regeln, auf die sich `query-dev` verlassen darf und muss:

1. **Derselbe Wert benutzt immer denselben Alias.** `order.pending_sum` und
   `computed.payment_state` liefern beide `pcr_pending_sum` mit identischem
   Ausdruck. Der Compiler muss daher alle Mappings der verwendeten Felder in
   **ein** Dict mischen und **einmal** `annotate()` aufrufen. Zwei getrennte
   `annotate()`-Aufrufe mit demselben Alias wirft Django ab.
2. **Die Reihenfolge im Mapping ist bedeutungstragend.** `pcr_payment_state`
   vergleicht per `Q()` gegen `pcr_pending_sum`; Django löst das auf, weil
   `annotate(**mapping)` in Dict-Reihenfolge hinzufügt. Das Mapping darf nicht
   sortiert werden. Test: `test_annotations_of_all_fields_merge_into_one_mapping`.

Kein Feld benutzt ein Aggregat über einen gejointen Pfad; alles ist eine
korrelierte `Subquery` nach dem Vorbild von `annotate_overpayments`. Das ist die
Voraussetzung dafür, dass ein Report mit `Betrag bezahlt`, `Offener Betrag` und
`Anzahl Positionen` in einer Abfrage keine Zeilen vervielfacht (`SPEC.md`
Abschnitt 4).

Die Zustandsmengen für Geldsummen sind wörtlich die von pretix
(`confirmed`+`refunded` für Zahlungen, `done`+`transit`+`created` für
Erstattungen, `docs/pretix-api-notes.md` 6.9). Wer hier abweicht, produziert
Zahlen, die der pretix-Oberfläche widersprechen.

### 3.1 `computed.payment_state` liefert Codes, `computed.order_status_label` Wörter

`computed.payment_state` gibt `unpaid` / `partially_paid` / `paid` / `overpaid`
zurück, die Übersetzung steckt in `choices`. Ein gespeicherter Filterwert bleibt
damit portabel und funktioniert auch dann, wenn der Report in einer anderen
Sprache läuft — terminierte Exporte laufen unter der Sprache des Schedules, nicht
der des Autors (`docs/pretix-api-notes.md` Abschnitt 5).

`computed.order_status_label` ist der Gegenfall: es existiert *genau*, um das
Wort zu liefern, das pretix anzeigt. Es hat deshalb `filter_operators=()` —
gefiltert wird über `order.status`, dessen Werte stabile Buchstaben sind.

### 3.2 `computed.age.<identifier>`: Alter zum Veranstaltungsdatum

Für jede Datums-Frage entsteht ein Feld, das das Alter in vollen Jahren zum
Beginn des Events berechnet, in der Zeitzone des Events. Das Referenzdatum ist
zur Bauzeit bekannt und damit eine Konstante im SQL — die Rechnung sind drei
Integer-Operationen, kein Python pro Zeile, und das Feld ist sortier- und
filterbar.

**Bekannte Grenze, benannt statt versteckt:** `Cast(text AS date)` scheitert auf
PostgreSQL für die ganze Abfrage, wenn eine Antwort kein gültiges Datum ist. Die
Subquery lässt deshalb nur Werte durch, die auf `^[0-9]{4}-[0-9]{2}-[0-9]{2}`
passen (`ISO_DATE_REGEX`). Ein formal passendes, unmögliches Datum
(`2026-02-30`) würde weiterhin scheitern; pretix validiert Datumsantworten im
Bestellformular, im Backend und in der API, das setzt also einen Rohschreiber
voraus. Der Ausfallmodus ist ein Datenbankfehler, nie eine falsche Zahl. **Die
Testumgebung läuft auf SQLite; dieser Pfad ist gegen PostgreSQL nicht
verifiziert** (siehe `handoff/status/registry-dev.md`).

Auf Basis `order` wird das Feld nicht angeboten: es bräuchte ein Aggregat über
Positionen, und „Durchschnittsalter je Bestellung" ist keine Frage, die gestellt
wird.

### 3.3 Meta-Properties sind Konstanten

`meta.event.<name>` wird als `Value(...)` annotiert, mit dem Wert aus
`Event.meta_data`.

`Event.meta_data` legt den organizerweiten Default unter den eigenen Wert des
Events. Ein Filter über `meta_values__property__name` würde deshalb jedes Event
übersehen, das den Default benutzt — die Asymmetrie zwischen Anzeige und Filter,
vor der `docs/pretix-api-notes.md` 6.7 warnt. Da ein `CompiledReport` zu genau
einem Event gehört (ADR 0001 Abschnitt 9), ist der Wert für alle Zeilen gleich
und darf ein Literal sein. Anzeige und Filter können dann per Konstruktion nicht
auseinanderlaufen, der Join verschwindet, und ein Multi-Event-Export bekommt
trotzdem je Event den eigenen Wert, weil er je Event kompiliert.

Preis: `meta.event.campaign = "x"` trifft alle Zeilen oder keine. Das ist die
korrekte Semantik — die Property beschreibt das Event, nicht die Bestellung.

`meta.subevent.*`, `meta.item.*` und `meta.variation.*` sind in v1 **nicht**
implementiert. Sie sind nicht konstant und bräuchten die Default-Auflösung pro
Zeile; `required_field_keys` verlangt sie nicht.

---

## 4. Entscheidung: `payment.providers` ist eine Python-Spalte

Der naheliegende Ausdruck ist `StringAgg`, und der ist PostgreSQL-only. pretix
läuft auch auf SQLite. Also `value_getter` plus
`prefetch_related=("payments",)`, und damit — nach der Regel aus ADR 0001 —
keine Filter, keine Sortierung, kein Aggregat. `_index.json` führt das Feld für
Basis `orderposition` ohnehin unter `not_sortable`; die Registry ist auf beiden
Basen konsistent.

Das ist gleichzeitig das Muster für jedes künftige Python-Feld: `orm_path`
bleibt `None`, und der Contract erzwingt den Rest. `seat.name`,
`order.full_code` und `position.code` sind die anderen drei — bei `seat.name`,
weil der Anzeigename nur in `Seat.__str__` existiert
(`docs/pretix-api-notes.md` 6.8).

Anforderung an `query-dev`: `QuerySet.iterator(chunk_size=...)` unterstützt
`prefetch_related` erst mit gesetztem `chunk_size`. Ohne das liefern diese
Spalten pro Zeile eine Extraabfrage.

---

## 5. Entscheidung: Aggregat-Hinweise als JSON-safe Primitive plus Hilfsfunktion

Auf Basis `order` ist ein Positionsfeld über `all_positions__…` erreichbar. Zwei
Bedingungen kennt nur die Registry:

1. `all_positions` enthält **stornierte** Positionen — `Order.positions` ist nur
   eine Python-Property über den gefilterten Manager
   (`docs/pretix-api-notes.md` 6.2 Fallstrick 1). Ein Aggregat darüber braucht
   also ein `filter=`, abhängig von `options.include_canceled_positions`.
2. Eine Antwort-Spalte auf Basis `order` läuft über
   `all_positions__answers__answer` und muss auf **ihre** Frage eingeschränkt
   werden, sonst aggregiert sie die Antworten aller Fragen des Events.

Modellierung: `ReportField.extra` trägt nur JSON-fähige Primitive
(`aggregate_relation`, `canceled_flag`, `aggregate_question_pk`), und
`registry/hints.py` baut daraus auf Anfrage ein `Q`:

```python
condition = hints.aggregate_filter(
    field, include_canceled_positions=definition.options.include_canceled_positions
)
expression = Sum(field.orm_path, filter=condition)
```

**Warum das `Q` nicht in `extra` steckt:** die Editor-API serialisiert Felder
nach JSON. Ein `Q`-Objekt in `extra` würde `json.dumps` brechen — abgesichert
durch `test_aggregate_hints_are_json_safe`.

`hints.aggregate_relation(field)` ist zusätzlich das Signal für die
Doppelzählungsfalle: zwei aggregierte Spalten über dieselbe Relation in einer
Abfrage zählen sich gegenseitig hoch, wenn der Compiler nicht mit Subqueries
oder `distinct=True` arbeitet (ADR 0001 Abschnitt 11 nennt sie ausdrücklich).

---

## 6. Entscheidung: `register_report_fields` als `EventPluginSignal`, Kern gewinnt

Das Signal liegt in `registry/signals.py`, nicht in
`pretix_custom_reports/signals.py` (dort liegen unsere *Empfänger*, und die
Datei gehört dem `integrator`). Fremdplugins bekommen damit einen Import, der
sich nicht ändert, wenn unsere Verdrahtung sich ändert:

```python
from pretix_custom_reports.registry.signals import register_report_fields
```

`EventPluginSignal` statt `GlobalSignal`, weil ein abgeschaltetes Plugin keine
Spalten beisteuern darf; pretix erzwingt das und prüft zusätzlich, dass der
Empfänger zu einer App mit `PretixPluginMeta` gehört
(`pretix/base/signals.py:92-141, 261-274`).

**Namespace:** ausschließlich `plugin.<django_app_label>.<name>`
(`contracts.plugin_field_key`). Django garantiert eindeutige App-Labels pro
Installation, damit ist eine Kollision zwischen zwei Plugins nicht
konstruierbar.

**Kollisionsregel, in dieser Reihenfolge geprüft:**

| Fall | Ergebnis | `reason` |
| --- | --- | --- |
| Key in einem der 15 Kern-Namespaces | verworfen | `reserved_namespace` |
| `provider` passt nicht zum App-Label im Key | verworfen | `wrong_provider` |
| Feld deklariert die angefragte Basis nicht | verworfen | `unsupported_base` |
| Key existiert schon (Kern oder früheres Plugin) | verworfen | `duplicate_key` |
| Rückgabe ist kein `ReportField` | verworfen | `not_a_field` |
| Empfänger wirft | verworfen | `receiver_failed` |

Kern gewinnt, und zwischen zwei Plugins gewinnt das erste — wobei „erste"
reproduzierbar ist, weil pretix Empfänger nach
`(is_core, __module__, __name__)` sortiert (`signals.py:242-249`).

Die `provider`-Prüfung ist die nicht offensichtliche: ohne sie könnte ein Plugin
seine Felder unter dem Präfix eines *anderen* Plugins parken und dieses Präfix
übernehmen, sobald das andere Plugin installiert wird.

`send_robust` statt `send`: ein fehlerhaftes Fremdplugin darf den
Report-Editor nicht mitnehmen. Verworfen wird nie still — jede Ablehnung geht
als `WARNING` ins Log **und** in `registry.diagnostics()`.

---

## 7. Entscheidung: Cache-Invalidierung über ein Token im geteilten Cache

Das ist der Punkt, den ADR 0001 Abschnitt 12 offengelassen hat.

### Zwei Schichten

**1. Prozesslokales Dict** (`registry/cache.py`, `OrderedDict`, LRU, max. 128
Einträge) hält die gebauten `{key: ReportField}`-Mappings.

Es *muss* prozesslokal sein: ein `ReportField` enthält Closures (`annotation`,
`choices`, `value_getter`), und Closures sind nicht picklebar.
`django.core.cache` kann die Felder also grundsätzlich nicht halten. Die
Obergrenze ist kein Detail: ein Celery-Worker berührt über seine Lebenszeit
viele Events, und ein unbegrenztes Dict wäre ein langsames Speicherleck, das
Closures festhält.

**2. Token in `django.core.cache`** entscheidet, ob ein lokaler Eintrag noch
gilt. Das Token ist ein Zufallsstring; Invalidieren heißt **löschen**, woraufhin
der nächste Leser ein neues erzeugt und alle Prozesse es merken. In Produktion
ist dieser Cache geteilt (Redis) — das ist es, was eine Invalidierung im
Webworker für den Celery-Worker sichtbar macht.

Das Token besteht aus drei Teilen, jeder eine Antwort auf „was könnte die
Feldtabelle falsch machen?":

| Teil | Grund |
| --- | --- |
| Event-Token | die Fragen des Events haben sich geändert |
| Organizer-Token | die Meta-Properties des Organizers haben sich geändert |
| `event.plugins` | ein Plugin wurde ein- oder ausgeschaltet |

`event.plugins` steckt direkt im Token statt hinter einem Signal, weil das eine
Änderung an `Event` ist und kein Modell betrifft, das die Empfänger beobachten.

### Was *nicht* im Token steckt, und warum das keine Lücke ist

Produkte, Kategorien, Varianten, Termine, Sitzplätze, Voucher und
Fragen-Optionen erreichen die Registry **ausschließlich** über faule
`choices`-Callables, die zur Anfragezeit laufen. Das ist eine bewusste
Entwurfsauflage an `registry/core.py` und `registry/choices.py`:

> **Alles Volatile gehört hinter ein Callable, nicht in die Feldstruktur.**

Deshalb muss ein Produkt-Edit gar nichts invalidieren
(`test_choices_are_not_cached`).

Die Empfänger decken immerhin `Item`, `ItemCategory`, `SubEvent` und `Discount`
zusätzlich zu `Question` ab. Heute ist das streng genommen unnötig; es ist
verdrahtet, damit an dem Tag, an dem jemand ein Feld hinzufügt, das Produktdaten
*doch* in seine Struktur backt, nicht der Cache das Ding ist, das kaputtgeht.
Kosten: der Neuaufbau eines kleinen Dicts nach einem Produkt-Edit.
`test_product_change_invalidates` hält den Empfänger davor, als toter Ballast
gelöscht zu werden.

### Zwei bewusst akzeptierte Ausfallmodi

- **Dummy-Cache-Backend** (so läuft die pretix-Testsuite,
  `pretix/testutils/settings.py:74-78`): `get_or_set` kann nichts ablegen, jeder
  Aufruf bekommt ein frisches Token, jeder Aufruf baut neu. Korrekt, nur langsam.
  Festgehalten in `test_dummy_cache_degrades_to_always_rebuild`.
- **Prozesslokales Backend** (`LocMemCache` mit mehreren Workern): eine
  Invalidierung in einem Prozess ist für die anderen unsichtbar.
  `MAX_AGE = 120` begrenzt das: ein lokaler Eintrag gilt nie länger als zwei
  Minuten, egal was der Cache sagt. Das ist das eine, was ein Token allein nicht
  leisten kann, und zwei Minuten veraltete Feldbibliothek ist ein akzeptabler
  Worst Case für eine Fehlkonfiguration.

### Verworfene Alternativen

- **`django.core.cache` für die Felder selbst.** Nicht möglich, siehe Closures.
- **Ein globaler Generationszähler.** Einfach und immer korrekt, aber jeder
  Produkt-Edit irgendeines Organizers würde jeden Event-Cache entwerten. In einer
  großen Installation wäre der Cache damit wirkungslos.
- **Inhaltsbasiertes Token** (`Question.objects.aggregate(Count, Max(id))`).
  Kostet eine Abfrage pro Zugriff und erkennt eine reine Umbenennung nicht:
  `Question` hat kein `last_modified`.
- **`cached_property` auf dem Event.** Lebt nur für die Dauer eines Requests und
  hilft dem Editor, der viele Requests macht, überhaupt nicht.
- **Kein Cache.** Wären zwei kleine Abfragen plus ein Signal-Rundlauf pro
  Tastendruck in der Feldsuche.

---

## 8. Entscheidung: kein Registry-Aufbau ohne Event, kein `scopes_disabled()`

Jeder Einstiegspunkt verlangt ein gespeichertes `Event` und wirft sonst
`ValueError`. Es gibt bewusst keine „alle Felder aller Events"-Sicht: die
Feldtabelle ist die Allowlist, gegen die ein untrusted Dokument aufgelöst wird,
und eine Liste, die nicht an ein Event gebunden ist, wäre eine Allowlist für den
falschen Mandanten.

Alle Abfragen laufen über `event.questions` bzw.
`event.organizer.meta_properties` — Reverse-Accessoren des Events selbst, also
strukturell eingeschränkt statt über ein Filter, das man vergessen kann. Wo doch
ein Manager benutzt wird (`Seat`, `Voucher`, `Item` in `choices`), steht
`event=ctx.event` explizit dabei; `Seat` hat gar keinen `ScopedManager`
(`docs/pretix-api-notes.md` 6.8), dort ist das Filter die einzige Absicherung.

**Die Registry ruft nie `scopes_disabled()`.** Sie berührt gescopete Modelle und
braucht deshalb einen aktiven Scope — genau den, den Control-Backend, API und
jeder `EventTask`-basierte Celery-Task bereitstellen
(`docs/pretix-api-notes.md` Abschnitt 7). Die Mandantentrennung an der Stelle
abzuschalten, die entscheidet, welche Daten ein Report benennen darf, wäre
genau der falsche Ort für eine Bequemlichkeit. Tests öffnen selbst einen Scope.

Zusätzlich prüft jedes ereignisgebundene Callable (`annotation`, `choices`), ob
`ctx.event` das Event ist, für das es gebaut wurde, und ob `ctx.base` stimmt.
Das kostet einen Vergleich und schließt den einen Weg, über den ein gecachtes
Feld Daten eines anderen Events lesen könnte.

---

## 9. Konsequenzen

**Positiv**

- Ein importiertes Dokument kann nur Felder benennen, die jemand bewusst
  aufgeschrieben hat. Zwei unabhängige Schichten (Key-Grammatik,
  Registry-Allowlist) müssten gleichzeitig versagen.
- Unauflösbare Keys sind ein regulärer Zustand mit einer maschinenlesbaren
  Begründung, nicht ein Stacktrace.
- Fremdplugins sind erweiterbar, ohne dass ein Plugin ein anderes oder den Kern
  beschädigen kann — auch nicht durch eine Exception.
- Ein Produkt-Edit invalidiert nichts, weil volatile Daten strukturell hinter
  Callables liegen.

**Negativ / Preis**

- Die Feldtabelle muss bei pretix-Upgrades gepflegt werden.
- Zahlen- und Datetime-Fragen sind nur textuell filterbar.
- Antwort-Felder sind auf Basis `order` nicht filterbar.
- `payment.providers`, `seat.name`, `order.full_code` und `position.code` sind
  reine Anzeigespalten.
- `query-dev` muss zwei Registry-Eigenheiten kennen (ein gemischtes
  `annotate()`, `hints.aggregate_filter`), die im Stub aus Welle 0c nicht
  vorkommen. Siehe
  `handoff/requests/registry-dev-an-query-dev-annotationen-und-aggregate.md`.
- Der Cache ist mit einem nicht geteilten Backend bis zu `MAX_AGE` Sekunden
  veraltet.

**Bindend für spätere Wellen**

1. Neue Kernfelder werden in `registry/core.py` eingetragen, nie introspektiert.
2. Alles Volatile gehört hinter `choices`/`annotation`, nicht in die
   Feldstruktur — sonst muss Abschnitt 7 erweitert werden.
3. Annotationsaliase beginnen mit `pcr_`; derselbe Wert benutzt denselben Alias.
4. ORM-Pfade für Aggregate kommen aus `field.orm_path`, die zugehörige
   Bedingung aus `registry.hints`, niemals aus einem Dokument.
5. `ReportField.extra` bleibt JSON-serialisierbar.
6. Fremdplugin-Felder ausschließlich unter `plugin.<app_label>.<name>`.
