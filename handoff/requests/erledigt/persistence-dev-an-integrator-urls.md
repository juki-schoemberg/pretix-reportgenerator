> **ERLEDIGT — 2026-08-02, `integrator` (Welle 4).** Vollständig übernommen.
> Was daraus wohin ging und welche Entscheidungen dabei getroffen wurden, steht
> in `handoff/status/integrator.md`.

# persistence-dev → integrator: URLs, ein Test und der CI-Migrationscheck

**Welle:** 1 → 4
**Betrifft:** `pretix_custom_reports/urls.py`, `tests/test_smoke.py`, CI-Konfiguration
**Nicht betroffen:** `signals.py` — die CRUD-Views brauchen dort **keinen** Eintrag.
Der Navigationseintrag existiert bereits und zeigt auf `event.index`.

---

## 1. Routen (kopierfertig)

Die fünf Routen liegen bereits fertig gebaut in meinem Dateibereich, damit sie
nicht doppelt gepflegt werden müssen: `pretix_custom_reports/views/crud.py`,
Modulvariable `event_urlpatterns`. **Bevorzugte Variante** — zwei Zeilen in
`urls.py`:

```python
from .views.crud import event_urlpatterns

urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/$",
        EventIndexView.as_view(),
        name="event.index",
    ),
] + event_urlpatterns
```

Wer es lieber explizit in `urls.py` sieht, kann stattdessen das hier einsetzen
(identisch zum Inhalt von `event_urlpatterns`, `_PREFIX` ist derselbe wie beim
bestehenden Eintrag):

```python
_PREFIX = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports"

urlpatterns += [
    re_path(_PREFIX + r"/reports/$",
            ReportListView.as_view(), name="event.reports"),
    re_path(_PREFIX + r"/reports/add/$",
            ReportCreateView.as_view(), name="event.reports.add"),
    re_path(_PREFIX + r"/reports/(?P<report>\d+)/$",
            ReportUpdateView.as_view(), name="event.reports.edit"),
    re_path(_PREFIX + r"/reports/(?P<report>\d+)/duplicate/$",
            ReportDuplicateView.as_view(), name="event.reports.duplicate"),
    re_path(_PREFIX + r"/reports/(?P<report>\d+)/delete/$",
            ReportDeleteView.as_view(), name="event.reports.delete"),
]
```

mit

```python
from .views.crud import (
    ReportCreateView, ReportDeleteView, ReportDuplicateView, ReportListView,
    ReportUpdateView,
)
```

Die **Namen** sind Vertrag: Templates, `get_success_url` und
`tests/test_permissions.py` reversen genau diese fünf Strings. Der Kwarg heißt
`report` und ist der Primärschlüssel (der stabile `identifier` ist die
Referenz für Scheduled Exports, nicht für URLs).

### Hinweis zu `event.index`

`event.index` zeigt heute auf `views/placeholder.py`. Ich habe den Namen
**nicht** angefasst, weil `PretixPluginMeta.navigation_links` und der
`nav_event`-Receiver darauf verweisen und `frontend-dev` den Editor dort
verdrahten wird. Sinnvolle Endgestalt (deine Entscheidung, gern mit
`frontend-dev` abstimmen): `event.index` auf `ReportListView` zeigen lassen und
`event.reports` als Alias behalten oder streichen. Bis dahin funktioniert
beides parallel.

## 2. `tests/test_smoke.py::test_no_migration_created_yet` muss weg

Der Test aus Welle 0a lautet „no migration may ship yet". Seit Welle 1 gibt es
`pretix_custom_reports/migrations/0001_initial.py`, der Test schlägt also fehl —
so gewollt, er war das Gate für genau diesen Moment. Vorschlag als Ersatz
(gleicher Zweck, jetzt in die andere Richtung):

```python
def test_exactly_one_migration_ships():
    """Migrations belong to persistence-dev, and there must be no duplicates."""
    import pathlib

    migrations = pathlib.Path(pretix_custom_reports.__file__).parent / "migrations"
    numbered = sorted(p.name for p in migrations.glob("0*.py"))
    assert numbered == ["0001_initial.py"]
```

## 3. CI: `makemigrations --check` braucht Argumente

`python -m pretix makemigrations --check` (ohne App-Label) ist in dieser
Umgebung **rot, aber nicht wegen uns**: Django will für `pretixbase` eine
Migration `0302_alter_scheduledorganizerexport_timezone_and_more` anlegen, weil
die `timezone`-Choices aus der tz-Datenbank des Betriebssystems kommen und von
der Maschine abweichen, auf der pretix seine Migration erzeugt hat. Ohne
`--dry-run` **schreibt der Befehl diese Datei in den pretix-Klon**, der laut
`ENVIRONMENT.md` reine Lesequelle ist.

Deshalb in CI und in `CLAUDE.md`s Befehlsliste bitte:

```bash
python -m pretix makemigrations pretix_custom_reports --check --dry-run
```

Das ist grün („No changes detected in app 'pretix_custom_reports'", Exit 0).

Zweite Eigenheit, die beim Lesen der Migration auffällt: pretix' eigener
`makemigrations`-Befehl entfernt `verbose_name`, `help_text`, `validators`,
`blank` und `choices` aus der Feld-Dekonstruktion
(`pretix/base/management/commands/_migrations.py`). In `0001_initial.py` fehlen
diese Argumente deshalb, obwohl das Modell sie deklariert. Konsequenz: **immer
`python -m pretix makemigrations`, nie `django-admin makemigrations`** — letzteres
würde eine leere Folgemigration erzeugen wollen.

## 4. Optional: Log-Typen für den Log-Viewer registrieren

Nicht blockierend, gehört aber in deinen Bereich (`signals.py` bzw. ein neues
Modul deiner Wahl) und macht das Audit-Log erst lesbar. Ein nicht registrierter
Action-Type erzeugt keinen Fehler, sondern eine leere Anzeige
(`docs/pretix-api-notes.md` Abschnitt 9). Die sechs Typen stehen als Konstanten
in `contracts/protocols.py`:

```python
from pretix.base.logentrytype_registry import EventLogEntryType, log_entry_types
from pretix_custom_reports import contracts

@log_entry_types.new_from_dict({
    contracts.LOG_ACTION_ADDED: _("The report has been created."),
    contracts.LOG_ACTION_CHANGED: _("The report has been changed."),
    contracts.LOG_ACTION_DELETED: _("The report has been deleted."),
    contracts.LOG_ACTION_EXECUTED: _("The report has been run."),
    contracts.LOG_ACTION_EXPORTED: _("The report has been exported."),
    contracts.LOG_ACTION_IMPORTED: _("The report has been imported."),
    contracts.LOG_ACTION_TEMPLATE_APPLIED: _("A report template has been applied."),
})
class ReportLogEntryType(EventLogEntryType):
    object_type = ReportDefinition
    object_link_wrapper = ...
```

Bitte die exakte Basisklasse und die Registry-API vor dem Übernehmen im Source
prüfen (`pretix/base/logentrytype_registry.py`) — ich habe sie nur gelesen, nicht
benutzt. **Achtung bei `object_link`:** `LoggedModel.all_logentries_link` läuft
für Organizer-Vorlagen auf `None`-Event und würde beim Rendern krachen
(`pretix/base/models/base.py:200-210`, `hasattr(self, 'event')` ist bei uns
`True`, `self.event` aber `None`). Vorlagen haben kein Event — wer einen
Objekt-Link baut, muss `report.is_template` behandeln.
