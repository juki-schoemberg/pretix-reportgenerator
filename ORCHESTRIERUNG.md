# pretix-custom-reports — Multi-Agent-Setup

Dieses Dokument beschreibt, wie mehrere Claude-Agents parallel an dem Plugin arbeiten,
ohne sich gegenseitig zu überschreiben.

---

## 1. Das eigentliche Problem bei Parallelisierung

Subagents in Claude Code laufen in **eigenen Kontextfenstern**, aber auf **demselben
Dateisystem**. Sie sehen die Zwischenstände der anderen nicht und können nicht
miteinander reden. Daraus folgen drei harte Regeln, die dieses Setup durchsetzt:

1. **Jede Datei hat genau einen Eigentümer.** Zwei Agents schreiben nie dieselbe Datei.
2. **Schnittstellen werden vor der Parallelisierung eingefroren.** Ohne stabile
   Contracts produzieren parallele Agents inkompatiblen Code.
3. **Kommunikation läuft ausschließlich über Dateien** (`handoff/`), nie über
   Annahmen über den Stand der anderen.

Alles, was nicht sauber trennbar ist — `urls.py`, `signals.py`, Migrationen,
Übersetzungen, repo-weites Formatieren — ist bewusst **seriell** und gehört einem
einzigen Agent.

---

## 2. Zwei Betriebsarten

### Modus A — Orchestrator + Subagents (Standard)

Eine Claude-Code-Session. Der Hauptagent dispatcht pro Welle mehrere Subagents
gleichzeitig. Gemeinsames Dateisystem, deshalb ist die Ownership-Tabelle Pflicht.

```
claude
> Lies ORCHESTRIERUNG.md und starte Welle 0.
```

**Gut für:** den Normalfall. Schneller Feedback-Zyklus, ein Git-Zustand.
**Grenze:** Kein echtes Isolieren. Ein Agent, der `black .` über das ganze Repo
laufen lässt, zerlegt die Arbeit der anderen.

### Modus B — Git Worktrees (für die schweren Wellen)

Echte Isolation über getrennte Checkouts und Branches. Erst ab Welle 1 sinnvoll —
vorher gibt es keinen gemeinsamen Ausgangsstand, von dem abgezweigt werden könnte:

```bash
git worktree add ../pcr-registry   feat/registry
git worktree add ../pcr-query      feat/query
git worktree add ../pcr-frontend   feat/frontend
git worktree add ../pcr-persist    feat/persistence
```

In jedem Verzeichnis eine eigene Claude-Code-Session, jeweils mit dem passenden
Agent-Prompt. Zusammenführung über Merge/PR durch den `integrator`.

**Gut für:** Welle 1 und 2, besonders `frontend-dev` (lange Laufzeit, viele Dateien).
**Kosten:** Merge-Aufwand, mehrere Terminals, höherer Verbrauch.

**Empfehlung:** Welle 0 und 3–4 in Modus A, Welle 1 und 2 in Modus B.

---

## 3. Agents

| Agent | Rolle | Schreibt Code? |
|---|---|---|
| `env-setup` | Baut venv, pretix-Klon, Demo-Daten, Start-Skripte | Skripte |
| `bootstrap-dev` | Erzeugt Plugin-Skelett, Tooling, Walking Skeleton | ja (Gerüst) |
| `pretix-researcher` | Liest den pretix-Source, erstellt verbindliche API-Notizen | nein |
| `contract-architect` | Friert Datentypen, Schema und Protokolle ein | ja (nur `contracts/`) |
| `registry-dev` | Feld-Registry inkl. Fragen und Fremdplugin-Signal | ja |
| `query-dev` | Definition → Queryset (Filter, Sortierung, Spalten) | ja |
| `persistence-dev` | Models, Migrationen, CRUD, Permissions, Logging | ja |
| `frontend-dev` | Editor-UI, Vorschau, JSON-Endpunkte | ja |
| `exporter-dev` | ListExporter, Multi-Event, Scheduled Exports | ja |
| `portability-dev` | Import/Export der Definitionen, Organizer-Vorlagen | ja |
| `security-reviewer` | Adversarialer Review, Angriffstests | nur Tests |
| `test-engineer` | Integrationstests, Performance-/Lasttests | nur Tests |
| `integrator` | Verdrahtung, i18n, Doku, Release, Merge | ja |

---

## 4. Wellenplan

