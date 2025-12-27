import argparse
import html
import json
import os
from string import Template
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from ki_council.clients import load_clients
from ki_council.council import build_judge_prompt, gather_responses, judge_responses
from ki_council.config import CONFIG_ENV_VAR


PAGE_TEMPLATE = Template("""<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>KI-Council</title>
    <style>
      :root {
        color-scheme: light dark;
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
        line-height: 1.5;
      }
      body {
        margin: 0;
        background: #f6f7fb;
        color: #202124;
      }
      main {
        max-width: 960px;
        margin: 0 auto;
        padding: 32px 24px 56px;
      }
      header {
        margin-bottom: 24px;
      }
      h1 {
        font-size: 2rem;
        margin-bottom: 8px;
      }
      h2 {
        margin-top: 0;
      }
      h3 {
        margin-top: 0;
      }
      .card {
        background: #fff;
        border-radius: 16px;
        box-shadow: 0 10px 30px rgba(20, 20, 20, 0.08);
        padding: 24px;
        margin-bottom: 24px;
      }
      .status {
        margin-top: 16px;
        padding: 12px 16px;
        border-radius: 12px;
        background: #eef3ff;
        color: #1a3b8f;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .status .dot {
        width: 10px;
        height: 10px;
        border-radius: 999px;
        background: #2f6fed;
        animation: pulse 1.2s infinite ease-in-out;
      }
      @keyframes pulse {
        0% { transform: scale(0.9); opacity: 0.6; }
        50% { transform: scale(1.15); opacity: 1; }
        100% { transform: scale(0.9); opacity: 0.6; }
      }
      .card article.card {
        margin-bottom: 16px;
        box-shadow: none;
        border: 1px solid #e4e6ef;
      }
      .card article.card:last-child {
        margin-bottom: 0;
      }
      label {
        font-weight: 600;
      }
      textarea {
        width: 100%;
        min-height: 140px;
        border-radius: 12px;
        border: 1px solid #d7d9e0;
        padding: 12px;
        font-size: 1rem;
        margin-top: 8px;
        margin-bottom: 16px;
      }
      input[type="number"] {
        width: 120px;
        padding: 8px;
        border-radius: 8px;
        border: 1px solid #d7d9e0;
        margin-left: 8px;
      }
      button {
        background: #2f6fed;
        color: #fff;
        border: none;
        border-radius: 999px;
        padding: 12px 20px;
        font-weight: 600;
        font-size: 1rem;
        cursor: pointer;
      }
      button:hover {
        background: #2456c9;
      }
      pre {
        white-space: pre-wrap;
        background: #f2f4f8;
        padding: 16px;
        border-radius: 12px;
        border: 1px solid #e4e6ef;
      }
      .responses {
        display: grid;
        gap: 16px;
      }
      .response-header {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 12px;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 999px;
        background: #f1f5ff;
        color: #1a3b8f;
        font-weight: 600;
        font-size: 0.85rem;
      }
      .model {
        font-size: 0.9rem;
        color: #5f6368;
      }
      details.prompt-details {
        border-radius: 12px;
        background: #f8f9fd;
        padding: 12px 16px;
      }
      details.prompt-details[open] {
        background: #eef1f8;
      }
      summary {
        cursor: pointer;
        font-weight: 600;
        margin-bottom: 12px;
      }
      .error {
        background: #ffecee;
        border: 1px solid #ffd0d6;
        color: #9b1c31;
        padding: 12px;
        border-radius: 12px;
      }
      footer {
        font-size: 0.85rem;
        color: #5f6368;
        margin-top: 32px;
      }
      @media (prefers-color-scheme: dark) {
        body {
          background: #0f1115;
          color: #f6f7fb;
        }
        .card {
          background: #171a21;
          box-shadow: none;
        }
        .status {
          background: #1b243b;
          color: #b9c7ff;
        }
        .status .dot {
          background: #7aa2ff;
        }
        .card article.card {
          border-color: #2a2f3a;
        }
        textarea,
        input[type="number"],
        pre {
          background: #11131a;
          border-color: #2a2f3a;
          color: #f6f7fb;
        }
        .badge {
          background: #1b243b;
          color: #b9c7ff;
        }
        details.prompt-details,
        details.prompt-details[open] {
          background: #11131a;
        }
        .error {
          background: #2b1216;
          border-color: #5a2029;
          color: #ffb4c2;
        }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>KI-Council</h1>
        <p>Prompt an mehrere LLMs schicken und die Antworten vergleichen.</p>
      </header>
      <section class="card">
        <form method="post">
          <label for="prompt">Prompt</label>
          <textarea id="prompt" name="prompt" required>$prompt</textarea>
          <div>
            <label for="max_tokens">Max tokens pro Antwort</label>
            <input id="max_tokens" name="max_tokens" type="number" min="64" max="4096" step="32" value="$max_tokens" />
          </div>
          <div style="margin-top: 16px;">
            <button type="submit">Antworten abrufen</button>
          </div>
        </form>
        <div id="status" class="status" data-state="idle">
          <span class="dot" aria-hidden="true"></span>
          <span class="status-text">Bereit.</span>
        </div>
      </section>
      $content
      <footer>
        Stelle sicher, dass API-Keys gesetzt sind (OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY).
      </footer>
    </main>
    <script>
      const form = document.querySelector("form");
      const status = document.getElementById("status");
      const statusText = status.querySelector(".status-text");
      const models = $models_json;
      let modelIndex = 0;
      let intervalId = null;

      const updateStatus = (text) => {
        statusText.textContent = text;
      };

      const startSpinner = () => {
        if (!models.length) {
          updateStatus("Modelle werden befragt...");
          return;
        }
        updateStatus(`Befrage: ${models[modelIndex]}`);
        intervalId = window.setInterval(() => {
          modelIndex = (modelIndex + 1) % models.length;
          updateStatus(`Befrage: ${models[modelIndex]}`);
        }, 1500);
      };

      form.addEventListener("submit", () => {
        status.dataset.state = "loading";
        modelIndex = 0;
        startSpinner();
      });

      if (status.dataset.state === "idle") {
        updateStatus("Bereit.");
      }
    </script>
  </body>
</html>
""")


