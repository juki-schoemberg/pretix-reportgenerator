# Status: persistence-dev — Welle 1

## Erledigt

**Modell** (`pretix_custom_reports/models.py`)

- `ReportDefinition(LoggedModel)` mit `event` XOR `organizer` als
  **DB-CheckConstraint** (`pcr_event_xor_organizer`), `name`, `description`,
  `identifier`, `base`, `definition` (JSONField), `schema_version`,
  `source_template` (FK self, `SET_NULL`), `created_by` (FK User, `SET_NULL`),
  `created_at`, `updated_at`, explizites `id = BigAutoField`.
- `identifier` genau nach `handoff/requests/contract-architect-an-persistence-dev-report-identifier.md`:
  `max_length=IDENTIFIER_MAX_LENGTH`, `RegexValidator(IDENTIFIER_RE)`,
  `UniqueConstraint(["event", "identifier"])`,
  `UniqueConstraint(["organizer", "identifier"], condition=Q(event__isnull=True))`,
  Erzeugung in `save()` (8 Zeichen aus `ABCDEFGHJKLMNPQRSTUVWXYZ3789`, mit
  Kollisionsschleife). `ensure_unique_identifier()` hängt nur bei Kollision im
  Ziel `-2`, `-3`, … an — das ist der Pfad für Event-Kopie und
  Vorlagen-Instanziierung, `save()` selbst benennt **nie** um.
- `ReportDefinitionQuerySet`: `for_event`, `event_reports`, `templates`,
  `templates_for_organizer`, `by_identifier`.
- Strukturvalidierung in `save()` **und** `clean()` über
  `contracts.validate_definition`. Ungültiges JSON kann nicht in die DB
  (`objects.create`, `save`, `save(update_fields=…)`). Zusätzlich wird die
  Definition auf `document.as_dict()` normalisiert, `base` und
  `schema_version` werden daraus abgeleitet bzw. gegengeprüft.
  **Keine Registry-Prüfung** — ein heute unauflösbarer Key ist ein legaler
  Speicherzustand (Test `test_unresolvable_field_key_is_stored`).
- Logging: `log_added` / `log_changed` / `log_deleted` / `log_executed` als
  Modellmethoden über `contracts.LOG_ACTION_*`. Payload aus `log_data()`,
  ohne Schlüssel, die `password`/`secret`/`api_key` enthalten (sonst maskiert
  `log_action` und mutiert das Dict in place).
- `duplicate(event=…, organizer=…, keep_identifier=…)` als gemeinsame Basis für
  „Duplizieren", Event-Kopie (Welle 2) und Vorlage-Instanziieren (setzt dann
  `source_template`).

**Migration** — `migrations/0001_initial.py`, als letzter Schritt erzeugt.
`python -m pretix makemigrations pretix_custom_reports --check --dry-run` →
„No changes detected", Exit 0. Zusätzlich gegen die Dev-Instanz angewandt
(`python -m pretix migrate pretix_custom_reports` → OK); Check-Constraint und
partieller Unique-Index sind in SQLite nachgeprüft.

**Formulare** (`forms.py`) — `ReportDefinitionForm` (Name, Beschreibung,
Identifier, Basis, Definition als JSON-Textarea), `PrettyJSONFormField`
(eingerücktes JSON), `ModelErrorRemapMixin`, `default_definition()`.

**Views** (`views/crud.py`) — Liste, Anlegen, Bearbeiten, Duplizieren
(POST-only), Löschen; `EventPermissionRequiredMixin`, `CompatDeleteView`,
`log_action` an allen schreibenden Pfaden, alle Querysets über
`request.event.custom_reports`.

**Templates** — `report_list.html`, `report_form.html`,
`report_confirm_delete.html` (erben `pretixcontrol/event/base.html`).

**Permissions** (exakt aus `docs/pretix-api-notes.md` Abschnitt 8.1)

| Was | Key |
|---|---|
| Lesen/Ausführen | `event.orders:read` |
| Anlegen/Ändern/Löschen (Event) | `event.settings.general:write` |
| Organizer-Äquivalent (Welle 2) | `organizer.settings.general:write` |