```
Vorbereitung (du: 3 Befehle, siehe SETUP.md)
  Repo anlegen, Setup-Paket entpacken, scripts/preflight.sh

Welle 0-env (seriell)
  env-setup           ──▶ venv, pretix-Klon, Demo-Daten, Start-Skripte
                      ──▶  [ DU PRÜFST IM BROWSER ]

Welle 0  (seriell, drei Stufen)
  0a  bootstrap-dev      ──▶ Plugin installierbar, Menüpunkt sichtbar, pytest grün
  0b  pretix-researcher  ──▶ docs/pretix-api-notes.md
  0c  contract-architect ──▶ contracts/ + Golden Fixtures
                          ──▶  [ FREIGABE DURCH DICH ]

Welle 1  (4 parallel)
  registry-dev │ query-dev │ persistence-dev │ frontend-dev (Shell gegen Fixtures)

Welle 2  (3 parallel)
  exporter-dev │ portability-dev │ frontend-dev (Verdrahtung gegen echte Endpunkte)

Welle 3  (2 parallel)
  security-reviewer │ test-engineer

Welle 4  (seriell)
  integrator
```

**Nach jeder Welle stoppst du und liest die Statusberichte.** Eine Welle startet
erst, wenn die vorige grün ist. Der Orchestrator darf Wellen nicht selbstständig
überspringen.

### Warum diese Schnitte funktionieren

- `query-dev` braucht die Registry nur als *Contract*, nicht als Implementierung.
  Er entwickelt gegen die Stub-Registry und die Golden-Fixtures aus Welle 0.
- `frontend-dev` kann die komplette UI gegen statische Fixture-JSONs bauen, bevor
  ein einziger echter Endpunkt existiert. Deshalb startet er bereits in Welle 1 —
  er ist der langsamste Agent, jede vorgezogene Stunde zählt.
- `persistence-dev` ist von Registry und Query-Compiler unabhängig: er speichert
  nur validiertes JSON gemäß Contract.

---

## 5. Dateieigentum (verbindlich)

Ein Agent darf **ausschließlich** unter seinen Pfaden schreiben. Braucht er eine
Änderung in fremdem Gebiet, legt er eine Anforderung unter
`handoff/requests/<von>-an-<ziel>-<thema>.md` ab und arbeitet weiter.

| Pfad | Eigentümer |
|---|---|
| `scripts/**`, `ENVIRONMENT.md`, alles außerhalb des Repos | `env-setup` |
| gesamtes Repo während Welle 0a | `bootstrap-dev` (danach Übergabe, s. u.) |
| `docs/pretix-api-notes.md` | `pretix-researcher` |
| `pretix_custom_reports/contracts/**` | `contract-architect` |
| `tests/fixtures/definitions/**` | `contract-architect` |
| `pretix_custom_reports/registry/**` | `registry-dev` |
| `tests/test_registry*.py` | `registry-dev` |
| `pretix_custom_reports/query/**` | `query-dev` |
| `tests/test_query*.py` | `query-dev` |
| `pretix_custom_reports/models.py`, `forms.py` | `persistence-dev` |
| `pretix_custom_reports/migrations/**` | `persistence-dev` |
| `pretix_custom_reports/views/crud.py` | `persistence-dev` |
| `templates/**/report_list.html`, `report_confirm_delete.html` | `persistence-dev` |
| `tests/test_models.py`, `tests/test_permissions.py` | `persistence-dev` |
| `pretix_custom_reports/views/editor.py`, `views/api.py` | `frontend-dev` |
| `pretix_custom_reports/static/**` | `frontend-dev` |
| `templates/**/editor*.html`, `preview*.html` | `frontend-dev` |
| `tests/test_editor_api.py` | `frontend-dev` |
| `pretix_custom_reports/exporters.py` | `exporter-dev` |
| `tests/test_exporters.py` | `exporter-dev` |
| `pretix_custom_reports/portability/**` | `portability-dev` |
| `pretix_custom_reports/views/portability.py`, `views/templates.py` | `portability-dev` |
| `tests/test_portability.py`, `tests/test_org_templates.py` | `portability-dev` |
| `tests/test_security.py` | `security-reviewer` |
| `tests/test_integration.py`, `tests/test_performance.py`, `tests/factories.py` | `test-engineer` |
| `pretix_custom_reports/urls.py`, `signals.py`, `apps.py`, `__init__.py` | `integrator` |
| `locale/**`, `README.md`, `setup.py`, `pyproject.toml`, CI | `integrator` |
| `docs/adr/**` | alle (je eine neue Datei, nie fremde ändern) |

### Streitpunkte, die bewusst seriell sind

- **Migrationen.** Nur `persistence-dev` erzeugt sie. Zwei parallel generierte
  `0002_*.py` sind der klassische Totalschaden in solchen Setups.
- **`urls.py` und `signals.py`.** Jeder Agent braucht dort Einträge — deshalb
  gehören sie dem `integrator`. Andere Agents schreiben ihre benötigten Zeilen in
  `handoff/requests/`, exakt kopierfertig.
- **Repo-weite Formatierung und Übersetzungen.** Ausschließlich `integrator`,
  ausschließlich zwischen den Wellen.

### Eigentumsübergang nach Welle 0a

`bootstrap-dev` legt Dateien an, die danach anderen gehören. Ab Welle 1 gilt:

