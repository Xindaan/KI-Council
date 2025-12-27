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
```

### Alternative: lokale Konfigurationsdatei

Du kannst eine lokale Konfigurationsdatei `.ki-council.json` verwenden (bereits in `.gitignore`), um Keys und Modelle zu hinterlegen. Die Datei wird im aktuellen Arbeitsverzeichnis und in übergeordneten Verzeichnissen gesucht. Alternativ kannst du den Pfad explizit über `KI_COUNCIL_CONFIG` setzen. Kommentare (`//`, `#`, `/* ... */`), abschließende Kommas und typische Smart Quotes (z. B. „ “) werden beim Laden toleriert:

```json
{
  "openai_api_key": "sk-...",
  "openai_model": "gpt-4o-mini",
  "openai_base_url": "https://api.openai.com/v1",
  "gemini_api_key": "...",
  "gemini_model": "gemini-1.5-flash",
  "anthropic_api_key": "...",
  "anthropic_model": "claude-3-haiku-20240307",
  "judge_api_key": "sk-...",
  "judge_model": "gpt-4o-mini",
  "judge_base_url": "https://api.openai.com/v1"
}
```

Umgebungsvariablen überschreiben Werte aus der Datei. Optional kannst du den Pfad zur Datei über `KI_COUNCIL_CONFIG` setzen.

## Nutzung

```bash
python -m ki_council.cli "Dein Prompt hier"
```

Oder als JSON-Ausgabe:

```bash
python -m ki_council.cli "Dein Prompt hier" --json
```

## Hinweise

- Für die Vergleichsanalyse wird standardmäßig OpenAI genutzt. Setze `JUDGE_API_KEY`, wenn du dafür einen separaten Key verwenden möchtest.
- Ohne gesetzte API-Keys läuft das Tool nicht.
