# ADR 0001 — Contracts: Feld-Keys, Definition-Schema, Protokolle

- **Status:** vorgeschlagen (wird mit `handoff/contracts-freigegeben.md` akzeptiert und eingefroren)
- **Datum:** 2026-07-27
- **Autor:** `contract-architect` (Welle 0c)
- **Betrifft:** alle Agents der Wellen 1–4. Jeder Abschnitt ist bindend.
- **Baut auf:** ADR 0000 (Grundaufbau), `docs/pretix-api-notes.md` (verbindliche
  API-Referenz), `SPEC.md` Abschnitte 3–6.

---

## Kontext

Vier Agents entwickeln in Welle 1 **parallel und ohne Rücksprache** gegen diese
Schnittstellen. Jede Unschärfe hier kostet vier Nacharbeiten. Umgekehrt gilt:
alles, was hier nicht festgelegt ist, wird später vier Mal unterschiedlich
entschieden.

Die zwei fachlichen Randbedingungen, aus denen fast alles Folgende fällt:

1. **Gespeichertes und importiertes JSON ist Untrusted Input** (`CLAUDE.md`
   Regel 2, `SPEC.md` Abschnitt 4). Es darf niemals einen ORM-Pfad, einen Lookup
   oder einen Operator enthalten, der ungeprüft in ein Queryset wandert.
2. **Report-Definitionen müssen über Events hinweg portabel sein** (`SPEC.md`
   F9/F10). Sie dürfen keine Primärschlüssel enthalten.

Beides zusammen ergibt: das JSON enthält **nur Feld-Keys**, und die Registry ist
die einzige Stelle, an der aus einem Key etwas Technisches wird.

---

## 1. Entscheidung: was in den Contracts liegt

`pretix_custom_reports/contracts/` enthält fünf Module:

| Modul | Inhalt |
| --- | --- |
| `errors.py` | Ausnahmehierarchie unter `ContractError` |
| `fields.py` | `ReportField`, alle Enums, Key-Grammatik, Operator-Tabelle |
| `definition.py` | Dokument-Dataclasses, Grenzwerte, Strukturvalidator, `SCHEMA_VERSION` |
| `protocols.py` | `FieldRegistry`, `QueryCompiler`, `CompiledReport`, geteilte Konstanten |
| `stubs.py` | lauffähige Attrappen für Registry und Compiler |

Keine Geschäftslogik: kein Queryset-Bau, keine Registry-Befüllung, keine
Datumsauflösung. Die einzigen Ausnahmen sind bewusst gewählt und in Abschnitt 12
begründet.

---

## 2. Entscheidung: Namensschema für Feld-Keys

Ein Key ist `<namespace>.<rest>`, getrennt am **ersten** Punkt:

```
order.code                      Kernfeld von Order
position.attendee_name          Kernfeld von OrderPosition
invoice_address.company         Kernfeld eines verbundenen Modells
answer.tshirt-size              Antwort auf Question(identifier="tshirt-size")
meta.event.campaign             Event-Meta-Property "campaign"
plugin.pretix_seating.zone      Feld eines Fremdplugins
```

**Regeln** (erzwungen von `contracts.validate_key`):

- Namespace kleingeschrieben und aus `ALL_NAMESPACES`. Die 15 Kern-Namespaces
  sind reserviert; Fremdplugins bekommen ausschließlich `plugin.`.
- Der Rest darf `A-Z a-z 0-9 . - _` enthalten — **exakt** der Zeichensatz, den
  `Question.identifier` erlaubt (`pretix/base/models/items.py:1683-1694`). Damit
  ist jeder legale Identifier wörtlich darstellbar, auch mit Punkten darin.
- Getrennt wird am ersten Punkt. Nur deshalb ist ein Identifier wie
  `arrival.date` eindeutig.
- **Kein doppelter Unterstrich, nirgends.**
- Keine leeren Segmente, kein führender/abschließender Punkt, höchstens 250
  Zeichen.

### Warum das `__`-Verbot

