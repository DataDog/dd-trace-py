import pytest

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestAzureEventHubsPatch(python_process, scenario):
    module = "tests.contrib.azure_eventhubs"
    module += "._test_azure_eventhubs_patch_scenarios"
    case = "TestAzureEventHubsPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestAzureEventHubsAioPatch(python_process, scenario):
    module = "tests.contrib.azure_eventhubs"
    module += "._test_azure_eventhubs_patch_scenarios"
    case = "TestAzureEventHubsAioPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
