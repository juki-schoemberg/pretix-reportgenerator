# exporter-dev → integrator: zwei Empfänger in `signals.py`

**Welle:** 2 → 4 (bzw. beim ersten Merge)
**Quelle:** `docs/pretix-api-notes.md` Abschnitt 3.2 und 5, `docs/adr/0001-contracts.md` Abschnitt 5
**Warum du:** `pretix_custom_reports/signals.py` gehört dir (`ORCHESTRIERUNG.md`
Abschnitt 5). Ich habe die Datei nicht angefasst — die Platzhalterzeilen 56/57
(„wave 2 exporter-dev …") sind genau das hier.

Ohne diese zwei Zeilen ist der Exporter **produktiv nicht sichtbar**: er
erscheint weder in der Export-Oberfläche des Events noch in der des Organizers,
und ein terminierter Export lässt sich nicht anlegen. Die Tests
(`tests/test_exporters.py`) verbinden dieselben zwei Funktionen selbst und
laufen deshalb schon jetzt grün — das ersetzt die Verdrahtung aber nicht.

---

## 1. Pflicht: kopierfertig, ans Ende von `pretix_custom_reports/signals.py`

```python
from pretix.base.signals import (
    register_data_exporters, register_multievent_data_exporters,
)

from pretix_custom_reports.exporters import (
    register_multievent_report_exporter, register_report_exporter,
)

# Saved reports as regular pretix exports. Registering them here is what makes
# them schedulable: pretix' scheduled exports are bound to a registered
# exporter identifier, so we need no scheduler of our own (CLAUDE.md rule 5).
#
# The receivers live in exporters.py rather than being defined here, so that
# there is exactly one definition and the tests connect the same objects the
# production code does. EventPluginSignal.connect resolves the owning app from
# the receiver's __module__ (pretix/base/signals.py:64-88), and both modules
# belong to this plugin, so either location works.
register_data_exporters.connect(
    register_report_exporter,
    dispatch_uid="pretix_custom_reports_exporter",
)
register_multievent_data_exporters.connect(
    register_multievent_report_exporter,
    dispatch_uid="pretix_custom_reports_multiexporter",
)
```

Die `dispatch_uid`s sind frei wählbar, aber `tests/test_exporters.py` benutzt
genau diese beiden Strings. Wenn du sie änderst, ändere sie dort mit — dann
merkt der Test weiterhin, wenn diese Datei und der Test auseinanderlaufen.

**Falls du die `@receiver`-Schreibweise bevorzugst** (so macht es der Kern, z. B.
`pretix/base/exporters/waitinglist.py:270-277`), ist auch das korrekt:

```python
@receiver(register_data_exporters, dispatch_uid="pretix_custom_reports_exporter")
def register_report_exporter_receiver(sender, **kwargs):
    return CustomReportExporter
```

Dann bitte den Test in `tests/test_exporters.py` (`registered`-Fixture) mit
anpassen — er verbindet zurzeit die Funktionen aus `exporters.py`.

---

## 2. Erwartete `DeprecationWarning` — bitte nicht „wegreparieren"

`register_multievent_data_exporters` ist ein
`OrganizerPluginSignal(allow_legacy_plugins=True)`
(`pretix/base/signals.py:507`), unser Plugin ist Event-Level
(`apps.py` setzt kein `level`). `connect()` löst deshalb aus:

```
DeprecationWarning: This signal will soon be only available for plugins that
declare to be organizer-level
```

Das ist der vorgesehene Legacy-Pfad (`pretix/base/signals.py:292-311`), pretix
selbst geht ihn für `ticketoutputpdf` und `stripe`. Zwei Konsequenzen, beide
gewollt und beide dokumentiert im Modul-Docstring von `exporters.py`:

1. Der Organizer-Exporter gilt für **jeden** Organizer als aktiv
   (`pretix/base/plugins.py:107-113`), auch wenn das Plugin dort in keinem Event
   eingeschaltet ist. Dann bietet er eine leere Report-Auswahl an — harmlos.
2. Falls du in der CI `filterwarnings = error` einführst, brauchst du denselben
   Filter, den pretix in `src/setup.cfg` setzt:
   `ignore:.*This signal will soon be only available for plugins that declare to be organizer-level.*`
   Aktuell steht in unserem `setup.cfg` kein `filterwarnings`, also ist nichts
   zu tun.

Der saubere Ausweg wäre `level = PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID` in
`PretixPluginMeta`. Das ist eine Entscheidung über den Plugin-Charakter, nicht
über den Exporter — sie gehört dir bzw. in eine ADR, und sie hätte Folgen für
`nav_organizer` und die Organizer-Vorlagen von `portability-dev`. Ich habe sie
**nicht** getroffen.

---

## 3. Neue englische Strings für den `de`-Katalog

Alle in `pretix_custom_reports/exporters.py`, alle mit `gettext`/`gettext_lazy`
markiert. Zwei Gruppen:

**Sichtbar in der Export-Oberfläche** (`gettext_lazy`):

| String | Fundstelle |
| --- | --- |
| `Custom report` | `verbose_name` |
| `Custom reports` (Kontext `export_category`) | `category`, `pgettext_lazy` |
| `Run one of the reports you defined in the report editor. …` | `description` |
| `Report` | Feldlabel |
| `Reports are matched by their internal identifier, …` | help_text, Multi-Event |
| `One of the reports saved for this event.` | help_text, Event |
| `Include canceled positions` | Feldlabel |
| `Include test mode orders` | Feldlabel |
| `Overrides the setting saved with the report, for this run only.` | help_text (2×, identisch) |
| `Maximum number of rows` | Feldlabel |
| `Leave empty to use the limit saved with the report. …` | help_text |
| `If an event cannot supply this report` | Feldlabel, nur Multi-Event |
| `Skip the event and export the rest` / `Fail the whole export` | Choices |
| `An event may not have this report at all, …` | help_text |
| `Use the setting saved with the report` / `Yes` / `No` | Override-Choices |

**Sichtbar in der Fehlermail eines terminierten Exports** (`gettext`, bewusst
nicht `lazy`, weil sie zur Laufzeit unter `language(schedule.locale)` gebildet
werden — `pretix/base/services/export.py:323`):

| String |
| --- |
| `The report "{identifier}" does not exist in event {event}. It was probably deleted or renamed after this export was configured.` |
| `The report "{name}" cannot be run for event {event}: {error}` |
| `The report "{identifier}" has {found} columns in event {event}, but {expected} in the events before it.` |
| `This report could not be run for any of the selected events.` |
| `There is no event you may run this export for.` |
| `This export cannot be written in the format "{format}". Please open the export configuration and pick a format again.` |
| `No report was selected for this export, or the stored selection is not a valid report identifier.` |
| `The stored value "{value}" for option "{option}" is not one of yes/no.` |
| `The stored row limit "{value}" is not a whole number between 1 and {maximum}.` |
| `Event slug`, `Event name` (Spaltenüberschriften im Multi-Event-Export) |

Die Meldungen der `ContractError`-Klassen selbst (`{error}` oben) kommen aus
`contracts/errors.py` und sind dort **nicht** übersetzbar — das ist eine
Entscheidung des `contract-architect`, kein Versehen von mir.

---

## 4. Kein weiterer Verdrahtungsbedarf

- **Keine URL.** Der Exporter hängt an den Export-Seiten von pretix
  (`control:event.orders.export`, `control:organizer.export`), nicht an einer
  eigenen Route.
- **Keine Migration.** Terminierte Exporte liegen in pretix' eigenen Modellen
  `ScheduledEventExport` / `ScheduledOrganizerExport`.
- **Kein `periodic_task`-Empfänger.** `run_scheduled_exports`
  (`pretix/base/services/export.py:496-521`) ist der Scheduler, wir hängen nur
  dran (`CLAUDE.md` Regel 5).
- **Keine neue Log-Action.** `pretix_custom_reports.report.executed` ist bereits
  in `contracts.LOG_ACTION_EXECUTED` deklariert; ich rufe nur
  `report.log_executed(...)` auf. Falls du `logentry_display` registrierst
  (steht als optionaler Punkt schon im Request von `persistence-dev`), sind die
  Nutzdaten: `row_count`, `format`, `exporter`, `multievent` plus die Felder aus
  `ReportDefinition.log_data()`.
