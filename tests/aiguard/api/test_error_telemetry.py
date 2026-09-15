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
from ddtrace.aiguard._api_client import _loggable_endpoint
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


class TestEndpointIsNotLoggedVerbatim:
    """Customers are asked for these debug logs during investigations, so an endpoint override
    carrying credentials must not end up in them.
    """

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://api.example.com/ai-guard", "https://api.example.com"),
            ("https://api.example.com:8443/ai-guard", "https://api.example.com:8443"),
            # userinfo, path and query credentials are all dropped
            ("https://user:s3cret@api.example.com/ai-guard", "https://api.example.com"),
            ("https://api.example.com/ai-guard?token=s3cret", "https://api.example.com"),
            ("https://proxy.example.com/customer-token/ai-guard", "https://proxy.example.com"),
            ("https://user:s3cret@proxy.example.com/t0ken/ai-guard?token=k3y", "https://proxy.example.com"),
            # IPv6 literals keep their brackets, so the host stays unambiguous against the port
            ("https://[2001:db8::1]/ai-guard", "https://[2001:db8::1]"),
            ("https://[2001:db8::1]:8443/ai-guard", "https://[2001:db8::1]:8443"),
            # an explicit port is reported even when it is falsy
            ("https://api.example.com:0/ai-guard", "https://api.example.com:0"),
            # nothing usable to log, and never the raw value
            ("not a url", "<unparsable>"),
            ("", "<unparsable>"),
            ("https://api.example.com:99999/ai-guard", "<unparsable>"),
        ],
    )
    def test_only_the_origin_is_kept(self, url, expected):
        assert _loggable_endpoint(url) == expected

    @pytest.mark.parametrize("secret", ["s3cret", "t0ken", "k3y"])
    def test_no_secret_reaches_the_startup_log(self, secret, caplog):
        with caplog.at_level("DEBUG", logger="ddtrace.aiguard._api_client"):
            AIGuardClient(
                endpoint="https://user:s3cret@api.example.com/t0ken/ai-guard?token=k3y",
                api_key="test-api-key",
                app_key="test-app-key",
            )

        assert secret not in caplog.text


# An endpoint override carrying a credential in all three places it can hide.
SECRET_ENDPOINT = "https://user:s3cret@proxy.example.com/t0ken/ai-guard?token=k3y"
SECRETS = ("s3cret", "t0ken", "k3y")


def _rendered(exc: BaseException) -> str:
    """The exception as exc_info logging and the span error stack render it, chained cause included."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


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
                "invalid base_url '<unparsable>': empty host",
            ),
            (
                "unsupported scheme 'ftp' in base_url 'ftp://user:s3cret@proxy.example.com' (use http, https)",
                "unsupported scheme 'ftp' in base_url 'ftp://proxy.example.com' (use http, https)",
            ),
            (
                "error sending request for url (https://user:s3cret@proxy.example.com/t0ken/evaluate?token=k3y)",
                "error sending request for url (https://proxy.example.com)",
            ),
            ("base_url 'https://[2001:db8::1]:8443' has no host", "base_url 'https://[2001:db8::1]:8443' has no host"),
            # Nothing to scrub, so nothing is lost.
            ("client error (Connect)", "client error (Connect)"),
        ],
    )
    def test_every_url_is_reduced_to_its_origin(self, message, expected):
        assert _scrub_urls(message) == expected

    @pytest.mark.parametrize("secret", SECRETS)
    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_a_quoted_endpoint_is_scrubbed_everywhere_it_surfaces(
        self, add_count_metric, secret, caplog, test_spans, ai_guard_client
    ):
        transport_error = ConnectionFailedError(f"error sending request for url ({SECRET_ENDPOINT}/evaluate)")

        with caplog.at_level("DEBUG", logger="ddtrace.aiguard._api_client"):
            with patch.object(ai_guard_client, "_execute_request", side_effect=transport_error):
                with pytest.raises(AIGuardClientError) as raised:
                    ai_guard_client.evaluate(MESSAGES)

        assert secret not in str(raised.value)
        assert secret not in _rendered(raised.value)
        assert secret not in caplog.text
        span = find_ai_guard_span(test_spans)
        assert secret not in span.get_tag(ERROR_MSG)
        assert secret not in span.get_tag(ERROR_STACK)
        # The classification still has to survive the scrubbing.
        assert _error_metrics(add_count_metric)[0]["type"] == AI_GUARD.ERROR_CONNECTION

    @patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
    def test_a_real_native_failure_does_not_leak_the_credential(self, add_count_metric, caplog, test_spans):
        """No mocked transport: an empty host makes the native client reject and quote the URL."""
        client = AIGuardClient(endpoint="https://user:s3cret@", api_key="test-api-key", app_key="test-app-key")

        with caplog.at_level("DEBUG", logger="ddtrace.aiguard._api_client"):
            with pytest.raises(AIGuardClientError) as raised:
                client.evaluate(MESSAGES)

        assert isinstance(raised.value.__cause__, InvalidConfigError)
        assert "s3cret" not in str(raised.value)
        assert "s3cret" not in _rendered(raised.value)
        assert "s3cret" not in caplog.text
        span = find_ai_guard_span(test_spans)
        assert "s3cret" not in span.get_tag(ERROR_MSG)
        assert "s3cret" not in span.get_tag(ERROR_STACK)
        assert _error_metrics(add_count_metric)[0]["type"] == AI_GUARD.ERROR_INVALID_CONFIG
