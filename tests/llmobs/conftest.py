import os

import pytest

from tests.llmobs.utils import logs_vcr
from tests.utils import request_token


@pytest.fixture(autouse=True)
def vcr_logs(request):
    marks = [m for m in request.node.iter_markers(name="vcr_logs")]
    assert len(marks) < 2
    if marks:
        mark = marks[0]
        cass = mark.kwargs.get("cassette", request_token(request).replace(" ", "_").replace(os.path.sep, "_"))
        with logs_vcr.use_cassette("%s.yaml" % cass):
            yield
    else:
        yield


def pytest_configure(config):
    config.addinivalue_line("markers", "vcr_logs: mark test to use recorded request/responses")