`__` ist Djangos Lookup-Trennzeichen. Der Angriff, gegen den dieser Contract
gebaut ist, sieht so aus:

```json
{"field": "order.event__organizer__slug"}
```

Das ist heute harmlos, weil der Key nur als Dictionary-Schlüssel in die Registry
geht und dort nicht gefunden wird. Das `__`-Verbot macht es **strukturell**
unmöglich, dass ein gespeicherter Key jemals als mehrstufiger ORM-Pfad
missverstanden wird — auch von künftigem, fehlerhaftem Code, der einen Key
versehentlich in ein `filter()` reicht. Zwei unabhängige Schutzschichten statt
einer.

**Preis:** eine Frage, deren `identifier` einen doppelten Unterstrich enthält,
kann nicht als `answer.<identifier>` angeboten werden. Die Registry muss ein
solches Feld überspringen und das sichtbar machen. Das ist ein sehr seltener
Fall — automatisch erzeugte Identifier bestehen aus
`ABCDEFGHJKLMNPQRSTUVWXYZ3789` (`items.py:1800-1812`) und enthalten überhaupt
keine Unterstriche —, und der Nutzer kann den Identifier umbenennen. Die
Alternative wäre gewesen, das Verbot auf den Namespace zu beschränken; dann wäre
die Schutzwirkung genau dort weg, wo die Keys aus Nutzerdaten stammen.

### Warum `plugin.<app_label>.<name>` kollisionsfrei ist

Django erzwingt eindeutige App-Labels innerhalb einer Installation. Zwei Plugins
können deshalb nie denselben Key erzeugen. `SPEC.md` Abschnitt 6 verlangt eine
definierte Konfliktbehandlung bei doppelten Keys; sie lautet: Kern-Namespaces
sind für Fremdplugins gesperrt, und `ReportField.__post_init__` weist ein Feld
zurück, dessen `provider` nicht zu seinem Namespace passt. Damit ist der Konflikt
nicht „aufgelöst", sondern nicht konstruierbar.

### Verworfene Alternativen

- **Modell + Feldname automatisch aus `Model._meta`.** Verworfen: gibt interne
  Felder frei (`secret`, `internal_secret`, `session_key`-artige), und ein
  Feld-Umbenennen in pretix würde stillschweigend alle gespeicherten Reports
  brechen. `SPEC.md` Abschnitt 6 verlangt ausdrücklich eine handgepflegte
  Auswahl.
- **Opake IDs (UUID je Feld).** Portabel, aber im JSON, im Log und im
  Fehlerdialog unlesbar. Ein Nutzer, der eine exportierte Datei ansieht, soll
  verstehen, was drinsteht.
- **Fragen über `Question.pk`.** Siehe Abschnitt 3.

---

## 3. Entscheidung: Portabilitätsstrategie

### 3.1 Keys sind portabel, Werte nicht immer

Ein Key enthält per Konstruktion keinen Primärschlüssel. Damit ist der
*Spaltenteil* einer Definition zwischen Events übertragbar, ohne dass irgendetwas
übersetzt werden müsste.

Für **Filterwerte** gilt das nicht automatisch. `order.status = "p"` bedeutet
überall dasselbe; `item = 42` nicht. `ReportField.value_scope`
(`ValueScope.GLOBAL` / `ValueScope.EVENT`) deklariert das pro Feld. Die Registry
soll `EVENT` möglichst vermeiden, indem sie natürlich stabile Werte anbietet —
die Referenz-Feldliste filtert Produkte deshalb über `item.internal_name` und
Kategorien über `item.category` (Name), nicht über PKs. Wo `EVENT` unvermeidlich
ist, muss die Portabilitätsschicht die Werte beim Import und beim Laden einer
Organizer-Vorlage über die Labels aus `ReportField.choices` per Name-Matching neu
auflösen und das dem Nutzer anzeigen (`SPEC.md` F9).

### 3.2 Fragen über `Question.identifier`

