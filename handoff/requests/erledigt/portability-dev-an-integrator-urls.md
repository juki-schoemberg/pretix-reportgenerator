> **ERLEDIGT — 2026-08-02, `integrator` (Welle 4).** Vollständig übernommen.
> Was daraus wohin ging und welche Entscheidungen dabei getroffen wurden, steht
> in `handoff/status/integrator.md`.

# portability-dev → integrator: URLs für Import/Export und Organizer-Vorlagen

**Welle:** 2
**Betrifft:** `pretix_custom_reports/urls.py`
**Ebenfalls betroffen (Abschnitt 4/5):** `signals.py` — Navigationseintrag auf
Organizer-Ebene, Log-Anzeigetexte. Das Event-Kopie-Signal liegt in einer
eigenen Datei: `handoff/requests/portability-dev-an-integrator-signals.md`.

---

## 1. Routen (kopierfertig, bevorzugte Variante)

Die Routen liegen fertig gebaut in meinem Dateibereich, damit sie nicht doppelt
gepflegt werden:

| Modulvariable | Datei | Anzahl |
|---|---|---|
| `portability_event_urlpatterns` | `views/portability.py` | 2 |
| `templates_event_urlpatterns` | `views/templates.py` | 2 |
| `templates_organizer_urlpatterns` | `views/templates.py` | 5 |

Drei Import-Zeilen und ein `+`:

```python
from .views.portability import portability_event_urlpatterns
from .views.templates import (
    templates_event_urlpatterns,
    templates_organizer_urlpatterns,
)

urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/$",
        EventIndexView.as_view(),
        name="event.index",
    ),
] + (
    event_urlpatterns                # persistence-dev
    + editor_urlpatterns             # frontend-dev
    + api_urlpatterns                # frontend-dev
    + portability_event_urlpatterns  # ich
    + templates_event_urlpatterns    # ich
    + templates_organizer_urlpatterns  # ich
)
```

Die Reihenfolge ist beliebig, die Präfixe überschneiden sich nicht.
`templates_organizer_urlpatterns` ist der **einzige** Block im Plugin unter
`^control/organizer/…` — die anderen Agents haben nur Event-Routen.

## 2. Dieselben Routen explizit

```python
_EVENT = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports"
_ORG   = r"^control/organizer/(?P<organizer>[^/]+)/customreports"

urlpatterns += [
    # Datei-Import/Export (SPEC.md F9)
    re_path(_EVENT + r"/reports/import/$",
            ReportImportView.as_view(), name="event.reports.import"),
    re_path(_EVENT + r"/reports/(?P<report>\d+)/export/$",
            ReportExportView.as_view(), name="event.reports.export"),

    # „Vorlage laden" im Event (SPEC.md F10)
    re_path(_EVENT + r"/reports/templates/$",
            TemplatePickView.as_view(), name="event.reports.templates"),
    re_path(_EVENT + r"/reports/templates/(?P<template>\d+)/$",
            TemplateApplyView.as_view(), name="event.reports.templates.apply"),

    # Vorlagenverwaltung auf Organizer-Ebene (SPEC.md F10)
    re_path(_ORG + r"/templates/$",
            TemplateListView.as_view(), name="organizer.templates"),
    re_path(_ORG + r"/templates/add/$",
            TemplateCreateView.as_view(), name="organizer.templates.add"),
    re_path(_ORG + r"/templates/(?P<template>\d+)/$",
            TemplateUpdateView.as_view(), name="organizer.templates.edit"),
    re_path(_ORG + r"/templates/(?P<template>\d+)/delete/$",
            TemplateDeleteView.as_view(), name="organizer.templates.delete"),
    re_path(_ORG + r"/templates/(?P<template>\d+)/export/$",
            TemplateExportView.as_view(), name="organizer.templates.export"),
]
```

**Namenskonvention:** Event-Routen heißen wie bei `persistence-dev`
(`event.reports.*`), Organizer-Routen `organizer.templates.*`. Der
URL-Parameter heißt bei Reports `report`, bei Vorlagen `template` — beide sind
Primärschlüssel, wie in `views/crud.py`. `frontend-dev` benutzt in seinen
Editor-Routen den `identifier`; die Uneinheitlichkeit ist dort schon angemerkt.
Wenn sie aufgelöst wird, kostet sie mich zwei Zeilen (`get_report` und
`get_object`).

## 3. Neue Templates

Sieben HTML-Dateien unter `pretix_custom_reports/templates/pretix_custom_reports/`,
alle neu und mit keiner bestehenden Datei kollidierend:

```
import_form.html            resolution_report.html   (Include, kein eigener View)
import_confirm.html         template_pick.html
template_list.html          template_apply.html
template_form.html
template_confirm_delete.html
```

Die Ownership-Tabelle in `ORCHESTRIERUNG.md` listet Templates einzeln je
Eigentümer (`report_list.html` → persistence-dev, `editor*.html` →
frontend-dev). Diese sieben Namen sind bisher unbelegt; ich habe sie angelegt,
weil ein View ohne Template nicht rendert. Falls die Tabelle ergänzt werden
soll: sie gehören zu `views/portability.py` bzw. `views/templates.py`.

