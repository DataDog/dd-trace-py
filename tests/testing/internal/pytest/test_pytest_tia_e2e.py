"""Exercise local TIA with fresh pytest processes, real Git history, and HTTP artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import typing as t
from urllib.parse import parse_qs
from urllib.parse import unquote_plus
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import pytest

from ddtrace.testing.internal.tia.selection import load_graph
from tests.testing.internal.pytest.test_pytest_xdist import MockCIVisibilityServer
from tests.testing.internal.pytest.test_pytest_xdist import _git_commit
from tests.testing.internal.pytest.test_pytest_xdist import _make_env
from tests.testing.internal.pytest.test_pytest_xdist import _MockCIVisibilityHandler
from tests.testing.internal.pytest.test_pytest_xdist import test_project  # noqa: F401
from tests.testing.internal.pytest.tia_corpus import write_corpus


_ENDPOINT = "/api/unstable/ci/tia/artifacts"
_REPOSITORY = "https://example.test/local-tia-corpus.git"
_IDENTITY = ("repository_id", "suite_id", "env_hash", "language", "compatibility_version")


class _ArtifactState:
    def __init__(self) -> None:
        self.artifacts: dict[tuple[str, ...], tuple[bytes, str]] = {}
        self.uploads: list[dict[str, t.Any]] = []
        self.lookups: list[dict[str, t.Any]] = []


class _ArtifactHandler(_MockCIVisibilityHandler):
    def do_GET(self) -> None:
        server = t.cast(t.Any, self.server)
        prefix = server.evp_prefix
        if self.path == "/info" and prefix:
            self._send_json({"endpoints": [f"{prefix}/"], "config": {"default_env": "test"}})
            return
        super().do_GET()

    def _prepare_evp(self) -> bool:
        server = t.cast(t.Any, self.server)
        prefix = server.evp_prefix
        if prefix is None:
            return True
        if not self.path.startswith(f"{prefix}/"):
            self._send_json({}, 404)
            return False
        # Match the Agent's decoded-query allowlist, which excludes JSON object punctuation.
        allowed = '/_-+@?&=.:"[]'
        if any(
            not (char.isascii() and (char.isalnum() or char in allowed))
            for char in unquote_plus(urlsplit(self.path).query)
        ):
            self._send_json({"error": "invalid EVP query string"}, 400)
            return False
        server.proxy_paths.append(self.path)
        assert self.headers.get("X-Datadog-EVP-Subdomain") in ("api", "citestcycle-intake", "citestcov-intake")
        assert self.headers.get("DD-API-KEY") is None
        self.path = self.path[len(prefix) :]
        self.headers["DD-API-KEY"] = "test-api-key-xdist"
        return True

    def do_PUT(self) -> None:
        if not self._prepare_evp():
            return
        if urlsplit(self.path).path != _ENDPOINT:
            super().do_PUT()
            return
        state: _ArtifactState = t.cast(t.Any, self.server).tia
        query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
        body = self._read_body()
        assert self.headers.get("Content-Type") == "application/octet-stream"
        assert self.headers.get("DD-API-KEY") == "test-api-key-xdist"
        assert self.headers.get("Content-Encoding") is None
        assert self.headers.get("DD-APPLICATION-KEY") is None
        identity = (
            *tuple(str(query[key]) for key in _IDENTITY),
            json.dumps(json.loads(query.get("configurations", "{}")), sort_keys=True),
        )
        key = (*identity, query["git_commit_sha"])
        deduplicated = state.artifacts.get(key, (None, None))[0] == body
        state.artifacts[key] = (body, query["git_commit_sha"])
        state.uploads.append({"query": query, "document": json.loads(body), "bytes": len(body)})
        self._send_json(
            {
                "data": {
                    "type": "ci_app_tia_artifact_upload",
                    "id": query["git_commit_sha"],
                    "attributes": {"blob_id": hashlib.sha256(body).hexdigest(), "deduplicated": deduplicated},
                }
            },
            201,
        )

    def do_POST(self) -> None:
        if not self._prepare_evp():
            return
        if self.path != f"{_ENDPOINT}/lookup":
            super().do_POST()
            return
        state: _ArtifactState = t.cast(t.Any, self.server).tia
        request = json.loads(self._read_body())
        if request["data"]["type"] != "ci_app_tia_artifact_lookup":
            self._send_json({"error": "unexpected resource type"}, 400)
            return
        assert isinstance(request["data"]["id"], str) and request["data"]["id"]
        attributes = request["data"]["attributes"]
        identity = (
            *tuple(str(attributes[key]) for key in _IDENTITY),
            json.dumps(attributes.get("configurations", {}), sort_keys=True),
        )
        lookup = {"request": attributes, "matched_commit": None, "source_commit": None}
        state.lookups.append(lookup)
        for commit in attributes["git_commit_shas"]:
            artifact = state.artifacts.get((*identity, commit))
            if artifact is None:
                continue
            body, source = artifact
            lookup.update(matched_commit=commit, source_commit=source)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-dd-tia-git-commit-sha", commit)
            self.send_header("x-dd-tia-source-commit", source)
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({}, 404)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _make_corpus(root: Path) -> None:
    write_corpus(root)
    _git(root, "remote", "add", "origin", _REPOSITORY)
    _git_commit(root, "Initial synthetic corpus")
    assert not _git(root, "status", "--porcelain")


def _ruby_shape(document: dict[str, t.Any]) -> dict[str, t.Any]:
    graph = load_graph(document["map"], document)
    assert graph is not None
    tests = []
    always = []
    for index, (nodeid, files) in enumerate(sorted(graph.tests.items())):
        suite, scoped_id = nodeid.split("::", 1)
        tests.append(
            {
                "suite": suite,
                "name": scoped_id.split("[", 1)[0],
                "parameters": {"arguments": {}, "metadata": {"scoped_id": scoped_id}},
                "files": sorted(files),
            }
        )
        if nodeid in graph.always:
            always.append(index)
    return {
        "version": 1,
        "scope": {"repository": _REPOSITORY, "framework": "pytest"},
        "base_commit": graph.base_commit,
        "always_run": always,
        "tests": tests,
        "global_files": sorted(graph.import_deps),
    }


@pytest.mark.parametrize("transport", ["agentless", "evp_v2", "evp_v4"])
def test_tia_ddcoverage_end_to_end(test_project: Path, tmp_path: Path, transport: str) -> None:  # noqa: F811
    artifact_root = Path(__file__).resolve().parents[4] / ".cache" / "tia-e2e"
    artifact_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=artifact_root))
    _make_corpus(test_project)
    first_commit = _git(test_project, "rev-parse", "HEAD")
    phases = []
    process_test = "tests/test_process.py::test_child"
    cart_tests = {"tests/test_cart.py::test_add[1]", "tests/test_cart.py::test_add[2]"}
    all_tests = cart_tests | {
        process_test,
        "tests/test_shipping.py::test_fee",
        "tests/test_threads.py::test_stock",
        "tests/test_smoke.py::test_smoke",
        "tests/test_static.py::test_static",
        "tests/test_dynamic.py::test_dynamic",
    }

    with MockCIVisibilityServer() as backend:
        state = _ArtifactState()
        server = t.cast(t.Any, backend.server)
        server.tia = state
        server.evp_prefix = None if transport == "agentless" else f"/evp_proxy/{transport.removeprefix('evp_')}"
        server.proxy_paths = []
        server.RequestHandlerClass = _ArtifactHandler

        def run(label: str, root: Path, selected: set[str], failures: int = 0) -> None:
            env = _make_env(
                backend.url,
                {
                    "DD_TIA_ENABLED": "1",
                    "DD_GIT_REPOSITORY_URL": _REPOSITORY,
                    "_CI_DD_GIT_REPOSITORY_URL": _REPOSITORY,
                    "DD_GIT_COMMIT_SHA": _git(root, "rev-parse", "HEAD"),
                    "DD_CIVISIBILITY_GIT_UPLOAD_ENABLED": "false",
                    "DD_CIVISIBILITY_GIT_UNSHALLOW_ENABLED": "false",
                    "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "false",
                },
            )
            if server.evp_prefix:
                env["DD_CIVISIBILITY_AGENTLESS_ENABLED"] = "false"
                env["DD_TRACE_AGENT_URL"] = backend.url
                env["_CI_DD_AGENT_URL"] = backend.url
                env.pop("DD_CIVISIBILITY_AGENTLESS_URL", None)
                env.pop("DD_API_KEY", None)
            env.pop("PYTEST_ADDOPTS", None)
            env.pop("_CI_DD_API_KEY", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-p",
                    "no:randomly",
                    "-p",
                    "no:benchmark",
                    "-p",
                    "no:cov",
                    "-q",
                    "--junitxml=.artifacts/junit.xml",
                    "tests",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=90,
            )
            (output / f"{label}.stdout").write_text(result.stdout)
            (output / f"{label}.stderr").write_text(result.stderr)
            assert result.returncode == (1 if failures else 0), result.stdout + result.stderr
            report = ET.parse(root / ".artifacts" / "junit.xml")
            executed = {
                f"{case.attrib['classname'].replace('.', '/')}.py::{case.attrib['name']}"
                for case in report.iter("testcase")
            }
            assert executed == selected, result.stdout + result.stderr
            assert len(list(report.iter("failure"))) == failures
            assert not _git(root, "status", "--porcelain")
            phases.append(
                {
                    "phase": label,
                    "commit": env["DD_GIT_COMMIT_SHA"],
                    "selected": sorted(executed),
                    "passed": len(executed) - failures,
                    "failed": failures,
                    "deselected": len(all_tests) - len(executed),
                    "lookups_so_far": len(state.lookups),
                }
            )

        run("initial", test_project, all_tests)
        assert len(state.uploads) == 1, f"Initial {transport} run did not upload an artifact; see {output}"
        baseline = state.uploads[0]["document"]
        graph = load_graph(baseline["map"], baseline)
        assert graph is not None
        assert graph.always == {process_test}
        assert "shop/settings.py" in graph.tests["tests/test_dynamic.py::test_dynamic"]
        assert {"conftest.py", "shop/fixtures.py", "shop/currency.py", "pytest.ini"} <= graph.import_deps
        assert "shop/stock.py" in graph.tests["tests/test_threads.py::test_stock"]
        run("unchanged", test_project, {process_test})
        assert len(state.lookups) == 1

        (test_project / "shop/cart.py").write_text("def items(quantity): return list(range(quantity + 1))\n")
        _git_commit(test_project, "Introduce a cart regression")
        run("cart_regression", test_project, cart_tests | {process_test}, failures=2)
        failed = state.uploads[-1]["document"]
        assert set(failed["map"]["failed"]) == cart_tests
        run("unchanged_failure", test_project, cart_tests | {process_test}, failures=2)

        (test_project / "shop/cart.py").write_text("def items(quantity): return [value for value in range(quantity)]\n")
        _git_commit(test_project, "Fix the cart implementation")
        run("cart_fixed", test_project, cart_tests | {process_test})
        assert not state.uploads[-1]["document"]["map"]["failed"]
        assert len(state.lookups) == 1

        current = _git(test_project, "rev-parse", "HEAD")
        identity = next(key[:-1] for key in state.artifacts if key[-1] == first_commit)
        state.artifacts[(*identity, current)] = state.artifacts[(*identity, first_commit)]
        clone = tmp_path / "fresh-checkout"
        _git(tmp_path, "clone", "--local", str(test_project), str(clone))
        assert not (clone / ".git" / ".testoptimization" / "tia").exists()
        run("remote_restore_source_commit", clone, cart_tests | {process_test})
        assert len(state.lookups) == 2
        assert state.lookups[-1]["matched_commit"] == current
        assert state.lookups[-1]["source_commit"] == first_commit

        settings_tests = {"tests/test_static.py::test_static", "tests/test_dynamic.py::test_dynamic"}
        (test_project / "shop/settings.py").write_text("VALUE = 2\n")
        _git_commit(test_project, "Introduce a cached dynamic import regression")
        run("cached_dynamic_import_regression", test_project, settings_tests | {process_test}, failures=2)
        (test_project / "shop/settings.py").write_text("VALUE = 1\nVERSION = 2\n")
        _git_commit(test_project, "Fix the cached dynamic import regression")
        run("cached_dynamic_import_fixed", test_project, settings_tests | {process_test})

        (test_project / "shop/currency.py").write_text("UNIT = 'EUR'\n")
        _git_commit(test_project, "Introduce a fixture plugin dependency regression")
        run("fixture_plugin_regression", test_project, all_tests, failures=3)
        (test_project / "shop/currency.py").write_text("UNIT = 'USD'\nVERSION = 2\n")
        _git_commit(test_project, "Fix the shared fixture dependency")
        run("fixture_plugin_fixed", test_project, all_tests)
        assert len(state.lookups) == 2
        assert backend.count_requests("/api/v2/citestcov") == 0
        assert all("configurations" not in upload["query"] for upload in state.uploads)
        assert all("configurations" not in lookup["request"] for lookup in state.lookups)
        if server.evp_prefix:
            assert any(urlsplit(path).path == f"{server.evp_prefix}{_ENDPOINT}" for path in server.proxy_paths)
            assert sum(path.endswith(f"{_ENDPOINT}/lookup") for path in server.proxy_paths) == 2

        comparison = _ruby_shape(baseline)
        current_bytes = json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
        ruby_bytes = json.dumps(comparison, sort_keys=True, separators=(",", ":")).encode()
        (output / "map-current.json").write_text(json.dumps(baseline, indent=2))
        (output / "map-ruby-shaped.json").write_text(json.dumps(comparison, indent=2))
        summary = {
            "transport": transport,
            "phases": phases,
            "map_sizes": {
                "current_json": len(current_bytes),
                "ruby_shaped_json": len(ruby_bytes),
                "current_gzip": len(gzip.compress(current_bytes, mtime=0)),
                "ruby_shaped_gzip": len(gzip.compress(ruby_bytes, mtime=0)),
            },
            "unique_files": len(graph.import_deps | set().union(*graph.tests.values())),
            "lookup_requests": state.lookups,
            "upload_count": len(state.uploads),
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
