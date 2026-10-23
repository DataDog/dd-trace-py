import pytest

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


def test_mcp_auto_patch_during_experiment_import(python_process):
    """MCP auto-patching must not recursively import a partial LLMObs experiment module."""
    code = """
import sys

from ddtrace._monkey import patch

patch(raise_errors=False, mcp=True)


class ImportMCPWhileExperimentInitializes:
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "pydantic_evals":
            return None

        sys.meta_path.remove(self)
        import mcp

        assert getattr(mcp, "__datadog_patch", False) is True
        return None


sys.meta_path.insert(0, ImportMCPWhileExperimentInitializes())
try:
    import ddtrace.llmobs._experiment
except ModuleNotFoundError as error:
    # The MCP suite does not require pydantic-evals. Its import is only used to
    # pause experiment initialization at the point that exposes this cycle.
    if error.name != "pydantic_evals":
        raise
"""

    _process_result = python_process(code)
    stderr = _process_result.stderr
    status = _process_result.returncode

    assert status == 0, stderr.decode()
    assert b"failed to enable ddtrace support for mcp" not in stderr


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestMCPPatch(python_process, scenario):
    module = "tests.contrib.mcp"
    module += "._test_mcp_patch_scenarios"
    case = "TestMCPPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
