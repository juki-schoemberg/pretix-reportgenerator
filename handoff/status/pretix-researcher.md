# Status: pretix-researcher — Welle 0b

Erledigt:
- Umgebung selbst verifiziert: pretix **2026.6.0**, Editable-Install des Klons
  unter `D:\Projekte\juki\pretix\src\pretix\`, Git-Tag `v2026.6.0`,
  Commit `fd565ecdb29c55a3e82dc15d94a848d193664caa`. Zusätzlich
  `django-scopes 2.0.0`, `defusedcsv 3.0.0`.
- `docs/pretix-api-notes.md` erstellt (14 Abschnitte). Alle Punkte aus
  `.claude/agents/pretix-researcher.md` "Zwingend zu dokumentieren" abgedeckt:
  - `BaseExporter` / `ListExporter` / `MultiSheetListExporter` /
    `OrganizerLevelExportMixin` mit wörtlichen Signaturen und allen Attributen
    (`identifier`, `verbose_name`, `category`, `featured`, `description`,
    zusätzlich `repeatable_read`).
  - `register_data_exporters` (EventPluginSignal) vs.
    `register_multievent_data_exporters` (OrganizerPluginSignal mit
    `allow_legacy_plugins=True`), Klassenunterschiede, `event_copy_data`,
    `periodic_task`.
  - Navigation: `nav_event`, `nav_organizer`, `nav_global`, `nav_topbar`,
    `nav_event_settings`, Dict-Keys, `merge_in`-Parent-Logik, `active`-Muster,
    vollständige Liste der in `navigation.py` verwendeten Permission-Keys.
  - Scheduled Exports: `AbstractScheduledExport` / `ScheduledEventExport` /
    `ScheduledOrganizerExport` mit vollständiger Feldliste, Befüllung von
    `export_identifier` / `export_form_data`, periodischer Task
    `run_scheduled_exports`, Owner-/Permission-Regeln, und das
    Fehlerverhalten bei fehlendem referenziertem Objekt (zwei Fälle,
    beide am Core-Test `tests/base/test_export.py` belegt).
  - `Order` und `OrderPosition` vollständig: Feldliste mit Typen, alle
    Relationen inkl. Reverse-Accessoren, `Meta.ordering`, Manager/Querysets,
    `all_positions` vs. `positions`.
  - `InvoiceAddress`, `Question` (inkl. Stabilitätsanalyse von
    `Question.identifier`), `QuestionOption`, `QuestionAnswer`, `Item`,
    `ItemVariation`, `SubEvent`, `Seat`, `OrderPayment`, `OrderRefund`,
    `Checkin`/`CheckinList`, `Voucher`, `Transaction`, Meta-Properties.
  - `django-scopes`: Setzpunkte, Celery-Task-Basisklassen, wo
    `scopes_disabled()` nötig ist, Scope-Pfad-Tabelle aller relevanten Modelle.
  - Permission-Mixins mit exakten Klassennamen und die vollständige Liste
    gültiger Permission-Strings 2026.6.0 inkl. Legacy-Mapping.
  - `log_action`-Signatur und Action-Type-Konventionen, plus
    `log_entry_types`-Registry.
  - CSV-Injection: **ja, bereits neutralisiert** — `defusedcsv` und
    `SafeWorkbook`, mit Zitat der Escape-Funktion.
  - Test-Fixtures und Konventionen der pretix-Testsuite.
- Pro Thema jeweils Modulpfad, wörtliche Signatur, Erklärung, Minimalbeispiel
  aus dem Core und ein Abschnitt "Fallstricke".
- Abschnitt 12 "Doku vs. Code" mit 6 festgestellten Abweichungen.
- Abschnitt 14 "Unklar geblieben" mit 8 offen gebliebenen Punkten.
- Die vier Verdrahtungsdetails von `bootstrap-dev` gegengeprüft und mit
  Fundstellen unterlegt: Plugin-`urlpatterns` an der URL-Wurzel
  (`multidomain/maindomain_urlconf.py:55-80`), Permission-Doppelpunkt-Form und
  Nichtexistenz von `event.items:read` (`base/permissions.py:219-225`),
  `nav_event`-Receiver-Form, `scopes_disabled()`-Automatismus nur innerhalb
  `src/tests/` (`src/tests/conftest.py:67-77`). Alle vier bestätigt.

Nicht erledigt (und warum):
- Keine vollständige Feldtabelle für `Event` — war nicht Teil der
  Rechercheliste. Nur punktuell dokumentiert (Manager, `Meta.ordering`,
  `meta_data`). Falls die Registry Event-Felder anbieten soll, muss das
  nachgezogen werden. Siehe "Unklar geblieben" Punkt 7.
- Einzelzeilennachweise für jedes der ~60 `Item`-Felder — bewusst nur als
  Bereichsangabe, um keine falschen Zeilennummern zu behaupten. Für `Order`
  und `OrderPosition` (die uns wirklich betreffen) ist die Liste dagegen
  vollständig und zusätzlich per Django-Introspektion verifiziert.
- Die verlinkte pretix-Online-Doku zu Logging-Konventionen wurde nicht
  abgerufen (kein Netzzugriff genutzt). Siehe "Unklar geblieben" Punkt 3.

Getroffene Entscheidungen: (Verweis auf ADR-Nummern)
- Keine. Dieser Agent trifft keine Architekturentscheidungen. Abschnitt 13
  ("was das für unser Plugin heißt") enthält ausschließlich Ableitungen aus
  dem Source, ausdrücklich markiert als Vorarbeit für den `contract-architect`,
  nicht als Festlegung.

Contract-Abweichungen: KEINE (zum Zeitpunkt dieses Laufs existiert
`pretix_custom_reports/contracts/` noch nicht; `contract-architect` startet
direkt nach mir).

Offene Anforderungen an andere:
- Keine `handoff/requests/`-Dateien geschrieben. Zwei Punkte, die der
  `contract-architect` beim Contract-Entwurf entscheiden muss und die ich
  bewusst offengelassen habe:
  1. Ablageform des Report-Bezugs in `export_form_data` (PK vs. stabiler Slug).
     Fakten dazu in Abschnitt 5.2 und 5.6; Core benutzt durchgängig PKs.
  2. Namensschema für Log-Action-Types des Plugins. Fakten in Abschnitt 9.
- Ein Punkt für `bootstrap-dev`/`integrator` (kein Blocker): unsere eigene
  `conftest.py` braucht den `scopes_disabled()`-Fixture-Hook explizit, und bei
  Übernahme von `filterwarnings = error` muss der
  `OrganizerPluginSignal`-DeprecationWarning-Filter mitkommen, sonst schlägt
  die Registrierung von `register_multievent_data_exporters` die Tests.
  Fakten in Abschnitt 3.5 Fallstrick 1 und Abschnitt 11.

Tests: 0 passed, 0 failed — dieser Agent schreibt laut Rollendefinition keinen
Produktivcode und keine Tests. Verifikation erfolgte durch Lektüre des
installierten Source, ergänzt um Django-Modell-Introspektion
(`django.setup()` mit `PRETIX_CONFIG_FILE`) für die Feldlisten von `Order` und
`OrderPosition`.

Nächster Schritt:
- `contract-architect` startet mit `docs/pretix-api-notes.md` als
  Voraussetzung. Besonders relevant für ihn: Abschnitt 1 (Exporter-Signaturen),
  Abschnitt 5 (Scheduled-Export-Anbindung inkl. Fehlerverhalten), Abschnitt 6.1
  bis 6.4 (Order/OrderPosition/Question für die `ReportField`-Registry),
  Abschnitt 6.2 "Fallstricke" (die vier Stolperstellen, die die Registry
  kapseln muss) und Abschnitt 8.1 (gültige Permission-Strings).
- Wenn ein Agent während Welle 1 eine Abweichung zwischen diesem Dokument und
  dem Source findet: Source gewinnt, `handoff/requests/` schreiben, damit die
  Referenz korrigiert wird.
