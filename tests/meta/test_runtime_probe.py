import importlib.util
from pathlib import Path
import types

import pytest


ROOT_CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"


@pytest.fixture(scope="module")
def root_conftest():
    # Load by path under a private name. A plain "import conftest" can resolve to whichever
    # conftest module pytest registered first.
    spec = importlib.util.spec_from_file_location("_root_conftest_under_test", ROOT_CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("DD_TEST_RUNTIME_PROBE", raising=False)
    return monkeypatch


CONTROLLER = types.SimpleNamespace()
XDIST_WORKER = types.SimpleNamespace(workerinput={})


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, False),
        ({"CI": "true"}, False),
        ({"DD_TEST_RUNTIME_PROBE": "1"}, True),
        ({"CI": "true", "DD_TEST_RUNTIME_PROBE": "1"}, True),
        ({"CI": "true", "DD_TEST_RUNTIME_PROBE": "0"}, False),
        ({"CI": "true", "DD_TEST_RUNTIME_PROBE": ""}, False),
        ({"CI": "true", "DD_TEST_RUNTIME_PROBE": "$DD_TEST_RUNTIME_PROBE"}, False),
    ],
)
def test_probe_enabled_only_on_explicit_opt_in(root_conftest, clean_env, env, expected):
    for key, value in env.items():
        clean_env.setenv(key, value)
    assert root_conftest._probe_enabled(CONTROLLER) is expected


def test_probe_disabled_on_xdist_workers(root_conftest, clean_env):
    clean_env.setenv("DD_TEST_RUNTIME_PROBE", "1")
    assert root_conftest._probe_enabled(XDIST_WORKER) is False
