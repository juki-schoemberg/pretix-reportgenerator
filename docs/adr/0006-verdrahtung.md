# ADR 0006 — Verdrahtung, Navigation, Log-Anzeige und Sprachfassung

- **Status:** akzeptiert
- **Datum:** 2026-08-02
- **Autor:** `integrator` (Welle 4)
- **Betrifft:** `pretix_custom_reports/urls.py`, `signals.py`, `apps.py`,
  `locale/**`, `README.rst`, CI
- **Baut auf:** ADR 0000 (Grundaufbau), ADR 0001 (Contracts, eingefroren),
  ADR 0002 (Registry), ADR 0005 (Editor), und die neun Dateien in
  `handoff/requests/erledigt/`

> Nummer 0006, weil 0003 und 0004 in Welle 1 für `query-dev` und
> `persistence-dev` reserviert wurden (ADR 0005 Kopf). Beide haben keine ADR
> geschrieben; die Lücke bleibt bewusst offen, statt sie nachträglich zu
> belegen und damit Verweise auf „ADR 0003" mehrdeutig zu machen.

---

## Kontext

Bis Welle 3 war das Plugin funktional vollständig, aber **nicht verdrahtet**:
`urls.py` kannte eine einzige Route auf eine Platzhalterseite, `signals.py`
hatte nur den `nav_event`-Empfänger, und die Kommentarzeilen 56–61 waren
Platzhalter. Vier Agents hatten ihre Routen- und Empfängerzeilen kopierfertig
in `handoff/requests/` hinterlegt.

Diese ADR hält die Entscheidungen fest, die beim Zusammenfügen anfielen und die
nicht schon in einer der Vorgänger-ADRs stehen. Alles, was hier nicht steht,
wurde wörtlich aus den Handoff-Dateien übernommen.

---

## 1. Entscheidung: die Routenlisten bleiben bei den Views

`urls.py` verkettet sechs Modulvariablen (`event_urlpatterns`,
`editor_urlpatterns`, `api_urlpatterns`, `portability_event_urlpatterns`,
`templates_event_urlpatterns`, `templates_organizer_urlpatterns`) und
definiert selbst genau eine Route.

**Begründung:** Jede Route steht damit genau einmal im Repo, und zwar neben der
View, die sie bedient. Die Alternative — alle 20 Routen ausgeschrieben in
`urls.py` — hätte jede spätere Änderung an einer View zu einer Änderung in
fremdem Dateigebiet gemacht. Die Präfixe überschneiden sich nicht; ein Test
prüft, dass alle 20 Namen eindeutig sind, sich reversen lassen und wieder auf
sich selbst auflösen (kein Shadowing).

## 2. Entscheidung: `event.index` zeigt auf `ReportListView`

`event.index` zeigte auf `views/placeholder.py` (Welle 0a). Sowohl
`persistence-dev` als auch `frontend-dev` haben das Ziel dem `integrator`
überlassen.

**Entscheidung:** `event.index` → `views/crud.py::ReportListView`.
`event.reports` bleibt als zweiter Name auf `…/customreports/reports/`
bestehen.

**Begründung:** Der Menüpunkt „Exports" (F1) muss zu etwas Benutzbarem führen,
und `event.index` ist der Name, auf den `PretixPluginMeta.navigation_links`
und der `nav_event`-Empfänger verweisen. `event.reports` **nicht** zu streichen
ist die billigere Hälfte der Entscheidung: `views/crud.py::get_success_url`,
`views/portability.py`, `views/templates.py` und drei Tests reversen genau
diesen Namen. Zwei URLs auf dieselbe Liste sind der geringere Preis gegenüber
Änderungen in vier fremden Dateien.

**Folge:** `views/placeholder.py` und
`templates/pretix_custom_reports/placeholder.html` sind toter Code. Sie werden
hier **nicht** gelöscht — fremdes Dateigebiet (`bootstrap-dev`; `views/__init__.py`
weist die Löschung `frontend-dev` zu) —, sondern im Statusbericht als offener
Punkt gemeldet.

## 3. Entscheidung: `identifier` in den Editor-URLs bleibt

`frontend-dev` benutzt in `editor.edit` den stabilen
`ReportDefinition.identifier`, die CRUD- und Vorlagenrouten benutzen den
Primärschlüssel (`report=<pk>`, `template=<pk>`). `frontend-dev` hat angeboten,
das anzugleichen.

**Entscheidung:** Die Abweichung bleibt.

**Begründung:** Die beiden URL-Familien beantworten verschiedene Fragen. Die
Editor-URL landet in Lesezeichen und in Dokumentation und soll eine
Event-Kopie überleben — dafür ist der Identifier gebaut (ADR 0001 Abschnitt
5.1), ein PK nicht. Die CRUD-URLs sind Formularziele innerhalb einer Sitzung;
dort ist der PK der kürzere und eindeutigere Weg, und ein Identifier hätte den
zusätzlichen Nachteil, dass er sich ändern kann, während der PK das nicht tut.
Der Preis ist eine Uneinheitlichkeit, die genau an einer Stelle sichtbar wird
(zwei Zeilen in `views/editor.py`), und die ist dort dokumentiert.

