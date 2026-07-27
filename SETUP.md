# Setup aus dem leeren Ordner

Du brauchst drei Befehle. Den Rest baut der Agent `env-setup` auf.

---

## Was du selbst machst

```bash
mkdir -p ~/dev/pretix-work/pretix-custom-reports
cd ~/dev/pretix-work/pretix-custom-reports

# Setup-Paket hier entpacken: .claude/, scripts/, CLAUDE.md,
# ORCHESTRIERUNG.md, ORCHESTRATOR-PROMPT.md, SPEC.md, SETUP.md
git init && git add -A && git commit -m "Agent-Setup"

bash scripts/preflight.sh
```

Das Preflight prüft Python, Compiler, Node, gettext und Plattenplatz und gibt dir
bei Bedarf **eine** Zeile zum Installieren der Systempakete aus. Es installiert
selbst nichts — genauso wenig wie der Agent später. Systemweite Installationen
laufen über dich, nicht unbemerkt im Hintergrund.

Läuft das Preflight sauber durch:

```bash
claude --add-dir ..
```

`--add-dir ..` ist zwingend. Claude Code arbeitet sonst nur im Plugin-Repo und kann
weder das venv noch den pretix-Klon eine Ebene höher anlegen.

Dann im Chat:

```
> Lies ORCHESTRIERUNG.md und starte Welle 0-env mit dem Agent env-setup.
```

---

## Was der Agent baut

```
~/dev/pretix-work/
├── venv/                     Python-Umgebung
├── pretix/                   Klon auf gepinnter Version: Dev-Server + Lesequelle
├── data/                     SQLite-DB, Medien, Logs
└── pretix-custom-reports/    dein Repo
```

Dazu Demo-Daten, die den Report-Builder überhaupt beurteilbar machen: zwei Events
(Einzeltermin und Veranstaltungsreihe), Produkte mit Varianten, Fragen aller Typen
und rund 200 Bestellungen quer über alle Status — mit Teilzahlungen, Gutscheinen,
Check-ins, fehlenden Rechnungsadressen und unbeantworteten Fragen.

Die Lücken sind Absicht. Ein Report, der über saubere Daten läuft, sieht immer
richtig aus; die falschen Zeilen entstehen bei stornierten Bestellungen und
unbeantworteten Fragen.

Am Ende liegen bereit:

| Skript | Zweck |
|---|---|
| `scripts/start-dev.sh` | Dev-Server starten |
| `scripts/reset-dev.sh` | DB zurücksetzen und neu seeden |
| `scripts/install-plugin.sh` | Plugin registrieren und Übersetzungen bauen |
| `ENVIRONMENT.md` | Version, Zugangsdaten, Stolpersteine |

**Warum pretix als Klon und nicht `pip install pretix`:** Der Klon ist zugleich
Dev-Server und die Quelle, die `pretix-researcher` in Welle 0b auswertet. Ein Wheel
gibt dir den Code, aber nicht die Core-Tests und Beispiel-Exporter, die als Vorlage
taugen — und ohne die rät der Rechercheur.

---

## Kontrolle vor Welle 0a

Wenn `env-setup` fertig meldet, prüfe selbst:

- `bash scripts/start-dev.sh`, dann `http://localhost:8000/control/`
- Login mit den Daten aus `ENVIRONMENT.md`
- beide Demo-Events sichtbar, Bestellungen vorhanden
- ein eingebauter Export (Bestellliste als CSV) liefert Zeilen

Der letzte Punkt ist der wichtigste: Er beweist, dass die Exporter-Infrastruktur
läuft, auf der das gesamte Plugin aufsetzt. Scheitert er hier, scheitert später
`exporter-dev` an derselben Stelle — nur mit viel mehr Code dazwischen.

---

## Danach

`ORCHESTRATOR-PROMPT.md` als Prompt geben. Der Ablauf ist dann:

```
0-env  env-setup          Umgebung + Demo-Daten          ← du prüfst
0a     bootstrap-dev      Plugin-Skelett, Menüpunkt      ← du prüfst
0b     pretix-researcher  API-Notizen
0c     contract-architect Contracts + Fixtures           ← du gibst frei
1–4    Parallelbetrieb
```