Die Organizer-Templates erben `pretixcontrol/organizers/base.html` und füllen
`{% block inner %}` (nicht `content`) — das ist bei pretix auf Organizer-Ebene
so, siehe `control/templates/pretixcontrol/organizers/teams.html`.

## 4. Navigationseintrag auf Organizer-Ebene (`signals.py`)

Die Vorlagenverwaltung ist sonst nur über die URL erreichbar. Vorschlag,
verifiziert gegen `docs/pretix-api-notes.md` Abschnitt 4.1/4.4 und
`pretix/control/navigation.py`:

```python
from pretix.control.signals import nav_organizer
from .views.templates import ORGANIZER_CHANGE_PERMISSION


@receiver(nav_organizer, dispatch_uid="pretix_custom_reports_nav_organizer")
def navbar_organizer(sender, request, organizer, **kwargs):
    if not request.user.has_organizer_permission(
        organizer, ORGANIZER_CHANGE_PERMISSION, request=request
    ):
        return []
    url = reverse(
        "plugins:pretix_custom_reports:organizer.templates",
        kwargs={"organizer": organizer.slug},
    )
    return [
        {
            "label": _("Report templates"),
            "url": url,
            "active": request.path.startswith(url),
            "icon": "table",
        }
    ]
```

Zwei Punkte, die `docs/pretix-api-notes.md` ausdrücklich nennt:

1. Ein Navigations-Receiver muss **immer eine Liste** zurückgeben, notfalls
   `[]` — `navigation.py` würde bei `None` in `list()` krachen (Abschnitt 3.3,
   Fallstrick 3).
2. `nav_organizer` ist ein `OrganizerPluginSignal`; ein Event-Level-Plugin hängt
   sich über die Legacy-Ausnahme dran und erzeugt dabei eine
   `DeprecationWarning` (Abschnitt 3, Fallstrick 1). Falls unsere
   pytest-Konfiguration irgendwann `filterwarnings = error` bekommt, muss die
   Warnung wie bei pretix selbst gefiltert werden.

Ein Event-Level-Eintrag „Import" ist **nicht** nötig — der Knopf gehört auf die
Report-Liste (`report_list.html`, persistence-dev) bzw. in den Editor
(frontend-dev), siehe Abschnitt 6.

## 5. Log-Anzeigetexte

`persistence-dev-an-integrator-urls.md` Abschnitt 5 enthält bereits die
Übersetzungstabelle für `logentry_display`. Alle drei Action-Types, die ich
schreibe, stehen dort schon:

```python
contracts.LOG_ACTION_EXPORTED: _("The report has been exported."),
contracts.LOG_ACTION_IMPORTED: _("The report has been imported."),
contracts.LOG_ACTION_TEMPLATE_APPLIED: _("A report template has been applied."),
```

Zusätzlich schreibt der Event-Kopie-Pfad `LOG_ACTION_ADDED` mit dem Extra-Feld
`copied_from_event` (siehe Signal-Request). Kein neuer Action-Type.

## 6. Knöpfe in fremden Templates (nur Vorschlag, nicht meine Dateien)

Damit die Funktionen auffindbar sind, fehlen drei Links, alle in fremdem
Gebiet:

`templates/.../report_list.html` (persistence-dev), neben „Neuen Report anlegen":

```html
<a href="{% url "plugins:pretix_custom_reports:event.reports.import" organizer=request.event.organizer.slug event=request.event.slug %}"
   class="btn btn-default">
    <span class="fa fa-upload"></span> {% trans "Import a report" %}
</a>
<a href="{% url "plugins:pretix_custom_reports:event.reports.templates" organizer=request.event.organizer.slug event=request.event.slug %}"
   class="btn btn-default">
    <span class="fa fa-clone"></span> {% trans "Load a template" %}
</a>
```

und in der Aktionsspalte je Zeile:

```html
<a href="{% url "plugins:pretix_custom_reports:event.reports.export" organizer=request.event.organizer.slug event=request.event.slug report=report.pk %}"
   class="btn btn-default btn-sm" title="{% trans "Export" %}" data-toggle="tooltip">
    <span class="fa fa-download"></span>
</a>
```

Im Editor (`editor.html`, frontend-dev) wäre der passende Ort das
JSON-Panel: „Als Datei exportieren" auf `event.reports.export`,
„Datei importieren" auf `event.reports.import`.

## 7. Was ich nicht brauche

- keine Migration (kein neues Modell, kein neues Feld)
- keine Änderung an `apps.py`
- keine neue Abhängigkeit in `pyproject.toml` (nur `json` aus der Standardbibliothek)
- keine `djangojs`-Übersetzungen (kein JavaScript)

## 8. Neue englische Strings

Alle neuen Strings stehen in den sieben Templates und in
`views/portability.py`/`views/templates.py` als `gettext_lazy`. Die
Auflösungsberichte selbst (`detail`-Texte in
`portability/resolution.py`) sind bewusst **nicht** übersetzt: sie landen
wörtlich im Log-Eintrag, und ein Log, dessen Text von der Sprache des
Bearbeiters abhängt, ist nicht auswertbar. Wenn der `de`-Katalog sie trotzdem
haben soll, ist die saubere Variante ein Mapping von `entry.status`/`entry.match`
auf übersetzte Sätze im Template — die Codes sind stabil und dafür gedacht.
