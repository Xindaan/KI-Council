import argparse
import html
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from string import Template
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from ki_council.clients import LLMError, LLMResponse, load_clients
from ki_council.council import build_judge_prompt, judge_responses
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
      .markdown > *:first-child {
        margin-top: 0;
      }
      .markdown > *:last-child {
        margin-bottom: 0;
      }
      .markdown h2,
      .markdown h3,
      .markdown h4 {
        margin-bottom: 8px;
      }
      .markdown p {
        margin: 8px 0;
      }
      .markdown ul {
        margin: 8px 0 8px 20px;
        padding: 0;
      }
      .markdown table {
        width: 100%;
        border-collapse: collapse;
        margin: 16px 0;
        font-size: 0.95rem;
      }
      .markdown table th {
        background: #f2f4f8;
        font-weight: 600;
        text-align: left;
        padding: 10px 12px;
        border: 1px solid #e4e6ef;
      }
      .markdown table td {
        padding: 10px 12px;
        border: 1px solid #e4e6ef;
      }
      .markdown table tr:nth-child(even) {
        background: #f9fafb;
      }
      .results-container {
        margin-top: 24px;
      }
      .hidden {
        display: none;
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
        .markdown table th {
          background: #11131a;
          border-color: #2a2f3a;
        }
        .markdown table td {
          border-color: #2a2f3a;
        }
        .markdown table tr:nth-child(even) {
          background: #171a21;
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
            <label for="max_tokens">Max Tokens pro Antwort</label>
            <input id="max_tokens" name="max_tokens" type="number" min="64" max="32768" step="64" value="$max_tokens" />
          </div>
          <div style="margin-top: 16px;">
            <button type="submit">Antworten abrufen</button>
          </div>
        </form>
        <div id="status" class="status" data-state="$status_state">
          <span class="dot" aria-hidden="true"></span>
          <span class="status-text">$status_text</span>
        </div>
      </section>
      <div id="results" class="results-container">
        $content
      </div>
      <footer>
        Stelle sicher, dass API-Keys gesetzt sind (OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY).
      </footer>
    </main>
    <script>
      const form = document.querySelector("form");
      const status = document.getElementById("status");
      const statusText = status.querySelector(".status-text");
      const results = document.getElementById("results");
      const submitButton = form.querySelector("button[type='submit']");

      const updateStatus = (text) => {
        statusText.textContent = text;
      };

      const setLoading = (isLoading) => {
        submitButton.disabled = isLoading;
        submitButton.textContent = isLoading ? "Bitte warten..." : "Antworten abrufen";
      };

      const pollStatus = async (jobId) => {
        const response = await fetch(`/status?id=${jobId}`);
        if (!response.ok) {
          updateStatus("Fehler bei der Anfrage.");
          setLoading(false);
          return;
        }
        const data = await response.json();
        updateStatus(data.message);
        if (data.state === "done") {
          results.innerHTML = data.html || "";
          status.dataset.state = "done";
          updateStatus("Fertig.");
          setLoading(false);
          return;
        }
        if (data.state === "error") {
          results.innerHTML = data.html || "";
          status.dataset.state = "error";
          updateStatus(data.message || "Fehler bei der Anfrage.");
          setLoading(false);
          return;
        }
        window.setTimeout(() => pollStatus(jobId), 1000);
      };

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        results.innerHTML = "";
        status.dataset.state = "loading";
        updateStatus("Anfrage läuft...");
        setLoading(true);

        const payload = {
          prompt: form.querySelector("#prompt").value,
          max_tokens: Number(form.querySelector("#max_tokens").value || 4096),
        };
        const response = await fetch("/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          updateStatus("Fehler bei der Anfrage.");
          setLoading(false);
          return;
        }
        const data = await response.json();
        pollStatus(data.job_id);
      });

      if (status.dataset.state === "idle") {
        updateStatus("Bereit.");
      } else if (status.dataset.state === "done") {
        updateStatus("Fertig.");
      } else if (status.dataset.state === "error") {
        updateStatus("Fehler bei der Anfrage.");
      }
    </script>
  </body>
