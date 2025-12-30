# KI-Council

Ein kleines CLI, das denselben Prompt an mehrere LLMs schickt und die Antworten anschließend durch ein weiteres LLM vergleichen lässt.

## Setup

Setze die API-Keys als Umgebungsvariablen:

```bash
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=...
```

Optional kannst du Modelle und Base-URLs konfigurieren:

```bash
export OPENAI_MODEL=gpt-4o-mini
export GEMINI_MODEL=gemini-1.5-flash
export ANTHROPIC_MODEL=claude-3-haiku-20240307
export JUDGE_MODEL=gpt-4o-mini
export OPENAI_MAX_TOKENS_PARAM=max_tokens
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
  "ca_bundle": "/path/to/ca-bundle.pem",
  "insecure_ssl": "false"
}
```

Falls du einen eigenen Zertifikatsspeicher brauchst (z. B. in einer Firmenumgebung), setze `KI_COUNCIL_CA_BUNDLE` oder `ca_bundle` in der Datei auf den Pfad zum CA‑Bundle. Für Debugging kannst du TLS‑Prüfungen mit `KI_COUNCIL_INSECURE=1` bzw. `"insecure_ssl": "true"` deaktivieren (nicht empfohlen).

Hinweis: Einige OpenAI-Modelle (z. B. `gpt-5`/`o1`) erwarten `max_completion_tokens` statt `max_tokens`. Du kannst den Parameter über `OPENAI_MAX_TOKENS_PARAM` oder `"openai_max_tokens_param"` auf `max_completion_tokens` setzen.

Umgebungsvariablen überschreiben Werte aus der Datei. Optional kannst du den Pfad zur Datei über `KI_COUNCIL_CONFIG` oder den CLI-Flag `--config` setzen.

## Nutzung

### Grundlegende Verwendung

```bash
python -m ki_council.cli "Dein Prompt hier"
```

### Erweiterte Optionen

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

## Hinweise

- Für die Vergleichsanalyse wird standardmäßig OpenAI genutzt. Setze `JUDGE_API_KEY`, wenn du dafür einen separaten Key verwenden möchtest.
- Ohne gesetzte API-Keys läuft das Tool nicht.
- Token-Informationen werden nur angezeigt, wenn die Provider-APIs diese zurückliefern.
