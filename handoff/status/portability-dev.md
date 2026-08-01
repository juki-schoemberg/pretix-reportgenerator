# Status: portability-dev — Welle 2

`pretix_custom_reports/portability/**` (2.1 kLOC), `views/portability.py`,
`views/templates.py`, sieben neue HTML-Templates, `tests/test_portability.py` +
`tests/test_org_templates.py` (**117 Tests, alle grün**). Contracts
unangetastet, kein Commit, keine fremde Python-Datei angefasst.

> **Hinweis zur Sitzung.** Der erste Lauf wurde vom Ausgabenlimit abgebrochen,
> unmittelbar vor dem Rauchtest der fertigen Module und damit vor den Views.
> Der zweite Lauf hat dort weitergemacht: Rauchtest, beide Views, beide
> Testmodule, Handoffs, dieser Bericht.

## Erledigt

### 1. Der Auslesepfad, in genau dieser Reihenfolge

```
rohe Bytes / eingefügter Text
  -> payload.load_json_object()      Größe, Tiefe, Knotenzahl, nur JSON
  -> envelope.parse_document()       Struktur, geschlossene Enums, keine ORM-Pfade
  -> resolution.resolve_definition() jeder Key gegen die Registry des ZIELEVENTS
  -> ImportPlan                      wird angezeigt, nichts geschrieben
  -> commit_import()                 eine Zeile, ein Log-Eintrag
```

Die Reihenfolge **ist** der Sicherheitsentwurf, deshalb steht sie so im
Moduldocstring von `importer.py`. Nach Schritt zwei sieht niemand mehr in die
Datei; die gespeicherte Definition wird aus Registry-Keys neu gebaut, von den
Contracts kanonisiert und von `ReportDefinition.save()` ein zweites Mal geprüft.

### 2. `payload.py` — was refüsiert wird, bevor irgendjemand etwas interpretiert

| Angriff | Abwehr |
| --- | --- |
| große Datei | `MAX_PAYLOAD_BYTES = 512 KiB`, auf den rohen Bytes, vor dem Dekodieren |
| tiefe Verschachtelung | eigener Textscan (`_max_depth`) **vor** `json.loads`, Grenze 20 |
| JSON-Bombe | `MAX_NODES = 20.000`, iterativ gezählt (keine Rekursion) |
| überlange Strings | `MAX_STRING_CHARS = 10.000`, deckt auch das freie `meta`-Objekt ab |
| Dezimal-Explosion | `MAX_NUMBER_DIGITS = 30` auf dem Literal + Endlichkeitsprüfung (`1e999` → `inf`) |
| `NaN`/`Infinity` | `parse_constant` lehnt ab — JavaScript, nicht JSON |
| doppelte Member | `object_pairs_hook` lehnt ab: zwei `"columns"` heißt, Validator und Mensch lesen verschiedene Dokumente |
| Callables/Objekte | gibt es nicht: `json.loads` baut nur Primitive |

Die Tiefenprüfung läuft bewusst auf dem **Text**, nicht über einen
`RecursionError` aus dem C-Scanner — auf dessen Stackverhalten möchte ich das
Produktionsverhalten nicht stützen.

Die letzte Zeile ist die, die den ganzen Rest trägt, deshalb ist sie ein Test
und kein Vorsatz: `test_the_package_never_deserialises_anything_but_json` parst
**jede** Datei des Pakets als AST und verbietet Import von
`pickle`/`marshal`/`yaml`/`shelve`/`subprocess`/`os` sowie Aufrufe von
`eval`/`exec`/`compile`/`__import__`. Über den Syntaxbaum, nicht über den Text —
die Module *reden* in ihren Docstrings über Pickle und Eval, und genau dort
gehört diese Begründung hin.

### 3. Das Dateiformat (`envelope.py`)

Der Umschlag ist eingefroren (`contracts.PortableReport`,
`validate_portable_document`); ich fülle ihn und besitze das freie `meta`:

