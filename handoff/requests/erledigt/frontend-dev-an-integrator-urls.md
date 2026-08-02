> **ERLEDIGT — 2026-08-02, `integrator` (Welle 4).** Vollständig übernommen.
> Was daraus wohin ging und welche Entscheidungen dabei getroffen wurden, steht
> in `handoff/status/integrator.md`.

# frontend-dev → integrator: URLs für Editor und JSON-Endpunkte

> **Stand nach Welle 2 (gelesen: 2026-08-01).** Abschnitt 1 gilt unverändert,
> **aber ohne** die beiden `api.examples`/`api.example`-Zeilen: die Mock-Views
> sind gelöscht, die Routen dürfen **nicht** in `urls.py` (sonst
> `ImportError`). Abschnitt 3 (`save_url`) ist erledigt. Abschnitt 4 ist
> abgearbeitet und nur noch Historie.

**Welle:** 1 (Routen) → 2 (Save-Verdrahtung)
**Betrifft:** `pretix_custom_reports/urls.py`
**Nicht betroffen:** `signals.py`, `apps.py` — der Editor braucht dort keinen
Eintrag. Der Navigationseintrag zeigt weiter auf `event.index`.

---

## 1. Routen (kopierfertig, bevorzugte Variante)

Die Routen liegen fertig gebaut in meinem Dateibereich, damit sie nicht doppelt
gepflegt werden: `views/editor.py` → `editor_urlpatterns`, `views/api.py` →
`api_urlpatterns`. Zwei Zeilen in `urls.py`:

```python
from .views.api import api_urlpatterns
from .views.editor import editor_urlpatterns

urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/$",
        EventIndexView.as_view(),
        name="event.index",
    ),
] + editor_urlpatterns + api_urlpatterns
```

Zusammen mit den Zeilen aus
`handoff/requests/persistence-dev-an-integrator-urls.md` also:

```python
urlpatterns = [
    re_path(..., EventIndexView.as_view(), name="event.index"),
] + event_urlpatterns + editor_urlpatterns + api_urlpatterns
```

Reihenfolge ist beliebig, die Präfixe überschneiden sich nicht.

Wer es explizit in `urls.py` sehen will (inhaltlich identisch zu den beiden
Modulvariablen, `_EVENT_PREFIX` ist derselbe Präfix wie beim bestehenden
Eintrag):

```python
_PREFIX = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/customreports/"

urlpatterns += [
    re_path(_PREFIX + r"editor/$",
            ReportEditorView.as_view(), name="editor.new"),
    re_path(_PREFIX + r"editor/(?P<identifier>[a-zA-Z0-9._-]+)/$",
            ReportEditorView.as_view(), name="editor.edit"),
    re_path(_PREFIX + r"api/fields/$",
            FieldLibraryView.as_view(), name="api.fields"),
    re_path(_PREFIX + r"api/validate/$",
            ValidateView.as_view(), name="api.validate"),
    re_path(_PREFIX + r"api/preview/$",
            PreviewView.as_view(), name="api.preview"),
]
```

Fünf Routen, nicht sieben: die zwei `api/examples/`-Zeilen aus Welle 1 sind seit
Welle 2 **weg** (siehe Abschnitt 4).

Die **Namen sind Vertrag**: `views/editor.py` reverst `api.fields`,
`api.preview` und `api.validate` und baut die JS-Konfiguration daraus;
`tests/test_editor_api.py` reverst alle fünf.

Seit Welle 2 reverst `views/editor.py` zusätzlich `event.reports.add` und
`event.reports.edit` aus `handoff/requests/persistence-dev-an-integrator-urls.md`
— **in einem `try/except NoReverseMatch`**. Fehlen die CRUD-Routen, bleibt der
Speichern-Knopf deaktiviert statt die Seite mit einem Fehler zu beenden; der
Editor funktioniert also auch mit unvollständiger `urls.py`, er kann dann nur
nicht speichern.

`identifier` ist bewusst der stabile `ReportDefinition.identifier`, nicht der
Primärschlüssel — anders als bei den CRUD-Routen von `persistence-dev`, die
`report=<pk>` verwenden. Begründung: die Editor-URL landet in Bookmarks und in
Doku, und der `identifier` übersteht Event-Kopien (ADR 0001 Abschnitt 5). Wenn
du die Abweichung nicht willst, sage es mir — der Wechsel auf `report=<pk>`
kostet mich zwei Zeilen in `editor.py` und eine im Test, aber ich möchte ihn
nicht stillschweigend machen.

