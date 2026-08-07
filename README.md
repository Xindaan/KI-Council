# KI-Council

A tool with a CLI and web UI that sends the same prompt to multiple LLMs and then has another LLM compare the responses.

## Setup

Set the API keys as environment variables:

```bash
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=...
```

Optionally you can configure models, base URLs, and timeout:

```bash
export OPENAI_MODEL=gpt-4o-mini
export GEMINI_MODEL=gemini-1.5-flash
export ANTHROPIC_MODEL=claude-3-haiku-20240307
export JUDGE_MODEL=gpt-4o-mini
export OPENAI_MAX_TOKENS_PARAM=max_tokens
export KI_COUNCIL_TIMEOUT=300  # Timeout in seconds (default: 300s / 5 min)
export KI_COUNCIL_JUDGE_TIMEOUT=600  # Judge timeout (default: 600s / 10 min)
```

### Alternative: configuration file

Instead of environment variables you can keep keys and models in a JSON file. It is looked up in this order, first hit wins:

1. The path in `KI_COUNCIL_CONFIG`, or the CLI flag `--config`
2. `.ki-council.json` in the current working directory or any parent directory
3. `config.json` in the per-user config directory — `$XDG_CONFIG_HOME/ki-council/` if that variable is set, otherwise `~/.config/ki-council/`. The name `.ki-council.json` is accepted there as well

Step 3 is the recommended place for personal keys: the file lives outside the repository, so it cannot be committed by accident. Step 2 stays available for a project-specific override and is already listed in `.gitignore`.

Since the file holds API keys, keep it readable only by yourself:

```bash
mkdir -p ~/.config/ki-council && chmod 700 ~/.config/ki-council
chmod 600 ~/.config/ki-council/config.json
```

