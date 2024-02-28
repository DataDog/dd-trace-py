import os
from pathlib import Path

import pytest


OUT = """Enabling debugging exploration testing
===================== DeterministicProfiler: probes stats ======================

Installed probes: 0/0

============================== Function coverage ===============================

No functions called
========================== LineCoverage: probes stats ==========================

Installed probes: 0/0

================================ Line coverage =================================

Source                                                       Lines Covered
==========================================================================
No lines found
"""


def expl_env(**kwargs):
    return {
        "PYTHONPATH": os.pathsep.join((str(Path(__file__).parent.resolve()), os.getenv("PYTHONPATH", ""))),
        **kwargs,
    }


@pytest.mark.subprocess(env=expl_env(), out=OUT)
def test_exploration_bootstrap():
    # We test that we get the expected output from the exploration debuggers
    # and no errors when running the sitecustomize.py script.
    pass


def check_output_file(o):
    assert not o

    output_file = Path("expl.txt")
    try:
        assert output_file.read_text() == OUT
        return True
    finally:
        if output_file.exists():
            output_file.unlink()


@pytest.mark.subprocess(
    env=expl_env(DD_DEBUGGER_EXPL_OUTPUT_FILE="expl.txt"),
    out=check_output_file,
)
def test_exploration_file_output():
    from pathlib import Path

    from tests.debugging.exploration._config import config

    assert config.output_file == Path("expl.txt")


@pytest.mark.subprocess(
    parametrize=dict(
        DD_REMOTE_CONFIGURATION_ENABLED=["1", "0"],
    ),
)
def test_rc_default_products_registered():
    """
    By default, RC should be enabled. When RC is enabled, we will always
    enable the tracer flare feature as well. There should be three products
    registered when DD_REMOTE_CONFIGURATION_ENABLED is True
    """
    import os

    from ddtrace.internal.utils.formats import asbool

    rc_enabled = asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED"))

    # Import this to trigger the preload
    from ddtrace import config
    import ddtrace.auto  # noqa:F401

    assert config._remote_config_enabled == rc_enabled

    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    assert bool(remoteconfig_poller._client._products.get("APM_TRACING")) == rc_enabled
    assert bool(remoteconfig_poller._client._products.get("AGENT_CONFIG")) == rc_enabled
    assert bool(remoteconfig_poller._client._products.get("AGENT_TASK")) == rc_enabled
