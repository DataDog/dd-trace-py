import pytest

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestDogpile_CachePatch(python_process, scenario):
    module = "tests.contrib.dogpile_cache"
    module += "._test_dogpile_cache_patch_scenarios"
    case = "TestDogpile_CachePatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