**Tests** — `tests/test_models.py` (43) + `tests/test_permissions.py` (24),
zusammen **67 passed, 0 failed**. Negative Fälle sind enthalten: Nutzer mit
fremder Permission (403 auf allen fünf Routen), Nur-Lese-Nutzer (Liste 200,
alle Schreibrouten 403, keine Schreib-Buttons im HTML), Nur-Schreib-Nutzer
(Liste 403), Anonym (302 auf Login), Report eines anderen Events (404, GET und
POST), Organizer-Vorlage über eine Event-URL (404), Nutzer eines anderen
Organizers (404), `Duplicate` per GET (405), ungültiges JSON/Basis-Mismatch
(200 + Formularfehler, kein DB-Schreibvorgang).

## Nicht erledigt (und warum)

- **URL-Verdrahtung.** `urls.py` gehört dem `integrator` (Welle 4). Die fünf
  Routen liegen kopierfertig als `views/crud.py::event_urlpatterns` und in
  `handoff/requests/persistence-dev-an-integrator-urls.md`. Bis dahin sind die
  Views produktiv **nicht erreichbar**. Damit sie trotzdem jetzt gegen den
  echten Resolver, die echte Control-Middleware und die echten
  Permission-Dekoratoren getestet werden, hängt eine modul-weite Fixture in
  `tests/test_permissions.py` (`crud_urls`) die Routen für die Dauer des Moduls
  in `pretix_custom_reports.urls.urlpatterns` ein, lädt den ROOT_URLCONF neu
  und entfernt sie danach wieder. Der Shim kann bei Welle 4 ersatzlos
  entfallen; die Tests laufen dann unverändert weiter.
- **Organizer-Vorlagen-CRUD.** `views/templates.py` gehört `portability-dev`.
  Modell, Manager, Form und Permission-Konstante unterstützen es vollständig
  (`ReportDefinitionForm(organizer=…)`, `templates_for_organizer`,
  `ORGANIZER_CHANGE_PERMISSION`), nur die Views fehlen bewusst.
- **Ausführen-View.** Ein „Report jetzt laufen lassen" bräuchte den
  Query-Compiler; der ist für mich gesperrt. Der Logeintrag dafür existiert als
  `report.log_executed(user=…, data={"row_count": …})` und ist getestet.
- **`tests/test_smoke.py::test_no_migration_created_yet`** schlägt jetzt fehl.
  Der Test ist das Welle-0-Gate „noch keine Migration" und gehört dem
  `integrator`; Ersatzvorschlag steht im Handoff. Es ist der einzige Fehlschlag,
  den ich verursache.

## Getroffene Entscheidungen

Keine neue ADR — alle Entscheidungen liegen innerhalb der Vorgaben von
`docs/adr/0001-contracts.md` Abschnitt 5.1 und `SPEC.md` Abschnitt 5. Vier
Punkte, die eine Begründung brauchen und im Code kommentiert sind:

1. **Eigener scope-fähiger Manager statt `ScopedManager`.**
   `ScopedManager(organizer=…)` bildet eine Dimension auf **einen** ORM-Pfad ab.
   Bei `event` XOR `organizer` ist der Organizer über zwei Pfade erreichbar;
   ein einziger Pfad würde die halbe Tabelle unsichtbar machen
   (`organizer='event__organizer'` → keine Vorlage ist je sichtbar). Der
   Manager filtert deshalb `Q(event__organizer=…) | Q(organizer=…)` und
   verhält sich sonst identisch zu `ScopedManager`, inklusive
   `DisabledQuerySet`/`ScopeError` ohne aktiven Scope
   (`test_scope_covers_both_sides_of_the_xor`,
   `test_queries_without_scope_fail_loudly`).
2. **`base` und `schema_version` sind denormalisierte Kopien.** Maßgeblich ist
   das JSON. `save()` leitet sie daraus ab, wenn leer, und lehnt einen
   Widerspruch ab, statt still zu überschreiben.
3. **`definition` wird beim Speichern kanonisiert** (`document.as_dict()`).
   `validate_definition` lehnt unbekannte Keys ab, es kann also nichts
   verloren gehen; dafür gibt es genau eine Repräsentation pro Definition in
   der DB (nötig für sinnvolle Diffs und für `changed_data`).
4. **`show_hidden_initial` wird für das Definition-Feld abgeschaltet.**
   `models.JSONField(default=dict)` hat einen *callable* Default, weshalb
   Django `show_hidden_initial=True` setzt; `bootstrap_field` rendert dieses
   versteckte Feld nicht, also stünde `definition` bei **jedem** Speichern in
   `changed_data` und der Audit-Log-Eintrag `changed_fields` wäre wertlos.