```json
{"schema_version": 1, "name": …, "description": …, "exported_at": …,
 "generator": "pretix-custom-reports 0.1.0", "source": "organizer/event",
 "meta": {"pretix_version": …, "base": …, "identifier": …, "created_by": …,
          "created_at": …, "references": [ … ]},
 "definition": { … }}
```

Damit sind alle von SPEC.md F9 verlangten Metadaten drin (Name, Beschreibung,
Basis, erstellt von/am, pretix-Version, `schema_version`, Definition).

- **Keine Primärschlüssel, nirgends.** `source` trägt Slugs, `meta` Identifier
  und Namen. Getestet, indem eine erzeugte Datei durchlaufen und gegen die vier
  PKs des Testfalls geprüft wird (`schema_version` ist die einzige legitime
  kleine Zahl und ausgenommen).
- **Der Dateiname ist ASCII-only** und kann den `Content-Disposition`-Header
  nicht verlassen (Test mit `../../etc/passwd "; rm -rf /` als Reportname).
- Import akzeptiert **Umschlag oder nackte Definition**, unterschieden am
  Vorhandensein von `definition` — nicht per Heuristik. Damit funktioniert
  Copy-Paste aus dem JSON-Panel des Editors genauso wie ein Dateiupload.

### 4. `meta.references` — warum es das gibt

Ein Key wie `answer.tshirt-size` ist portabel, weil er keinen Primärschlüssel
enthält. Er ist *nicht* portabel in dem Sinne, dass das Zielevent eine Frage mit
diesem Identifier hätte: Identifier tippen Menschen. Wenn er nicht passt, bleibt
nur der **Name** — und der steht nicht im Key. Deshalb schreibt der Export je
eventspezifischem Key einen Eintrag `{key, label, kind, identifier}`.

Vertrauensmodell (im Moduldocstring ausgeschrieben): die Liste ist ein
**Hinweis, nie Daten**. Sie kann ausschließlich helfen, einen Key zu *finden*,
den die Zielregistry ohnehin veröffentlicht; ein Eintrag für einen Key, den die
Definition gar nicht benutzt, wird verworfen; ein kaputter Eintrag wird ignoriert
statt fatal, weil das schlimmstenfalls aus „gemappt" ein „nicht gefunden" macht —
die sichere Richtung. Kein Feld-Key verschwindet dadurch: **jeder** Key der
Definition bekommt seine Zeile im Bericht, mit oder ohne Hinweis.

### 5. Die Auflösungsschicht (`resolution.py`) — einmal gebaut, viermal genutzt

`resolve_definition(definition, *, event, registry, references, strategy)` ist
die einzige Implementierung. Aufrufer: Datei-Import, „Vorlage laden",
Event-Kopie — und sie ist dieselbe Naht, an der der Editor eine Definition gegen
ein Event prüfen könnte.

**Was sie garantiert:** alles, was sie ausgibt, kommt aus der Registry des
Zielevents. Ein Key bleibt nur stehen, wenn `registry.resolve()` ein Feld
geliefert hat, und geschrieben wird `field.key` — die Schreibweise der Registry,
nicht die der Datei. Ein Namenstreffer wird in `registry.get_fields()` gesucht
und gibt einen von *deren* Keys zurück. Filterwerte auf eventspezifischen
Feldern werden gegen `field.choices()` gematcht.

**Matching-Kaskade je Key:**

1. exakt (`registry.resolve`, für `answer.*` case-insensitiv — ADR 0001 3.2)
2. Identifier normalisiert (klein, ohne `-`/`_`/Sonderzeichen):
   `answer.TShirt-Size` → `answer.tshirt_size`, Status `mapped`, `match=identifier`
3. Name aus `meta.references` gegen `field.label`, Status `mapped`, `match=name`
4. sonst `missing`. Mehrere gleich gute Treffer → `ambiguous`, **nie geraten**.

