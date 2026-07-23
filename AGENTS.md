# AGENTS.md — Arbeitsanleitung fuer KI-Coding-Agenten (Codex, Claude, ...)

## Was dieses Projekt ist

**Model-Downgrade-Advisor**: schickt echte Nutzer-Prompts (Dateien oder
ChatGPT-/Claude-History-Export) an mehrere Kandidaten-Modelle, vergleicht
blind gegen eine Baseline (Positions-Swap, optional Judge-Jury) und
empfiehlt das guenstigste Modell, das gut genug ist — mit Ersparnis in USD.
Pivot-Entscheidung und Wettbewerbsanalyse: siehe PLAN.md Abschnitt 1-2.

## Pflichtlektuere vor der Arbeit

**PLAN.md** — Strategie, Architektur, Design-Entscheidungen mit
Begruendung, bekannte Schwaechen. Entscheidungen dort nicht ohne Not
umstossen; Verworfenes (PLAN.md 2.) nicht wieder aufmachen.

Die `T-####`-Verweise in PLAN.md zeigen auf STATE.md/TASK.md — die
Steuerdateien des Maintainers. Sie sind bewusst **nicht** Teil dieses
Repos; ein Task-Verweis ohne auffindbaren Task ist also kein Fehler,
sondern eine Notiz aus dem privaten Backlog.

## Harte Regeln

- **Nur Python-Standardbibliothek.** Keine neuen Dependencies — das ist
  Produkt-Feature (laeuft in gesperrten Firmenumgebungen), nicht Nostalgie.
  urllib statt requests/httpx, unittest statt pytest, ThreadPool statt
  asyncio.
- **Tests machen NIE Netz-Calls.** Eval-Logik wird ueber die
  Dependency-Injection-Punkte getestet (`run_eval(generate_fn=...,
  judge_fn=...)`). Fakes siehe tests/test_evaluate.py.
- **Keine echten API-Calls ohne explizites Nutzer-OK** — echte Laeufe
  kosten Geld. Fuer Verifikation ohne Kosten: `--dry-run`.
- **Secrets**: .ki-council.json ist gitignored und bleibt es. Nie Keys
  in Code, Tests oder Fixtures.
- Kandidaten mit eigenem `base_url` duerfen NIE automatisch den
  Provider-API-Key erben (Key-Leak an fremde URLs; Regression-Test in
  tests/test_evaluate.py, Begruendung PLAN.md 4.5).
- **Ein falscher Preis ist schlimmer als ein fehlender.** Das gesamte
  Produkt ist das Geld-Verdikt; ein still danebenliegender Preis empfiehlt
  das falsche Modell, ohne dass es jemand merkt. Ein unbekanntes Modell
  bleibt daher preislos ("unknown") und wird vom Verdikt ausgeschlossen —
  es erbt NIE den Preis eines aehnlich heissenden Nachbarn. Preise nie aus
  dem eigenen Modellwissen ergaenzen (Trainingsstand veraltet still):
  Live-Quelle oder Hersteller-Seite lesen, `TABLE_VERIFIED` nur nach
  echtem Nachlesen hochsetzen. Begruendung PLAN.md 4.6.
- **Sprache**: Code, Kommentare, Docstrings und README Englisch — das
  ist die nach aussen sichtbare Flaeche. PLAN.md/AGENTS.md bleiben
  Deutsch in ASCII (keine Umlaute).

## Verifikation & Definition of Done

```bash
cd <repo-root>
python3 -m unittest discover -s tests    # muss gruen sein; Stand 2026-07-14: 71/71
python3 -m ki_council.evaluate <prompts> --config <cfg> --dry-run   # CLI-Smoke ohne Kosten
# Der Dry-Run holt Live-Preise (Netz, kostenlos) und zeigt Preis + Quelle je
# Kandidat. Ohne Netz: --no-live-prices (faellt auf die Tabelle zurueck).
```

Vor jedem Commit:
1. Tests gruen, Pass-Count nennen.
2. README bei nutzerbemerkbaren Aenderungen (CLI/Config/Verhalten).

## Code-Landkarte

Neue Eval-Funktionalitaet: `ki_council/evaluate.py` (Kern + CLI),
`ki_council/promptsets.py` (Prompt-Quellen inkl. History-Import),
`ki_council/pricing.py` (Preistabelle). Alt-Features (Council-Fanout,
Web-UI): `council.py`, `cli.py`, `web.py` — funktionieren unabhaengig
vom Eval-Modus. Details und Datenfluss: PLAN.md Abschnitt 3.
