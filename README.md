# KI-Council

Ein Tool mit CLI und Web-UI, das denselben Prompt an mehrere LLMs schickt und die Antworten anschließend durch ein weiteres LLM vergleichen lässt.

## Setup

Setze die API-Keys als Umgebungsvariablen:

```bash
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=...
```

Optional kannst du Modelle, Base-URLs und Timeout konfigurieren:

```bash
export OPENAI_MODEL=gpt-4o-mini
export GEMINI_MODEL=gemini-1.5-flash
export ANTHROPIC_MODEL=claude-3-haiku-20240307
export JUDGE_MODEL=gpt-4o-mini
export OPENAI_MAX_TOKENS_PARAM=max_tokens
export KI_COUNCIL_TIMEOUT=300  # Timeout in Sekunden (Standard: 300s / 5 Min)
export KI_COUNCIL_JUDGE_TIMEOUT=600  # Judge-Timeout (Standard: 600s / 10 Min)
```

### Alternative: lokale Konfigurationsdatei

Du kannst eine lokale Konfigurationsdatei `.ki-council.json` verwenden (bereits in `.gitignore`), um Keys und Modelle zu hinterlegen. Die Datei wird im aktuellen Arbeitsverzeichnis und in übergeordneten Verzeichnissen gesucht. Alternativ kannst du den Pfad explizit über `KI_COUNCIL_CONFIG` setzen. Kommentare (`//`, `#`, `/* ... */`), abschließende Kommas und typische Smart Quotes (z. B. „ “) werden beim Laden toleriert:

```json
{
  "openai_api_key": "sk-...",
  "openai_model": "gpt-4o-mini",
  "openai_base_url": "https://api.openai.com/v1",
  "openai_max_tokens_param": "max_tokens",
  "gemini_api_key": "...",
  "gemini_model": "gemini-1.5-flash",
  "anthropic_api_key": "...",
  "anthropic_model": "claude-3-haiku-20240307",
  "judge_api_key": "sk-...",
  "judge_model": "gpt-4o-mini",
  "judge_base_url": "https://api.openai.com/v1",
  "timeout": "300",
  "judge_timeout": "600",
  "ca_bundle": "/path/to/ca-bundle.pem",
  "insecure_ssl": "false"
}
```

Falls du einen eigenen Zertifikatsspeicher brauchst (z. B. in einer Firmenumgebung), setze `KI_COUNCIL_CA_BUNDLE` oder `ca_bundle` in der Datei auf den Pfad zum CA‑Bundle. Für Debugging kannst du TLS‑Prüfungen mit `KI_COUNCIL_INSECURE=1` bzw. `"insecure_ssl": "true"` deaktivieren (nicht empfohlen).

Hinweis: Einige OpenAI-Modelle (z. B. `gpt-5`/`o1`) erwarten `max_completion_tokens` statt `max_tokens`. Du kannst den Parameter über `OPENAI_MAX_TOKENS_PARAM` oder `"openai_max_tokens_param"` auf `max_completion_tokens` setzen.

Umgebungsvariablen überschreiben Werte aus der Datei. Optional kannst du den Pfad zur Datei über `KI_COUNCIL_CONFIG` oder den CLI-Flag `--config` setzen.

## Nutzung

Installiere das Paket einmalig im Projektordner (achte auf den abschließenden Punkt und führe es im Repo-Root aus):

```bash
cd /pfad/zum/KI-Council
python3 -m pip install -e .
```

```bash
python -m ki_council.web
# Öffne dann http://127.0.0.1:8000 im Browser
```

Optionen für die Web-UI:
```bash
# Mit Custom Host/Port
python -m ki_council.web --host 0.0.0.0 --port 8080

# Mit Custom Config
python -m ki_council.web --config /pfad/zur/.ki-council.json
```

### Web-Oberfläche

Starte eine einfache Web-UI für den Council:

```bash
python -m ki_council.web --host 127.0.0.1 --port 8000
```

Danach im Browser öffnen: `http://127.0.0.1:8000`

Oder als JSON-Ausgabe:

```bash
python -m ki_council.cli "Dein Prompt hier"
```

Erweiterte CLI-Optionen:

