import pytest

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestAzureServiceBusPatch(python_process, scenario):
    module = "tests.contrib.azure_servicebus"
    module += "._test_azure_servicebus_patch_scenarios"
    case = "TestAzureServiceBusPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestAzureServiceBusAioPatch(python_process, scenario):
    module = "tests.contrib.azure_servicebus"
    module += "._test_azure_servicebus_patch_scenarios"
    case = "TestAzureServiceBusAioPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
