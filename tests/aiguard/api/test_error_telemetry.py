"""Error telemetry has to say *why* an evaluation failed, not just that it did.

A customer investigation stalled because every failure reported type=client_error: there was no
way to tell a request that never left the pod (proxy, firewall, DNS) from one the service received
and rejected (bad app key, missing entitlement, rate limit). These tests pin the classification
and the tag values that distinguish them.
Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6600426215
"""

import traceback
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard._api_client import AIGuardClient
from ddtrace.aiguard._api_client import _classify_transport_error
from ddtrace.aiguard._api_client import _scrub_urls
from ddtrace.aiguard._api_client import _status_tag
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpClientError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import InvalidConfigError
from ddtrace.internal.native import TimedOutError
from tests.aiguard.utils import find_ai_guard_span
from tests.aiguard.utils import mock_evaluate_response


MESSAGES = [{"role": "user", "content": "What is the meaning of life?"}]


def _error_metrics(add_count_metric: Mock) -> list[dict[str, str]]:
    """Tags of every ai_guard.error point, as dicts for order-independent assertions."""
    return [dict(args[3]) for args, _ in add_count_metric.call_args_list if args[1] == AI_GUARD.ERROR_METRIC]


def _evaluate_failing_with(
    ai_guard_client: AIGuardClient, exc: BaseException, add_count_metric: Mock
) -> dict[str, str]:
    with patch.object(ai_guard_client, "_execute_request", side_effect=exc):
        with pytest.raises(AIGuardClientError):
            ai_guard_client.evaluate(MESSAGES)
    errors = _error_metrics(add_count_metric)
    assert len(errors) == 1
    return errors[0]


class TestClassification:
    @pytest.mark.parametrize(
        "exc,expected",
        [
            (ConnectionFailedError("refused"), AI_GUARD.ERROR_CONNECTION),
            (TimedOutError("too slow"), AI_GUARD.ERROR_TIMEOUT),
            (InvalidConfigError("bad url"), AI_GUARD.ERROR_INVALID_CONFIG),
            (HttpIoError("broken pipe"), AI_GUARD.ERROR_NETWORK),
            # An unrecognised transport failure still reports as a transport problem.
            (HttpClientError("something else"), AI_GUARD.ERROR_CLIENT),
            # Our own bug must not inflate the transport buckets that alerting reads.
            (ValueError("bug in our code"), AI_GUARD.ERROR_INTERNAL),
            (TypeError("bug in our code"), AI_GUARD.ERROR_INTERNAL),
        ],
    )
    def test_transport_errors_are_classified(self, exc, expected):
        assert _classify_transport_error(exc) == expected

    @pytest.mark.parametrize("status", AI_GUARD.STATUSES)
    def test_declared_statuses_are_reported_verbatim(self, status):
        assert _status_tag(status) == str(status)

    @pytest.mark.parametrize("status", [None, 0, 201, 418, 599])
    def test_undeclared_statuses_are_clamped(self, status):
        """Keeps the tag bounded: an unexpected status cannot open a new series."""
        assert _status_tag(status) == AI_GUARD.STATUS_OTHER


