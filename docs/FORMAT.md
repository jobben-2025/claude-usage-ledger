# Protokollformat (Claude Code) — Befund und Regeln

Stand: 2026-10-04, Claude-Code-Protokolle auf einem macOS-Rechner. Nur Feldnamen, Zählweisen und
Regeln; keine Inhalte. Das Format ist nicht dokumentiert und kann sich ändern — der Sammler muss
unbekannte Felder und fehlende Felder tolerieren.

## Dateien

- Hauptprotokoll: `<projects>/<kodierter-cwd>/<sessionId>.jsonl`
- Sub-Agent-Protokoll: `<projects>/<kodierter-cwd>/<sessionId>/subagents/agent-<id>.jsonl`
  (Zeilen mit `isSidechain: true`, `agentId` gesetzt).
- Eine Zeile = ein JSON-Objekt. Zeilen, die nicht parsen (z. B. halb geschriebene letzte Zeile), überspringen.

Messung: `glob('*/*.jsonl')` = 413 Hauptprotokolle, `glob('*/*/subagents/agent-*.jsonl')` = 160,
zusammen 92 450 Zeilen.

## Zeilentypen

Nur `type == "assistant"` mit `message.usage` trägt Verbrauch (27 080 von 92 450 Zeilen).
Alle anderen Typen (`user`, `attachment`, `system`, `queue-operation`, `ai-title`, `last-prompt`,
`mode`, `permission-mode`, `file-history-snapshot`, …) ignorieren.

## Felder einer Verbrauchszeile

Oberste Ebene (immer vorhanden): `type`, `uuid`, `parentUuid`, `timestamp` (ISO 8601, UTC `Z`),
`sessionId`, `cwd`, `requestId`, `isSidechain`, `entrypoint`, `userType`, `version`, `gitBranch`,
`message`. Optional: `agentId` (Sub-Agent), `effort`, `perTurnEffort`, `attributionSkill`,
`attributionAgent`, `isApiErrorMessage`, `error`.

`message`: `id`, `model`, `usage`, `role`, `content` (Inhalt wird nie gelesen).

`message.usage`:

| Feld | Vorhanden | Bedeutung |
|---|---|---|
| `input_tokens` | immer | frische Eingabe |
| `output_tokens` | immer | Ausgabe (inkl. Denk-Tokens) |
| `cache_creation_input_tokens` | immer | Cache-Schreiben gesamt |
| `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` | immer | Aufteilung nach Cache-Dauer (Summe = Gesamtwert) |
| `cache_read_input_tokens` | immer | Cache-Lesen |
| `output_tokens_details.thinking_tokens` | ca. 72 % | Denk-Anteil (bereits in `output_tokens` enthalten, nicht addieren) |
| `server_tool_use.web_search_requests` / `web_fetch_requests` | ca. 72 % | Zähler Serverwerkzeuge |
| `service_tier` | immer (Fehlerzeilen `null`) | z. B. `standard` |
| `speed` | ca. 72 % | z. B. `standard` (Fast-Modus würde hier abweichen) |
| `iterations` | ca. 72 % | Liste je Teil-Aufruf; **nicht** zusätzlich zählen (Summe steckt in den Hauptfeldern) |
| `fallback_credit` | selten | ignorieren |

Fehlende oder `null`-Werte zählen als 0 (`or 0`), nie als Fehler.

## Doppelungen — Regel

Dieselbe Antwort steht mehrfach im Protokoll (eine Zeile je Inhaltsblock bzw. Streaming-Stand;
`apiBlockIndex` zählt die Blöcke). Schlüssel: **`(message.id, requestId)`**.

Messung (Skript, 2026-10-04): 27 080 Verbrauchszeilen → 13 835 eindeutige Schlüssel; 9 933 Schlüssel
mit ≥ 2 Zeilen (2: 7 470, 3: 2 027, 4: 217, 5+: 197). Nur 13 245 Zeilen sind Überschuss — ohne
Deduplizierung würde der Verbrauch rund doppelt gezählt.
Bei 8 465 dieser Schlüssel sind alle `usage`-Objekte identisch; bei 1 468 weicht **ausschließlich**
`output_tokens` ab (wächst von Zeile zu Zeile), Eingabe- und Cachewerte sind immer gleich.
In allen 1 468 Fällen hat die **letzte** Zeile das Maximum an `output_tokens`.
Kein Schlüssel kommt in mehr als einer Datei oder unter mehr als einer `sessionId` vor.

**Regel:** je Schlüssel eine Zeile zählen — die mit dem größten `output_tokens` (bei Gleichstand die
letzte). Eingabe-/Cachefelder aus derselben Zeile. Fehlt `message.id` oder `requestId`: Zeile einzeln
zählen (in der Stichprobe nie der Fall).

