"""Ties the SSI runtime allow-list to the interpreters whose payloads are actually built.

``RUNTIMES_ALLOW_LIST`` in ``lib-injection/sources/sitecustomize.py`` decides which
interpreters single-step instrumentation will inject into. The payload it then reaches for is
a ``site-packages-ddtrace-py<major>.<minor>-<platform>-<arch>`` directory, and those
directories only exist for the interpreters in the ``download_dependency_wheels`` matrix in
``.gitlab/package.yml``. The guard and the packaging matrix live in different files and have
historically moved in separate commits -- for 3.14 the packaging matrix was extended first
and the allow-list bound followed later -- so nothing structural stops the guard from
admitting an interpreter whose payload was never built. On such an interpreter the injector
gets past the allow-list and then aborts on a missing directory, or worse, finds a directory
built for a different ABI.

These tests assert that direction of the invariant: everything the allow-list admits must be
shipped. The reverse is allowed, since building a payload before opening the guard to it is
the normal, safe order of operations.
"""

import ast
import os
import types

import pytest
import yaml


_LIB_INJECTION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../lib-injection"))
_DL_WHEELS_PATH = os.path.join(_LIB_INJECTION_DIR, "dl_wheels.py")
_PACKAGE_YML_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.gitlab/package.yml"))


class _GitLabLoader(yaml.SafeLoader):
    """SafeLoader that tolerates GitLab's ``!reference`` tag."""


_GitLabLoader.add_constructor("!reference", lambda loader, node: None)


@pytest.fixture(scope="module")
def sitecustomize():
    """The shipped injection ``sitecustomize``, executed from source under an alias.

    Plain ``import sitecustomize`` would be ambiguous: the interpreter imports any
    ``sitecustomize`` on the path at startup, so ``sys.modules`` may already hold an unrelated
    one. Executing the source text directly also sidesteps ``__pycache__``, which otherwise
    resolves stale bytecode when an edit happens to leave the file the same length within the
    same mtime second -- exactly what editing this bound looks like.
    """
    path = os.path.join(_LIB_INJECTION_DIR, "sources", "sitecustomize.py")
    with open(path) as f:
        source = f.read()
    module = types.ModuleType("ssi_sitecustomize_under_test")
    module.__file__ = path
    exec(compile(source, path, "exec"), module.__dict__)
    return module


def _admitted_minor_versions(sitecustomize):
    """The ``major.minor`` strings the cpython allow-list entry admits.

    The bound is compared with a strict ``<`` in ``runtime_version_is_supported``, so ``max``
    is exclusive and the last admitted minor is one below it.
    """
    bounds = sitecustomize.RUNTIMES_ALLOW_LIST["cpython"]
    low, high = bounds["min"].version, bounds["max"].version
    assert low[0] == high[0], "allow-list bounds span two major versions; this test cannot enumerate that"
    return [f"{low[0]}.{minor}" for minor in range(low[1], high[1])]


def _dl_wheels_supported_versions():
    """``supported_versions`` from ``dl_wheels.py``, read without executing the module.

    ``dl_wheels.py`` shells out to pip and builds its argument parser at import time, so it is
    parsed rather than imported.
    """
    with open(_DL_WHEELS_PATH) as f:
        tree = ast.parse(f.read(), filename=_DL_WHEELS_PATH)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "supported_versions" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"no module-level `supported_versions` assignment found in {_DL_WHEELS_PATH}")


def _download_dependency_wheels_versions():
    """The ``PYTHON_VERSION`` values of the ``download_dependency_wheels`` job matrix."""
    with open(_PACKAGE_YML_PATH) as f:
        doc = yaml.load(f, Loader=_GitLabLoader)
    job = doc["download_dependency_wheels"]
    matrix = job["parallel"]["matrix"]
    versions = [entry["PYTHON_VERSION"] for entry in matrix if "PYTHON_VERSION" in entry]
    assert versions, "download_dependency_wheels matrix carries no PYTHON_VERSION entries"
    return versions


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(_download_dependency_wheels_versions, id="download_dependency_wheels_matrix"),
        pytest.param(_dl_wheels_supported_versions, id="dl_wheels_supported_versions"),
    ],
)
def test_every_admitted_runtime_has_a_payload(sitecustomize, source):
    """Every interpreter the allow-list admits must be one we build an injection payload for.

    ``download_dependency_wheels`` is the job that produces the payload directories;
    ``dl_wheels.py``'s ``supported_versions`` is the ``argparse`` ``choices=`` list that job
    has to pass through. Widening the allow-list without widening both is the failure this
    catches.
    """
    admitted = _admitted_minor_versions(sitecustomize)
    shipped = set(source())
    missing = [version for version in admitted if version not in shipped]
    assert not missing, (
        f"RUNTIMES_ALLOW_LIST admits Python {', '.join(missing)} but {source.__name__} does not build a payload "
        "for it. Widen the packaging first, then the allow-list."
    )


@pytest.mark.parametrize(
    "python_version, supported",
    [
        ("3.8.20", False),
        ("3.9.0", True),
        ("3.14.0", True),
        ("3.14.7", True),
        ("3.15.0a1", False),
        ("3.15.0b3", False),
        ("3.15.0", False),
        ("3.16.0", False),
    ],
)
def test_supported_runtime_range(sitecustomize, python_version, supported):
    """Pins today's posture: 3.9 through 3.14 inclusive, and 3.15 pre-releases declined.

    3.15 has no cp315 SSI payload, so admitting it would put an ABI-mismatched or absent
    ddtrace on the path of every injected 3.15 process. Raising the bound has to change this
    test too, which is the point: it should be a reviewed decision taken alongside the
    packaging change, not a one-line constant edit.
    """
    assert sitecustomize.runtime_version_is_supported("cpython", python_version) is supported
