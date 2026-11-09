import pytest

from tests.contrib.web_framework_lifecycle import assert_web_lifecycle


@pytest.mark.parametrize("mode", ["unregister", "disabled", "standalone"])
def test_request_lifecycle_without_apm(run_python_code_in_subprocess, mode):
    assert_web_lifecycle(run_python_code_in_subprocess, "bottle", mode)