## Auszuschließen

- `message.model == "<synthetic>"` (62 Zeilen, `isApiErrorMessage: true`): alle Tokens 0, kein Verbrauch.
- Nicht-Assistentenzeilen (siehe oben).

Modelle in der Stichprobe: `claude-sonnet-5`, `claude-opus-5`, `claude-opus-5-5`, `claude-sonnet-5-5`,
`claude-haiku-4-5-20251001`, `claude-opus-4-8`, `claude-fable-5-1`. Modell-IDs unverändert speichern;
die Preiszuordnung (TASK5) tolerant gegen unbekannte IDs.

## Sub-Agenten

Sub-Agent-Antworten stehen ausschließlich in `…/subagents/agent-*.jsonl` (160 Dateien, 4 611
eindeutige Antworten, alle `isSidechain: true`); Hauptdateien enthalten nur `isSidechain: false`
(9 226). Beide Quellen zählen, getrennt gekennzeichnet (`subagent` ja/nein, `agentId`). Es gibt keine
Überschneidung (Schlüssel dateiübergreifend eindeutig).

## Unbeaufsichtigt vs. interaktiv

`entrypoint` je Zeile (Hauptprotokolle, Zeilen mit Wert): `cli` (interaktiv), `sdk-cli`
(nicht interaktiv, `claude -p`), `claude-vscode` (IDE). Sitzungsebene: 279 Hauptprotokolle `sdk-cli`,
133 `cli`, 1 `claude-vscode`; `entrypoint` ist innerhalb einer Session konstant.
`userType` ist überall `external` und trennt nichts.

**Regel:** `entrypoint == "sdk-cli"` ⇒ `unattended = true` (nicht interaktiv, also Handoff-Wrapper
und sonstige Skripte, die `claude -p` aufrufen); sonst `false`.
Grenze: Nicht jeder `sdk-cli`-Lauf ist ein Handoff-Lauf. Von 279 `sdk-cli`-Sessions beginnen nur 32
innerhalb von 120 s nach einer `firing …`-Zeile in `~/.claude/handoffs/*.log` (bei `cli`: 1 von 133).
Die Logs sind daher nur eine unvollständige Zusatzquelle; eine Unterscheidung „Handoff" vs. „anderes
Skript" ist über das Protokoll nicht eindeutig möglich. Falls nötig, später als optionales Feld
`handoff_hint` aus den Logfenstern (Zeitstempel „firing …" bis nächste Zeile derselben Datei) ableiten;
nicht Teil der Pflichtregel.

## Projektzuordnung

`cwd` ist je Zeile gesetzt und kann sich innerhalb einer Session ändern (95 von 413 Hauptprotokollen
haben ≥ 2 verschiedene `cwd`). Deshalb **je Zeile** zuordnen, nicht je Datei.

1. `cwd` unter `<HOME>/PROJECTS/<name>/…` ⇒ Projekt `<name>` (Unterordner zählen zum Projekt).
   Das Wurzelverzeichnis der Projekte ist konfigurierbar (Default `~/PROJECTS`).
2. Sonst Sonderfälle mit festen Bezeichnern: `cwd == <HOME>` ⇒ `(home)`; `<HOME>/.claude/…` ⇒
   `(claude-config)`; sonstiger Pfad unter `<HOME>` ⇒ Bezeichner aus dem ersten Pfadsegment unter
   `<HOME>` (z. B. `.cache/<x>`, `Desktop/<x>`), außerhalb `<HOME>` ⇒ `(other)`.
3. Fehlt `cwd`: Fallback auf den kodierten Ordnernamen unter `<projects>/` (Schrägstriche → `-`; die
   Dekodierung ist mehrdeutig, wenn Namen `-` enthalten — deshalb nur als Fallback und dann
   Präfixvergleich gegen vorhandene Projektordner).
4. Altbestand: Projekte lagen früher unter `~/Desktop`; solche Zeilen bekommen den Namen des Ordners
   unter `<HOME>/Desktop/`, damit sie nicht in `(other)` verschwinden.

Messung (Session-`cwd`-Werte, 2026-10-04): 523 unter dem Projektwurzelverzeichnis, 61 unter einem
Cache-Ordner, 5 = `<HOME>`, 1 unter `.claude`, 4 sonstige.

## Offen / Annahmen

- `cleanupPeriodDays` wurde auf 365 gesetzt; ohne Archiv (TASK7) gehen ältere Protokolle verloren.
- Inkrementelles Einlesen: Schlüssel `(message.id, requestId)` ist idempotent; der Sammler darf
  Dateien erneut lesen (Offsets/mtime nur als Optimierung).
- Zeitstempel in UTC speichern; Tagesgrenzen erst im Bericht in lokaler Zeit bilden.
