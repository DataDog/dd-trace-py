import pytest

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestRQPatch(python_process, scenario):
    module = "tests.contrib.rq"
    module += "._test_rq_patch_scenarios"
    case = "TestRQPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
