import httpretty
import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.utils.http import connector


parameters = ["http"]
# httpretty doesn't work with https/http.client/Python 3.14 and there is no apparent replacement
if PYTHON_VERSION_INFO < (3, 14):
    parameters.append("https")


@pytest.mark.parametrize("scheme", parameters)
def test_connector(scheme):
    with httpretty.enabled():
        httpretty.register_uri(httpretty.GET, "%s://localhost:8181/api/test" % scheme, body='{"hello": "world"}')

        connect = connector("%s://localhost:8181" % scheme)
        with connect() as conn:
            conn.request("GET", "/api/test")
            response = conn.getresponse()
            assert response.status == 200
            assert response.read() == b'{"hello": "world"}'