## 2. Warum der volle `control/`-Präfix

Verifiziert in `pretix/multidomain/maindomain_urlconf.py`: `urlpatterns` eines
Plugins wird an der URL-**Wurzel** eingehängt, nicht unter `/control/`. Der
Präfix muss also ausgeschrieben werden, genau wie beim bestehenden
`event.index`-Eintrag und wie in `pretix/plugins/webcheckin/urls.py`. Ein
`event_patterns`-Eintrag wäre falsch: das ist der Presale-Pfad.

## 3. `save_url` — erledigt in Welle 2

Der Editor postet auf `event.reports.add` bzw. `event.reports.edit` und schickt
`name`, `description`, `identifier`, `base` und `definition` (JSON-String) —
genau die Felder von `forms.ReportDefinitionForm`. `views/crud.py` blieb dabei
unangetastet.

**Ein Detail, das du beim Verdrahten kennen musst:** `identifier` wird als
verstecktes Feld **mitgeschickt**, wenn ein bestehender Report bearbeitet wird.
Das Formularfeld ist optional, ein leerer Wert lässt `ReportDefinition.save()`
aber einen **neuen** Identifier erzeugen — und damit bricht jeder Scheduled
Export, der den Report über den stabilen Identifier referenziert. Wer eine
weitere Ansicht auf dasselbe Formular baut (Vorlagen, Import), muss dasselbe
tun. Test: `tests/test_editor_api.py::test_editor_posts_the_stable_identifier_back`
(inklusive Gegenprobe ohne das Feld).

Ohne `event.settings.general:write` ist `save_url` `None` und der Knopf bleibt
deaktiviert — der Editor selbst hängt an `event.orders:read`, weil die Vorschau
echte Bestelldaten zeigt.

Offene Abstimmung mit `persistence-dev` (nicht blockierend, Welle 2):
`ReportCreateView`/`ReportUpdateView` rendern heute `report_form.html` mit dem
JSON-Textfeld. Zwei Varianten, beide ohne Änderung an `crud.py`:

* Der Editor postet auf die CRUD-View, `report_form.html` bleibt als
  Fallback-Ansicht („JSON direkt bearbeiten") bestehen — mein Vorschlag.
* Oder `crud.py` rendert `editor.html`. Dann müsste `crud.py` meinen
  Kontext (`config`) mitbauen; das verteilt den Editor auf zwei Eigentümer und
  ist mir lieber nicht.

## 4. Zwei Routen sind wieder ausgebaut — erledigt in Welle 2

`api.examples` und `api.example` haben die Golden Fixtures als Beispiel-Reports
geliefert, damit die UI vor Registry und Persistenz klickbar ist. Der
Mock-Abschnitt in `views/api.py` ist gelöscht, `MockDefinitionListView` und
`MockDefinitionView` existieren nicht mehr. **Bitte die beiden Zeilen nicht aus
einer älteren Fassung dieses Dokuments übernehmen** — sie würden beim Import von
`urls.py` einen `ImportError` auslösen, also beim Start des Servers, nicht erst
beim Aufruf.

## 5. Übersetzungen (Welle 4)

Alle neuen Strings sind englisch. Sie stehen in
`templates/pretix_custom_reports/editor.html`,
`templates/pretix_custom_reports/preview_table.html`, `views/api.py`
(Operator-, Aggregat-, Format- und Gruppenlabels) und `views/editor.py`
(`ReportEditorView.js_strings`, die Strings für das JavaScript).

**Wichtig:** in den `.js`-Dateien steht bewusst *kein* übersetzbarer String und
kein `gettext()`-Aufruf. Django's JavaScript-Katalog (`javascript-catalog`)
liefert für ein Out-of-Tree-Plugin keine eigenen Kataloge mit, deshalb kommen
alle Texte über `js_strings()` aus dem normalen Python-Katalog. `makemessages`
braucht also **keinen** `-d djangojs`-Lauf für dieses Plugin.

## 6. Kein Eintrag in `signals.py`

Der `nav_event`-Receiver bleibt wie er ist. Sinnvolle Endgestalt (deine
Entscheidung, wie von `persistence-dev` vorgeschlagen): `event.index` auf
`ReportListView` zeigen lassen. Der Editor wird von dort verlinkt („Neuer
Report"), er braucht keinen eigenen Menüpunkt.