Verifiziert (`docs/pretix-api-notes.md` Abschnitt 6.4):

- eindeutig pro Event auf DB-Ebene (`unique_together ('event', 'identifier')`),
- nie leer (wird beim ersten `save()` erzeugt),
- **übersteht Event-Kopien**: `copy_data_from` setzt nur `pk = None` und
  `event = self` (`pretix/base/models/event.py:1090-1099`),
- **aber jederzeit vom Nutzer änderbar**, im Backend wie über die API.

Daraus folgt die zentrale Festlegung: **„Key nicht auflösbar" ist ein regulärer
Zustand, kein Fehlerfall.** Konkret bindend:

1. `FieldRegistry.resolve()` gibt `None` zurück, es wirft nicht.
2. Eine Definition mit unauflösbaren Keys bleibt **ladbar und anzeigbar**. Der
   Editor markiert die betroffenen Spalten, statt die Seite scheitern zu lassen.
3. Beim Import bietet die Portabilitätsschicht „überspringen / abbrechen" an
   (`SPEC.md` F9), niemals stilles Verschlucken.
4. Beim **Ausführen** ist ein unauflösbarer Key dagegen ein harter Fehler:
   `FieldResolutionError` mit der vollständigen Liste. Ein Report, der stillschweigend
   Spalten weglässt, ist schlimmer als ein Report, der nicht läuft.
5. Der Exporter fängt das ab und wirft `ExportError` — siehe Abschnitt 5.2.

Die Registry sollte Antwort-Keys **case-insensitiv** auflösen, weil pretix die
Eindeutigkeit von `identifier` case-insensitiv prüft
(`Question._clean_identifier`) und damit zwei Fragen, die sich nur in der
Groß-/Kleinschreibung unterscheiden, gar nicht existieren können.

---

## 4. Entscheidung: Strukturvalidierung und Feldauflösung sind getrennt

Der Validator in `definition.py` prüft **nur die Struktur**:

- Schlüssel bekannt, JSON-Typen korrekt, `schema_version` unterstützt
- Feld-Keys **wohlgeformt** (Grammatik aus Abschnitt 2)
- Operatoren, Aggregate, Basen, Richtungen sind Enum-Mitglieder
- `value` passt zur `ValueKind` des Operators
- Größen- und Verschachtelungsgrenzen

Er prüft **nicht**, ob ein Feld existiert, ob der Operator zu diesem Feld passt,
ob es sortierbar ist oder auf dieser Basis verfügbar. Dafür braucht es ein Event
und die Registry.

### Warum diese Trennung nicht kosmetisch ist

- **`persistence-dev`** kann JSON validieren und speichern, ohne dass die
  Registry existiert. Das ist die Voraussetzung dafür, dass er in Welle 1
  parallel arbeiten kann.
- **`frontend-dev`** kann einen Entwurf lokal und ohne Serverrunde prüfen.
- **`query-dev`** entwickelt gegen `contracts.stubs`, nicht gegen die echte
  Registry.
- **`SPEC.md` F9** verlangt es fachlich: Registry-Validierung findet **beim
  Import** statt, nicht beim Speichern des JSON. Eine gespeicherte Definition
  darf einen Key enthalten, der heute nicht auflösbar ist (umbenannte Frage) und
  morgen wieder — sonst wäre sie beim Umbenennen unrettbar verloren.

### Die Naht zwischen den Stufen

`ReportDefinition.iter_field_references()` liefert jede Key-Verwendung mit Pfad,
Nutzungsart, Aggregat und Operator. Die zweite Stufe ist eine Schleife darüber;
`contracts.find_unresolved_fields()` ist die geteilte Kurzform, damit Importer,
Editor und Exporter garantiert dieselbe Prüfung machen.