## Contract-Abweichungen

**KEINE.** `contracts/` und `tests/fixtures/definitions/` sind unangetastet.
Der Namensgleichklang ist bewusst und dokumentiert: `contracts.ReportDefinition`
ist das validierte *Dokument* (frozen dataclass),
`models.ReportDefinition` die *Datenbankzeile*, die so ein Dokument trägt. Wer
beides braucht, importiert Module statt Namen.

## Offene Anforderungen an andere

- `handoff/requests/persistence-dev-an-integrator-urls.md` — fünf Routen,
  Ersatz für `test_no_migration_created_yet`, CI-Aufruf für
  `makemigrations --check` (nur mit App-Label und `--dry-run`, sonst schreibt
  der Befehl in den pretix-Klon), optionale Registrierung der Log-Typen.
- Keine Anforderung an `registry-dev` oder `query-dev`.

### API-Oberfläche für Welle 2 (kein Request, nur Wissen)

```python
event.custom_reports                      # related_name, Event-Reports
organizer.custom_report_templates         # related_name, Vorlagen
ReportDefinition.objects.for_event(event).by_identifier(ident)   # Exporter-Lookup
ReportDefinition.objects.templates_for_organizer(organizer)
report.validated_definition()             # -> contracts.ReportDefinition
report.duplicate(event=…, organizer=…, keep_identifier=True)
report.ensure_unique_identifier()
report.log_executed(user=…, data={"row_count": …, "format": "xlsx"})
report.is_template                        # event_id is None
```

Für `exporter-dev`: der Lookup ist **immer**
`event.custom_reports.get(identifier=…)` bzw. `objects.for_event(event)`, nie
global — Identifier sind nur pro Event bzw. pro Organizer eindeutig
(ADR 0001 Abschnitt 5.1, Sicherheitsauflage). Ein `ReportDefinition.DoesNotExist`
ist in `contracts.ReportNotFoundError` zu übersetzen.

Für `portability-dev`: `duplicate()` ist der einzige Kopierpfad, den ich
gebaut habe, und er behält den Identifier (Suffix nur bei Kollision). Die
XOR-Regel heißt: eine Vorlage hat `event=None, organizer=<org>`, eine
Instanz `event=<event>, organizer=None, source_template=<vorlage>`.

## Tests

`pytest tests/test_models.py tests/test_permissions.py -q` → **67 passed,
0 failed**.
Gesamtlauf zum Zeitpunkt des Abschlusses: 410 passed, 2 failed — davon
1 × `test_smoke.py::test_no_migration_created_yet` (gewolltes Gate, siehe
Handoff, mein Fehlschlag) und 1 × `test_registry_cache.py` (`registry-dev`,
parallel in Arbeit, reproduziert ohne meine Module). Die Zahl steigt weiter,
solange die anderen Welle-1-Agents schreiben.
`flake8`, `isort -c`, `black --check` über meine fünf Python-Dateien: grün.

## Nächster Schritt

1. Orchestrator: Handoff an `integrator` einplanen (Routen + Smoke-Test), oder
   die zwei Zeilen in `urls.py` vorziehen, wenn Welle 2 die Views braucht —
   `frontend-dev` und `portability-dev` bauen auf denselben Modellen auf.
2. Welle 2: `exporter-dev` gegen `for_event(...).by_identifier(...)`,
   `portability-dev` gegen `duplicate()` und `templates_for_organizer()`.
3. Bei Bedarf `report.log_executed(...)` aus Exporter und Vorschau aufrufen —
   der Aufrufer ist noch nicht verdrahtet, weil er in fremdem Gebiet liegt.

---

# Nacharbeit vor Welle 4 — S-001 (Plugin-Gate in den CRUD-Views)

Anlass: Befund **S-001** des `security-reviewer` (`docs/security-review.md`,
Schweregrad mittel). Kein Wellen-3-Beitrag, sondern ein gezielter Fix-Lauf.

## Was war

`views/api.py`, `views/portability.py` und `views/templates.py` haben je einen
Plugin-Aktiv-Check, `views/editor.py` erbt ihn. `views/crud.py` hatte keinen.
Folge: Liste, Anlege-, Änderungs-, Duplizier- und Löschansicht antworteten in
einem Event mit abgeschaltetem Plugin weiter mit 200. SPEC.md F1 verlangt das
Gegenteil — „Plugin aus" ist im pretix-Modell die Notbremse, und
Control-Panel-URLs eines Plugins liegen an der URL-Wurzel, also greift der
Presale-Wrapper aus `pretix/multidomain/plugin_handler.py` dort nicht.

