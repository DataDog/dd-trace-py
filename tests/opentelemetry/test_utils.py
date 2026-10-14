from types import SimpleNamespace

import pytest

from ddtrace.internal.opentelemetry.utils import set_http_exporter_marker


@pytest.mark.parametrize("layout", ["client", "session"])
def test_set_http_exporter_marker(layout):
    if layout == "client":
        exporter = SimpleNamespace(_client=SimpleNamespace(_headers={"x-client": "configured"}))
        expected_headers = ((exporter._client._headers, "x-client"),)
    else:
        exporter = SimpleNamespace(
            _headers={"x-exporter": "configured"},
            _session=SimpleNamespace(headers={"x-session": "configured"}),
        )
        expected_headers = (
            (exporter._headers, "x-exporter"),
            (exporter._session.headers, "x-session"),
        )

    set_http_exporter_marker(exporter)

    for headers, existing_header in expected_headers:
        assert headers[existing_header] == "configured"
        assert headers["dd-otlp-export"] == "true"