Die zwei Stufen haben zwei eigene Ausnahmen: `DefinitionValidationError`
(Struktur, sammelt **alle** Fehler mit stabilen Codes aus `ErrorCode`) und
`FieldResolutionError` / `CompilationError` (Registry-Stufe). Die
`invalid/`-Fixtures sind entsprechend mit `stage` markiert
(`tests/fixtures/definitions/invalid/_expectations.json`); vier davon sind
absichtlich strukturell **gültig** und scheitern erst in Stufe zwei. Ohne diese
Markierung würde die Trennung im Test wieder verschwimmen.

---

## 5. Entscheidung: Report-Referenz und Ablage in `export_form_data`

`pretix-researcher` hat diese Frage bewusst offengelassen
(`docs/pretix-api-notes.md` Abschnitt 14.1): Core benutzt durchgängig PKs.

### 5.1 Entscheidung: stabiler `identifier`, kein PK

Jede `ReportDefinition` bekommt ein Feld `identifier`, modelliert nach
`Question.identifier`:

- `CharField(max_length=190)`, Zeichensatz `^[a-zA-Z0-9.\-_]+$`
  (`contracts.IDENTIFIER_RE`, `contracts.validate_identifier`)
- eindeutig pro Event (`unique_together ('event', 'identifier')`) bzw. pro
  Organizer für Vorlagen (`event IS NULL`)
- wird automatisch erzeugt, wenn der Nutzer keinen angibt
- bleibt bei Event-Kopie und beim Instanziieren einer Organizer-Vorlage
  **erhalten**; nur bei Kollision im Ziel wird ein Suffix angehängt

Der Exporter legt ihn unter `contracts.EXPORT_FORM_REPORT_KEY` (`"report"`) in
`export_form_data` ab.

**Begründung, gegen die Core-Gewohnheit:**

1. **Multi-Event.** `register_multievent_data_exporters` bekommt eine
   Event-Liste; `ScheduledOrganizerExport` läuft über alle ausgewählten Events
   (`services/export.py:439-444`). Ein PK zeigt immer in genau ein Event. Ein
   Identifier lässt sich je Event auflösen — und genau das ist die Konstellation,
   die aus `SPEC.md` F10 entsteht: dieselbe Vorlage in mehreren Events, deren
   Kopien denselben Identifier tragen.
2. **Fehlermeldung.** `export_form_data` wird beim Ausführen nicht revalidiert
   (`docs/pretix-api-notes.md` Abschnitt 5.2). Wenn eine Referenz tot ist, ist
   „Report 'attendee-list' does not exist in event DUMMY" für den Empfänger der
   Fehlermail brauchbar, „Report 42 not found" nicht.
3. **Import/Export.** Der Identifier ist ohnehin das, was in einer exportierten
   Datei als Wiedererkennungsmerkmal taugt.

**Preis:** eine zusätzliche Spalte plus Unique-Constraint plus
Generierungslogik für `persistence-dev`, und ein Formularfeld, das kein
`ModelChoiceField` ist (sonst würde `ExporterForm.clean` den PK daraus machen,
`pretix/control/forms/orders.py:264-278`). Ein `ChoiceField`, dessen Choices aus
den Reports des Events kommen, ist der passende Typ.

**Sicherheitsauflage, unabhängig vom Typ:** Der Exporter darf den Wert aus
`export_form_data` niemals ungeprüft nachschlagen. Die Abfrage ist immer
`event.<related_name>.get(identifier=...)`, nie eine globale. Sonst wäre ein
manipuliertes `export_form_data` ein organizer-übergreifendes Leck.

### 5.2 Auflage an den Exporter (Welle 2)

Aus `docs/pretix-api-notes.md` Abschnitt 5.6, Fall B: fehlt ein in
`export_form_data` referenziertes Objekt, fängt pretix das **nicht** ab. Es gibt
fünf Celery-Retries à 120 s und danach eine Mail mit dem Text „Internal Error".

Bindend: `exporters.py` fängt `ReportNotFoundError` und jede andere
`ContractError` ab und wirft `pretix.base.services.export.ExportError` mit
verständlichem Text. Die Ausnahmehierarchie in `contracts/errors.py` existiert
genau dafür — ein `except ContractError` genügt.

---

