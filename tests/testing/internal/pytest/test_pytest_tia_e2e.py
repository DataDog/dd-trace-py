"""Exercise local TIA with fresh pytest processes, real Git history, and HTTP artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import typing as t
from urllib.parse import parse_qs
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from ddtrace.testing.internal.tia.selection import load_graph
from tests.testing.internal.pytest.test_pytest_xdist import MockCIVisibilityServer
from tests.testing.internal.pytest.test_pytest_xdist import _git_commit
from tests.testing.internal.pytest.test_pytest_xdist import _make_env
from tests.testing.internal.pytest.test_pytest_xdist import _MockCIVisibilityHandler
from tests.testing.internal.pytest.test_pytest_xdist import test_project  # noqa: F401


_ENDPOINT = "/api/unstable/ci/tia/artifacts"
_REPOSITORY = "https://example.test/local-tia-corpus.git"
_IDENTITY = ("repository_id", "suite_id", "env_hash", "language", "compatibility_version")


class _ArtifactState:
    def __init__(self) -> None:
        self.artifacts: dict[tuple[str, ...], tuple[bytes, str]] = {}
        self.uploads: list[dict[str, t.Any]] = []
        self.lookups: list[dict[str, t.Any]] = []


class _ArtifactHandler(_MockCIVisibilityHandler):
    def do_PUT(self) -> None:
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
        identity = tuple(str(query[key]) for key in _IDENTITY)
        key = (*identity, query["git_commit_sha"])
        deduplicated = state.artifacts.get(key, (None, None))[0] == body
        state.artifacts[key] = (body, query["git_commit_sha"])
        state.uploads.append({"query": query, "document": json.loads(body), "bytes": len(body)})
        self._send_json({"blob_id": hashlib.sha256(body).hexdigest(), "deduplicated": deduplicated}, 201)

    def do_POST(self) -> None:
        if self.path != f"{_ENDPOINT}/lookup":
            super().do_POST()
            return
        state: _ArtifactState = t.cast(t.Any, self.server).tia
        request = json.loads(self._read_body())
        assert request["data"]["type"] == "test_params"
        attributes = request["data"]["attributes"]
        identity = tuple(str(attributes[key]) for key in _IDENTITY)
        lookup = {"request": attributes, "matched_commit": None, "source_commit": None}
        state.lookups.append(lookup)
        for commit in attributes["git_commit_shas"]:
            artifact = state.artifacts.get((*identity, commit))
            if artifact is None:
                continue
            body, source = artifact
            lookup.update(matched_commit=commit, source_commit=source)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
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
    sources = {
        ".gitignore": "__pycache__/\n.pytest_cache/\n.artifacts/\n.coverage*\n",
        "pytest.ini": "[pytest]\ntestpaths = tests\n",
        "shop/__init__.py": "NAME = 'shop'\n",
        "shop/cart.py": "def items(quantity): return list(range(quantity))\n",
        "shop/shipping.py": "def fee(): return 5\n",
        "shop/stock.py": "def available(): return True\n",
        "shop/currency.py": "UNIT = 'USD'\n",
        "shop/child.py": "def value(): return 42\n",
        "conftest.py": """
            import pytest
            @pytest.fixture(scope="session")
            def currency():
                from shop.currency import UNIT
                return UNIT
        """,
        "tests/test_cart.py": """
            import pytest
            from shop.cart import items
            @pytest.mark.parametrize("quantity", [1, 2])
            def test_add(quantity, currency):
                assert currency == "USD"
                assert len(items(quantity)) == quantity
        """,
        "tests/test_shipping.py": """
            from shop.shipping import fee
            def test_fee(currency):
                assert currency == "USD"
                assert fee() == 5
        """,
        "tests/test_threads.py": """
            import threading
            from shop.stock import available
            def test_stock():
                results = []
                worker = threading.Thread(target=lambda: results.append(available()))
                worker.start()
                worker.join()
                assert results == [True]
        """,
        "tests/test_process.py": """
            import subprocess
            import sys
            def test_child():
                subprocess.run([sys.executable, "-c", "from shop.child import value; assert value() == 42"], check=True)
        """,
        "tests/test_smoke.py": "def test_smoke(): assert True\n",
    }
    for name, content in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip())
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


def test_tia_ddcoverage_end_to_end(test_project: Path, tmp_path: Path) -> None:  # noqa: F811
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
    }

    with MockCIVisibilityServer() as backend:
        state = _ArtifactState()
        server = t.cast(t.Any, backend.server)
        server.tia = state
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
        baseline = state.uploads[0]["document"]
        graph = load_graph(baseline["map"], baseline)
        assert graph is not None
        assert graph.always == {process_test}
        assert {"conftest.py", "shop/currency.py", "pytest.ini"} <= graph.import_deps
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

        (test_project / "shop/currency.py").write_text("UNIT = 'USD'\nVERSION = 2\n")
        _git_commit(test_project, "Change a shared fixture dependency")
        run("global_dependency", test_project, all_tests)
        assert len(state.lookups) == 2
        assert backend.count_requests("/api/v2/citestcov") == 0

        comparison = _ruby_shape(baseline)
        current_bytes = json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
        ruby_bytes = json.dumps(comparison, sort_keys=True, separators=(",", ":")).encode()
        (output / "map-current.json").write_text(json.dumps(baseline, indent=2))
        (output / "map-ruby-shaped.json").write_text(json.dumps(comparison, indent=2))
        summary = {
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
