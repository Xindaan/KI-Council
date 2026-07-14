# PLAN.md — KI-Council: Strategie, Architektur, Entscheidungen

Ausfuehrliche Begruendungen. Aufgabenquelle ist TASK.md, Kurzstatus in
STATE.md — dieses Dokument erklaert das WARUM und das WIE.

## 1. Mission (Pivot vom 2026-07-13)

KI-Council war ein generisches "frag mehrere LLMs, lass einen Judge
vergleichen"-Tool. Das ist als Kategorie tot (siehe 2.). Neuer Fokus:

> **Model-Downgrade-Advisor**: "Wirf dem Tool 20 deiner echten Prompts
> hin — es sagt dir, welches guenstigste Modell (inkl. lokaler via
> Ollama) fuer deine Arbeit reicht, und was du damit sparst."

Das Council-Pattern (paralleler Fan-out + Judge) ist damit vom Produkt
zum Mechanismus degradiert. Zielgruppe: Einzelnutzer/Devs, die pauschal
fuers Topmodell zahlen, ohne zu wissen, wo das billige reicht.

## 2. Wettbewerbslage (recherchiert 2026-07-13, GitHub-API-Zahlen)

| Repo/Tool | Stars | Status | Warum es R1 nicht abdeckt |
|---|---|---|---|
| karpathy/llm-council | 22.623 | verlassen 2025-11 | Council-Antwortqualitaet, kein Eval/Kosten. Klone erreichen 1-14 Stars |
| ianarawjo/ChainForge | 3.011 | aktiv | visuelles Flow-Tool fuer Prompt-Engineering, kein Kosten-Verdikt |
| lmarena/arena-hard-auto | 1.047 | aktiv | fixe Fragesets, fuer Modell-Entwickler |
| irthomasthomas/llm-consortium | 403 | aktiv | Laufzeit-Ensemble (MEHR zahlen pro Query), Gegenteil unserer Oekonomie |
| kolenaIO/autoarena | 108 | tot seit 2024-12 | naechster Nachbar: Head-to-Head + Judge-Jury, aber Enterprise-Framing, kein Kosten-Verdikt, kein History-Import |
| Promptfoo | (OpenAI-Akquise 03/2026, Core MIT) | aktiv | maechtig, aber YAML-Config pro Testfall, Team/CI-Framing |

**Risiko-Basisrate**: AutoArenas 108 Stars zeigen, dass die Kategorie kein
Selbstlaeufer ist. Unsere Differenzierung haengt an genau zwei Dingen:

1. **Geld-Verdikt**: "Wechsle zu X, spare Y%" statt neutralem Ranking.
2. **History-Import**: ChatGPT-/Claude-Export replayen = Einstiegshuerde
   null ("wir haben deine Prompts schon" statt "kuratiere ein Testset").

Ohne Distribution (T-0005: README Englisch, pip, Demo-GIF, Show HN) ist
das Projekt trotzdem chancenlos — dann bewusst archivieren.

Verworfene Richtungen (nicht wieder aufmachen): Cost-Routing (RouteLLM/
Martian/Not Diamond/LiteLLM besetzen das), mehrrundige Debatte-Loops
(Nutzen empirisch duenn, Kosten 3-10x), Council-Polishing (Basisrate
1-14 Stars).

## 3. Architektur

Python >= 3.9, **nur Standardbibliothek** (harte Constraint, siehe 4.9).
Flache Modulstruktur in `ki_council/`:

| Modul | Zeilen ca. | Verantwortung |
|---|---|---|
| clients.py | 470 | HTTP-Clients (urllib): OpenAIClient (auch OpenAI-kompatibel/Ollama via base_url), GeminiClient, AnthropicClient. `generate(prompt, max_tokens) -> LLMResponse` mit Token-Zaehlung |
| config.py | 130 | .ki-council.json (tolerantes JSON: Kommentare, trailing commas, Smart Quotes) + Env-Vars; Env schlaegt Datei |
| council.py | 240 | Alt-Feature: paralleler Fan-out + Freitext-Judge (judge_prompt.txt) |
| cli.py | 160 | Alt-Feature CLI (`python -m ki_council.cli "Prompt"`) |
| web.py | 830 | Alt-Feature Web-UI (`python -m ki_council.web`), Eval-Modus dort NICHT integriert (T-0009) |
| **pricing.py** | 130 | Preistabelle USD/1M Tokens (Snapshot 2026-07), Longest-Prefix-Match, `model_prices`-Override, Localhost => 0 USD |
| **promptsets.py** | 230 | Prompt-Quellen: JSONL, Ordner (.txt/.md), TXT (Zeile=Prompt), ChatGPT-Export (mapping-Nodes), Claude-Export (chat_messages); Dedupe, Min-Laenge 8, Limit |
| **evaluate.py** | 560 | Eval-Kern + CLI: Kandidaten/Judges aus Config, Batch-Runner (ThreadPool), Blind-Pairwise-Judging, Aggregation, Verdikt, Markdown-Report, summary.json |

Fett = neu seit Pivot. Tests in `tests/` (unittest, stdlib, keine
Netz-Calls): test_pricing.py, test_promptsets.py, test_evaluate.py.

### Datenfluss eines Eval-Runs

1. `load_prompts(source, limit)` -> List[PromptItem] (id, text, source).
2. `build_candidates(config)` -> List[Candidate] aus `eval_candidates`,
   Fallback: die drei Council-Provider. `pick_baseline`: explizit
   (`--baseline`/`eval_baseline`) oder teuerster Kandidat (Output-Preis).
3. Phase 1 (parallel, `--workers`, Default 4): jeder Prompt x jeder
   Kandidat -> `responses.jsonl`. Fehler pro Response erfasst.
4. Phase 2: pro Prompt x Nicht-Baseline-Kandidat, pro Judge, **beide
   Orientierungen** (Baseline als A und als B). Verdikt-Parsing aus
   letzter Zeile (A/B/TIE). `combine_orientations`: nur in beiden
   Orientierungen bestaetigter Sieg zaehlt, sonst Tie.
   Jury-Mehrheit (`majority_vote`, strikte Mehrheit sonst Tie)
   -> `judgments.jsonl`.
   Baseline-Fehler => Prompt skipped; Kandidaten-Fehler => Loss.
5. Aggregation: Win/Tie/Loss, win_or_tie_rate, Kosten aus gemessenen
   Tokens x Preis. `pick_recommendation`: guenstigster Kandidat mit
   Rate >= threshold (Default 0.9) UND Kosten < Baseline.
6. Output: `eval_out/<ts>/report.md` (Verdikt + Tabelle + Caveats),
   `summary.json`, plus die beiden JSONL-Logs.

### Config-Schema (Eval-Teil, .ki-council.json)

```json
{
  "eval_candidates": [
    {"name": "big", "provider": "openai", "model": "gpt-4o"},
    {"name": "local", "provider": "openai", "model": "llama3.1:8b",
     "base_url": "http://localhost:11434/v1"}
  ],
  "eval_baseline": "big",
  "eval_judges": [{"provider": "openai", "model": "gpt-4o-mini"}],
  "model_prices": {"mein-modell": [0.5, 1.5]}
}
```

`provider`: openai | gemini | anthropic (openai deckt alle OpenAI-
kompatiblen APIs ab). `price_input`/`price_output` pro Kandidat moeglich.
Judges ohne `eval_judges`: Fallback auf JUDGE_API_KEY/JUDGE_MODEL bzw.
OpenAI-Key (wie Council).

## 4. Design-Entscheidungen (mit Begruendung)

1. **Win-or-Tie-Semantik**: Tie heisst "Downgrade ok" — wir suchen
   Gleichwertigkeit, nicht Ueberlegenheit. Kehrseite: Tie-Inflation
   durch schwache Judges (T-0008 offen).
