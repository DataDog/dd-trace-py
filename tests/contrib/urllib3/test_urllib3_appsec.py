import mock
import pytest
import urllib3

from ddtrace import config
from ddtrace._trace.span import _get_64_highest_order_bits_as_hex
from ddtrace.internal.settings.asm import config as asm_config
from tests.contrib.config import HTTPBIN_CONFIG
from tests.contrib.urllib3.test_urllib3 import BaseUrllib3TestCase


HOST = HTTPBIN_CONFIG["host"]
PORT = HTTPBIN_CONFIG["port"]
SOCKET = "{}:{}".format(HOST, PORT)
URL_200 = "http://{}/status/200".format(SOCKET)


class TestUrllib3(BaseUrllib3TestCase):
    @pytest.mark.skip(reason="urlib3 does not set the ASM Manual keep tag so x-datadog headers are not propagated")
    def test_distributed_tracing_apm_opt_out_true(self):
        """Tests distributed tracing headers are passed by default"""
        # Check that distributed tracing headers are passed down; raise an error rather than make the
        # request since we don't care about the response at all
        config.urllib3["distributed_tracing"] = True
        self.tracer.enabled = False
        # Ensure the ASM SpanProcessor is set
        self.tracer.configure(apm_tracing_disabled=True, appsec_enabled=True)
        assert asm_config._apm_opt_out
        with mock.patch(
            "urllib3.connectionpool.HTTPConnectionPool._make_request", side_effect=ValueError
        ) as m_make_request:
            with pytest.raises(ValueError):
                self.http.request("GET", URL_200)

            spans = self.pop_spans()
            s = spans[0]
            expected_headers = {
                "x-datadog-trace-id": str(s._trace_id_64bits),
                "x-datadog-parent-id": str(s.span_id),
                "x-datadog-sampling-priority": "1",
                "x-datadog-tags": "_dd.p.dm=-0,_dd.p.tid={}".format(_get_64_highest_order_bits_as_hex(s.trace_id)),
                "traceparent": s.context._traceparent,
                # outgoing headers must contain last parent span id in tracestate
                "tracestate": s.context._tracestate.replace("dd=", "dd=p:{:016x};".format(s.span_id)),
            }

            if int(urllib3.__version__.split(".")[0]) >= 2:
                m_make_request.assert_called_with(
                    mock.ANY,
                    "GET",
                    "/status/200",
                    body=None,
                    chunked=mock.ANY,
                    headers=expected_headers,
                    timeout=mock.ANY,
                    retries=mock.ANY,
                    response_conn=mock.ANY,
                    preload_content=mock.ANY,
                    decode_content=mock.ANY,
                )
            else:
                m_make_request.assert_called_with(
                    mock.ANY,
                    "GET",
                    "/status/200",
                    body=None,
                    chunked=mock.ANY,
                    headers=expected_headers,
                    timeout=mock.ANY,
                )

    def test_distributed_tracing_apm_opt_out_false(self):
        """Test with distributed tracing disabled does not propagate the headers"""
        config.urllib3["distributed_tracing"] = True
        # Ensure the ASM SpanProcessor is set.
        self.tracer.configure(apm_tracing_disabled=False, appsec_enabled=True)
        self.tracer.enabled = False
        assert not asm_config._apm_opt_out
        with mock.patch(
            "urllib3.connectionpool.HTTPConnectionPool._make_request", side_effect=ValueError
        ) as m_make_request:
            with pytest.raises(ValueError):
                self.http.request("GET", URL_200)

            if int(urllib3.__version__.split(".")[0]) >= 2:
                m_make_request.assert_called_with(
                    mock.ANY,
                    "GET",
                    "/status/200",
                    body=None,
                    chunked=mock.ANY,
                    headers={},
                    timeout=mock.ANY,
                    retries=mock.ANY,
                    response_conn=mock.ANY,
                    preload_content=mock.ANY,
                    decode_content=mock.ANY,
                )
            else:
                m_make_request.assert_called_with(
                    mock.ANY,
                    "GET",
                    "/status/200",
                    body=None,
                    chunked=mock.ANY,
                    headers={},
                    timeout=mock.ANY,
                )
