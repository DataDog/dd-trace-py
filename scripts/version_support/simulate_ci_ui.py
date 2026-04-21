"""Local web UI for running version_support.simulate_ci.

Run with:
    PYTHONPATH=scripts python -m version_support.simulate_ci_ui
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
import queue
import re
import subprocess
import sys
import textwrap
import threading
import typing as t
import webbrowser

from version_support import collect_venvs
from version_support.gen_child_pipeline import ROOT


PYTHON_CHOICES = ("3.9", "3.10", "3.11", "3.12", "3.13", "3.14", "3.15")


@dataclass(frozen=True)
class SuiteOption:
    name: str
    python_versions: tuple[str, ...]


def _extract_versions(selector: str | list[str] | None) -> set[str]:
    if selector is None:
        return set()

    if isinstance(selector, list):
        return {value for value in selector if value in PYTHON_CHOICES}

    if selector in PYTHON_CHOICES:
        return {selector}

    if selector.endswith("+"):
        start = selector[:-1]
        if start in PYTHON_CHOICES:
            start_index = PYTHON_CHOICES.index(start)
            return set(PYTHON_CHOICES[start_index:])

    if selector.startswith("select_pys("):
        min_match = re.search(r'min_version\s*=\s*"(\d+\.\d+)"', selector)
        max_match = re.search(r'max_version\s*=\s*"(\d+\.\d+)"', selector)
        min_version = min_match.group(1) if min_match else PYTHON_CHOICES[0]
        max_version = max_match.group(1) if max_match else PYTHON_CHOICES[-1]
        if min_version not in PYTHON_CHOICES or max_version not in PYTHON_CHOICES:
            return set()
        min_index = PYTHON_CHOICES.index(min_version)
        max_index = PYTHON_CHOICES.index(max_version)
        if min_index > max_index:
            return set()
        return set(PYTHON_CHOICES[min_index : max_index + 1])

    return set()


def _suite_options() -> list[SuiteOption]:
    riotfile_path = ROOT / "riotfile.py"
    suites = collect_venvs(riotfile_path.read_text(encoding="utf-8"), str(riotfile_path))

    options: list[SuiteOption] = []
    for suite_name in sorted(suites):
        suite = suites[suite_name]
        python_versions: set[str] = set()
        for nested in suite.venvs or []:
            python_versions.update(_extract_versions(nested.pys))
        options.append(
            SuiteOption(
                name=suite_name,
                python_versions=tuple(sorted(python_versions, key=PYTHON_CHOICES.index)),
            )
        )
    return options


def _build_spec_json(
    *, suite_name: str, package_version: str, python_versions: list[str], package_name: str | None = None
) -> str:
    target = {
        "py": ",".join(python_versions),
        "version_to_test": [package_version],
    }
    if package_name:
        target["package"] = package_name

    spec = {
        "integrations": [
            {
                "name": suite_name,
                "targets": [target],
            }
        ]
    }
    return json.dumps(spec)


def _infer_support(exit_code: int, stdout: str) -> tuple[bool, str]:
    if exit_code != 0:
        return False, "Unsupported (simulation command failed)."

    hash_match = re.search(r"hashes \((\d+)\):", stdout)
    if hash_match is None:
        return False, "Unsupported (no riot hashes reported)."

    hash_count = int(hash_match.group(1))
    if hash_count <= 0:
        return False, "Unsupported (suite resolved but no runnable riot hashes)."

    return True, f"Supported ({hash_count} riot hash(es) found)."


def _run_simulation(
    *,
    suite_name: str,
    package_version: str,
    package_name: str | None,
    python_versions: list[str],
    run_tests: bool,
    no_start_services: bool,
    skip_build: bool,
) -> dict[str, t.Any]:
    command, env = _simulation_command(
        suite_name=suite_name,
        package_version=package_version,
        package_name=package_name,
        python_versions=python_versions,
        run_tests=run_tests,
        no_start_services=no_start_services,
        skip_build=skip_build,
    )

    proc = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    supported, support_message = _infer_support(proc.returncode, proc.stdout)
    if run_tests and proc.returncode == 0:
        support_message = f"{support_message} Test execution completed successfully."

    return {
        "supported": supported,
        "exit_code": proc.returncode,
        "message": support_message,
        "command": " ".join(command),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _simulation_command(
    *,
    suite_name: str,
    package_version: str,
    package_name: str | None,
    python_versions: list[str],
    run_tests: bool,
    no_start_services: bool,
    skip_build: bool,
) -> tuple[list[str], dict[str, str]]:
    spec_json = _build_spec_json(
        suite_name=suite_name,
        package_version=package_version,
        package_name=package_name,
        python_versions=python_versions,
    )
    command = [sys.executable, "-u", "-m", "version_support.simulate_ci", "--spec-json", spec_json]
    if run_tests:
        command.append("--run-tests")
        if no_start_services:
            command.append("--no-start-services")
        if skip_build:
            command.append("--skip-build")

    env = os.environ.copy()
    scripts_path = str(ROOT / "scripts")
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{scripts_path}:{pythonpath}" if pythonpath else scripts_path
    return command, env


def _stream_simulation(
    *,
    suite_name: str,
    package_version: str,
    package_name: str | None,
    python_versions: list[str],
    run_tests: bool,
    no_start_services: bool,
    skip_build: bool,
) -> t.Iterator[dict[str, t.Any]]:
    command, env = _simulation_command(
        suite_name=suite_name,
        package_version=package_version,
        package_name=package_name,
        python_versions=python_versions,
        run_tests=run_tests,
        no_start_services=no_start_services,
        skip_build=skip_build,
    )
    yield {"type": "start", "command": " ".join(command)}

    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    if proc.stdout is None or proc.stderr is None:
        raise RuntimeError("Unable to capture process output.")

    line_queue: queue.Queue[tuple[str, str] | None] = queue.Queue()

    def _pump(stream_name: str, stream: t.TextIO) -> None:
        for line in stream:
            line_queue.put((stream_name, line))
        line_queue.put(None)

    threads = (
        threading.Thread(target=_pump, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=_pump, args=("stderr", proc.stderr), daemon=True),
    )
    for thread in threads:
        thread.start()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    done_streams = 0
    while done_streams < 2:
        item = line_queue.get()
        if item is None:
            done_streams += 1
            continue
        stream_name, line = item
        if stream_name == "stdout":
            stdout_lines.append(line)
        else:
            stderr_lines.append(line)
        yield {"type": "output", "stream": stream_name, "line": line}

    return_code = proc.wait()
    for thread in threads:
        thread.join(timeout=0.1)

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    supported, support_message = _infer_support(return_code, stdout)
    if run_tests and return_code == 0:
        support_message = f"{support_message} Test execution completed successfully."
    yield {
        "type": "result",
        "supported": supported,
        "exit_code": return_code,
        "message": support_message,
        "command": " ".join(command),
        "stdout": stdout,
        "stderr": stderr,
    }


PAGE_TEMPLATE = textwrap.dedent(
    """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>simulate_ci UI</title>
    <style>
      :root {
        color-scheme: light dark;
        --ok: #1c7c44;
        --bad: #b42318;
        --border: #4f4f4f66;
      }
      body {
        font-family: ui-sans-serif, system-ui, sans-serif;
        margin: 0;
        background: canvas;
        color: canvastext;
      }
      .container {
        max-width: 980px;
        margin: 0 auto;
        padding: 24px;
      }
      h1 {
        margin: 0 0 8px 0;
        font-size: 1.5rem;
      }
      .muted {
        opacity: 0.8;
      }
      .card {
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 16px;
        margin-top: 16px;
      }
      .grid {
        display: grid;
        gap: 12px;
        grid-template-columns: 1fr 1fr;
      }
      .full {
        grid-column: 1 / -1;
      }
      label {
        font-weight: 600;
        display: block;
        margin-bottom: 6px;
      }
      select, input[type="text"] {
        width: 100%;
        box-sizing: border-box;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 9px 10px;
        background: transparent;
        color: inherit;
      }
      .py-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 16px;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 10px;
      }
      .py-pill {
        white-space: nowrap;
      }
      .checks {
        display: flex;
        gap: 16px;
        flex-wrap: wrap;
      }
      button {
        border: none;
        border-radius: 8px;
        padding: 10px 14px;
        font-weight: 600;
        cursor: pointer;
      }
      .primary {
        background: #2563eb;
        color: white;
      }
      button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      .status {
        font-weight: 700;
      }
      .status.ok {
        color: var(--ok);
      }
      .status.bad {
        color: var(--bad);
      }
      pre {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 12px;
        overflow: auto;
        max-height: 350px;
        background: #00000012;
      }
      @media (max-width: 760px) {
        .grid {
          grid-template-columns: 1fr;
        }
      }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>Version Support: simulate_ci</h1>
      <div class="muted">Select a suite, package version, and Python versions; then run the simulation.</div>
      <div class="card">
        <div class="grid">
          <div class="full">
            <label for="suite">Test suite</label>
            <select id="suite"></select>
          </div>
          <div>
            <label for="version">Package version selector</label>
            <input id="version" type="text" placeholder="==2.11.4" value="==2.11.4" />
          </div>
          <div>
            <label for="package">Package name (optional)</label>
            <input id="package" type="text" placeholder="mysqlclient" />
          </div>
          <div>
            <label>Execution mode</label>
            <div class="checks">
              <label><input id="run-tests" type="checkbox" checked /> run tests (full workflow)</label>
              <label><input id="no-start-services" type="checkbox" /> do not start services</label>
              <label><input id="skip-build" type="checkbox" /> skip build</label>
            </div>
          </div>
          <div class="full">
            <label>Python versions</label>
            <div id="py-list" class="py-list"></div>
          </div>
        </div>
        <div style="margin-top: 14px; display: flex; gap: 10px; align-items: center;">
          <button id="run" class="primary">Run simulate_ci</button>
          <div id="running" class="muted" hidden>Running...</div>
        </div>
      </div>

      <div class="card" id="result-card" hidden>
        <div id="status-line" class="status"></div>
        <div id="message" style="margin-top: 6px;"></div>
        <div class="muted" style="margin-top: 8px;">Command</div>
        <pre id="command"></pre>
        <div class="muted">stdout</div>
        <pre id="stdout"></pre>
        <div class="muted">stderr</div>
        <pre id="stderr"></pre>
      </div>
    </div>

    <script>
      const suiteOptions = __SUITE_OPTIONS__;
      const allVersions = __PYTHON_CHOICES__;

      const suiteSelect = document.getElementById("suite");
      const pyList = document.getElementById("py-list");
      const runBtn = document.getElementById("run");
      const running = document.getElementById("running");
      const resultCard = document.getElementById("result-card");
      const statusLine = document.getElementById("status-line");
      const commandBlock = document.getElementById("command");
      const stdoutBlock = document.getElementById("stdout");
      const stderrBlock = document.getElementById("stderr");

      function bySuite(name) {
        return suiteOptions.find((suite) => suite.name === name);
      }

      function selectedPyVersions() {
        return [...pyList.querySelectorAll("input[type=checkbox]:checked")].map((element) => element.value);
      }

      function renderPythonChoices(suiteName) {
        const suite = bySuite(suiteName);
        const preferred = (suite && suite.python_versions.length) ? suite.python_versions : allVersions;
        pyList.innerHTML = "";
        for (const version of allVersions) {
          const wrapper = document.createElement("label");
          wrapper.className = "py-pill";
          const checked = preferred.includes(version) ? "checked" : "";
          wrapper.innerHTML = `<input type="checkbox" value="${version}" ${checked}/> ${version}`;
          pyList.appendChild(wrapper);
        }
      }

      function initSuites() {
        for (const suite of suiteOptions) {
          const option = document.createElement("option");
          option.value = suite.name;
          option.textContent = suite.name;
          suiteSelect.appendChild(option);
        }
        renderPythonChoices(suiteSelect.value);
      }

      suiteSelect.addEventListener("change", () => renderPythonChoices(suiteSelect.value));

      runBtn.addEventListener("click", async () => {
        const pythonVersions = selectedPyVersions();
        if (!pythonVersions.length) {
          alert("Choose at least one Python version.");
          return;
        }
        const packageVersion = document.getElementById("version").value.trim();
        if (!packageVersion) {
          alert("Enter a package version selector.");
          return;
        }

        const payload = {
          suite_name: suiteSelect.value,
          package_version: packageVersion,
          package_name: document.getElementById("package").value.trim(),
          python_versions: pythonVersions,
          run_tests: document.getElementById("run-tests").checked,
          no_start_services: document.getElementById("no-start-services").checked,
          skip_build: document.getElementById("skip-build").checked,
        };

        runBtn.disabled = true;
        running.hidden = false;
        resultCard.hidden = false;
        statusLine.textContent = "RUNNING";
        statusLine.className = "status";
        document.getElementById("message").textContent = "Starting simulate_ci...";
        commandBlock.textContent = "";
        stdoutBlock.textContent = "";
        stderrBlock.textContent = "";
        try {
          const response = await fetch("/api/run-stream", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload),
          });
          if (!response.ok || !response.body) {
            const errorText = await response.text();
            throw new Error(errorText || `HTTP ${response.status}`);
          }

          const decoder = new TextDecoder();
          const reader = response.body.getReader();
          let buffered = "";
          let finalResult = null;
          while (true) {
            const {value, done} = await reader.read();
            if (done) {
              break;
            }
            buffered += decoder.decode(value, {stream: true});
            const lines = buffered.split("\\n");
            buffered = lines.pop() || "";
            for (const line of lines) {
              if (!line.trim()) {
                continue;
              }
              const event = JSON.parse(line);
              if (event.type === "start") {
                commandBlock.textContent = event.command;
              } else if (event.type === "output") {
                const targetId = event.stream === "stderr" ? "stderr" : "stdout";
                const target = document.getElementById(targetId);
                target.textContent += event.line;
                target.scrollTop = target.scrollHeight;
              } else if (event.type === "result") {
                finalResult = event;
              } else if (event.type === "error") {
                throw new Error(event.message);
              }
            }
          }
          if (!finalResult) {
            throw new Error("Simulation stream finished without result.");
          }
          const data = finalResult;
          statusLine.textContent = data.supported ? "SUPPORTED" : "NOT SUPPORTED";
          statusLine.className = `status ${data.supported ? "ok" : "bad"}`;
          document.getElementById("message").textContent = data.message;
          commandBlock.textContent = data.command;
          stdoutBlock.textContent = data.stdout || "(no stdout)";
          stderrBlock.textContent = data.stderr || "(no stderr)";
        } catch (error) {
          alert("Failed to run simulation: " + error);
        } finally {
          runBtn.disabled = false;
          running.hidden = true;
        }
      });

      initSuites();
    </script>
  </body>
