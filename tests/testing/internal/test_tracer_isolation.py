"""
Guard the boundary between ddtrace.testing and the ddtrace tracer.

ddtrace.testing is meant to become a standalone package that only touches the tracer when tracer integration is
enabled (global trace filter, `--ddtrace-patch-all`). These tests run a real pytest session in a subprocess and
record every import performed *by* ddtrace.testing modules (importing `ddtrace` itself already loads the tracer, so
inspecting `sys.modules` could not tell who is responsible).
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import typing as t

import pytest


# Tracer entry points that ddtrace.testing must not import unless tracer integration is enabled.
TRACER_MODULES = (
    "ddtrace",
    "ddtrace._monkey",
    "ddtrace._trace",
    "ddtrace.trace",
    "ddtrace.contrib.internal.logging",
    "ddtrace.contrib.internal.selenium",
)

_RUNNER = textwrap.dedent(
    """
    import builtins
    import json
    import sys

    imported_by_testing = set()
    _real_import = builtins.__import__


    def _recording_import(name, globals=None, locals=None, fromlist=(), level=0):
        importer = (globals or {}).get("__name__", "")
        if level == 0 and importer.startswith("ddtrace.testing") and name.split(".")[0] == "ddtrace":
            imported_by_testing.add(name)
            imported_by_testing.update(f"{name}.{f}" for f in (fromlist or ()))
        return _real_import(name, globals, locals, fromlist, level)


    builtins.__import__ = _recording_import

    from unittest.mock import patch

    import pytest

    from tests.testing.mocks import mock_api_client_settings
    from tests.testing.mocks import network_mocks

    test_dir, *args = sys.argv[1:]
    with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as api_client:
        api_client.return_value = mock_api_client_settings()
        ret = pytest.main(["-p", "no:cacheprovider", "--ddtrace", "--no-cov", "-q", *args, test_dir])

    print("RESULT" + json.dumps({"ret": int(ret), "imports": sorted(imported_by_testing)}))
    """
)


def _run_session(tmp_path: Path, *args: str, **extra_env: str) -> dict[str, t.Any]:
    (tmp_path / "test_trivial.py").write_text("def test_trivial():\n    assert True\n")
    env = {
        **os.environ,
        "DD_API_KEY": "test-key",
        "DD_CIVISIBILITY_AGENTLESS_ENABLED": "false",
        "DD_TRACE_PY_ENABLE_ITR_FOR_JOB": "false",
        **extra_env,
    }
    repo_root = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [repo_root, env.get("PYTHONPATH")]))
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(tmp_path), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT")]
    assert lines, f"runner did not report a result:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    result = json.loads(lines[-1][len("RESULT") :])
    assert result["ret"] == 0, proc.stdout + proc.stderr
    return t.cast("dict[str, t.Any]", result)


def _tracer_imports(imports: list[str]) -> set[str]:
    return {name for name in imports if name in TRACER_MODULES}


def test_tracer_integration_disabled_does_not_import_tracer(tmp_path: Path) -> None:
    result = _run_session(tmp_path, _DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER="1")

    assert _tracer_imports(result["imports"]) == set()


def test_tracer_integration_disabled_does_not_import_ci_visibility(tmp_path: Path) -> None:
    result = _run_session(tmp_path, _DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER="1")

    assert [name for name in result["imports"] if name.startswith("ddtrace.internal.ci_visibility")] == []


# Prints the ddtrace modules that importing a module loads beyond `import ddtrace`. The global tracer is off, so
# `import ddtrace` itself loads no tracer modules.
_ADDED_MODULES = textwrap.dedent(
    """
    import importlib
    import json
    import sys

    import ddtrace

    before = set(sys.modules)
    importlib.import_module(sys.argv[1])
    print("RESULT" + json.dumps(sorted(m for m in set(sys.modules) - before if m.startswith("ddtrace"))))
    """
)


def _added_modules(module: str) -> list[str]:
    env = {**os.environ, "_DD_GLOBAL_TRACER_INIT": "false", "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false"}
    proc = subprocess.run(
        [sys.executable, "-c", _ADDED_MODULES, module], env=env, capture_output=True, text=True, timeout=120
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT")]
    assert lines, proc.stdout + proc.stderr
    return t.cast("list[str]", json.loads(lines[-1][len("RESULT") :]))


def _is_tracer_or_ci_visibility(name: str) -> bool:
    return any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in ("ddtrace.trace", "ddtrace._trace", "ddtrace.internal.ci_visibility", "ddtrace.internal.sampling")
    )


def test_source_info_imports_no_tracer_modules() -> None:
    added = _added_modules("ddtrace.testing.internal.source_info")

    assert "ddtrace.testing.internal.source_info" in added
    assert [name for name in added if _is_tracer_or_ci_visibility(name)] == []


def test_plugin_module_does_not_import_ci_visibility() -> None:
    # The plugin still loads the tracer through ddtrace.contrib.internal.coverage, so only ci_visibility is checked.
    added = _added_modules("ddtrace.testing.internal.pytest.plugin")

    assert "ddtrace.testing.internal.pytest.plugin" in added
    assert [name for name in added if name.startswith("ddtrace.internal.ci_visibility")] == []


def test_tracer_integration_enabled_imports_tracer(tmp_path: Path) -> None:
    """Positive control: makes sure the import recording used above actually sees tracer imports."""
    result = _run_session(tmp_path, _DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER="0")

    assert {"ddtrace", "ddtrace.trace"} <= _tracer_imports(result["imports"])


@pytest.mark.parametrize("patch_all", ["--ddtrace-patch-all"])
def test_patch_all_imports_integrations_lazily(tmp_path: Path, patch_all: str) -> None:
    result = _run_session(tmp_path, patch_all, _DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER="0")

    assert "ddtrace._monkey" in result["imports"]