Namensmatching gibt es **nur** in den Namensräumen `answer`, `meta`, `plugin` —
dort tippt ein Mensch den Identifier. Ein Kern-Key löst auf oder eben nicht;
`order.CODE` wird nicht stillschweigend `order.code`, denn dort wäre eine
Abweichung ein Tippfehler oder ein Angriff, keine Umbenennung
(`test_a_core_key_is_never_matched_by_similarity`).

**Werte:** für Felder mit `value_scope=EVENT` und `choices` wird jeder
String-Wert gegen die Werteliste des Zielevents geprüft — exakt, sonst
normalisiert (`regular TICKET` → `Regular ticket`, als `mapped` angezeigt), sonst
`missing`. Sonderfall mit Absicht: ist die Werteliste der Registry abgeschnitten
(`registry.choices.MAX_CHOICES`, 500), heißt „nicht in der Liste" nicht „gibt es
nicht" — dann Status `unverified` und der Wert bleibt unverändert. Andernfalls
würde ein Event mit 600 Produkten beim Import Filter verlieren.

**Drei Strategien:**

| | fehlende Referenz | Ergebnis |
| --- | --- | --- |
| `ABORT` (Default) | blockiert | nichts wird geschrieben |
| `SKIP` (Nutzerentscheidung) | wird entfernt, sichtbar als `dropped` | reduzierter Report |
| `KEEP` (nur Event-Kopie) | bleibt unverändert stehen | nie ein Fehlschlag |

`ResolutionStrategy.coerce()` fällt bei jedem unbekannten Wert auf `ABORT`
zurück — der Wert kommt aus einem Formularfeld, also aus dem Browser.

**Zwei Nachprüfungen, bevor irgendetwas gespeichert werden darf:**

1. `contracts.validate_definition()` auf dem umgebauten Dokument. Ohne das
   könnte `SKIP` ein Dokument ohne Spalten hinterlassen
   (`test_an_import_that_would_lose_every_column_is_refused`).
2. `query.plan.check_definition()` — die Prüfung des **Compilers**, nicht eine
   zweite Implementierung derselben Regeln. Sie fängt „Aggregat auf Basis
   `order` fehlt", „Feld ist hier nicht sortierbar", „Operator für ein
   Geldfeld nicht erlaubt". Wäre der Import großzügiger als der Compiler,
   speicherten wir Reports, die beim ersten terminierten Lauf scheitern
   (`docs/pretix-api-notes.md` 5.6).

### 6. Auflösungsbericht vor dem Speichern

`ResolutionReport` mit einer `ResolutionEntry` **je Referenzstelle** (nicht je
Key): Pfad (`columns[3]`, `filters.children[0]`), Verwendung, Status, Quelle,
Ziel, Quell-/Ziellabel, `match`, `dropped`, und ein Satz Begründung. Dazu
`issues` für das, was keine Entscheidung retten kann. `as_dict()` geht
unverändert in den Log-Eintrag — damit bleibt die Entscheidung nachvollziehbar,
nachdem der Bildschirm weg ist.

Der Bestätigungsschritt der View postet **den Originaltext** zurück, nicht die
aufgelöste Definition. Eine aufgelöste Definition aus einem Browser ist wieder
Untrusted Input, nur mit einem Haken daneben. Schritt zwei wiederholt deshalb
Größenprüfung, Parsing, Strukturvalidierung und Auflösung. Es gibt genau **einen**
Weg in die Datenbank, und er beginnt immer bei `load_json_object`.

### 7. Organizer-Vorlagen (`templating.py`, `views/templates.py`)

- Organizer-Ebene: Liste / Anlegen / Ändern / Löschen / Exportieren unter
  `^control/organizer/<organizer>/customreports/templates/…`, alle mit
  `OrganizerPermissionRequiredMixin` und `organizer.settings.general:write`
  (`docs/pretix-api-notes.md` 8.2). Queryset immer
  `templates_for_organizer(request.organizer)` — das ist gleichzeitig
  Mandantengrenze **und** XOR-Filter, ein Event-Report ist über diese URLs nicht
  erreichbar (getestet, 404).