</html>
""")


@dataclass
class Job:
    job_id: str
    prompt: str
    max_tokens: int
    state: str = "queued"
    message: str = "Warte auf Start..."
    html: str = ""
    completed: int = 0
    total: int = 0
    responses: list[LLMResponse] = field(default_factory=list)


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _update_job(job_id: str, **updates: object) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        for key, value in updates.items():
            setattr(job, key, value)


def _run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return

    try:
        clients = load_clients()
        if not clients:
            raise LLMError(
                "Keine LLM-Clients konfiguriert. "
                "Bitte API-Keys setzen oder eine Konfigurationsdatei verwenden."
            )

        _update_job(
            job_id,
            state="running",
            message="Anfragen werden an LLMs versendet...",
            total=len(clients) + 1,  # +1 for judge
            completed=0,
        )

        responses: list[LLMResponse] = []
        with ThreadPoolExecutor(max_workers=len(clients)) as executor:
            future_map = {executor.submit(client.generate, job.prompt, job.max_tokens): client for client in clients}
            for future in as_completed(future_map):
                client = future_map[future]
                provider = client.__class__.__name__.replace("Client", "").lower()
                model = client.model
                try:
                    response = future.result()
                except Exception as exc:  # noqa: BLE001
                    response = LLMResponse(
                        provider=provider,
                        model=model,
                        content=f"Fehler bei der Anfrage: {exc}",
                    )
                responses.append(response)
                _update_job(
                    job_id,
                    completed=len(responses),
                    message=f"Antwort von {provider} ({model}) erhalten ({len(responses)}/{len(clients)}).",
                )

        responses.sort(key=lambda r: r.provider)

        # Update status before judge analysis
        _update_job(
            job_id,
            completed=len(clients),
            message="Alle Antworten erhalten. Judge-Modell analysiert und vergleicht die Antworten...",
        )

        responses_text, judgment = judge_responses(job.prompt, responses)
        judge_prompt = build_judge_prompt(job.prompt, responses_text)
        content = _render_results(responses, judgment, judge_prompt)
        _update_job(job_id, state="done", message="Fertig.", html=content, completed=len(clients) + 1)
    except Exception as exc:  # noqa: BLE001
        content = _render_error(str(exc))
        _update_job(job_id, state="error", message=f"Fehler: {exc}", html=content)

def _render_error(message: str) -> str:
    return f'<section class="card"><div class="error">{html.escape(message)}</div></section>'


def _render_markdown(text: str) -> str:
    escaped = html.escape(text or "")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    lines = escaped.splitlines()
    html_lines = []
    list_open = False
    table_mode = False
    table_rows = []

    def _is_table_row(line: str) -> bool:
        """Check if line is a table row (starts and ends with |)."""
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3

    def _is_table_separator(line: str) -> bool:
        """Check if line is a table separator (|---|---|)."""
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            return False
        parts = [p.strip() for p in stripped.split("|")[1:-1]]
        return all(re.match(r"^:?-+:?$", p) for p in parts if p)

    def _parse_table_row(line: str) -> list:
        """Parse a table row into cells."""
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    def _flush_table() -> None:
        """Render accumulated table rows."""
        nonlocal table_mode, table_rows
        if not table_rows:
            return

        html_lines.append("<table>")
        # First row is header
        if table_rows:
            html_lines.append("<thead><tr>")
            for cell in table_rows[0]:
                html_lines.append(f"<th>{cell}</th>")
            html_lines.append("</tr></thead>")
        # Rest are body rows
        if len(table_rows) > 1:
            html_lines.append("<tbody>")
            for row in table_rows[1:]:
                html_lines.append("<tr>")
                for cell in row:
                    html_lines.append(f"<td>{cell}</td>")
                html_lines.append("</tr>")
            html_lines.append("</tbody>")
        html_lines.append("</table>")

        table_mode = False
        table_rows = []

    for line in lines:
        stripped = line.strip()

        # Handle empty lines
        if not stripped:
            if table_mode:
                _flush_table()
            if list_open:
                html_lines.append("</ul>")
                list_open = False
            continue

        # Check for table rows
        if _is_table_row(stripped):
            if list_open:
                html_lines.append("</ul>")
                list_open = False
            if _is_table_separator(stripped):
                # Skip separator line, but stay in table mode
                continue
            table_mode = True
            table_rows.append(_parse_table_row(stripped))
            continue

        # If we were in table mode but this line isn't a table, flush the table
        if table_mode:
            _flush_table()

        # Handle headings
        if stripped.startswith("### "):
            if list_open:
                html_lines.append("</ul>")
                list_open = False
            html_lines.append(f"<h4>{stripped[4:]}</h4>")
            continue
        if stripped.startswith("## "):
            if list_open:
                html_lines.append("</ul>")
                list_open = False
            html_lines.append(f"<h3>{stripped[3:]}</h3>")
            continue
        if stripped.startswith("# "):
            if list_open:
                html_lines.append("</ul>")
                list_open = False
            html_lines.append(f"<h2>{stripped[2:]}</h2>")
            continue

        # Handle lists
        if stripped.startswith(("- ", "* ")):
            if not list_open:
                html_lines.append("<ul>")
                list_open = True
            html_lines.append(f"<li>{stripped[2:]}</li>")
            continue

        # Regular paragraph
        if list_open:
            html_lines.append("</ul>")
            list_open = False
        html_lines.append(f"<p>{stripped}</p>")

    # Flush any remaining table or list
    if table_mode:
        _flush_table()
    if list_open:
        html_lines.append("</ul>")

    return "".join(html_lines)


def _render_page(
    prompt: str,
    max_tokens: int,
    content: str,
    status_state: str = "idle",
    status_text: str = "Bereit.",
) -> bytes:
    html_page = PAGE_TEMPLATE.safe_substitute(
        prompt=html.escape(prompt or ""),
        max_tokens=max_tokens,
        content=content,
        status_state=status_state,
        status_text=html.escape(status_text),
    )
    return html_page.encode("utf-8")


def _render_response_blocks(responses: list) -> str:
    blocks = []
    for response in responses:
        provider = html.escape(response.provider)
        model = html.escape(response.model)
        content = _render_markdown(response.content)
        blocks.append(
            "<article class=\"card\">"
            "<div class=\"response-header\">"
            f"<span class=\"badge\">{provider}</span>"
            f"<span class=\"model\">{model}</span>"
            "</div>"
            f"<div class=\"markdown\">{content}</div>"
            "</article>"
        )
    return "".join(blocks)


def _render_results(responses: list, comparison: str, judge_prompt: str) -> str:
    responses_html = _render_response_blocks(responses)
    comparison_html = _render_markdown(comparison)
    prompt_html = _render_markdown(judge_prompt)
    return (
        "<section class=\"card\">"
        "<h2>Antworten</h2>"
        f"<div class=\"responses\">{responses_html}</div>"
        "</section>"
        "<section class=\"card\">"
        "<h2>Bewertungs-Prompt</h2>"
        "<details class=\"prompt-details\">"
        "<summary>Prompt anzeigen</summary>"
        f"<div class=\"markdown\">{prompt_html}</div>"
        "</details>"
        "</section>"
        "<section class=\"card\">"
        "<h2>Vergleich</h2>"
        f"<div class=\"markdown\">{comparison_html}</div>"
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

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/status":
            query = urlparse(self.path).query
            params = parse_qs(query)
            job_id = (params.get("id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send_json({"state": "error", "message": "Unbekannte Anfrage."}, HTTPStatus.NOT_FOUND)
                return
            payload = {
                "state": job.state,
                "message": job.message,
                "completed": job.completed,
                "total": job.total,
                "html": job.html if job.state in {"done", "error"} else "",
            }
            self._send_json(payload)
            return

        page = _render_page("", 4096, "")
        self._send_page(page)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/run":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Ungültige Anfrage."}, HTTPStatus.BAD_REQUEST)
                return
            prompt = str(payload.get("prompt", "")).strip()
            max_tokens = payload.get("max_tokens", 4096)
            try:
                max_tokens = int(max_tokens)
            except (TypeError, ValueError):
                max_tokens = 4096
            if not prompt:
                self._send_json({"error": "Bitte einen Prompt eingeben."}, HTTPStatus.BAD_REQUEST)
                return
            job_id = uuid.uuid4().hex
            job = Job(job_id=job_id, prompt=prompt, max_tokens=max_tokens)
            with JOBS_LOCK:
                JOBS[job_id] = job
            thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
            thread.start()
            self._send_json({"job_id": job_id})
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        data = parse_qs(body)
        prompt = (data.get("prompt") or [""])[0].strip()
        max_tokens_raw = (data.get("max_tokens") or ["4096"])[0]
        try:
            max_tokens = int(max_tokens_raw)
        except ValueError:
            max_tokens = 4096

        if not prompt:
            page = _render_page(
                "",
                max_tokens,
                _render_error("Bitte einen Prompt eingeben."),
                status_state="error",
                status_text="Fehler bei der Anfrage.",
            )
            self._send_page(page, HTTPStatus.BAD_REQUEST)
            return

        try:
            responses = []
            clients = load_clients()
            if not clients:
                raise LLMError(
                    "Keine LLM-Clients konfiguriert. "
                    "Bitte API-Keys setzen oder eine Konfigurationsdatei verwenden."
                )
            with ThreadPoolExecutor(max_workers=len(clients)) as executor:
                future_map = {
                    executor.submit(client.generate, prompt, max_tokens): client for client in clients
                }
                for future in as_completed(future_map):
                    client = future_map[future]
                    provider = client.__class__.__name__.replace("Client", "").lower()
                    model = client.model
                    try:
                        response = future.result()
                    except Exception as exc:  # noqa: BLE001
                        response = LLMResponse(
                            provider=provider,
                            model=model,
                            content=f"Fehler bei der Anfrage: {exc}",
                        )
                    responses.append(response)
            responses.sort(key=lambda r: r.provider)
            responses_text, judgment = judge_responses(prompt, responses)
            judge_prompt = build_judge_prompt(prompt, responses_text)
            content = _render_results(responses, judgment, judge_prompt)
            status_state = "done"
            status_text = "Fertig."
        except Exception as exc:  # noqa: BLE001
            content = _render_error(str(exc))
            status_state = "error"
            status_text = "Fehler bei der Anfrage."

        page = _render_page(
            prompt,
            max_tokens,
            content,
            status_state=status_state,
            status_text=status_text,
        )
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