class TestErrorTelemetryTags:
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_connection_failure_is_distinguishable_from_a_rejection(self, add_count_metric, ai_guard_client):
        """The case that was undiagnosable: the request never reached the service."""
        tags = _evaluate_failing_with(ai_guard_client, ConnectionFailedError("refused"), add_count_metric)

        assert tags["type"] == AI_GUARD.ERROR_CONNECTION
        # No status: nothing answered, so reporting one would be a lie.
        assert "http_status" not in tags

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_timeout_is_reported_as_its_own_type(self, add_count_metric, ai_guard_client):
        tags = _evaluate_failing_with(ai_guard_client, TimedOutError("too slow"), add_count_metric)

        assert tags["type"] == AI_GUARD.ERROR_TIMEOUT
        assert "http_status" not in tags

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_internal_error_is_not_reported_as_a_transport_failure(self, add_count_metric, ai_guard_client):
        tags = _evaluate_failing_with(ai_guard_client, ValueError("bug in our code"), add_count_metric)

        assert tags["type"] == AI_GUARD.ERROR_INTERNAL

    @pytest.mark.parametrize(
        "status,expected_status",
        [(401, "401"), (403, "403"), (404, "404"), (429, "429"), (503, "503"), (418, AI_GUARD.STATUS_OTHER)],
    )
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_rejection_carries_the_status(self, add_count_metric, ai_guard_client, status, expected_status):
        """401 (bad app key), 403/404 (entitlement or wrong endpoint) and 429 all used to look alike."""
        response = mock_evaluate_response("ALLOW")
        response.status = status

        with patch.object(ai_guard_client, "_execute_request", return_value=response):
            with pytest.raises(AIGuardClientError):
                ai_guard_client.evaluate(MESSAGES)

        errors = _error_metrics(add_count_metric)
        assert len(errors) == 1
        assert errors[0]["type"] == AI_GUARD.ERROR_BAD_STATUS
        assert errors[0]["http_status"] == expected_status

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_call_path_tags_are_still_present(self, add_count_metric, ai_guard_client):
        """The new tags are additive: the existing call-path dimensions must survive."""
        tags = _evaluate_failing_with(ai_guard_client, ConnectionFailedError("refused"), add_count_metric)

        assert tags["source"] == AI_GUARD.SOURCE_SDK
        assert tags["integration"] == AI_GUARD.INTEGRATION_NONE


class TestInternalErrorsAreNotAttributedToTransport:
    """Only the transport and response paths set a specific type, so anything raised elsewhere in
    evaluate() is our own code failing and must not land in the buckets egress alerting reads.
    """

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_failure_before_the_request_reports_internal_error(self, add_count_metric, ai_guard_client):
        """_get_tool_name runs before the HTTP call, so its failure never reaches the classifier."""
        with patch.object(ai_guard_client, "_get_tool_name", side_effect=RuntimeError("bug")):
            with pytest.raises(RuntimeError):
                ai_guard_client.evaluate(MESSAGES)

        errors = _error_metrics(add_count_metric)
        assert len(errors) == 1
        assert errors[0]["type"] == AI_GUARD.ERROR_INTERNAL

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_failure_after_the_response_reports_internal_error(self, add_count_metric, ai_guard_client):
        """A raise while building the result is equally ours, and equally not a transport failure."""
        response = mock_evaluate_response("ALLOW")
        with patch.object(ai_guard_client, "_execute_request", return_value=response):
            with patch.object(ai_guard_client, "_messages_for_meta_struct", side_effect=RuntimeError("bug")):
                with pytest.raises(RuntimeError):
                    ai_guard_client.evaluate(MESSAGES)

        errors = _error_metrics(add_count_metric)
        assert len(errors) == 1
        assert errors[0]["type"] == AI_GUARD.ERROR_INTERNAL


# An endpoint override carrying a credential in all three places it can hide.
SECRET_ENDPOINT = "https://user:s3cret@proxy.example.com/t0ken/ai-guard?token=k3y"
# The same credentials with no scheme, which urlparse cannot turn into a host: the shape that
# defeats a URL-shaped sanitizer.
SCHEME_RELATIVE_SECRET_ENDPOINT = "//user:s3cret@proxy.example.com/t0ken/ai-guard?token=k3y"
SECRETS = ("s3cret", "t0ken", "k3y")


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