- Event-Ebene: „Vorlage laden" listet die Vorlagen des **eigenen** Organizers,
  zeigt den Auflösungsbericht und erzeugt erst nach Bestätigung eine **Kopie**
  über `report.duplicate(event=…, source_template=…)` — kein Live-Link, wie
  SPEC.md F10 es für v1 verlangt (Test: Vorlage danach ändern lässt die Kopie in
  Ruhe).
- **Berechtigung an beiden Enden** (`assert_template_accessible`): das Zielevent
  braucht immer `event.settings.general:write`; eine Vorlage eines **anderen**
  Organizers zusätzlich `organizer.settings.general:write` auf **jenem**
  Organizer. Die v1-UI bietet nur Vorlagen des eigenen Organizers an, der
  cross-Organizer-Pfad ist also aus dem Browser nicht erreichbar — die Prüfung
  existiert trotzdem und ist getestet, weil eine API oder ein
  Management-Command keine zweite Erinnerung bekommt.
- Ein `OrganizerPluginActiveMixin` gibt 404, wenn das Plugin in keinem Event des
  Organizers aktiv ist — nach dem Vorbild von
  `pretix/plugins/banktransfer/views.py` (`OrganizerBanktransferView`).

### 8. Event-Kopie (`eventcopy.py`)

Logik fertig und getestet, Registrierung liegt als kopierfertiger Empfänger in
`handoff/requests/portability-dev-an-integrator-signals.md` (Signatur wörtlich
gegen `docs/pretix-api-notes.md` 3.3 geprüft).

Entscheidungen: Strategie `KEEP` — was gemappt werden kann, wird gemappt, der
Rest bleibt stehen. Ein nicht auflösbarer Key ist ein legaler Speicherzustand
(`models.py`); Spalten still zu verwerfen wäre bei einer Event-Kopie schlechter,
den Report ganz auszulassen am schlechtesten. Ein einzelner kaputter Report
bricht die Kopie nicht ab (`CopyResult.failed`). Der Bericht landet im
`LOG_ACTION_ADDED`-Eintrag der Kopie als `copied_from_event`.

**Ein Fund, der ohne Test unsichtbar geblieben wäre:** pretix kann ein Event in
einen **anderen Organizer** kopieren (`Event.copy_data_from`,
`is_cross_organizer`), und der aktive Scope ist dabei der *Ziel*organizer. Ein
Zugriff über `source_event.custom_reports.all()` liefert dann stillschweigend
**null** Reports — kein Fehler, nur ein Event ohne Reports. Die Quellreports
werden deshalb unter `scopes_disabled()` mit hartem `event=`-Filter gelesen,
Bauweise wie `ReportDefinition._identifier_taken`. Test:
`test_an_event_copy_across_organizers_still_finds_the_reports`.

## Nicht erledigt (und warum)

- **URL-Verdrahtung und Navigationseintrag.** `urls.py` und `signals.py` gehören
  dem `integrator`. Neun Routen liegen kopierfertig als Modulvariablen
  (`portability_event_urlpatterns`, `templates_event_urlpatterns`,
  `templates_organizer_urlpatterns`) und in
  `handoff/requests/portability-dev-an-integrator-urls.md`. Bis dahin sind die
  Views produktiv **nicht erreichbar**; die Tests hängen sie über eine
  modulweite Fixture in den echten Resolver ein, exakt wie
  `tests/test_permissions.py` es seit Welle 1 macht.
- **Import-/Export-Knöpfe in `report_list.html` und im Editor.** Fremdes Gebiet
  (persistence-dev/frontend-dev). Fertiges HTML liegt in Abschnitt 6 des
  URL-Handoffs.
