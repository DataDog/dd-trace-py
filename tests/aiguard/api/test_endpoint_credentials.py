"""DD_AI_GUARD_ENDPOINT can carry a credential, and a failed evaluation must not report it.

The value can hide a token in the userinfo, in a path segment or in a query parameter, and a proxy
override commonly embeds one. These tests pin the four places a transport failure surfaces it: the
raised exception, the traceback rendered from it, the ddtrace debug logs and the span error tags.
Jira: https://datadoghq.atlassian.net/browse/APPSEC-70144
"""

import builtins
import sys
import traceback
from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard._api_client import AIGuardClient
from ddtrace.aiguard._api_client import _scrub_urls
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.internal.native import ConnectionFailedError
from tests.aiguard.utils import find_ai_guard_span


MESSAGES = [{"role": "user", "content": "What is the meaning of life?"}]

# An endpoint override carrying a credential in all three places one can hide.
SECRET_ENDPOINT = "https://user:s3cret@proxy.example.com/t0ken/ai-guard?token=k3y"
# The same credentials with no scheme, which urlparse cannot turn into a host: the shape that
# defeats a URL-shaped sanitizer.
SCHEME_RELATIVE_SECRET_ENDPOINT = "//user:s3cret@proxy.example.com/t0ken/ai-guard?token=k3y"
SECRETS = ("s3cret", "t0ken", "k3y")

# What the native client quotes back at us when it cannot reach the endpoint it was handed.
SECRET_MESSAGE = f"error sending request for url ({SECRET_ENDPOINT}/evaluate)"


class QuotesTheEndpointInStr(Exception):
    """A failure whose message lives in a custom __str__ rather than in args."""

    def __str__(self):
        return SECRET_MESSAGE


class RaisesOnStr(Exception):
    """A failure we cannot even render, so there is nothing safe to quote from it."""

    def __str__(self):
        raise RuntimeError(SECRET_MESSAGE)


def _quotes_in_its_own_message() -> BaseException:
    return ConnectionFailedError(SECRET_MESSAGE)


def _quotes_in_a_cause() -> BaseException:
    """The transport wraps the failure that actually quoted the URL, and a traceback walks down."""
    exc = ConnectionFailedError("client error (Connect)")
    exc.__cause__ = ConnectionFailedError(SECRET_MESSAGE)
    return exc


def _quotes_in_a_context() -> BaseException:
    exc = ConnectionFailedError("client error (Connect)")
    exc.__context__ = ConnectionFailedError(SECRET_MESSAGE)
    return exc


def _quotes_in_a_cyclic_chain() -> BaseException:
    """A chain can point back at itself, which anything walking it has to survive."""
    exc = ConnectionFailedError(SECRET_MESSAGE)
    nested = ConnectionFailedError("client error (Connect)")
    exc.__cause__ = nested
    nested.__cause__ = exc
    return exc


def _quotes_in_a_note() -> BaseException:
    """PEP 678 notes are rendered under the message and are not reachable through args."""
    exc = ConnectionFailedError("client error (Connect)")
    exc.add_note(SECRET_MESSAGE)
    return exc


def _quotes_in_a_group_member() -> BaseException:
    """PEP 654 renders every member of a group, not just the group's own message."""
    return builtins.ExceptionGroup("transport failed", [ConnectionFailedError(SECRET_MESSAGE)])


# Every shape a transport failure can quote the endpoint in. Each is a reason not to chain it:
# scrubbing the message alone leaves the rest of the exception graph rendering the credential.
TRANSPORT_FAILURES = {
    "own_message": _quotes_in_its_own_message,
    "custom_str": QuotesTheEndpointInStr,
    "cause": _quotes_in_a_cause,
    "context": _quotes_in_a_context,
    "cyclic_chain": _quotes_in_a_cyclic_chain,
}
if sys.version_info >= (3, 11):
    TRANSPORT_FAILURES["note"] = _quotes_in_a_note
    TRANSPORT_FAILURES["group_member"] = _quotes_in_a_group_member


def _client(endpoint: str) -> AIGuardClient:
    return AIGuardClient(endpoint=endpoint, api_key="test-api-key", app_key="test-app-key")


