"""Error telemetry has to say *why* an evaluation failed, not just that it did.

A customer investigation stalled because every failure reported type=client_error: there was no
way to tell a request that never left the pod (proxy, firewall, DNS) from one the service received
and rejected (bad app key, missing entitlement, rate limit). These tests pin the classification
and the tag values that distinguish them.
Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6600426215
"""

from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard._api_client import _classify_transport_error
from ddtrace.aiguard._api_client import _loggable_endpoint
from ddtrace.aiguard._api_client import _status_tag
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpClientError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import InvalidConfigError
from ddtrace.internal.native import TimedOutError
from tests.aiguard.utils import mock_evaluate_response


MESSAGES = [{"role": "user", "content": "What is the meaning of life?"}]


def _error_metrics(add_count_metric: Mock) -> list[dict[str, str]]:
    """Tags of every ai_guard.error point, as dicts for order-independent assertions."""
    return [dict(args[3]) for args, _ in add_count_metric.call_args_list if args[1] == AI_GUARD.ERROR_METRIC]


def _evaluate_failing_with(ai_guard_client: Any, exc: BaseException, add_count_metric: Mock) -> dict[str, str]:
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
            ("https://api.example.com/ai-guard", "https://api.example.com/ai-guard"),
            ("https://api.example.com:8443/ai-guard", "https://api.example.com:8443/ai-guard"),
            # userinfo and query credentials are dropped
            ("https://user:s3cret@api.example.com/ai-guard", "https://api.example.com/ai-guard"),
            ("https://api.example.com/ai-guard?token=s3cret", "https://api.example.com/ai-guard"),
            ("https://user:s3cret@api.example.com/ai-guard?token=t0ken", "https://api.example.com/ai-guard"),
            # nothing usable to log, and never the raw value
            ("not a url", "<unparsable>"),
            ("", "<unparsable>"),
        ],
    )
    def test_credentials_are_stripped(self, url, expected):
        assert _loggable_endpoint(url) == expected

    @pytest.mark.parametrize("secret", ["s3cret", "t0ken"])
    def test_no_secret_reaches_the_startup_log(self, secret, caplog):
        from ddtrace.aiguard._api_client import AIGuardClient

        with caplog.at_level("DEBUG", logger="ddtrace.aiguard._api_client"):
            AIGuardClient(
                endpoint="https://user:s3cret@api.example.com/ai-guard?token=t0ken",
                api_key="test-api-key",
                app_key="test-app-key",
            )

        assert secret not in caplog.text