Comments on their own line (`//`, `#`, `/* ... */`), trailing commas, and typical smart quotes (e.g. „ ") are tolerated when loading:

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

If you need your own certificate store (e.g. in a corporate environment), set `KI_COUNCIL_CA_BUNDLE` or `ca_bundle` in the file to the path of the CA bundle. For debugging, you can disable TLS checks with `KI_COUNCIL_INSECURE=1` or `"insecure_ssl": "true"` (not recommended).

Note: some OpenAI models (e.g. `gpt-5`/`o1`) expect `max_completion_tokens` instead of `max_tokens`. You can set the parameter via `OPENAI_MAX_TOKENS_PARAM` or `"openai_max_tokens_param"` to `max_completion_tokens`.

Environment variables override values from the file. Optionally you can set the path to the file via `KI_COUNCIL_CONFIG` or the CLI flag `--config`.

## Usage

Install the package once in the project folder (note the trailing period and run it from the repo root):

```bash
cd /pfad/zum/KI-Council
python3 -m pip install -e .
```

```bash
python -m ki_council.web
# Then open http://127.0.0.1:8000 in your browser
```

Options for the web UI:
```bash
# With a custom host/port
python -m ki_council.web --host 0.0.0.0 --port 8080

# With a custom config
python -m ki_council.web --config ~/.config/ki-council/config.json
```

### Web interface

Start a simple web UI for the council:

```bash
python -m ki_council.web --host 127.0.0.1 --port 8000
```

Then open in the browser: `http://127.0.0.1:8000`

Or as JSON output:

```bash
python -m ki_council.cli "Dein Prompt hier"
```

Advanced CLI options:

```bash
# JSON output for programmatic processing
python -m ki_council.cli "Dein Prompt hier" --json

# Nur bestimmte Provider verwenden
python -m ki_council.cli "Dein Prompt hier" --providers openai,anthropic

# Verbose mode for detailed logs
python -m ki_council.cli "Dein Prompt hier" --verbose

# Adjust the maximum number of tokens
python -m ki_council.cli "Dein Prompt hier" --max-tokens 1000

# Benutzerdefinierte Konfigurationsdatei
python -m ki_council.cli "Dein Prompt hier" --config ~/.config/ki-council/config.json

# Debug mode (shows provider configuration)
python -m ki_council.cli "Dein Prompt hier" --debug

# Kombinierte Optionen
python -m ki_council.cli "Dein Prompt hier" --providers openai,gemini --verbose --max-tokens 2000
```

### Downgrade advisor: which (cheapest) model is enough for your prompts?

The eval mode sends a set of **your real prompts** to several candidate models, compares each candidate blindly against a baseline model (anonymized, with position swapping against judge bias, optionally with a jury of multiple judges), and recommends the cheapest model that wins or ties in at least 90% of cases — including a savings estimate.

```bash
# Prompts from a JSONL file (one line = {"prompt": "..."})
python -m ki_council.evaluate prompts.jsonl

# Straight from your ChatGPT or Claude data export (conversations.json):
# nimmt die erste User-Nachricht der letzten 25 Unterhaltungen
python -m ki_council.evaluate ~/Downloads/conversations.json --limit 25

# Look at the plan first (no API calls, no cost)
python -m ki_council.evaluate prompts.jsonl --dry-run

# Adjust baseline and threshold
python -m ki_council.evaluate prompts/ --baseline gross --threshold 0.85
```

Other prompt sources: a folder with `.txt`/`.md` files (one file = one prompt) or a `.txt` file (one line = one prompt).

You configure candidates and jury in your config file (see above; without `eval_candidates` the three council providers are used):

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

Notes:

- **Ollama/local models:** `provider: "openai"` with `base_url: "http://localhost:11434/v1"` — local endpoints are automatically calculated at 0 €. A candidate with its own `base_url` deliberately does **not** automatically receive your provider key (no key leak to foreign URLs); set `api_key` explicitly if needed.
- **Jury:** where possible, use a judge that is not from the same family as your baseline — models tend to favor their own answers.
- **Cost control:** `--limit` (default 25) limits the number of prompts; `--dry-run` shows the number of API calls and the prices of each candidate in advance.
- **Results:** `eval_out/<timestamp>/` with `report.md` (recommendation + table), `summary.json`, `responses.jsonl`, `judgments.jsonl`.
- **Not all prompts are equal.** Before the run, a classifier sorts each prompt into a category (`coding`, `analysis`, `writing`, `factual`, `other`) and a difficulty. The report shows the rate **per category** — otherwise good everyday performance masks a failure at exactly the work you keep the expensive model for. Can be disabled with `--no-classify`, switched with `--segment-by difficulty`. Costs one cheap additional call per prompt.
- **Rule of thumb for sample size:** segmentation eats into the sample. With 25 prompts across four categories, about 6 remain per segment — too few for a statement. For solid categories, use `--limit 100` or more.
- **How robust is the verdict?** The report shows a 95% confidence interval and marks the recommendation as **preliminary** if the sample does not support it (at a threshold of 0.9, even a flawless run needs ~35 prompts). The **Judge health** section shows how much of the verdict rests on ties rather than wins — a judge that cannot tell the answers apart otherwise gives away downgrade recommendations.
- **Customize the judge prompt:** copy `ki_council/pairwise_judge_prompt.txt` to `pairwise_judge_prompt.txt.local` (ignored by Git).

#### What's scarce for you? (`--optimize`)

The question is always "what is the smallest model that's enough for this
work?" — but *small* means something different depending on the situation:

| `--optimize` | When your scarce resource is … | Typical case |
|---|---|---|
| `cost` (default) | **Money** — you pay per token | A feature runs via the API in production |
| `tokens` | **Quota** — you're hitting the rate limit | Coding on a subscription; the large model eats your window |
| `latency` | **Time** — you're waiting too long | Interactive work, short prompts |

The report **always shows all three** columns, regardless of what you
optimize for — a model that saves money but doubles the wait time is a bad
trade, and you should see that. Only the *verdict* follows the currency you
chose: with `--optimize latency`, a model does not win just because it's
cheaper.

#### Where the prices come from

The verdict is only as good as the prices it calculates with — a wrong
price silently recommends the wrong model. That's why every price has a
source, and the report states it. Order:

1. **Your config** (`model_prices` or `price_input`/`price_output` on the
   candidate) — beats everything else.
2. **Local endpoint** — costs nothing, calculated at 0.
3. **Live source** ([models.dev](https://models.dev)) — fetched during the
   run and also knows newly released models. Can be disabled with
   `--no-live-prices`, a different source via `--prices-url`.
4. **Built-in table** — offline fallback only. It was last checked against
   vendor pages on **2026-07-14** and knows nothing about models released
   later; the report warns when a price comes from here.

If a model is not known anywhere, its costs remain **empty** — it
deliberately does not inherit the price of a similarly named neighboring
model. A missing price is honest, a wrong one would be dangerous.

## New features

### Robust error handling
- If a provider fails, responses from other providers are still shown
- Failed providers are marked in the output with an error message

### Token tracking
- Automatic tracking of tokens used per provider
- Display of prompt, completion, and total tokens in the output

### Provider selection
- You can select providers specifically via `--providers`
- Example: `--providers openai,anthropic` uses only these two
- Also possible via environment variable `KI_COUNCIL_PROVIDERS` or config entry `"providers"`

### Logging
- `--verbose` or `-v` enables detailed logs of API calls
- Helpful for debugging and understanding the flow

### Customizable judge prompt
- **Default prompt:** `ki_council/judge_prompt.txt` (managed by Git)
- **Local override:** `ki_council/judge_prompt.txt.local` (ignored by Git, overrides default)
- Available placeholders:
  - `{prompt}` - original prompt from the user
  - `{responses_text}` - formatted responses of all providers
  - `{judge_model}` - name of the judge model (e.g. "gpt-4o-mini")
  - `{other_providers}` - comma-separated list of the other providers (e.g. "anthropic, gemini")
- **Tip:** copy `judge_prompt.txt` to `judge_prompt.txt.local` for your own customizations that are not overwritten by Git

## Notes

- OpenAI is used by default for the comparison analysis. Set `JUDGE_API_KEY` if you want to use a separate key for this.
- The tool will not run without API keys set.
- Token information is only displayed if the provider APIs return it.