- **Kein Überschreiben beim Import.** Ein Import legt immer neu an. „Diese Datei
  in Report X einspielen" würde aus „schau dir das mal an" eine destruktive
  Operation machen; wenn das gewünscht ist, gehört es in eine eigene View mit
  eigener Bestätigung.
- **Kein Import auf Organizer-Ebene.** Eine Vorlage kann exportiert, aber nicht
  als Datei importiert werden. Der Grund ist inhaltlich: ohne Event gibt es
  keine Registry, gegen die aufgelöst werden könnte, also gäbe es genau die
  ungeprüfte Übernahme, die dieses Paket verhindert. Der Weg dahin ist: in ein
  Event importieren, prüfen, dort als Vorlage speichern. Nachrüstbar (Auflösung
  gegen ein wählbares Referenzevent), aber nicht in v1.
- **Vorlagen-Export trägt keine `meta.references`.** Eine Vorlage hat kein Event,
  also kein `field.label` zu ihren Fragen-Keys. Der Import matcht dann über
  Identifier — dasselbe, was „Vorlage laden" tut. Ist im Docstring der View
  vermerkt.

## Getroffene Entscheidungen

Keine neue ADR: alles liegt innerhalb von ADR 0001 (Abschnitte 3.1, 3.2, 5.1)
und SPEC.md F9/F10. Sechs Punkte, die eine Begründung brauchen und im Code
kommentiert sind:

1. **Der Bestätigungsschritt re-parst die Originalbytes** statt eine aufgelöste
   Definition aus dem Browser zu übernehmen (Abschnitt 6).
2. **Namensmatching nur in `answer`/`meta`/`plugin`.** Bei Kern-Keys wäre
   „fast passend" ein Tippfehler oder ein Angriff (Abschnitt 5).
3. **`ambiguous` ist ein Fehlschlag, kein Ratespiel.** Zwei Fragen mit demselben
   Namen → der Nutzer entscheidet, nicht der Importeur.
4. **Abgeschnittene Werteliste ⇒ `unverified`, Wert bleibt.** „Nicht in den
   ersten 500" heißt nicht „gibt es nicht".
5. **Nutzungsprüfung über `query.plan.check_definition`** statt einer zweiten
   Regelimplementierung (Abschnitt 5, Punkt 2).
6. **Auflösungstexte (`detail`) sind bewusst nicht übersetzt.** Sie landen
   wörtlich im Log; ein Log, dessen Text von der Sprache des Bearbeiters
   abhängt, ist nicht auswertbar. Für die UI sind `status`/`match` als stabile
   Codes gedacht — das Template übersetzt sie.

## Contract-Abweichungen

**KEINE.** `pretix_custom_reports/contracts/` und
`tests/fixtures/definitions/` sind unangetastet. `PortableReport` und
`validate_portable_document` haben exakt gepasst; das freie `meta` war genau der
Platz, den die Referenzliste brauchte. Kein Eintrag in `handoff/blockers.md`
nötig.

**Eine Grenzüberschreitung, die ich melde, statt sie zu verstecken:** ich habe
sieben neue HTML-Templates unter
`pretix_custom_reports/templates/pretix_custom_reports/` angelegt
(`import_form`, `import_confirm`, `resolution_report`, `template_list`,
`template_form`, `template_confirm_delete`, `template_pick`, `template_apply`).
Die Ownership-Tabelle listet Templates **einzeln je Eigentümer**; diese Namen
waren unbelegt, und ein View ohne Template rendert nicht. Keine fremde Datei
angefasst. Falls die Tabelle ergänzt werden soll: sie gehören zu
`views/portability.py` bzw. `views/templates.py` (steht auch im URL-Handoff).

## Offene Anforderungen an andere

1. **`handoff/requests/portability-dev-an-integrator-urls.md`** — neun Routen
   (drei Import-Zeilen), Navigationseintrag `nav_organizer` für die
   Vorlagenverwaltung (sonst ist sie nur über die URL erreichbar), Hinweis auf
   die sieben neuen Templates, drei Knöpfe in fremden Templates.
