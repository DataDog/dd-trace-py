import contextlib
import logging
from unittest.mock import patch as mock_patch

import pytest

from ddtrace import constants
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec._utils import _hash_user_id
from ddtrace.appsec.trace_utils import block_request_if_user_blocked
from ddtrace.appsec.trace_utils import should_block_user
from ddtrace.appsec.trace_utils import track_custom_event
from ddtrace.appsec.trace_utils import track_user_login_failure_event
from ddtrace.appsec.trace_utils import track_user_login_success_event
from ddtrace.appsec.trace_utils import track_user_signup_event
from ddtrace.appsec.track_user_sdk import track_user
from ddtrace.appsec.track_user_sdk import track_user_id
from ddtrace.contrib.internal.trace_utils import set_user
from ddtrace.ext import user
import ddtrace.internal.telemetry
import tests.appsec.rules as rules
from tests.appsec.utils import asm_context
from tests.appsec.utils import is_blocked
from tests.utils import TracerTestCase


@contextlib.contextmanager
def capture_telemetry_metrics():
    """Patch the telemetry writer's add_*_metric methods and record their calls.

    Yields a list of tuples matching the legacy _namespace.add_metric format:
    (metric_type, namespace, name, value, tags).
    """
    metrics = []
    tw = ddtrace.internal.telemetry.telemetry_writer

    def _rec(metric_type):
        def _f(namespace, name, value, tags=None):
            metrics.append((metric_type, getattr(namespace, "value", namespace), name, value, tags))

        return _f

    with (
        mock_patch.object(tw, "add_count_metric", _rec("count")),
        mock_patch.object(tw, "add_gauge_metric", _rec("gauge")),
        mock_patch.object(tw, "add_rate_metric", _rec("rate")),
        mock_patch.object(tw, "add_distribution_metric", _rec("distribution")),
    ):
        yield metrics


config_asm = {"_asm_enabled": True}
config_good_rules = {"_asm_static_rule_file": rules.RULES_GOOD_PATH, "_asm_enabled": True}