## 4. Entscheidung: der `nav_organizer`-Empfänger prüft zusätzlich das Plugin

Übernommen aus `handoff/requests/erledigt/portability-dev-an-integrator-urls.md`
Abschnitt 4, mit **einer** Ergänzung:

```python
if not organizer.events.filter(plugins__contains=PLUGIN_MODULE).exists():
    return []
```

**Begründung:** `nav_organizer` ist ein `OrganizerPluginSignal`, wir sind ein
Event-Level-Plugin und hängen uns über die Legacy-Ausnahme dran. Der Empfänger
läuft deshalb für **jeden** Organizer, auch für solche, die das Plugin nie
eingeschaltet haben. `views/templates.py::OrganizerPluginActiveMixin` antwortet
in dem Fall mit 404 — ein Menüpunkt, der garantiert ins Leere führt, wäre
schlechter als kein Menüpunkt. Die Ergänzung stellt nur dieselbe Frage wie die
View, sie trifft keine neue fachliche Entscheidung.

Die dabei entstehende `DeprecationWarning` ist gewollt und bleibt (siehe
Abschnitt 7).

## 5. Entscheidung: Log-Typen werden registriert, Objekt-Link selbst gebaut

`log_entry_types.new_from_dict({...})` auf einer `EventLogEntryType`-Unterklasse
in `signals.py`, für alle sieben Action-Types aus `contracts/protocols.py`.
Verifiziert in `pretix/base/logentrytypes.py` und
`pretix/base/logentrytype_registry.py`, Vorbild `pretix/plugins/badges/signals.py`.

Zwei Abweichungen vom Vorschlag aus
`handoff/requests/erledigt/persistence-dev-an-integrator-urls.md` Abschnitt 4:

1. **Import aus `pretix.base.logentrytypes`,** nicht aus
   `pretix.base.logentrytype_registry` — `EventLogEntryType` liegt im ersten
   Modul, das zweite enthält nur die Registry und die Basisklasse.
2. **`get_object_link_info` ist überschrieben,** statt
   `object_link_viewname`/`object_link_argname` zu setzen. Die geerbte
   Implementierung reverst mit `logentry.event.slug`; eine Organizer-Vorlage
   hat gar kein Event (`event=None`, XOR mit `organizer`), und der Fehler
   träte beim *Rendern der Log-Seite* auf. Vorlagen bekommen deshalb den Link
   auf `organizer.templates.edit`, Event-Reports den auf `event.reports.edit`.

**Kein Shredder-Mixin.** `shred_pii` wird in pretix 2026.6.0 nirgends
aufgerufen, und pretix' eigener `CoreEventLogEntryType` deklariert auch keines.
Wenn Log-Shredding kommt, ist die Klasse die richtige Stelle.

## 6. Entscheidung: der `de`-Katalog ist in der Sie-Form

pretix pflegt zwei deutsche Kataloge: `de` (Sie) und `de_Informal` (Du),
verifiziert in `pretix/locale/de/LC_MESSAGES/django.po` gegen
`pretix/locale/de_Informal/…`. Wir liefern `de`, also die **Sie-Form**. Eine
Du-Form im `de`-Katalog würde in derselben Oberfläche neben der Sie-Form von
pretix stehen.

Terminologie ist an pretix' eigenen `de`-Katalog angeglichen (Bestellnummer,
Bestellposition, Verkaufskanal, Rechnungsadresse, Termin für `SubEvent`,
Zutrittsprodukt, Name Teilnehmer*in). Ein `de_Informal`-Katalog ist möglich,
aber nicht Teil dieser Welle.

**Extraktion ohne `xgettext`:** Die Referenzumgebung hat kein `gettext`
(`ENVIRONMENT.md` Stolperstein 3), `makemessages` ist damit nicht lauffähig.
Der Katalog wurde stattdessen mit Djangos eigenem
`django.utils.translation.template.templatize()` (derselbe Vorverarbeitungs-
schritt, den `makemessages` benutzt) plus `babel` und `polib` erzeugt — 442
Einträge, 100 % übersetzt, Platzhalter maschinell gegengeprüft. Sobald
`gettext` verfügbar ist, ist `make localegen` der reguläre Weg; die Datei ist
ein normales `.po` und `msgmerge`-fähig.

**Kein `djangojs`-Lauf.** In den `.js`-Dateien steht bewusst kein übersetzbarer
String; alle Texte kommen über `ReportEditorView.js_strings()` aus dem
Python-Katalog (ADR 0005 Abschnitt 8).

## 7. Entscheidung: die `DeprecationWarning` bleibt stehen

