> **ERLEDIGT — 2026-08-10, `integrator`.** Vollständig übernommen:
> `template_editor_urlpatterns` hängt in `urls.py` neben
> `templates_organizer_urlpatterns`, `signals.py` bleibt wie gefordert ohne
> zweiten Menüpunkt, und die 25 neuen Strings aus Abschnitt 5 stehen im
> `de`-Katalog. Details in `handoff/status/integrator.md`.
>
> Eine Abweichung von Abschnitt 4: `signals.py` wurde doch angefasst, aber
> nicht an der Navigation — `ReportLogEntryType.get_object_link_info`
> verlinkt Vorlagen jetzt auf `organizer.templates.editor.edit` statt auf
> das JSON-Formular (Auftrag des Orchestrators, ADR 0006 Abschnitt 9).

# frontend-dev → integrator: URLs für den grafischen Vorlagen-Editor

**Betrifft:** `pretix_custom_reports/urls.py` (nur diese Datei)
**Nicht betroffen:** `signals.py`, `apps.py`, `views/api.py`, `views/templates.py`,
`registry/**`. Der `nav_organizer`-Eintrag „Report templates" bleibt wie er ist;
der Editor wird aus der Vorlagenliste verlinkt, nicht aus dem Menü.

---

## 1. Die zwei Zeilen (kopierfertig)

Die Routen liegen fertig gebaut in meinem Dateibereich, damit sie nicht doppelt
gepflegt werden: `views/editor.py` → `template_editor_urlpatterns`. Eine eigene
Liste, absichtlich **nicht** an `editor_urlpatterns` angehängt — die eine hängt
unter `control/event/…`, die andere unter `control/organizer/…`, und du sollst
beim Lesen von `urls.py` sehen, welche Ebene du gerade verdrahtest.

Import ergänzen:

```python
from .views.editor import editor_urlpatterns, template_editor_urlpatterns
```

Und in die Verkettung aufnehmen, direkt **neben** `templates_organizer_urlpatterns`,
weil beides Organizer-Ebene ist:

```python
urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/$",
        ReportListView.as_view(),
        name="event.index",
    ),
] + (
    event_urlpatterns                  # persistence-dev: CRUD (5)
    + editor_urlpatterns               # frontend-dev: editor shell (2)
    + api_urlpatterns                  # frontend-dev: JSON endpoints (3)
    + portability_event_urlpatterns    # portability-dev: file import/export (2)
    + templates_event_urlpatterns      # portability-dev: use a template (2)
    + templates_organizer_urlpatterns  # portability-dev: manage templates (5)
    + template_editor_urlpatterns      # frontend-dev: template editor (2)  <-- neu
)
```

Reihenfolge ist beliebig, die Präfixe überschneiden sich nicht: die zwei neuen
Routen liegen unter `…/customreports/templates/editor/`, `TemplateUpdateView`
unter `…/customreports/templates/<pk>/`. `editor` ist kein `\d+`, also kann keine
der beiden die andere schlucken.

## 2. Was genau dazukommt (Namen und Routen sind Vertrag)

Explizit, falls du es in `urls.py` sehen willst — inhaltlich identisch zur
Modulvariable:

```python
_ORG_PREFIX = r"^control/organizer/(?P<organizer>[^/]+)/customreports"

urlpatterns += [
    re_path(_ORG_PREFIX + r"/templates/editor/$",
            TemplateEditorView.as_view(),
            name="organizer.templates.editor.new"),
    re_path(_ORG_PREFIX + r"/templates/editor/(?P<template>\d+)/$",
            TemplateEditorView.as_view(),
            name="organizer.templates.editor.edit"),
]
```

Die **Namen sind Vertrag**: `portability-dev` verlinkt sie aus
`template_list.html` (siehe
`handoff/requests/frontend-dev-an-portability-dev-template-editor-verlinkung.md`),
`tests/test_editor_api.py` reverst beide.

`template` ist der **Primärschlüssel**, nicht der `identifier` — anders als beim
Event-Editor (`editor.edit` nimmt den Identifier, Begründung in
`handoff/requests/.../frontend-dev-an-integrator-urls.md` Abschnitt 1). Grund für
die Abweichung: diese beiden Routen stehen direkt neben
`organizer.templates.edit` und `organizer.templates.export` von `portability-dev`,
die beide `template=<pk>` verwenden, und der Editor postet bzw. verlinkt dorthin.
Zwei Adressierungsarten auf derselben Ebene wären eine Falle.

## 3. Was der Editor auf Organizer-Ebene reverst

Alles über `url_or_none()`, also in `try/except NoReverseMatch`. Fehlt eine
Route, kostet das einen Knopf, nicht die Seite:

| Name | Kwargs | Wofür | Eigentümer |
|---|---|---|---|
| `organizer.templates.add` | `organizer` | Ziel des Formulars (neue Vorlage) | portability-dev |
| `organizer.templates.edit` | `organizer`, `template` | Ziel des Formulars (bestehende Vorlage) | portability-dev |
| `organizer.templates.export` | `organizer`, `template` | „Als Datei exportieren" | portability-dev |
| `organizer.templates` | `organizer` | „Abbrechen" / „Zurück zu den Vorlagen" | portability-dev |
| `api.fields`, `api.preview`, `api.validate` | `organizer`, **`event`** | Feldbibliothek, Vorschau, Validierung des **Referenz-Events** | frontend-dev |

Der letzte Punkt ist der einzige, der Erklärung braucht: eine Vorlage hat kein
Event, eine Feldbibliothek aber nur *für* ein Event (welche Fragen, Produkte und
Meta-Properties es gibt, ist Event-Daten). Der Editor lässt sich deshalb ein
**Referenz-Event** wählen (`?reference_event=<slug>`) und zeigt seine drei
JSON-Endpunkte auf genau dieses Event. `views/api.py` bleibt unverändert und
bleibt event-gebunden — die Vorschau zeigt echte Bestellungen dieses Events und
muss auf `event.orders:read` gattet bleiben.

Zulässig sind nur Events des Organizers, für die das Plugin aktiv ist **und** auf
denen der Nutzer `event.orders:read` hat. Genau ein zulässiges Event wird
automatisch gewählt, bei mehreren kommt eine Auswahlseite, bei keinem eine
Meldung — kein 500.

## 4. Kein neuer Menüeintrag, keine Änderung an `signals.py`

Der `nav_organizer`-Receiver in `signals.py` zeigt weiter auf
`organizer.templates` (die Liste). Der Editor ist von dort aus erreichbar; ein
zweiter Menüpunkt „Vorlagen-Editor" neben „Report templates" wäre für denselben
Gegenstand zwei Einstiege.

## 5. Übersetzungen (`de`-Katalog, dein Gebiet)

Alle neuen Strings sind englisch, wie in CLAUDE.md Regel 8 gefordert. Neu
hinzugekommen sind sie in:

* `templates/pretix_custom_reports/editor.html` — die vier Abschnittsüberschriften
  („Name and report base", „Content", „Sorting and options", „Result") samt ihrer
  einzeiligen Erläuterungen, der Titel „Template editor", der Referenz-Event-
  Hinweis, die Vorschau-Warnung und die Template-Varianten von zwei
  Hilfetexten („Save this template to be able to export it as a file.", der
  „You cannot save this template"-Block).
* `templates/pretix_custom_reports/editor_choose_event.html` — komplett neu.
* `views/editor.py` — eine neue Fehlermeldung („That event cannot be used as a
  reference: …").

In `js_strings()` ist **kein** neuer Eintrag dazugekommen; die JavaScript-Seite
hat sich nicht geändert.