| Von bootstrap-dev erzeugt | Ab Welle 1 Eigentümer |
|---|---|
| `apps.py`, `signals.py`, `urls.py`, `__init__.py`, `setup.py`, CI, `Makefile` | `integrator` |
| `models.py`, `forms.py` | `persistence-dev` |
| `tests/conftest.py` | `test-engineer` |
| `contracts/__init__.py` | `contract-architect` |

`bootstrap-dev` setzt in jede dieser Dateien einen Kopfkommentar mit dem künftigen
Eigentümer. Er legt außerdem **alle** Paketverzeichnisse inklusive `__init__.py`
vorab an — auch die leeren. Fehlt etwa `views/__init__.py`, legen es in Welle 1
`persistence-dev`, `frontend-dev` und `portability-dev` gleichzeitig an, und der
Konflikt fällt erst beim Merge auf.

---

## 6. Kommunikation über Dateien

```
handoff/
├── contracts-freigegeben.md      # gesetzt von dir, Gate für Welle 1
├── status/<agent>.md             # Statusbericht, überschreibt sich pro Welle
├── requests/<von>-an-<ziel>-*.md # Änderungswunsch in fremdem Gebiet
└── blockers.md                   # Append-only, alles was eine Welle blockiert
docs/adr/NNNN-<titel>.md          # Architekturentscheidung, unveränderlich
```

**Statusbericht-Format** (jeder Agent, am Ende seines Laufs):

```markdown
# Status: <agent> — Welle <n>
Erledigt:
Nicht erledigt (und warum):
Getroffene Entscheidungen: (Verweis auf ADR-Nummern)
Contract-Abweichungen: KEINE | <Beschreibung + Begründung>
Offene Anforderungen an andere: (Verweis auf handoff/requests/)
Tests: <n> passed, <n> failed
Nächster Schritt:
```

**Contract-Änderungen sind ein Sonderfall.** Kein Agent ändert `contracts/`
eigenmächtig. Wer eine Änderung braucht: `handoff/blockers.md` ergänzen, Arbeit
stoppen, dich fragen. Ein still angepasster Contract macht die parallele Arbeit
der anderen drei Agents wertlos.

---

## 7. Gemeinsame Grundregeln für alle Agents

Diese Regeln stehen zusätzlich in `CLAUDE.md` und gelten ohne Ausnahme:

1. pretix-APIs **immer** im installierten Source verifizieren, nie aus dem
   Gedächtnis. `docs/pretix-api-notes.md` ist die verbindliche Referenz.
2. Feldzugriff ausschließlich über die Registry. **Niemals** ORM-Pfade,
   Lookups oder Operatoren aus gespeichertem oder importiertem JSON verwenden —
   das ist Untrusted Input.
3. Alle Querysets hart auf Event bzw. berechtigte Events des Organizers
   einschränken. `django-scopes` beachten, besonders in Celery-Tasks.
4. Kein eigener Scheduler. Terminierung läuft über pretix Scheduled Exports.
5. Ausgabeformate über `ListExporter`, keine eigene CSV/XLSX-Erzeugung.
6. Neue Strings englisch, `de`-Katalog pflegt der `integrator`.
7. Nur im eigenen Dateibereich schreiben. Kein `black .` / `isort .` über das
   ganze Repo — nur über eigene Dateien.
8. Keine `git commit`-Aufrufe durch Wellen-Agents in Modus A; der Orchestrator
   committet nach jeder Welle. In Modus B committet jeder auf seinem Branch.

---

## 8. Ablauf konkret

```
0. SETUP.md: Repo anlegen, preflight.sh, Claude Code mit --add-dir .. starten
0b. Welle 0-env: env-setup → Umgebung + Demo-Daten
   STOPP: selbst einloggen, einen eingebauten Export laufen lassen
1. Welle 0a: bootstrap-dev → Plugin installierbar, Menüpunkt sichtbar
   STOPP: selbst im Browser prüfen, bevor es weitergeht
2. Welle 0b/0c: API-Notizen + Contracts + Golden Fixtures
3. Contracts lesen, ggf. korrigieren, handoff/contracts-freigegeben.md setzen
4. Welle 1 (Modus B empfohlen) → 4 Branches
5. integrator merged, verdrahtet urls/signals, Tests grün
6. Welle 2 → 2–3 Branches
7. integrator merged
8. Welle 3 → Security + Integration, Findings abarbeiten
9. Welle 4 → Doku, i18n, Release
```

**Realistische Erwartung:** Der Zeitgewinn liegt bei Welle 1 und 2, weil dort vier
bzw. drei wirklich unabhängige Arbeitspakete existieren. Welle 0 zu parallelisieren
bringt nichts und produziert widersprüchliche Contracts. Wenn du unsicher bist, ob
ein Schnitt sauber ist: Prüfe, ob die beiden Agents eine gemeinsame Datei anfassen
müssten. Falls ja, ist er nicht sauber.