</html>
"""
)


def _html_page(options: list[SuiteOption]) -> str:
    payload = [{"name": option.name, "python_versions": list(option.python_versions)} for option in options]
    return PAGE_TEMPLATE.replace("__SUITE_OPTIONS__", json.dumps(payload)).replace(
        "__PYTHON_CHOICES__", json.dumps(PYTHON_CHOICES)
    )


class SimulateCiUiHandler(BaseHTTPRequestHandler):
    suite_options: list[SuiteOption] = []

    def _send_json(self, payload: dict[str, t.Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_stream_event(self, payload: dict[str, t.Any]) -> None:
        body = (json.dumps(payload) + "\n").encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def log_message(self, format: str, *args: t.Any) -> None:  # noqa: A002
        del format, args

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND.value, "Not Found")
            return
        self._send_html(_html_page(self.suite_options))

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/run", "/api/run-stream"}:
            self.send_error(HTTPStatus.NOT_FOUND.value, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"Invalid JSON payload: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        suite_name = str(payload.get("suite_name") or "")
        package_version = str(payload.get("package_version") or "")
        package_name = str(payload.get("package_name") or "").strip() or None
        python_versions = payload.get("python_versions")
        run_tests = bool(payload.get("run_tests"))
        no_start_services = bool(payload.get("no_start_services"))
        skip_build = bool(payload.get("skip_build"))

        suite_names = {option.name for option in self.suite_options}
        if suite_name not in suite_names:
            self._send_json({"error": "Unknown suite selected."}, status=HTTPStatus.BAD_REQUEST)
            return
        if not package_version:
            self._send_json({"error": "Package version selector is required."}, status=HTTPStatus.BAD_REQUEST)
            return
        if (
            not isinstance(python_versions, list)
            or not python_versions
            or any(not isinstance(v, str) for v in python_versions)
        ):
            self._send_json({"error": "At least one Python version must be selected."}, status=HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/api/run":
            try:
                result = _run_simulation(
                    suite_name=suite_name,
                    package_version=package_version,
                    package_name=package_name,
                    python_versions=python_versions,
                    run_tests=run_tests,
                    no_start_services=no_start_services,
                    skip_build=skip_build,
                )
            except Exception as exc:  # pragma: no cover - defensive HTTP guard
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json(result)
            return

        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for event in _stream_simulation(
                suite_name=suite_name,
                package_version=package_version,
                package_name=package_name,
                python_versions=python_versions,
                run_tests=run_tests,
                no_start_services=no_start_services,
                skip_build=skip_build,
            ):
                self._send_stream_event(event)
        except Exception as exc:  # pragma: no cover - defensive HTTP guard
            self._send_stream_event({"type": "error", "message": str(exc)})


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8788, help="Port to bind (default: 8788).")
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not auto-open the UI in a browser tab.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    suite_options = _suite_options()
    if not suite_options:
        print("error: no suites found in riotfile.py", file=sys.stderr)
        return 1

    handler_class = type("BoundSimulateCiUiHandler", (SimulateCiUiHandler,), {})
    handler_class.suite_options = suite_options

    server = ThreadingHTTPServer((args.host, args.port), handler_class)
    url = f"http://{args.host}:{args.port}"
    print(f"simulate_ci UI available at {url}")
    if not args.no_open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping simulate_ci UI")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