def _rendered(exc: BaseException) -> str:
    """The exception as exc_info logging and the span error stack render it, chained cause included."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _assert_clean(secret: str, raised, caplog, test_spans) -> None:
    """Assert a secret reached none of the four places a failure is reported."""
    assert secret not in str(raised.value)
    assert secret not in _rendered(raised.value)
    assert secret not in caplog.text
    span = find_ai_guard_span(test_spans)
    assert secret not in span.get_tag(ERROR_MSG)
    assert secret not in span.get_tag(ERROR_STACK)


class TestEveryUrlShapeIsRedacted:
    @pytest.mark.parametrize(
        "message,expected",
        [
            # Messages the native client and libdd actually produce, credentials and all.
            (
                "invalid base_url 'https://user:s3cret@': empty host",
                "invalid base_url '<endpoint>': empty host",
            ),
            (
                "unsupported scheme 'ftp' in base_url 'ftp://user:s3cret@proxy.example.com' (use http, https)",
                "unsupported scheme 'ftp' in base_url '<endpoint>' (use http, https)",
            ),
            (
                "error sending request for url (https://user:s3cret@proxy.example.com/t0ken/evaluate?token=k3y)",
                "error sending request for url (<endpoint>)",
            ),
            # A scheme-relative endpoint, and the bare authority left when the scheme is missing.
            (
                "native HTTP client connection to //user:s3cret@proxy.example.com/t0ken failed",
                "native HTTP client connection to <endpoint> failed",
            ),
            (
                "invalid base_url '://user:s3cret@proxy.example.com': relative URL without a base",
                "invalid base_url ':<endpoint>': relative URL without a base",
            ),
            # Nothing URL-shaped, so nothing is lost.
            ("client error (Connect)", "client error (Connect)"),
        ],
    )
    def test_url_shapes(self, message, expected):
        assert _scrub_urls(message) == expected


class TestCredentialsNeverReachAFailureReport:
    """The transport quotes the URL it was handed back at us, and that message is re-rendered by
    the failure log, by a rendered traceback and by the span error tags.
    """

    @pytest.mark.parametrize("secret", SECRETS)
    @pytest.mark.parametrize("failure", TRANSPORT_FAILURES.values(), ids=list(TRANSPORT_FAILURES))
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_no_shape_of_transport_failure_reaches_a_report(
        self, add_count_metric, failure, secret, caplog, test_spans, ai_guard_client
    ):
        """Whichever part of the failure quotes the endpoint, only our scrubbed message is reported."""
        with caplog.at_level("DEBUG", logger="ddtrace"):
            with patch.object(ai_guard_client, "_execute_request", side_effect=failure()):
                with pytest.raises(AIGuardClientError) as raised:
                    ai_guard_client.evaluate(MESSAGES)

        _assert_clean(secret, raised, caplog, test_spans)
        # Both would resurrect the failure: __cause__ through a rendered traceback, __context__
        # through anything walking the chain itself.
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_the_report_names_the_failure_that_was_dropped(self, add_count_metric, ai_guard_client):
        """Nothing survives the failure but this message, so it has to say what went wrong."""
        with patch.object(ai_guard_client, "_execute_request", side_effect=ConnectionFailedError(SECRET_MESSAGE)):
            with pytest.raises(AIGuardClientError) as raised:
                ai_guard_client.evaluate(MESSAGES)

        assert str(raised.value) == (
            "Unexpected error calling AI Guard service (ConnectionFailedError): "
            "error sending request for url (<endpoint>)"
        )

    @pytest.mark.parametrize("endpoint", [SECRET_ENDPOINT, SCHEME_RELATIVE_SECRET_ENDPOINT])
    @pytest.mark.parametrize("secret", SECRETS)
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_an_unparsable_endpoint_is_scrubbed_from_its_own_error(
        self, add_count_metric, secret, endpoint, caplog, test_spans
    ):
        """The scheme-relative form has no host for urlparse to find, so the literal value is what
        has to be removed. No transport mocking: the native client rejects the URL offline.
        """
        client = _client(endpoint)

        with caplog.at_level("DEBUG", logger="ddtrace"):
            with pytest.raises(AIGuardClientError) as raised:
                client.evaluate(MESSAGES)

        _assert_clean(secret, raised, caplog, test_spans)

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_an_endpoint_that_is_not_url_shaped_is_still_removed(self, add_count_metric, caplog, test_spans):
        """A value with no scheme and no // for a URL pattern to anchor on is removed literally,
        so a future transport message quoting it cannot leak what the pattern would miss.
        """
        endpoint = "proxy.example.com/t0ken"
        client = _client(endpoint)

        with caplog.at_level("DEBUG", logger="ddtrace"):
            with patch.object(
                client, "_execute_request", side_effect=ConnectionFailedError(f"cannot reach {endpoint}")
            ):
                with pytest.raises(AIGuardClientError) as raised:
                    client.evaluate(MESSAGES)

        _assert_clean("t0ken", raised, caplog, test_spans)

    @pytest.mark.parametrize("secret", SECRETS)
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_a_failure_that_cannot_be_rendered_is_reported_by_type_only(
        self, add_count_metric, secret, caplog, test_spans, ai_guard_client
    ):
        """Reading the message is what raised, so the type name is all there is left to report."""
        with caplog.at_level("DEBUG", logger="ddtrace"):
            with patch.object(ai_guard_client, "_execute_request", side_effect=RaisesOnStr()):
                with pytest.raises(AIGuardClientError) as raised:
                    ai_guard_client.evaluate(MESSAGES)

        _assert_clean(secret, raised, caplog, test_spans)
        assert str(raised.value) == "Unexpected error calling AI Guard service (RaisesOnStr)"
        # The fallback log names the type only: exc_info would render what we could not read.
        assert "Could not render AI Guard transport error message (RaisesOnStr)" in caplog.text


class TestTheTransportNeverSeesTheCredential:
    """Userinfo is stripped before the request leaves, because everything downstream of that call
    logs and quotes the base URL it was given, and none of it is ours to sanitize.
    """

    @pytest.mark.parametrize(
        "endpoint,expected_base",
        [
            (SECRET_ENDPOINT, "https://proxy.example.com"),
            ("https://user@proxy.example.com/ai-guard", "https://proxy.example.com"),
            # No credential to strip, and the port, IPv6 brackets and scheme all survive.
            ("https://api.example.com:8443/ai-guard", "https://api.example.com:8443"),
            ("https://[2001:db8::1]:8443/ai-guard", "https://[2001:db8::1]:8443"),
        ],
    )
    def test_the_base_url_handed_to_the_transport_has_no_userinfo(self, endpoint, expected_base, ai_guard_client):
        with patch("ddtrace.aiguard._api_client.HTTPConnection") as connection:
            connection.return_value.getresponse.side_effect = ConnectionFailedError("refused")
            with pytest.raises(ConnectionFailedError):
                ai_guard_client._execute_request(f"{endpoint}/evaluate", {})

        assert connection.call_args[0][0] == expected_base

    def test_the_path_and_query_still_reach_the_transport(self, ai_guard_client):
        """Stripping userinfo must not disturb what the request actually targets."""
        with patch("ddtrace.aiguard._api_client.HTTPConnection") as connection:
            connection.return_value.getresponse.side_effect = ConnectionFailedError("refused")
            with pytest.raises(ConnectionFailedError):
                ai_guard_client._execute_request("https://user:s3cret@host.example.com/ai-guard?v=1", {})

        assert connection.return_value.request.call_args[0][1] == "/ai-guard?v=1"

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_a_real_connection_failure_logs_no_credential(self, add_count_metric, caplog, test_spans):
        """ddtrace.internal.http logs the base URL on a refused connection, before AI Guard sees
        the exception. Port 1 on loopback is refused without leaving the machine.
        """
        client = _client("https://user:s3cret@127.0.0.1:1/ai-guard")

        with caplog.at_level("DEBUG", logger="ddtrace"):
            with pytest.raises(AIGuardClientError) as raised:
                client.evaluate(MESSAGES)

        # Proves the transport really logged, so the absence below is not an empty capture.
        assert "127.0.0.1:1" in caplog.text
        _assert_clean("s3cret", raised, caplog, test_spans)
