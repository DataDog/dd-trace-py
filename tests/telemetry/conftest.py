import mock
import pytest


@pytest.fixture
def mock_time():
    with mock.patch("time.time") as mt:
        mt.return_value = 1642544540
        yield mt