class EventsSDKTestCase(TracerTestCase):
    _BLOCKED_USER = "123456"

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):  # noqa: F811
        self._caplog = caplog

    def test_track_user_login_event_success_without_metadata(self):
        with asm_context(tracer=self.tracer, span_name="test_success1", config=config_asm) as span:
            track_user_login_success_event(
                self.tracer,
                "1234",
                metadata=None,
                name="John",
                email="test@test.com",
                scope="test_scope",
                role="boss",
                session_id="test_session_id",
            )

            entry_span = span._service_entry_span
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX

            assert entry_span.get_tag("appsec.events.users.login.success.track") == "true"
            assert entry_span.get_tag("_dd.appsec.events.users.login.success.sdk") == "true"
            assert entry_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.IDENT
            assert not entry_span.get_tag("%s.track" % failure_prefix)
            assert entry_span.context.sampling_priority == constants.USER_KEEP
            # set_user tags
            assert entry_span.get_tag(user.ID) == "1234"
            assert entry_span.get_tag(user.NAME) == "John"
            assert entry_span.get_tag(user.EMAIL) == "test@test.com"
            assert entry_span.get_tag(user.SCOPE) == "test_scope"
            assert entry_span.get_tag(user.ROLE) == "boss"
            assert entry_span.get_tag(user.SESSION_ID) == "test_session_id"

    def test_track_user_login_event_success_in_span_without_metadata(self):
        with asm_context(tracer=self.tracer, span_name="test_success1", config=config_asm) as parent_span:
            user_span = self.trace("user_span")
            user_span.parent_id = parent_span.span_id
            track_user_login_success_event(
                self.tracer,
                "1234",
                metadata=None,
                name="John",
                email="test@test.com",
                scope="test_scope",
                role="boss",
                session_id="test_session_id",
                span=user_span,
            )

            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC

            assert user_span.get_tag("%s.track" % success_prefix) == "true"
            assert user_span.get_tag("_dd.appsec.events.users.login.success.sdk") == "true"
            assert user_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.IDENT
            assert not user_span.get_tag("%s.track" % failure_prefix)
            assert user_span.context.sampling_priority == constants.USER_KEEP
            # set_user tags
            assert user_span.get_tag(user.ID) == "1234" and parent_span.get_tag(user.ID) is None
            assert user_span.get_tag(user.NAME) == "John" and parent_span.get_tag(user.NAME) is None
            assert user_span.get_tag(user.EMAIL) == "test@test.com" and parent_span.get_tag(user.EMAIL) is None
            assert user_span.get_tag(user.SCOPE) == "test_scope" and parent_span.get_tag(user.SCOPE) is None
            assert user_span.get_tag(user.ROLE) == "boss" and parent_span.get_tag(user.ROLE) is None
            assert (
                user_span.get_tag(user.SESSION_ID) == "test_session_id" and parent_span.get_tag(user.SESSION_ID) is None
            )
            user_span.finish()

    def test_track_user_login_event_success_auto_mode_safe(self):
        with asm_context(tracer=self.tracer, span_name="test_success1", config=config_asm) as span:
            track_user_login_success_event(
                self.tracer,
                "1234",
                metadata=None,
                name="John",
                email="test@test.com",
                scope="test_scope",
                role="boss",
                session_id="test_session_id",
                login_events_mode=LOGIN_EVENTS_MODE.ANON,
            )

            entry_span = span._service_entry_span
            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            assert entry_span.get_tag("%s.track" % success_prefix) == "true"
            assert not entry_span.get_tag("_dd.appsec.events.users.login.success.sdk")
            assert entry_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == str(LOGIN_EVENTS_MODE.ANON)
            # the session id must be anonymized in anonymization mode, never exported in clear text
            assert entry_span.get_tag(user.SESSION_ID) == _hash_user_id("test_session_id")
            assert entry_span.get_tag(user.SESSION_ID) != "test_session_id"

    def test_track_user_login_event_success_auto_mode_extended(self):
        with asm_context(tracer=self.tracer, span_name="test_success1", config=config_asm) as span:
            track_user_login_success_event(
                self.tracer,
                "1234",
                metadata=None,
                name="John",
                email="test@test.com",
                scope="test_scope",
                role="boss",
                session_id="test_session_id",
                login_events_mode=LOGIN_EVENTS_MODE.IDENT,
            )

            entry_span = span._service_entry_span
            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            assert entry_span.get_tag("%s.track" % success_prefix) == "true"
            assert not entry_span.get_tag("_dd.appsec.events.users.login.success.sdk")
            assert entry_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == str(LOGIN_EVENTS_MODE.IDENT)
            # the session id is kept as is in identification mode
            assert entry_span.get_tag(user.SESSION_ID) == "test_session_id"

    def test_track_user_login_event_success_with_metadata(self):
        with (
            capture_telemetry_metrics() as metrics,
            asm_context(tracer=self.tracer, span_name="test_success2", config=config_asm) as span,
        ):
            track_user_login_success_event(self.tracer, "1234", metadata={"foo": "bar"})
            entry_span = span._service_entry_span
            assert entry_span.get_tag("appsec.events.users.login.success.track") == "true"
            assert entry_span.get_tag("_dd.appsec.events.users.login.success.sdk") == "true"
            assert entry_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.IDENT
            assert entry_span.get_tag("%s.success.foo" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC) == "bar"
            assert entry_span.context.sampling_priority == constants.USER_KEEP
            # set_user tags
            assert entry_span.get_tag(user.ID) == "1234"
            assert not entry_span.get_tag(user.NAME)
            assert not entry_span.get_tag(user.EMAIL)
            assert not entry_span.get_tag(user.SCOPE)
            assert not entry_span.get_tag(user.ROLE)
            assert not entry_span.get_tag(user.SESSION_ID)
            assert (
                "count",
                "appsec",
                "sdk.event",
                1,
                (("event_type", "login_success"), ("sdk_version", "v1")),
            ) in metrics

    def test_track_user_login_event_failure_user_exists(self):
        with asm_context(tracer=self.tracer, span_name="test_failure", config=config_asm) as span:
            track_user_login_failure_event(
                self.tracer,
                "1234",
                True,
                metadata={"foo": "bar"},
                login="johntest",
                name="John Test",
                email="john@test.net",
            )
            entry_span = span._service_entry_span

            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC

            assert entry_span.get_tag("%s.track" % failure_prefix) == "true"
            assert entry_span.get_tag("_dd.appsec.events.users.login.failure.sdk") == "true"
            assert entry_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE) == LOGIN_EVENTS_MODE.IDENT
            assert not entry_span.get_tag("%s.track" % success_prefix)
            assert not entry_span.get_tag("_dd.appsec.events.users.login.success.sdk")
            assert not entry_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE)
            assert entry_span.get_tag("%s.%s" % (failure_prefix, user.ID)) == "1234"
            assert entry_span.get_tag("%s.%s" % (failure_prefix, user.EXISTS)) == "true"
            assert entry_span.get_tag("%s.foo" % failure_prefix) == "bar"
            assert entry_span.get_tag("%s.%s" % (failure_prefix, "login")) == "johntest"
            assert entry_span.get_tag("%s.%s" % (failure_prefix, "username")) == "John Test"
            assert entry_span.get_tag("%s.%s" % (failure_prefix, "email")) == "john@test.net"

            assert entry_span.context.sampling_priority == constants.USER_KEEP
            # set_user tags: shouldn't have been called
            assert not entry_span.get_tag(user.ID)
            assert not entry_span.get_tag(user.NAME)
            assert not entry_span.get_tag(user.EMAIL)
            assert not entry_span.get_tag(user.SCOPE)
            assert not entry_span.get_tag(user.ROLE)
            assert not entry_span.get_tag(user.SESSION_ID)

    def test_track_user_login_event_failure_user_doesnt_exists(self):
        with (
            capture_telemetry_metrics() as metrics,
            self.trace("test_failure") as span,
        ):
            track_user_login_failure_event(
                self.tracer,
                "john",
                False,
                metadata={"foo": "bar"},
            )
            entry_span = span._service_entry_span
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            assert entry_span.get_tag("%s.%s" % (failure_prefix, user.EXISTS)) == "false"
            assert metrics == [
                ("count", "appsec", "sdk.event", 1, (("event_type", "login_failure"), ("sdk_version", "v1")))
            ]

    def test_track_user_signup_event_exists(self):
        with (
            capture_telemetry_metrics() as metrics,
            self.trace("test_signup_exists") as span,
        ):
            track_user_signup_event(self.tracer, "john", True)
            entry_span = span._service_entry_span
            assert entry_span.get_tag(APPSEC.USER_SIGNUP_EVENT) == "true"
            assert entry_span.get_tag(user.ID) == "john"
            assert metrics == [("count", "appsec", "sdk.event", 1, (("event_type", "signup"), ("sdk_version", "v1")))]

    def test_custom_event(self):
        with (
            capture_telemetry_metrics() as metrics,
            self.trace("test_custom") as span,
        ):
            event = "some_event"
            track_custom_event(self.tracer, event, {"foo": "bar"})
            entry_span = span._service_entry_span

            assert entry_span.get_tag("%s.%s.foo" % (APPSEC.CUSTOM_EVENT_PREFIX, event)) == "bar"
            assert entry_span.get_tag("%s.%s.track" % (APPSEC.CUSTOM_EVENT_PREFIX, event)) == "true"
            assert ("count", "appsec", "sdk.event", 1, (("event_type", "custom"), ("sdk_version", "v1"))) in metrics

    def test_set_user_blocked(self):
        with asm_context(tracer=self.tracer, span_name="fake_span", config=config_good_rules) as span:
            set_user(
                self.tracer,
                user_id=self._BLOCKED_USER,
                email="usr.email",
                name="usr.name",
                session_id="usr.session_id",
                role="usr.role",
                scope="usr.scope",
            )
        assert span.get_tag(user.ID)
        assert span.get_tag(user.EMAIL)
        assert span.get_tag(user.SESSION_ID)
        assert span.get_tag(user.NAME)
        assert span.get_tag(user.ROLE)
        assert span.get_tag(user.SCOPE)
        assert span.get_tag(user.SESSION_ID)
        assert span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE) == LOGIN_EVENTS_MODE.SDK
        assert span.get_tag("usr.id") == str(self._BLOCKED_USER)
        assert is_blocked(span)

    def test_track_user_blocked(self):
        with asm_context(tracer=self.tracer, span_name="fake_span", config=config_good_rules) as span:
            track_user(
                "login",
                user_id=self._BLOCKED_USER,
                session_id="usr.session_id",
                metadata={
                    "email": "usr.email",
                    "name": "usr.name",
                    "role": "usr.role",
                    "scope": "usr.scope",
                },
            )
        assert span.get_tag(user.ID)
        assert span.get_tag(user.EMAIL)
        assert span.get_tag(user.SESSION_ID)
        assert span.get_tag(user.NAME)
        assert span.get_tag(user.ROLE)
        assert span.get_tag(user.SCOPE)
        assert span.get_tag(user.SESSION_ID)
        assert span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE) == LOGIN_EVENTS_MODE.SDK
        # assert metadata tags are not set for usual data
        assert span.get_tag("appsec.events.auth_sdk.track") is None
        assert span.get_tag("usr.id") == str(self._BLOCKED_USER)
        assert is_blocked(span)

    def test_track_user_id_blocked(self):
        with asm_context(tracer=self.tracer, span_name="fake_span", config=config_good_rules) as span:
            track_user_id(
                self._BLOCKED_USER,
                session_id="usr.session_id",
                metadata={
                    "email": "usr.email",
                    "name": "usr.name",
                    "role": "usr.role",
                    "scope": "usr.scope",
                },
            )
        assert span.get_tag(user.ID)
        assert span.get_tag(user.EMAIL)
        assert span.get_tag(user.SESSION_ID)
        assert span.get_tag(user.NAME)
        assert span.get_tag(user.ROLE)
        assert span.get_tag(user.SCOPE)
        assert span.get_tag(user.SESSION_ID)
        assert span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE) == LOGIN_EVENTS_MODE.SDK
        # assert metadata tags are not set for usual data
        assert span.get_tag("appsec.events.auth_sdk.track") is None
        assert span.get_tag("usr.id") == str(self._BLOCKED_USER)
        assert is_blocked(span)

    def test_no_span_doesnt_raise(self):
        from ddtrace.trace import tracer

        with self._caplog.at_level(logging.DEBUG):
            try:
                should_block_user(tracer, "111")
                block_request_if_user_blocked("111")
                track_custom_event(tracer, "testevent", {})
                track_user_login_success_event(tracer, "111", {})
                track_user_login_failure_event(tracer, "111", {})
                set_user(tracer, "111")
            except Exception as e:
                pytest.fail("Should not raise but raised %s" % str(e))

            assert any("No root span" in record.message for record in self._caplog.records)
            assert any(record.levelno == logging.WARNING for record in self._caplog.records)


@pytest.mark.subprocess(env=dict(DD_APPSEC_ENABLED="true"))
def test_user_blocking_listener_registered_without_appsec_trace_utils():
    """Regression test for APPSEC-68564.

    ``ddtrace.contrib.trace_utils.set_user`` enforces user blocking by dispatching the
    ``set_user_for_asm`` event. The ``block_user`` listener must be registered during AppSec
    startup even when ``ddtrace.appsec.trace_utils`` (and the user-tracking SDK) are never
    imported, otherwise a blocked user can bypass blocking.
    """
    import sys

    from ddtrace.appsec._listeners import load_appsec
    from ddtrace.internal import core

    # Enabling AppSec must not require importing the public user-tracking modules.
    load_appsec()

    assert "ddtrace.appsec.trace_utils" not in sys.modules
    assert "ddtrace.appsec.track_user_sdk" not in sys.modules
    assert core.event_hub.has_listeners("set_user_for_asm"), "block_user listener was not registered"
