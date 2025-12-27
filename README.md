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

Installiere das Paket einmalig im Projektordner (achte auf den abschließenden Punkt):

```bash
python3 -m pip install -e .
```

```bash
python -m ki_council.cli "Dein Prompt hier"
```

### Web-Oberfläche

Starte eine einfache Web-UI für den Council:

```bash
python -m ki_council.web --host 127.0.0.1 --port 8000
```

Danach im Browser öffnen: `http://127.0.0.1:8000`

Oder als JSON-Ausgabe:

```bash
python -m ki_council.cli "Dein Prompt hier" --json
```

Konfigurations- und Diagnosetipps:

```bash
python -m ki_council.cli "Dein Prompt hier" --config /pfad/zur/.ki-council.json --debug
```

## Hinweise

- Für die Vergleichsanalyse wird standardmäßig OpenAI genutzt. Setze `JUDGE_API_KEY`, wenn du dafür einen separaten Key verwenden möchtest.
- Ohne gesetzte API-Keys läuft das Tool nicht.