2. **`handoff/requests/portability-dev-an-integrator-signals.md`** — der
   `event_copy_data`-Empfänger. Sieben Zeilen, ruft nur
   `copy_reports_to_event`. Enthält die Begründung der Signatur und den
   cross-Organizer-Fallstrick.

Keine Anforderung an `persistence-dev` (`duplicate()`, `templates_for_organizer`,
`ORGANIZER_CHANGE_PERMISSION` und `validated_definition()` haben unverändert
gepasst), keine an `registry-dev` (`value_scope`, namensbasierte `choices` und
`extra[question_identifier]` sind genau das, was die Auflösung braucht), keine an
`query-dev` (`check_definition` ist die Naht, die es versprochen hat).

### API-Oberfläche für Welle 3 (kein Request, nur Wissen)

```python
from pretix_custom_reports.portability.payload import load_json_object
from pretix_custom_reports.portability.envelope import build_export_document, parse_document
from pretix_custom_reports.portability.resolution import resolve_definition, ResolutionStrategy
from pretix_custom_reports.portability.importer import plan_import, commit_import
from pretix_custom_reports.portability.templating import plan_template, apply_template
from pretix_custom_reports.portability.eventcopy import copy_reports_to_event
```

Für den `security-reviewer` die drei Stellen, an denen ich zuerst suchen würde:

1. `payload.py` — die Grenzwerte sind gesetzt, nicht gemessen. Ein
   512-KiB-Dokument mit 20.000 Knoten durch den vollen Pfad zu jagen und die
   Zeit zu messen, wäre eine sinnvolle Ergänzung (DoS über CPU statt Speicher).
2. `resolution._resolve_value_list` — der einzige Ort, an dem ein *Wert* aus der
   Datei stehen bleibt (bei `unverified` und unter `KEEP`). Er landet in
   `definition["filters"]`, wird vom Compiler typgecastet und nie als Lookup
   verwendet; das ist die Behauptung, die ein Angriffstest prüfen sollte.
3. `views/portability.py::_document_text` — liest höchstens
   `MAX_PAYLOAD_BYTES + 1` Bytes aus dem Upload. Ein Test mit einer
   absichtlich falsch gemeldeten `upload.size` gehört dazu.

## Tests

`pytest tests/test_portability.py tests/test_org_templates.py -q` →
**117 passed, 0 failed** (mehrfach, auch mit `pytest-randomly` in wechselnder
Reihenfolge).

| Datei | Tests | Inhalt |
| --- | --- | --- |
| `tests/test_portability.py` | 93 | Payload-Gate, Dateiformat, Roundtrip, alle `invalid/`-Fixtures, Auflösungsschicht, Importeur, Views, Event-Kopie |
| `tests/test_org_templates.py` | 24 | Vorlagen-Modell, Laden ins Event, Rechte an beiden Enden, Organizer-Views, Event-Views |

Die von der Definition of Done verlangten, namentlich:

| Kriterium | Nachweis |
| --- | --- |
| Roundtrip Export→Import ergibt identische Definition | `test_export_then_import_gives_an_identical_definition` (Spalten, Filter mit Choice-Werten, Sortierung), `test_the_round_trip_survives_a_second_event_with_the_same_questions`, `test_every_golden_fixture_round_trips_through_a_file` (alle 10 Golden Fixtures) |
| jede Datei aus `invalid/` wird abgelehnt, ein Test je Fall | `test_every_invalid_fixture_is_rejected_on_import`, parametrisiert über alle 17 Dateien, und zwar gegen die Stufe, die `_expectations.json` nennt: `structure` muss `DefinitionValidationError` mit den dort genannten Codes werfen, `registry` muss `plan.ok is False` liefern **und** `commit_import` muss `ImportRejected` werfen. Danach jeweils `ReportDefinition.objects.count() == 0`. Dazu ein eigener Test für `smuggled_orm_path.json`, der prüft, dass das Dokument **abgelehnt** und nicht bereinigt wird |
| Vorlage → Event mit abweichenden Fragen erzeugt korrekten Bericht | `test_loading_a_template_into_an_event_with_different_questions`: eine Referenz `found`, eine erfolgreich `mapped` (`answer.tshirt-size` → `answer.tshirt_size`, inklusive angezeigtem Ziellabel), eine `missing` (`answer.newsletter`, mit Pfad `columns[2]`); danach `ImportRejected` unter `abort` und die korrekt reduzierte Kopie unter `skip` |
| Statusbericht | diese Datei |