## 6. Entscheidung: Contracts sind reines Python, `__init__` re-exportiert

`contracts/` importiert **ausschließlich die Standardbibliothek**. Django- und
pretix-Typen erscheinen nur unter `typing.TYPE_CHECKING`.

Gründe: `python -c "from pretix_custom_reports.contracts import *"` läuft ohne
konfigurierte Settings und ohne Datenbank; die Contract-Tests laufen in
Millisekunden; und ein Contract, der Modelle importiert, zieht früher oder später
Geschäftslogik an. `label` ist deshalb als `Any` typisiert und darf ein `str`,
ein Django-Lazy-Objekt oder ein `LazyI18nString` sein — alles, was `str()`
überlebt.

**Bewusste Abweichung von ADR 0000 Abschnitt 9, Punkt 3**, der verlangt, dass
Paket-`__init__` importfrei bleiben. Die dortige Begründung ist, dass
Re-Exports ein `__init__` zu einem gemeinsamen Schreibziel mehrerer Agents
machen. Das trifft hier nicht zu: `contracts/` hat genau einen Eigentümer und ist
nach der Freigabe eingefroren. Der flache Namensraum ist umgekehrt genau das,
was die vier Agents importieren, und die Definition of Done dieser Welle verlangt
ihn ausdrücklich. `views/__init__.py` und die übrigen Pakete bleiben unberührt.

`stubs.py` wird **nicht** mit re-exportiert. Wer Attrappen benutzt, schreibt das
hin: `from pretix_custom_reports.contracts.stubs import stub_registry`. Ein
versehentlicher Stub im Produktivpfad soll im Diff sichtbar sein.

---

## 7. Entscheidung zu F3: Positionsfelder bei Basis `order` sind **aggregiert**, nicht gesperrt

`SPEC.md` F3 lässt beide Wege offen. Gewählt: **aggregiert**.

### Modellierung

Nicht über synthetische Keys (`position.price:sum`), sondern über ein Attribut
an der Spalte:

```json
{"field": "position.price", "label": "Position sum", "aggregate": "sum"}
```

Das `ReportField` deklariert dazu:

- `bases` — auf welchen Basen es überhaupt vorkommt,
- `requires_aggregate_on` — auf welchen Basen ein Aggregat **Pflicht** ist,
- `aggregates` — welche Aggregate erlaubt sind.

Erlaubte Aggregate: `count`, `count_distinct`, `sum`, `min`, `max`, `avg`,
`join`. `SPEC.md` nennt fünf; `avg` und `count_distinct` sind ergänzt, weil beide
in Django Einzeiler sind und „Anzahl verschiedener Produkte je Bestellung" sonst
nicht ausdrückbar wäre.

### Begründung

- **Sperren wäre eine Sackgasse.** Die häufigsten Fragen an einen
  bestellbasierten Report sind „wie viele Positionen", „Summe der Positionen",
  „welche Produkte". Mit einer Sperre müsste man den Report auf Basis
  `orderposition` bauen und bekäme jede Bestellung mehrfach — also genau die
  Zeilenvervielfachung, die Basis `order` vermeiden soll.
- **Der Key bleibt stabil.** `position.price` ist auf beiden Basen derselbe Key;
  nur die Verwendung unterscheidet sich. Ein Report, der von `orderposition` auf
  `order` umgestellt wird, verliert seine Spalten nicht, er braucht nur
  Aggregate. Mit synthetischen Keys wären das zwei getrennte Feldbibliotheken.
- **Die Feldbibliothek bleibt gleich groß.** Sieben Aggregate × ~30
  Positionsfelder wären 210 zusätzliche Einträge in der Suchliste.

### Zwei Folgeentscheidungen

**a) Filter auf Positionsfeldern brauchen bei Basis `order` *kein* Aggregat.**
„Bestellungen, die ein Produkt X enthalten" ist eine EXISTS-Bedingung und
semantisch eindeutig. Nur *Spalten* brauchen eines, weil nur dort eine
Einzelzelle entstehen muss.