## Was ich geändert habe

Genau eine Datei: `pretix_custom_reports/views/crud.py`.

* Neues `PluginActiveMixin` (404, wenn `"pretix_custom_reports" not in
  event.get_plugins()`) — **wörtlich dieselbe Semantik** wie in `views/api.py`.
* `EventReportMixin` erbt davon. Damit hängen alle fünf Views am Gate, ohne dass
  eine einzelne Klasse angefasst werden musste.
* `PluginActiveMixin` in `__all__`, Modul-Docstring um einen Satz ergänzt.

Bewusst **dupliziert statt importiert**, wie `views/portability.py` es schon
tut: ein Import aus `views/api.py` (Gebiet `frontend-dev`) würde eine
Modulabhängigkeit zwischen zwei Agentengebieten aufmachen. Das Mixin an einer
gemeinsamen Stelle zu besitzen wäre sauberer, ist aber ein Eigentumswechsel und
liegt laut Security-Review beim `integrator`. Der Kommentar im Code sagt das so.

MRO-Reihenfolge geprüft: `ReportListView(EventReportMixin,
EventPermissionRequiredMixin, ListView)` linearisiert zu
`… → EventReportMixin → PluginActiveMixin → EventPermissionRequiredMixin → …`.
Das Plugin-Gate läuft also **vor** der Rechteprüfung — identisch zu
`views/api.py` (`_ApiView(PluginActiveMixin, EventPermissionRequiredMixin, View)`)
und zu `views/portability.py`. Für ein abgeschaltetes Event gibt es damit 404
statt 403, was die richtige Auskunft ist: die Ressource existiert dort nicht.

Keine Migration. Reine View-Logik, kein Modellfeld angefasst.
`urls.py` und `signals.py` unverändert, die Routenliste am Dateiende ist
dieselbe wie zuvor.

## Tests

* `pytest tests/test_security.py -q -k plugin_is_off --runxfail` → **2 passed**.
  Damit läuft `test_every_event_view_404s_when_the_plugin_is_off` jetzt echt
  grün, nicht nur „nicht mehr rot".
* `pytest tests/test_models.py tests/test_permissions.py -q` → **67 passed**,
  unverändert. Kein Bestandstest hat sich auf 200 bei abgeschaltetem Plugin
  verlassen (`-k crud` über `tests/` selektiert nichts, geprüft).
* Gesamtlauf `pytest tests -q -m "not performance"` → **992 passed, 2 failed,
  9 xfailed**. Die zwei Fehlschläge:
  1. `test_security.py::test_every_event_view_404s_when_the_plugin_is_off` —
     `XPASS(strict)`. **Erwartet**: der Test trägt noch
     `@pytest.mark.xfail(strict=True)`, und die Datei gehört dem
     `security-reviewer`, nicht mir. Marker entfernen ist Sache des
     Orchestrators.
  2. `test_smoke.py::test_no_migration_created_yet` — vorbestehend seit Welle 1
     (das Welle-0-Gate, das genau dann fällt, wenn ich Migrationen anlege).
     Nicht durch diesen Lauf verursacht.
* `flake8`, `black --check`, `isort -c` über `views/crud.py`: grün.
* `python -m pretix makemigrations --check` meldet für
  `pretix_custom_reports` **nichts Ausstehendes** (die Meldung betrifft
  `pretix.base` selbst, Locale-Choices im Kern-Klon — umgebungsbedingt und
  unabhängig vom Plugin).

## Offen / für den Orchestrator

1. `@pytest.mark.xfail(strict=True)` an
   `test_every_event_view_404s_when_the_plugin_is_off` entfernen — bis dahin ist
   die Suite durch diesen Fix rot. Absichtlich nicht von mir angefasst.
2. Das Plugin-Gate steht nun **dreimal** wörtlich im Repo (`api.py`,
   `portability.py`, `crud.py`). Kandidat für ein gemeinsames
   `views/_mixins.py` beim `integrator` in Welle 4; bis dahin gilt: wer eines
   ändert, ändert alle drei.
3. `ReportDuplicateView` ist POST-only und daher vom Regressionstest des
   `security-reviewer` (nur GETs) nicht abgedeckt — hängt aber über
   `EventReportMixin` am selben Gate.
