# Contracts freigegeben — Welle 0c

Freigegeben von: Tobias Berndt (Orchestrator-Nutzer)
Freigegeben am: 2026-07-30
Basis: Commit `29fd7bb` (Welle 0c: Contracts eingefroren)

`pretix_custom_reports/contracts/` und `tests/fixtures/definitions/` sind ab
sofort eingefroren. Änderungen ausschließlich über `handoff/blockers.md` und
erneute Freigabe.

Geprüft vor Freigabe (siehe `docs/adr/0001-contracts.md`):

1. Abschnitt 7 — F3: Positionsfelder auf Basis `order` sind aggregiert, nicht
   gesperrt.
2. Abschnitt 5.1 — Report-Referenz über stabilen `identifier`, nicht PK.
3. `tests/fixtures/definitions/_index.json` → `required_field_keys` (48 Keys,
   verbindlich für `registry-dev`).
4. Abschnitt 2 — doppelter Unterstrich in Feld-Keys überall verboten.

Welle 1 ist freigegeben: `registry-dev`, `query-dev`, `persistence-dev`,
`frontend-dev` (Shell gegen Fixtures) laufen parallel.

**Betriebsart:** Modus A (gemeinsamer Checkout), nicht Modus B (Worktrees) wie
in `ORCHESTRIERUNG.md` §2 empfohlen. Begründung: Der Orchestrator dispatcht
Subagents im selben Claude-Code-Job, nicht als getrennte Terminal-Sessions —
echte Worktree-Isolation ist mit dem Pfadlayout dieses Projekts (`../pretix`,
`../venv` eine Ebene über dem Repo) nicht praktikabel (siehe
`.claude/settings.json`, `bgIsolation: none`). Die Ownership-Tabelle in
`ORCHESTRIERUNG.md` §5 ist für Welle 1 vollständig überschneidungsfrei
(`registry/**`, `query/**`, `models.py`+`forms.py`+`migrations/**`+
`views/crud.py`, `views/editor.py`+`views/api.py`+`static/**`), daher trägt
Modus A hier ohne zusätzliches Risiko.
