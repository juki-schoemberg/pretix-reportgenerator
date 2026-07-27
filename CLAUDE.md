# pretix-custom-reports

Plugin für frei konfigurierbare Auswertungen über Bestellungen und
Bestellpositionen in pretix.

## Wer bin ich?

Prüfe zuerst, welcher Agent du bist (`.claude/agents/`). Schreibe **ausschließlich**
in deinem Dateibereich laut Ownership-Tabelle in `ORCHESTRIERUNG.md`. Änderungen in
fremdem Gebiet gehen als Datei nach `handoff/requests/`.

## Grundregeln (ohne Ausnahme)

1. pretix-APIs immer im installierten Source verifizieren, nie aus dem Gedächtnis.
   `docs/pretix-api-notes.md` ist die verbindliche Referenz.
2. Feldzugriff ausschließlich über die Registry. ORM-Pfade, Lookups und Operatoren
   kommen **immer** aus einem `ReportField`, **nie** aus gespeichertem oder
   importiertem JSON. Importierte Dateien sind Untrusted Input.
3. Kein `eval`, kein Raw SQL aus Nutzereingaben, kein String-Bau von Lookups.
4. Alle Querysets hart auf Event bzw. berechtigte Events einschränken.
   `django-scopes` beachten, besonders in Celery-Tasks.
5. Kein eigener Scheduler. Terminierung läuft über pretix Scheduled Exports.
6. Ausgabeformate über `ListExporter`, keine eigene CSV/XLSX-Erzeugung.
7. Contracts (`pretix_custom_reports/contracts/`) sind nach Freigabe eingefroren.
   Änderungswunsch → `handoff/blockers.md`, Arbeit stoppen, eskalieren.
8. Neue Strings englisch. Den `de`-Katalog pflegt ausschließlich der `integrator`.
9. Kein `black .` / `isort .` über das ganze Repo. Nur über eigene Dateien.
10. In Modus A committet nur der Orchestrator. In Modus B jeder auf seinem Branch.

## Umgebung

Das Plugin liegt neben einem pretix-Klon im selben venv:

```
~/dev/pretix-work/{venv, pretix, pretix-custom-reports}
```

Der pretix-Source unter `../pretix/` ist die verbindliche Referenz für alle
API-Fragen. Aufbau und Zugangsdaten in `ENVIRONMENT.md`, Ersteinrichtung in
`SETUP.md`.

Kein Agent führt `sudo` aus. Kein `pip install` ohne aktives venv.

## Befehle

```
bash scripts/start-dev.sh               # Dev-Server
bash scripts/reset-dev.sh               # DB zurücksetzen + Demo-Daten
bash scripts/install-plugin.sh          # Plugin registrieren
pytest                                  # Tests
pytest -m "not performance"             # ohne Lasttests
flake8 . && isort -c . && black --check .
python -m pretix makemigrations --check
pip install -e .                        # Plugin registrieren
make                                    # Übersetzungen kompilieren
```

## Struktur

```
contracts/    eingefrorene Typen, Schema, Protokolle, Stubs
registry/     Feldbibliothek (Core, Fragen, Fremdplugins)
query/        Definition -> Queryset
portability/  Import/Export, Vorlagen-Auflösung
views/        crud (persistence) | editor+api (frontend) | portability | templates
exporters.py  ListExporter + Scheduled-Export-Anbindung
```

## Weiterführend

- `SPEC.md` — fachliche Anforderungen
- `ORCHESTRIERUNG.md` — Wellenplan und Dateieigentum
- `docs/adr/` — Architekturentscheidungen
- `docs/extending.md` — Feld-Signal für Fremdplugins
