"""lifecycle gate: import inertness, activation idempotency and cleanup.

Covers the plan's negative fixtures: an import-started thread and a
duplicate activation, plus the positive inert/idempotent paths.
"""

from __future__ import annotations

from pathlib import Path

from package_split.evidence import FAIL
from package_split.evidence import NOT_RUN
from package_split.evidence import PASS
from package_split.gates import lifecycle
import pytest

from ._support import Args
from ._support import make_report
from ._support import make_tree


ACTIVATION = """
[contracts.activation_api]
module = "{module}"
activate = "activate"
deactivate = "deactivate"
"""


def run_lifecycle(manifest_path: Path, **kwargs):
    from package_split.manifest import load_manifest

    manifest = load_manifest(manifest_path)
    report = make_report("lifecycle", manifest_path)
    lifecycle.run_gate(manifest, Args(**kwargs), report)
    return report


def test_inert_package_passes(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "__version__ = '1.0'\n", "mod.py": "value = 42\n"},
    )
    report = run_lifecycle(tmp_path / "package-contract.toml")
    assert report.status == PASS, report.summary()


def test_import_started_thread_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "import threading\n"
                "def _poller():\n"
                "    import time\n"
                "    time.sleep(30)\n"
                "threading.Thread(target=_poller, daemon=True).start()\n"
                "__version__ = '1.0'\n"
            ),
        },
    )
    report = run_lifecycle(tmp_path / "package-contract.toml")
    assert report.status == FAIL
    side_effects = [c for c in report.checks if c.name == "import-side-effects"]
    assert side_effects and side_effects[0].status == FAIL, report.summary()


def test_network_on_import_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "import socket\nsocket.getaddrinfo('example.com', 80)\n"},
    )
    report = run_lifecycle(tmp_path / "package-contract.toml")
    assert report.status == FAIL


def test_logging_configuration_on_import_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "import logging\nhandler = logging.StreamHandler()\nlogging.getLogger().addHandler(handler)\n"
            ),
        },
    )
    report = run_lifecycle(tmp_path / "package-contract.toml")
    assert report.status == FAIL


def test_clean_activation_passes(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "import threading\n"
                "REGISTRY = []\n"
                "_thread = None\n"
                "_stop = None\n"
                "\n"
                "def _poll(stop):\n"
                "    while not stop.is_set():\n"
                "        stop.wait(0.05)\n"
                "\n"
                "def activate():\n"
                "    global _thread, _stop\n"
                "    if REGISTRY:\n"
                "        return\n"
                "    REGISTRY.append('instance')\n"
                "    import threading\n"
                "    _stop = threading.Event()\n"
                "    _thread = threading.Thread(target=_poll, args=(_stop,), daemon=True)\n"
                "    _thread.start()\n"
                "\n"
                "def deactivate():\n"
                "    global _thread, _stop\n"
                "    if not REGISTRY:\n"
                "        return\n"
                "    REGISTRY.clear()\n"
                "    if _stop is not None:\n"
                "        _stop.set()\n"
                "    if _thread is not None:\n"
                "        _thread.join(timeout=5)\n"
                "        _thread = None\n"
            ),
        },
        manifest_kwargs={
            "contracts_extra": ACTIVATION.format(module="sample"),
        },
    )
    report = run_lifecycle(
        tmp_path / "package-contract.toml",
        activation_singletons=["sample:REGISTRY"],
    )
    assert report.status == PASS, report.summary()
    activation = [c for c in report.checks if c.name == "activation"]
    assert activation and activation[0].status == PASS


def test_duplicate_activation_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "REGISTRY = []\n"
                "def activate():\n"
                "    REGISTRY.append('instance')\n"
                "def deactivate():\n"
                "    REGISTRY.clear()\n"
            ),
        },
        manifest_kwargs={
            "contracts_extra": ACTIVATION.format(module="sample"),
        },
    )
    report = run_lifecycle(
        tmp_path / "package-contract.toml",
        activation_singletons=["sample:REGISTRY"],
    )
    assert report.status == FAIL
    activation = [c for c in report.checks if c.name == "activation"]
    assert activation and activation[0].status == FAIL
    assert any("duplicate-activation" in str(c.data.get("violations")) for c in activation)


def test_activation_thread_leak_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "import threading\n"
                "REGISTRY = []\n"
                "\n"
                "def _poll():\n"
                "    import time\n"
                "    time.sleep(60)\n"
                "\n"
                "def activate():\n"
                "    if REGISTRY:\n"
                "        return\n"
                "    REGISTRY.append('instance')\n"
                "    threading.Thread(target=_poll, daemon=True).start()\n"
                "\n"
                "def deactivate():\n"
                "    REGISTRY.clear()\n"
            ),
        },
        manifest_kwargs={
            "contracts_extra": ACTIVATION.format(module="sample"),
        },
    )
    report = run_lifecycle(
        tmp_path / "package-contract.toml",
        activation_singletons=["sample:REGISTRY"],
    )
    assert report.status == FAIL
    activation = [c for c in report.checks if c.name == "activation"]
    assert activation and activation[0].status == FAIL


def test_activation_singletons_required_for_duplicate_detection(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "REGISTRY = []\n"
                "def activate():\n"
                "    REGISTRY.append('instance')\n"
                "def deactivate():\n"
                "    REGISTRY.clear()\n"
            ),
        },
        manifest_kwargs={
            "contracts_extra": ACTIVATION.format(module="sample"),
        },
    )
    report = run_lifecycle(tmp_path / "package-contract.toml")
    # Without a declared registry, duplicate detection is explicitly not run.
    assert any(c.name == "activation-singletons" and c.status == NOT_RUN for c in report.checks)


@pytest.mark.skipif(
    not hasattr(__import__("multiprocessing"), "get_context"),
    reason="multiprocessing contexts unavailable",
)
def test_fork_spawn_single_activation(tmp_path):
    # Clean activation must work exactly once per process under both fork
    # and spawn start methods; the clean-activation fixture above covers
    # this through the probe's child runs.
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "REGISTRY = []\n"
                "def activate():\n"
                "    if not REGISTRY:\n"
                "        REGISTRY.append('instance')\n"
                "def deactivate():\n"
                "    REGISTRY.clear()\n"
            ),
        },
        manifest_kwargs={
            "contracts_extra": ACTIVATION.format(module="sample"),
        },
    )
    report = run_lifecycle(
        tmp_path / "package-contract.toml",
        activation_singletons=["sample:REGISTRY"],
    )
    assert report.status == PASS, report.summary()
