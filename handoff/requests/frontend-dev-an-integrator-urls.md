# frontend-dev → integrator: URLs für Editor und JSON-Endpunkte

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
    # Welle 1 only, siehe Abschnitt 4
    re_path(_PREFIX + r"api/examples/$",
            MockDefinitionListView.as_view(), name="api.examples"),
    re_path(_PREFIX + r"api/examples/(?P<slug>[a-z0-9][a-z0-9_.-]*)/$",
            MockDefinitionView.as_view(), name="api.example"),
]
```

Die **Namen sind Vertrag**: `views/editor.py` reverst `api.fields`,
`api.preview`, `api.validate`, `api.examples` und baut die Editor-URLs für die
JS-Konfiguration daraus; `tests/test_editor_api.py` reverst alle sieben.

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

## 3. Welle 2: `save_url` setzen (eine Zeile)

Der Editor rendert schon ein `<form>` mit `name`, `description` und den
versteckten Feldern `definition` (JSON-String) und `base` — genau die Felder,
die `forms.ReportDefinitionForm` erwartet (`identifier` ist dort optional).
Solange kein Ziel existiert, ist der Speichern-Knopf deaktiviert.

Sobald die CRUD-Routen stehen, setze ich in `views/editor.py`
`ctx["save_url"]` auf `event.reports.add` bzw. `event.reports.edit`. **Das ist
meine Arbeit, nicht deine** — hier steht es nur, damit du weißt, dass der Editor
kein zweites Speicher-Backend braucht und `views/crud.py` unangetastet bleiben
kann.

Offene Abstimmung mit `persistence-dev` (nicht blockierend, Welle 2):
`ReportCreateView`/`ReportUpdateView` rendern heute `report_form.html` mit dem
JSON-Textfeld. Zwei Varianten, beide ohne Änderung an `crud.py`:

* Der Editor postet auf die CRUD-View, `report_form.html` bleibt als
  Fallback-Ansicht („JSON direkt bearbeiten") bestehen — mein Vorschlag.
* Oder `crud.py` rendert `editor.html`. Dann müsste `crud.py` meinen
  Kontext (`config`) mitbauen; das verteilt den Editor auf zwei Eigentümer und
  ist mir lieber nicht.

## 4. Welle 2: zwei Routen wieder ausbauen

`api.examples` und `api.example` liefern die Golden Fixtures als Beispiel-Reports
und existieren nur, damit die UI vor Registry und Persistenz vollständig
klickbar ist. Sie sind in einer installierten Kopie des Plugins **inert** (das
Fixture-Verzeichnis liegt unter `tests/`, das `pyproject.toml` nicht paketiert →
beide Views antworten mit 404), können also gefahrlos stehen bleiben. Sauber ist
trotzdem: in Welle 2 lösche ich den Mock-Abschnitt in `views/api.py`, und dann
müssen diese beiden Routen mit raus. Ich melde mich, wenn es soweit ist.

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