```bash
# JSON-Ausgabe für programmatische Verarbeitung
python -m ki_council.cli "Dein Prompt hier" --json

# Nur bestimmte Provider verwenden
python -m ki_council.cli "Dein Prompt hier" --providers openai,anthropic

# Verbose-Modus für detaillierte Logs
python -m ki_council.cli "Dein Prompt hier" --verbose

# Maximale Token-Anzahl anpassen
python -m ki_council.cli "Dein Prompt hier" --max-tokens 1000

# Benutzerdefinierte Konfigurationsdatei
python -m ki_council.cli "Dein Prompt hier" --config /pfad/zur/.ki-council.json

# Debug-Modus (zeigt Provider-Konfiguration)
python -m ki_council.cli "Dein Prompt hier" --debug

# Kombinierte Optionen
python -m ki_council.cli "Dein Prompt hier" --providers openai,gemini --verbose --max-tokens 2000
```

### Downgrade-Advisor: Welches (günstigste) Modell reicht für deine Prompts?

Der Eval-Modus schickt einen Satz **deiner echten Prompts** an mehrere Kandidaten-Modelle, vergleicht jeden Kandidaten blind gegen ein Baseline-Modell (anonymisiert, mit Positions-Tausch gegen Judge-Bias, optional mit einer Jury aus mehreren Judges) und empfiehlt das günstigste Modell, das in mindestens 90% der Fälle gewinnt oder gleichzieht — inklusive Ersparnis-Schätzung.

```bash
# Prompts aus einer JSONL-Datei (eine Zeile = {"prompt": "..."})
python -m ki_council.evaluate prompts.jsonl

# Direkt aus deinem ChatGPT- oder Claude-Datenexport (conversations.json):
# nimmt die erste User-Nachricht der letzten 25 Unterhaltungen
python -m ki_council.evaluate ~/Downloads/conversations.json --limit 25

# Erst den Plan ansehen (keine API-Calls, keine Kosten)
python -m ki_council.evaluate prompts.jsonl --dry-run

# Baseline und Schwelle anpassen
python -m ki_council.evaluate prompts/ --baseline gross --threshold 0.85
```

Weitere Prompt-Quellen: Ordner mit `.txt`/`.md`-Dateien (eine Datei = ein Prompt) oder `.txt`-Datei (eine Zeile = ein Prompt).

Kandidaten und Jury konfigurierst du in `.ki-council.json` (ohne `eval_candidates` werden die drei Council-Provider verwendet):

```json
{
  "eval_candidates": [
    {"name": "gross", "provider": "openai", "model": "gpt-5.4"},
    {"name": "mini", "provider": "openai", "model": "gpt-5.4-mini"},
    {"name": "haiku", "provider": "anthropic", "model": "claude-haiku-4-5"},
    {"name": "local", "provider": "openai", "model": "llama3.1:8b", "base_url": "http://localhost:11434/v1"}
  ],
  "eval_baseline": "gross",
  "eval_judges": [
    {"provider": "anthropic", "model": "claude-sonnet-5"},
    {"provider": "gemini", "model": "gemini-3.5-flash"}
  ],
  "model_prices": {"mein-firmen-modell": [0.5, 1.5]}
}
```

Hinweise:

- **Ollama/lokale Modelle:** `provider: "openai"` mit `base_url: "http://localhost:11434/v1"` — lokale Endpoints werden automatisch mit 0 € gerechnet. Ein Kandidat mit eigenem `base_url` erhält bewusst **nicht** automatisch deinen Provider-Key (kein Key-Leak an fremde URLs); bei Bedarf `api_key` explizit setzen.
- **Jury:** Nimm möglichst einen Judge, der nicht aus derselben Familie wie deine Baseline stammt — Modelle bevorzugen tendenziell die eigenen Antworten.
- **Kostenkontrolle:** `--limit` (Default 25) begrenzt die Promptzahl; `--dry-run` zeigt vorab die Anzahl der API-Calls und die Preise jedes Kandidaten.
- **Ergebnisse:** `eval_out/<timestamp>/` mit `report.md` (Empfehlung + Tabelle), `summary.json`, `responses.jsonl`, `judgments.jsonl`.
- **Nicht alle Prompts sind gleich.** Vor dem Lauf sortiert ein Klassifizierer jeden Prompt in eine Kategorie (`coding`, `analysis`, `writing`, `factual`, `other`) und eine Schwierigkeit. Der Report zeigt die Rate **pro Kategorie** — sonst überdeckt gute Alltagsleistung ein Versagen bei genau der Arbeit, für die du das teure Modell hältst. Abschaltbar mit `--no-classify`, umschaltbar mit `--segment-by difficulty`. Kostet einen billigen Zusatz-Call pro Prompt.
- **Faustregel Stichprobengröße:** Segmentierung frisst Stichprobe. Bei 25 Prompts auf vier Kategorien bleiben ~6 pro Segment — zu wenig für eine Aussage. Für belastbare Kategorien eher `--limit 100` und mehr.
- **Wie belastbar ist das Verdikt?** Der Report weist ein 95-%-Konfidenzintervall aus und markiert die Empfehlung als **vorläufig**, wenn die Stichprobe sie nicht trägt (bei Schwelle 0,9 braucht es selbst bei fehlerfreiem Lauf ~35 Prompts). Der Abschnitt **Judge health** zeigt, wie viel des Verdikts auf Unentschieden statt auf Siegen beruht — ein Judge, der die Antworten nicht auseinanderhalten kann, verschenkt sonst Downgrade-Empfehlungen.
- **Judge-Prompt anpassen:** `ki_council/pairwise_judge_prompt.txt` nach `pairwise_judge_prompt.txt.local` kopieren (wird von Git ignoriert).