**b) Sortieren nach aggregierten Werten ist in v1 nicht möglich.** Die Registry
liefert Positionsfelder auf Basis `order` mit `sortable=False`. Grund: sinnvoll
wäre „sortiere nach der Summe", und das setzt voraus, dass die Sortierung weiß,
welches Aggregat gemeint ist — also ein `aggregate` auch im Sortiereintrag, mit
der Folgefrage, was passiert, wenn das Aggregat in keiner Spalte vorkommt.
`SPEC.md` F7 verlangt nur „(Feld, Richtung)". Die Erweiterung ist rückwärts­
kompatibel möglich (ein optionales `aggregate` im Sortiereintrag), kostet dann
aber keine Migration. Nicht in v1.

---

## 8. Entscheidung: Log-Action-Types

Präfix **`pretix_custom_reports.`**, also `pretix_custom_reports.report.added`,
`.changed`, `.deleted`, `.executed`, `.exported`, `.imported`,
`.template_applied` (Konstanten in `contracts/protocols.py`).

`pretix-researcher` hatte die Frage offengelassen
(`docs/pretix-api-notes.md` Abschnitt 14.3) und die Doku nicht abrufen können.
Sie ist im Klon vorhanden und eindeutig,
`doc/development/implementation/logging.rst`:

> The positional `action` argument should represent the type of action and
> should be **globally unique**, we recommend to **prefix it with your package
> name**, e.g. `paypal.payment.rejected`.

Die Core-Plugins benutzen `pretix.plugins.<name>.<objekt>.<verb>` (z. B.
`pretix.plugins.badges.layout.added`, `pretix/plugins/badges/views.py:106`). Das
ist für ein Fremdplugin keine passende Vorlage: `pretix.` ist der Namensraum des
Kerns, und `LogEntryTypeRegistry` ist eine globale Registry über alle Plugins
(`pretix/base/signals.py:480`). Unser Paketname ist eindeutig und entspricht
wörtlich der Empfehlung.

Zusätzlich verifiziert und bindend: **keine `data`-Schlüssel, die `password`,
`secret` oder `api_key` als Teilstring enthalten.** `log_action` maskiert solche
Werte per Substring-Match und mutiert das übergebene Dict dabei **in place**
(`pretix/base/models/base.py:153-163`). Ein Feld-Key `secret` im Log-Payload
macht die Logs unbrauchbar.

Anzeige über `pretix.base.logentrytype_registry`, nicht über die als deprecated
markierten Signale `logentry_display` / `logentry_object_link`
(`pretix/base/signals.py:879-893`).

---

## 9. Weitere festgelegte Punkte (kurz begründet)

**Operatoren sind ein geschlossener, semantischer Satz.** `contains` heißt
„enthält, ohne Rücksicht auf Groß-/Kleinschreibung"; dass daraus `__icontains`
wird, entscheidet der Compiler. Ein gespeicherter Operator sieht damit nie wie
ein ORM-Lookup aus. Sechs relative Datumsoperatoren decken `SPEC.md` F6 ab
(`relative_today`, `relative_last_days`, `relative_next_days`,
`relative_current_month`, `relative_current_year`,
`relative_since_event_start`); `relative_next_days` ist ergänzt, weil
Subevent-Daten in der Zukunft liegen.

**`ValueKind` statt Typprüfung gegen das Feld.** Jeder Operator deklariert, ob
er keinen Wert, einen Skalar, eine Liste, ein Paar oder eine Tageszahl erwartet.
Das ist die einzige Wertprüfung, die ohne Registry möglich ist — und sie fängt
bereits `relative_last_days: "seven"` und `between: 5` ab. Der verbleibende
Konflikt (Text in ein Geldfeld) gehört strukturell in Stufe zwei; die Fixture
`invalid/field_type_conflict.json` dokumentiert das ausdrücklich.

