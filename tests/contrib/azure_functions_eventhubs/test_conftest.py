import tempfile
from unittest import mock

import pytest

from tests.contrib.azure_functions_eventhubs.conftest import _wait_for_function_routes


def test_wait_for_function_routes() -> None:
    with tempfile.TemporaryFile(mode="w+b") as stdout_log:
        stdout_log.write(b"/api/sendeventbatch\n/api/sendeventsingle\n")
        stdout_log.flush()

        _wait_for_function_routes(stdout_log)


def test_wait_for_function_routes_timeout() -> None:
    with tempfile.TemporaryFile(mode="w+b") as stdout_log:
        with mock.patch("tests.contrib.azure_functions_eventhubs.conftest.time.sleep") as sleep:
            with pytest.raises(TimeoutError, match="did not finish indexing"):
                _wait_for_function_routes(stdout_log)

    assert sleep.call_count == 100
