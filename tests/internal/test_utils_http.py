import httpretty
import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.utils.http import connector


@pytest.mark.skipif(
    PYTHON_VERSION_INFO >= (3, 14),
    reason="httpretty doesn't work with Python 3.14 and there is no apparent replacement that works with http.client",
)
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_connector(scheme):
    with httpretty.enabled():
        httpretty.register_uri(httpretty.GET, "%s://localhost:8181/api/test" % scheme, body='{"hello": "world"}')

        connect = connector("%s://localhost:8181" % scheme)
        with connect() as conn:
            conn.request("GET", "/api/test")
            response = conn.getresponse()
            assert response.status == 200
            assert response.read() == b'{"hello": "world"}'