#### Woher die Preise kommen

Das Verdikt ist nur so gut wie die Preise, mit denen es rechnet — ein falscher
Preis empfiehlt still das falsche Modell. Deshalb hat jeder Preis eine Quelle,
und der Report weist sie aus. Reihenfolge:

1. **Deine Config** (`model_prices` oder `price_input`/`price_output` am
   Kandidaten) — schlägt alles andere.
2. **Lokaler Endpoint** — kostet nichts, wird mit 0 gerechnet.
3. **Live-Quelle** ([models.dev](https://models.dev)) — wird beim Lauf
   abgerufen und kennt auch neu erschienene Modelle. Abschaltbar mit
   `--no-live-prices`, andere Quelle via `--prices-url`.
4. **Eingebaute Tabelle** — nur Offline-Fallback. Sie wurde zuletzt am
   **2026-07-14** gegen die Hersteller-Seiten geprüft und weiß nichts über
   später erschienene Modelle; der Report warnt, wenn ein Preis von hier stammt.

Ist ein Modell nirgends bekannt, bleiben seine Kosten **leer** — es erbt
bewusst nicht den Preis eines ähnlich heißenden Nachbarmodells. Ein fehlender
Preis ist ehrlich, ein falscher wäre gefährlich.

## Neue Features

### Robuste Fehlerbehandlung
- Wenn ein Provider fehlschlägt, werden weiterhin Antworten von anderen Providern angezeigt
- Fehlgeschlagene Provider werden in der Ausgabe mit Fehlermeldung gekennzeichnet

### Token-Tracking
- Automatisches Tracking der verwendeten Tokens pro Provider
- Anzeige von Prompt-, Completion- und Gesamt-Tokens in der Ausgabe

### Provider-Auswahl
- Über `--providers` kannst du gezielt Provider auswählen
- Beispiel: `--providers openai,anthropic` verwendet nur diese beiden
- Auch über Umgebungsvariable `KI_COUNCIL_PROVIDERS` oder Config-Eintrag `"providers"` möglich

### Logging
- `--verbose` oder `-v` aktiviert detaillierte Logs zu API-Aufrufen
- Hilfreich zum Debuggen und Verstehen des Ablaufs

### Anpassbarer Judge-Prompt
- **Standard-Prompt:** `ki_council/judge_prompt.txt` (wird von Git verwaltet)
- **Lokaler Override:** `ki_council/judge_prompt.txt.local` (wird von Git ignoriert, überschreibt Standard)
- Verfügbare Platzhalter:
  - `{prompt}` - Original-Prompt des Nutzers
  - `{responses_text}` - Formatierte Antworten aller Provider
  - `{judge_model}` - Name des Judge-Modells (z.B. "gpt-4o-mini")
  - `{other_providers}` - Komma-getrennte Liste der anderen Provider (z.B. "anthropic, gemini")
- **Tipp:** Kopiere `judge_prompt.txt` nach `judge_prompt.txt.local` für eigene Anpassungen, die nicht von Git überschrieben werden

## Hinweise

- Für die Vergleichsanalyse wird standardmäßig OpenAI genutzt. Setze `JUDGE_API_KEY`, wenn du dafür einen separaten Key verwenden möchtest.
- Ohne gesetzte API-Keys läuft das Tool nicht.
- Token-Informationen werden nur angezeigt, wenn die Provider-APIs diese zurückliefern.