**Formatierung über benannte Stile, nicht über Formatstrings.** `date_style`,
`number_style`, `boolean_style` sind Enums, `separator` ist auf acht Zeichen
begrenzt. Ein `strftime`-Muster aus einer importierten Datei wäre ein
Freitextfeld mit Codegeruch für einen Nutzen, den `SPEC.md` nicht verlangt.

**Genau eine Verschachtelungsebene.** Wurzelgruppe: Bedingungen und Gruppen.
Verschachtelte Gruppe: nur Bedingungen. Eine leere Wurzelgruppe wird zu
„kein Filter" normalisiert; eine leere verschachtelte Gruppe wird abgelehnt, weil
ein leeres ODER keine definierte Bedeutung hat.

**Mindestens eine Spalte.** Ein Report ohne Spalten liefert einen leeren Export;
pretix macht daraus bei jedem terminierten Lauf einen `ExportEmptyError` samt
Mail (`docs/pretix-api-notes.md` Abschnitt 5.6). Ihn gar nicht erst speicherbar
zu machen ist freundlicher. `contracts.empty_definition()` liefert den
UI-Startzustand, der bewusst noch nicht validiert.

**Harte Obergrenzen** (200 Spalten, 100 Bedingungen, 25 Gruppen, 8
Sortierstufen, 500 Listenwerte, 1000 Zeichen je Wert). Eine importierte Datei ist
Untrusted Input; ohne Grenzen ist sie ein billiger DoS gegen den Editor und den
Query-Planer.

**Optionen bleiben minimal**: `include_canceled_positions` (`OrderPosition.all`
statt `.objects`, `docs/pretix-api-notes.md` Abschnitt 6.2),
`include_testmode_orders`, `row_limit`. Alles, was sich als Filter ausdrücken
lässt, ist ein Filter — „nur bezahlte Bestellungen" ist
`order.status in ["p"]`, keine Option.

**`schema_version` steht im JSON selbst**, nicht nur in einer Modellspalte. Ein
JSON-Blob, der nur neben seiner Datenbankspalte interpretierbar ist, ist nicht
portabel. `persistence-dev` darf die Zahl für indizierte Abfragen in eine Spalte
spiegeln; maßgeblich ist das JSON.

**`CompiledReport` liefert nur sichtbare Spalten.** Versteckte Spalten
(`hidden: true`) bleiben in der Definition erhalten und werden beim Kompilieren
verworfen. Der Exporter muss nicht filtern.

**Ein `CompiledReport` gehört zu genau einem Event.** Multi-Event-Exporte
kompilieren je Event und hängen die Ergebnisse aneinander. Damit ist
`CLAUDE.md` Regel 4 (jedes Queryset hart auf ein Event begrenzt) trivial erfüllt,
statt in jedem Queryset neu nachgewiesen werden zu müssen.

---

## 10. Golden Fixtures als geteilte Testbasis

`tests/fixtures/definitions/` enthält zehn gültige Definitionen, eine
Export-Hülle und siebzehn ungültige Beispiele. Zwei Punkte sind bindend:

1. **Jeder Feld-Key, den eine gültige Fixture benutzt, muss in der echten
   Registry existieren** — die Liste steht maschinenlesbar in `_index.json`
   unter `required_field_keys`. Sonst sind die Fixtures keine gemeinsame Basis,
   sondern vier verschiedene.
2. **Außerhalb von `invalid/` steht kein ORM-Pfad in einer Fixture.** Der Test
   `test_valid_fixture_uses_no_orm_paths` sichert das ab.

Dateien mit führendem Unterstrich (`_index.json`,
`invalid/_expectations.json`) sind Metadaten, keine Fixtures.

---

## 11. Konsequenzen

**Positiv**

- Ein gespeichertes oder importiertes JSON kann per Konstruktion keinen ORM-Pfad
  transportieren; zwei unabhängige Schichten (Key-Grammatik, Registry-Allowlist)
  müssten gleichzeitig versagen.
- Die vier Wellen-1-Agents hängen nicht aneinander: Struktur ohne Registry,
  Registry ohne Compiler, Compiler gegen Stub, Frontend gegen Fixtures.