class TestTheEndpointIsNeverLogged:
    """The endpoint is not logged at all, not even reduced to its origin.

    Two rounds of review found a way past every attempt to keep a safe-looking part of it, so the
    value is simply absent from the logs.
    """

    def test_the_startup_line_reports_the_timeout_and_nothing_else(self, caplog):
        with caplog.at_level("DEBUG", logger="ddtrace"):
            AIGuardClient(endpoint=SECRET_ENDPOINT, api_key="test-api-key", app_key="test-app-key")

        assert "AI Guard client ready" in caplog.text
        assert "proxy.example.com" not in caplog.text
        for secret in SECRETS:
            assert secret not in caplog.text

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_the_failure_line_reports_the_classification_and_nothing_else(
        self, add_count_metric, caplog, test_spans, ai_guard_client
    ):
        with caplog.at_level("DEBUG", logger="ddtrace"):
            with patch.object(ai_guard_client, "_execute_request", side_effect=ConnectionFailedError("refused")):
                with pytest.raises(AIGuardClientError):
                    ai_guard_client.evaluate(MESSAGES)

        assert f"AI Guard evaluation failed ({AI_GUARD.ERROR_CONNECTION})" in caplog.text
        assert "api.example.com" not in caplog.text


class TestCredentialsNeverReachAFailureReport:
    """The transport quotes the URL it was handed back at us, and that message is re-rendered by
    the failure log, by the chained traceback and by the span error tags.
    """

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
    def test_every_url_shape_is_redacted(self, message, expected):
        assert _scrub_urls(message) == expected

    @pytest.mark.parametrize("secret", SECRETS)
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_a_quoted_endpoint_is_scrubbed_everywhere_it_surfaces(
        self, add_count_metric, secret, caplog, test_spans, ai_guard_client
    ):
        transport_error = ConnectionFailedError(f"error sending request for url ({SECRET_ENDPOINT}/evaluate)")

        with caplog.at_level("DEBUG", logger="ddtrace"):
            with patch.object(ai_guard_client, "_execute_request", side_effect=transport_error):
                with pytest.raises(AIGuardClientError) as raised:
                    ai_guard_client.evaluate(MESSAGES)

        _assert_clean(secret, raised, caplog, test_spans)
        # The classification still has to survive the scrubbing.
        assert _error_metrics(add_count_metric)[0]["type"] == AI_GUARD.ERROR_CONNECTION

    @pytest.mark.parametrize("endpoint", [SECRET_ENDPOINT, SCHEME_RELATIVE_SECRET_ENDPOINT])
    @pytest.mark.parametrize("secret", SECRETS)
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_an_unparsable_endpoint_is_scrubbed_from_its_own_error(
        self, add_count_metric, secret, endpoint, caplog, test_spans
    ):
        """The scheme-relative form has no host for urlparse to find, so the literal value is what
        has to be removed. No transport mocking: the native client rejects the URL offline.
        """
        client = AIGuardClient(endpoint=endpoint, api_key="test-api-key", app_key="test-app-key")

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
        client = AIGuardClient(endpoint=endpoint, api_key="test-api-key", app_key="test-app-key")

        with caplog.at_level("DEBUG", logger="ddtrace"):
            with patch.object(
                client, "_execute_request", side_effect=ConnectionFailedError(f"cannot reach {endpoint}")
            ):
                with pytest.raises(AIGuardClientError) as raised:
                    client.evaluate(MESSAGES)

        _assert_clean("t0ken", raised, caplog, test_spans)


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

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_a_real_connection_failure_logs_no_credential(self, add_count_metric, caplog, test_spans):
        """ddtrace.internal.http logs the base URL on a refused connection, before AI Guard sees
        the exception. Port 1 on loopback is refused without leaving the machine.
        """
        client = AIGuardClient(
            endpoint="https://user:s3cret@127.0.0.1:1/ai-guard", api_key="test-api-key", app_key="test-app-key"
        )

        with caplog.at_level("DEBUG", logger="ddtrace"):
            with pytest.raises(AIGuardClientError) as raised:
                client.evaluate(MESSAGES)

        # Proves the transport really logged, so the absence below is not an empty capture.
        assert "127.0.0.1:1" in caplog.text
        _assert_clean("s3cret", raised, caplog, test_spans)
        assert _error_metrics(add_count_metric)[0]["type"] == AI_GUARD.ERROR_CONNECTION