Bewusst enthaltene Negativfälle über die DoD hinaus: `unknown_field_key.json`
ist die **einzige** `invalid/`-Datei, die eine Nutzerentscheidung retten kann
(`skip` wirft beide Keys weg) — dass das sichtbar passiert und nicht still, ist
ein eigener Test. Weiter: Rechteprüfung auf allen Views (Nur-Lese-Nutzer 403,
fremdes Event 404, fremder Organizer 404, Plugin aus 404), Import ohne Datei,
kaputtes JSON, hostiler Identifier in der Datei, hostiler Reportname im
Dateinamen, Import, der jede Spalte verlieren würde, unbekannter
Strategie-Wert, Event-Kopie über Organizergrenzen.

Gesamtsuite `pytest tests/ -q`: **831 passed, 1 failed**. Der Fehlschlag ist
`tests/test_smoke.py::test_no_migration_created_yet` — das Welle-0-Gate, das
`persistence-dev` bewusst gebrochen hat, Ersatz liegt in dessen Handoff. In
einem von drei Gesamtläufen kam zusätzlich
`tests/test_editor_api.py::test_browser_drag_and_drop_adds_a_column` rot
(Playwright-Timeout); der Test schlägt auch beim alleinigen Lauf seines Moduls
fehl, also ohne meine Module, und ist offenbar zeitabhängig. Für `frontend-dev`
oder den `test-engineer`, nicht für mich.

Lint über die eigenen Dateien, alle drei sauber:

```
flake8   pretix_custom_reports/portability views/portability.py views/templates.py tests/test_portability.py tests/test_org_templates.py -> rc 0
isort -c (dieselben Pfade)                                                        -> rc 0
black --check (dieselben Pfade)                                                   -> 13 files unchanged
```

Kein `black .` / `isort .` über das Repo, kein `git commit`, keine Datei außerhalb
von `pretix_custom_reports/portability/**`, `views/portability.py`,
`views/templates.py`, den sieben neuen Templates, `tests/test_portability.py`,
`tests/test_org_templates.py` und `handoff/**` angefasst.

## Nächster Schritt

1. **Orchestrator/`integrator`:** die zwei Handoffs einplanen. Ohne die neun
   Routen ist der ganze Bereich nur über Tests erreichbar; ohne den
   `nav_organizer`-Eintrag findet niemand die Vorlagenverwaltung; ohne den
   `event_copy_data`-Empfänger verliert jede Event-Kopie ihre Reports.
2. **`frontend-dev`:** Import-/Export-Knöpfe an `event.reports.import` und
   `event.reports.export` hängen (Abschnitt 6 des URL-Handoffs). Das JSON-Panel
   bleibt der schnelle Weg, die Datei ist der portable.
3. **`security-reviewer` (Welle 3):** die drei Stellen aus „API-Oberfläche für
   Welle 3" oben. Besonders: eine Datei bauen, die *nach* der Auflösung noch
   etwas in der gespeicherten Definition stehen hat, das aus der Datei stammt —
   erlaubt sind dort nur Labels, Filterwerte und geschlossene Enums.
4. **`test-engineer` (Welle 3):** ein Integrationstest, der `copy_data_from`
   wirklich aufruft, sobald das Signal verdrahtet ist (Vorlage steht in
   Abschnitt 5 des Signal-Handoffs).