- Definitionen sind zwischen Events übertragbar, ohne dass Import und
  Vorlagen-Laden zwei verschiedene Mechaniken brauchen.
- Fehler sind maschinenlesbar (`ErrorCode`) und vollständig (alle auf einmal),
  nicht „erster Fehler, englischer Text".

**Negativ / Preis**

- Der Aggregat-Mechanismus macht `query-dev` messbar aufwendiger als eine Sperre:
  sieben Aggregatfunktionen, Subquery-Annotationen statt Joins, und die
  Doppelzählungsfalle bei mehreren aggregierten Spalten in einer Abfrage.
- Der stabile `identifier` ist zusätzlicher Aufwand für `persistence-dev` und
  weicht von der Core-Gewohnheit ab.
- Das `__`-Verbot macht Fragen mit doppeltem Unterstrich im Identifier
  unbenutzbar.
- Der Contract ist umfangreich. Wer nur eine Spalte hinzufügen will, muss
  trotzdem `ReportField` verstehen.

**Bindend für spätere Wellen**

1. Feld-Keys ausschließlich nach Abschnitt 2. Kern-Namespaces sind für
   Fremdplugins gesperrt.
2. Unauflösbare Keys sind beim Laden und Importieren regulär, beim Ausführen
   fatal (Abschnitt 3.2).
3. Struktur- und Registry-Validierung bleiben getrennt (Abschnitt 4).
4. Report-Referenz über `identifier`, Nachschlagen immer eventgebunden
   (Abschnitt 5.1). Der Exporter wirft `ExportError` (Abschnitt 5.2).
5. Positionsfelder auf Basis `order` nur mit Aggregat, nicht sortierbar
   (Abschnitt 7).
6. Log-Action-Types mit dem Präfix aus Abschnitt 8, keine sensiblen
   Teilstrings in `data`.
7. Die `required_field_keys` aus `_index.json` müssen in der Registry
   existieren (Abschnitt 10).

Änderungswünsche an all dem: `handoff/blockers.md`, Arbeit stoppen, eskalieren
(`CLAUDE.md` Regel 7).

---

## 12. Was hier bewusst nicht entschieden wurde

- **Wie die Registry gecacht wird.** Der Contract sagt „pro `(event, base)`" und
  schweigt zu `cached_property`, `django.core.cache` oder gar nichts.
- **Wie Aggregate in SQL umgesetzt werden.** `Subquery` vs. `annotate` mit
  `distinct=True` ist eine Performance-, keine Vertragsfrage. `SPEC.md`
  Abschnitt 4 verlangt lediglich, dass kein N+1 entsteht.
- **Welche Kernfelder es über die `required_field_keys` hinaus gibt.**
  `registry-dev` entscheidet; `SPEC.md` F4 nennt den Umfang.
- **Wie relative Datumsfilter auf Zeitzonen abgebildet werden.** Terminierte
  Exporte laufen unter `override(schedule.tz)` und mit der Sprache des Schedules
  (`docs/pretix-api-notes.md` Abschnitt 5, Fallstrick 7). Dass das beachtet
  werden muss, steht hier; wie, entscheidet `query-dev`.
- **Das Datenmodell selbst.** `SPEC.md` Abschnitt 5 ist ein Vorschlag.
  Festgelegt sind aus dieser ADR nur `identifier`, `schema_version` im JSON und
  die Existenz von `source_template` (`SPEC.md` F10).

Die drei kleinen Zugeständnisse an „keine Geschäftslogik in den Contracts"
sind: `find_unresolved_fields()` (acht Zeilen, damit alle dieselbe Prüfung
machen), die Invarianten in `ReportField.__post_init__` (Typprüfung, kein
Verhalten) und die Registry-Stufen-Prüfungen im `StubQueryCompiler` (ohne die
wäre der Stub kein brauchbares Übungsziel). Jede davon ersetzt vier gleiche
Implementierungen durch eine.