def _model_labels() -> list[str]:
    labels = []
    for client in load_clients():
        provider = client.__class__.__name__.replace("Client", "").lower()
        labels.append(f"{provider} · {client.model}")
    return labels


def _render_page(prompt: str, max_tokens: int, content: str) -> bytes:
    html_page = PAGE_TEMPLATE.safe_substitute(
        prompt=html.escape(prompt or ""),
        max_tokens=max_tokens,
        content=content,
        models_json=json.dumps(_model_labels(), ensure_ascii=False),
    )
    return html_page.encode("utf-8")


def _render_error(message: str) -> str:
    return f'<section class="card"><div class="error">{html.escape(message)}</div></section>'


def _render_response_blocks(responses: list) -> str:
    blocks = []
    for response in responses:
        provider = html.escape(response.provider)
        model = html.escape(response.model)
        content = html.escape(response.content)
        blocks.append(
            "<article class=\"card\">"
            "<div class=\"response-header\">"
            f"<span class=\"badge\">{provider}</span>"
            f"<span class=\"model\">{model}</span>"
            "</div>"
            f"<pre>{content}</pre>"
            "</article>"
        )
    return "".join(blocks)


def _render_results(responses: list, comparison: str, judge_prompt: str) -> str:
    responses_html = _render_response_blocks(responses)
    return (
        "<section class=\"card\">"
        "<h2>Antworten</h2>"
        f"<div class=\"responses\">{responses_html}</div>"
        "</section>"
        "<section class=\"card\">"
        "<h2>Bewertungs-Prompt</h2>"
        "<details class=\"prompt-details\">"
        "<summary>Prompt anzeigen</summary>"
        f"<pre>{html.escape(judge_prompt)}</pre>"
        "</details>"
        "</section>"
        "<section class=\"card\">"
        "<h2>Vergleich</h2>"
        f"<pre>{html.escape(comparison)}</pre>"
        "</section>"
    )


class CouncilHandler(BaseHTTPRequestHandler):
    server_version = "KI-CouncilWeb/1.0"

    def _send_page(self, page: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_GET(self) -> None:
        page = _render_page("", 2048, "")
        self._send_page(page)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        data = parse_qs(body)
        prompt = (data.get("prompt") or [""])[0].strip()
        max_tokens_raw = (data.get("max_tokens") or ["2048"])[0]
        try:
            max_tokens = int(max_tokens_raw)
        except ValueError:
            max_tokens = 2048

        if not prompt:
            page = _render_page("", max_tokens, _render_error("Bitte einen Prompt eingeben."))
            self._send_page(page, HTTPStatus.BAD_REQUEST)
            return

        try:
            responses = gather_responses(prompt, max_tokens=max_tokens)
            responses_text, judgment = judge_responses(prompt, responses)
            judge_prompt = build_judge_prompt(prompt, responses_text)
            content = _render_results(responses, judgment, judge_prompt)
        except Exception as exc:  # noqa: BLE001
            content = _render_error(str(exc))

        page = _render_page(prompt, max_tokens, content)
        self._send_page(page)

    def log_message(self, format: str, *args: object) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the KI-Council web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--config", help="Path to a .ki-council.json file to use for this run.")
    return parser


def run_server(host: str, port: int) -> None:
    server = HTTPServer((host, port), CouncilHandler)
    print(f"KI-Council Web UI running on http://{host}:{port}")
    server.serve_forever()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.config:
        os.environ[CONFIG_ENV_VAR] = args.config

    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