Zwei Stellen erzeugen beim Import
`DeprecationWarning: This signal will soon be only available for plugins that
declare to be organizer-level`: `nav_organizer` und
`register_multievent_data_exporters`. Beides sind
`OrganizerPluginSignal(allow_legacy_plugins=True)`, wir sind Event-Level.

**Entscheidung:** Der saubere Ausweg (`level =
PLUGIN_LEVEL_EVENT_ORGANIZER_HYBRID` in `PretixPluginMeta`) wird **nicht**
gegangen. Das ist eine Entscheidung über den Charakter des Plugins mit Folgen
für Aktivierung, Navigation und Exporter-Sichtbarkeit, und sie braucht mehr als
eine Verdrahtungswelle. Sie ist als offener Punkt an den Orchestrator gemeldet.

Solange `setup.cfg` kein `filterwarnings = error` setzt, kostet das nichts.
Wenn es eingeführt wird, braucht es denselben Filter, den pretix in seinem
eigenen `setup.cfg` setzt.

## 8. Konsequenzen

* Das Plugin ist ab hier produktiv benutzbar: 20 Routen, sechs Empfänger,
  sieben Log-Typen, ein vollständiger `de`-Katalog.
* `views/placeholder.py` und `placeholder.html` sind tot und sollten von ihrem
  Eigentümer entfernt werden.
* Der CI-Migrationscheck braucht App-Label und `--dry-run`
  (`python -m pretix makemigrations pretix_custom_reports --check --dry-run`);
  `CLAUDE.md` und `.github/workflows/tests.yml` sind entsprechend korrigiert.
* Ein Integrationstest schlägt seit dem Verdrahten fehl, weil er die
  Event-Kopie doppelt ausführt — gemeldet, nicht repariert (fremdes Gebiet).

---

## 9. Nachtrag 2026-08-10: Vorlagen-Editor, Navigationslabel, Log-Links

Nachgetragen statt oben eingearbeitet, weil ADRs nicht rückwirkend umgeschrieben
werden (`ORCHESTRIERUNG.md` Abschnitt 6: „unveränderlich"). Wer die Abschnitte 2
und 5 liest, muss
diesen Nachtrag mitlesen — die drei Punkte hier setzen dort etwas außer Kraft.

**a) `template_editor_urlpatterns` ist die siebte Routenliste.** Aus
`views/editor.py` (frontend-dev), verdrahtet neben
`templates_organizer_urlpatterns`, weil beides Organizer-Ebene ist. Damit sind es
**22 Routen**, nicht 20. Anleitung und Begründung der pk-Adressierung:
`handoff/requests/erledigt/frontend-dev-an-integrator-template-editor-urls.md`.
Die beiden Namen `organizer.templates.editor.new` / `.edit` sind Vertrag —
`template_list.html` verlinkt sie **ohne** `try/except`, sie fehlen zu lassen
kostet die ganze Vorlagenliste, nicht nur einen Knopf.

**b) Das Event-Label heißt „Reports", nicht mehr „Exports".** Nutzerwunsch: die
Menüpunkte auf Event- und Veranstalter-Ebene sollen erkennbar zusammengehören.
„Exports" war zudem eine Fehlbenennung — das Plugin ist ein Report-Builder, kein
zweiter Exporteur (`PretixPluginMeta.description`). Das Organizer-Label bleibt
„Report templates"; das gemeinsame Wort „Report" ist die Verbindung. Der String
steht an drei Stellen und muss dort synchron bleiben:
`signals.py::navbar_event_entry`, `apps.py::PretixPluginMeta.navigation_links`
(Plugin-Einstellungsliste über `EventPlugins.prepare_links`) und
`tests/test_smoke.py::NAV_LABEL`. Im `de`-Katalog ändert sich nichts: „Exports"
und „Reports" waren beide mit „Auswertungen" übersetzt.

`placeholder.html` benutzt weiterhin `{% trans "Exports" %}` und hält den msgid
allein am Leben. Sie ist seit Abschnitt 2 toter Code und bleibt fremdes
Dateigebiet — mitgeändert wird sie deshalb nicht, gemeldet ist sie weiterhin.

**c) Log-Einträge verlinken in den Editor, nicht ins JSON-Formular.** Abschnitt 5
schrieb Vorlagen den Link auf `organizer.templates.edit` und Event-Reports den
auf `event.reports.edit` zu. Beides sind die reinen Formularansichten. Das ist
dieselbe Fehlerklasse wie in der Reportliste (Commit `3a56a0a`) und die Log-Seite
war der letzte verbliebene Ort dieser Art. Jetzt:

| Fall | Ziel | Adressierung |
|---|---|---|
| Event-Report | `editor.edit` | `identifier` |
| Vorlage | `organizer.templates.editor.edit` | `pk` |

Die zwei Adressierungsarten sind kein Versehen, sondern Abschnitt 3 dieser ADR
plus Abschnitt 2 der Handoff-Datei aus (a). Der `NoReverseMatch`-Fallback auf
den reinen Namen bleibt: eine unauflösbare Route darf die Log-Seite nicht
zerlegen.