2. **Positions-Swap**: jedes Paar wird zweimal gejudged (A/B getauscht);
   nur beidseitig bestaetigte Siege zaehlen. Ein rein positions-biased
   Judge produziert dadurch nur Ties, keine falschen Siege
   (Regression-Test: test_position_biased_judge_is_neutralized_by_swap).
3. **PoLL-Jury** (von AutoArena validierte Technik): mehrere kleine
   Judges verschiedener Familien statt ein Frontier-Judge; strikte
   Mehrheit, sonst Tie. Default bleibt 1 Judge (Kosten).
4. **Unparsebare Judge-Antworten degradieren zu Tie** und werden gezaehlt
   (`parse_failures` im Report) — kein Crash, keine stillen Luecken.
5. **Key-Leak-Schutz**: Kandidat mit eigenem `base_url` erbt NIE den
   Provider-Key (sonst ginge der OpenAI-Key an fremde URLs); ohne
   expliziten `api_key` wird Platzhalter "local" gesendet (Ollama
   ignoriert ihn). Regression-Test vorhanden.
6. **Preistabelle statisch mit Override**: stdlib-only heisst kein
   Pricing-API-Call; Tabelle ist Snapshot (2026-07), Longest-Prefix-Match
   (gpt-4o-mini-2024-07-18 -> gpt-4o-mini), `model_prices` ueberschreibt,
   Localhost-Endpoints automatisch 0 USD. Unbekanntes Modell => Kosten
   "unknown" => vom Verdikt ausgeschlossen.
7. **Kostenkontrolle als Produkt-Feature**: `--limit` Default 25,
   `--dry-run` zeigt API-Call-Anzahl vor dem ersten echten Call.
   Judge-Calls skalieren mit Prompts x Kandidaten x Judges x 2.
8. **Nur Single-Turn-Replay**: aus History-Exports wird nur die erste
   User-Nachricht pro Unterhaltung extrahiert — Multi-Turn-Replay waere
   unfair (Kontext haengt von den Antworten ab). Bewusste Grenze, im
   Report als Caveat ausgewiesen.
9. **stdlib-only bleibt**: Alleinstellung in gesperrten Firmenumgebungen
   (CA-Bundle/Proxy-Support existiert), kein Dependency-Management.
   Konsequenz: urllib statt httpx, ThreadPool statt asyncio, unittest
   statt pytest.
10. **Testbarkeit via Dependency Injection**: `run_eval(...,
    generate_fn=..., judge_fn=...)` — Tests injizieren Fakes, es gibt
    KEINE Netz-Calls in Tests. Fake-Judge liest die anonymisierten
    A/B-Sektionen und urteilt nach Sentinel-Qualitaet.

## 5. Bekannte Schwaechen / offene Punkte

- **Tie-Inflation** (T-0008): unzuverlaessiger Judge => viele Ties =>
  Schwelle scheinbar erreicht. Geplant: Warnung ab hoher Tie-Quote +
  Judge-Agreement-Statistik im Report.
- **Identitaets-Leak** (T-0010): Antworten wie "As Claude, ..." verraten
  dem Judge das Modell trotz Anonymisierung. Scrubbing pruefen.
- **Web-UI kennt den Eval-Modus nicht** (T-0009).
- **Preis-Snapshot altert**; Override existiert, aber Default-Tabelle
  braucht gelegentliche Pflege (pricing.py, DEFAULT_PRICES).
- **Kein echter API-Lauf bisher** (T-0012): alles nur mit Fakes + dry-run
  verifiziert. Erster Real-Lauf kostet Cents und braucht Keys.

## 6. Verifikation

```bash
cd <repo-root>/src/KI-Council
python3 -m unittest discover -s tests        # Stand 2026-07-13: 55/55 OK
python3 -m ki_council.evaluate prompts.jsonl --config cfg.json --dry-run
```

Definition of Done pro Aenderung: Tests gruen (Pass-Count nennen),
TASK.md-Bewegung, STATE.md-Update, README bei nutzerbemerkbaren
Aenderungen. Details: ~/src/CLAUDE.md bzw. AGENTS.md.
