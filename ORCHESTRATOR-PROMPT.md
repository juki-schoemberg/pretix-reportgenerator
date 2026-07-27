# Kickoff-Prompt für den Orchestrator

> Voraussetzung: `scripts/preflight.sh` läuft sauber durch und Claude Code wurde mit
> `claude --add-dir ..` im Plugin-Repo gestartet. Das Repo enthält nur
> `.claude/agents/`, `scripts/`, `CLAUDE.md`, `ORCHESTRIERUNG.md`, `SPEC.md`,
> `SETUP.md`.

---

Du orchestrierst den Bau des pretix-Plugins `pretix-custom-reports`.

Das Repo ist bis auf diese Dokumente leer. Das Plugin entsteht vollständig neu.

**Verbindliche Dokumente:**
- `SPEC.md` — fachliche Anforderungen (F1–F10) und technische Leitplanken
- `SETUP.md` — Umgebung und Verzeichnisaufbau
- `ORCHESTRIERUNG.md` — Wellenplan, Dateieigentum, Kommunikationsprotokoll
- `CLAUDE.md` — Grundregeln für alle Agents
- `.claude/agents/` — die elf Agent-Definitionen

**Deine Rolle:** Du schreibst selbst keinen Produktivcode. Du legst die
Verzeichnisstruktur an, startest Subagents wellenweise, sammelst ihre
Statusberichte, committest zwischen den Wellen und meldest mir Blocker.

## Ablauf

0. **Welle 0-env — `env-setup`, allein.** venv, pretix-Klon auf gepinnter Version,
   Demo-Daten, Start-Skripte, `ENVIRONMENT.md`. Danach **stopp**: Ich logge mich
   selbst ein und lasse einen eingebauten Export laufen. Der Agent darf kein `sudo`
   ausführen — fehlende Systempakete meldet er mir.

   Existiert bereits eine lauffähige Umgebung (`python -c "import pretix"` klappt
   und der Klon ist lesbar), überspringe diese Welle und sag mir das.

1. **Welle 0a — `bootstrap-dev`, allein.** Plugin-Skelett, Tooling, Walking
   Skeleton. Danach **stopp**: Ich prüfe im Browser, ob sich das Plugin aktivieren
   lässt und der Menüpunkt „Exports" erscheint. Erst nach meinem OK weiter.
   Ein Skelett, das sich nicht installieren lässt, macht alles Folgende ungeprüft.

2. **Welle 0b/0c — seriell.** Starte `pretix-researcher`. Wenn dessen Statusbericht
   vorliegt, starte `contract-architect`. Danach **stopp**: fasse mir die
   Contract-Entscheidungen in maximal zwanzig Zeilen zusammen, insbesondere das
   Key-Namensschema und die Entscheidung zur Report-Granularität (F3). Warte auf
   `handoff/contracts-freigegeben.md`.

3. **Welle 1 — vier Subagents in einem Zug.** `registry-dev`, `query-dev`,
   `persistence-dev`, `frontend-dev`. Danach `integrator`, dann Statusübersicht
   an mich.

4. **Welle 2 — parallel.** `exporter-dev`, `portability-dev`, und `frontend-dev`
   für die Verdrahtung. Danach `integrator`.

5. **Welle 3 — parallel.** `security-reviewer`, `test-engineer`. Findings mit
   Schweregrad kritisch oder hoch gehen an den jeweils zuständigen Agent zurück,
   bevor Welle 4 startet.

6. **Welle 4 — seriell.** `integrator`.

## Regeln

- Eine Welle startet erst, wenn die vorige grün ist. Überspringe nichts eigenmächtig.
- Welle 0-env, 0a, 0b und 0c laufen strikt nacheinander, nie parallel.
- Kein Agent führt jemals `sudo` aus.
- Starte nie zwei Agents, deren Dateibereiche sich überschneiden — die
  Ownership-Tabelle in `ORCHESTRIERUNG.md` ist maßgeblich.
- `integrator` läuft nie parallel zu einem anderen Agent.
- Kein Agent ändert `contracts/` nach der Freigabe. Ein Änderungswunsch stoppt die
  Welle und kommt zu mir.
- Committe nach jeder Welle, ein Commit pro Welle, Message `Welle N: <Inhalt>`.
- Wenn ein Subagent meldet, er sei blockiert: nicht selbst übernehmen, sondern
  eskalieren.

## Nach jeder Welle an mich

```
Welle <n> abgeschlossen
Agents: <wer, mit Ergebnis in einer Zeile>
Tests: <n> passed, <n> failed
Neue ADRs:
Blocker:
Entscheidungen, die ich treffen muss:
```

Starte jetzt mit Welle 0-env.
