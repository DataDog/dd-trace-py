"""Verify that user tags can change between uploads.

The cached exporter does NOT bake user tags any more — they ride on every
``Exporter_send_blocking`` call via ``optional_additional_tags``. This test
runs ``tag_rotation_program.py`` against an in-process HTTP server, captures
the multipart bodies, and asserts each cycle's expected ``phase`` tag is
present in the corresponding upload's body.
"""
import http.server
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest


PHASES = ("setup", "warmup", "production")


class _CapturingHandler(http.server.BaseHTTPRequestHandler):
    captured: list[bytes] = []
    paths: list[str] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        _CapturingHandler.captured.append(body)
        _CapturingHandler.paths.append(self.path)
        # 202 Accepted matches what the trace agent returns for profile intake
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args, **_kwargs) -> None:  # silence stderr noise
        pass


class _Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="profiling extension only supported on linux/macos; uploads gated to linux for CI",
)
def test_user_tags_change_between_uploads() -> None:
    _CapturingHandler.captured = []
    _CapturingHandler.paths = []

    server = _Server(("127.0.0.1", 0), _CapturingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        env = os.environ.copy()
        env["DD_TRACE_AGENT_URL"] = f"http://127.0.0.1:{port}"
        env["DD_PROFILING_ENABLED"] = "1"
        # Disable other intakes that might race the mock
        env.pop("DD_PROFILING_OUTPUT_PPROF", None)

        driver = Path(__file__).parent / "tag_rotation_program.py"
        proc = subprocess.run(
            [sys.executable, str(driver)],
            env=env,
            timeout=60,
            capture_output=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    stdout = proc.stdout.decode(errors="replace")
    stderr = proc.stderr.decode(errors="replace")
    assert proc.returncode == 0, f"driver failed:\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"

    profiling_uploads = [
        body for path, body in zip(_CapturingHandler.paths, _CapturingHandler.captured) if "profiling" in path
    ]
    assert len(profiling_uploads) >= len(PHASES), (
        f"expected at least {len(PHASES)} profiling uploads, got {len(profiling_uploads)}; "
        f"paths captured: {_CapturingHandler.paths}"
    )

    # Tags are encoded somewhere in the multipart body (libdatadog wraps them
    # in a form field). We just look for the verbatim "phase:<value>" string,
    # which is the canonical Datadog tag encoding and robust to multipart
    # framing changes.
    for cycle, expected_phase in enumerate(PHASES):
        body = profiling_uploads[cycle]
        token = f"phase:{expected_phase}".encode()
        assert token in body, (
            f"cycle {cycle}: expected tag '{token.decode()}' not found in upload body "
            f"(len={len(body)} bytes). First 200 bytes: {body[:200]!r}"
        )

    # Cross-check: an OLD phase tag must NOT appear in a LATER upload.
    # This is what would happen if the exporter still baked the user tags.
    for older_cycle, newer_cycle in [(0, 1), (0, 2), (1, 2)]:
        older_tag = f"phase:{PHASES[older_cycle]}".encode()
        newer_body = profiling_uploads[newer_cycle]
        assert older_tag not in newer_body, (
            f"stale tag from cycle {older_cycle} ('{older_tag.decode()}') leaked into upload "
            f"for cycle {newer_cycle} — exporter is baking user tags instead of using per-send additional_tags"
        )
