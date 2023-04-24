import logging

import pytest

from ddtrace import constants
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec.trace_utils import block_request_if_user_blocked
from ddtrace.appsec.trace_utils import should_block_user
from ddtrace.appsec.trace_utils import track_custom_event
from ddtrace.appsec.trace_utils import track_user_login_failure_event
from ddtrace.appsec.trace_utils import track_user_login_success_event
from ddtrace.contrib.trace_utils import set_user
from ddtrace.ext import SpanTypes
from ddtrace.ext import user
from ddtrace.internal import _context
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.appsec.test_processor import tracer_appsec  # noqa: F401
from tests.utils import TracerTestCase
from tests.utils import override_env


class EventsSDKTestCase(TracerTestCase):
    _BLOCKED_USER = "123456"

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tracer_appsec, caplog):  # noqa: F811
        self._caplog = caplog
        self._tracer_appsec = tracer_appsec

    def test_track_user_login_event_success_without_metadata(self):
        with self.trace("test_success1"):
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

            root_span = self.tracer.current_root_span()

            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX

            assert root_span.get_tag("%s.track" % success_prefix) == "true"
            assert not root_span.get_tag("%s.track" % failure_prefix)
            assert root_span.get_tag(constants.MANUAL_KEEP_KEY) == "true"
            # set_user tags
            assert root_span.get_tag(user.ID) == "1234"
            assert root_span.get_tag(user.NAME) == "John"
            assert root_span.get_tag(user.EMAIL) == "test@test.com"
            assert root_span.get_tag(user.SCOPE) == "test_scope"
            assert root_span.get_tag(user.ROLE) == "boss"
            assert root_span.get_tag(user.SESSION_ID) == "test_session_id"

    def test_track_user_login_event_success_with_metadata(self):
        with self.trace("test_success2"):
            track_user_login_success_event(self.tracer, "1234", metadata={"foo": "bar"})
            root_span = self.tracer.current_root_span()
            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX
            assert root_span.get_tag("%s.track" % success_prefix) == "true"
            assert root_span.get_tag("%s.foo" % success_prefix) == "bar"
            assert root_span.get_tag(constants.MANUAL_KEEP_KEY) == "true"
            # set_user tags
            assert root_span.get_tag(user.ID) == "1234"
            assert not root_span.get_tag(user.NAME)
            assert not root_span.get_tag(user.EMAIL)
            assert not root_span.get_tag(user.SCOPE)
            assert not root_span.get_tag(user.ROLE)
            assert not root_span.get_tag(user.SESSION_ID)

    def test_track_user_login_event_failure_user_exists(self):
        with self.trace("test_failure"):
            track_user_login_failure_event(
                self.tracer,
                "1234",
                True,
                metadata={"foo": "bar"},
            )
            root_span = self.tracer.current_root_span()

            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX

            assert root_span.get_tag("%s.track" % failure_prefix) == "true"
            assert not root_span.get_tag("%s.track" % success_prefix)
            assert root_span.get_tag("%s.%s" % (failure_prefix, user.ID)) == "1234"
            assert root_span.get_tag("%s.%s" % (failure_prefix, user.EXISTS)) == "true"
            assert root_span.get_tag("%s.foo" % failure_prefix) == "bar"
            assert root_span.get_tag(constants.MANUAL_KEEP_KEY) == "true"
            # set_user tags: shouldn't have been called
            assert not root_span.get_tag(user.ID)
            assert not root_span.get_tag(user.NAME)
            assert not root_span.get_tag(user.EMAIL)
            assert not root_span.get_tag(user.SCOPE)
            assert not root_span.get_tag(user.ROLE)
            assert not root_span.get_tag(user.SESSION_ID)

    def test_track_user_login_event_failure_user_doesnt_exists(self):
        with self.trace("test_failure"):
            track_user_login_failure_event(
                self.tracer,
                "john",
                False,
                metadata={"foo": "bar"},
            )
            root_span = self.tracer.current_root_span()
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX
            assert root_span.get_tag("%s.%s" % (failure_prefix, user.EXISTS)) == "false"

    def test_custom_event(self):
        with self.trace("test_custom"):
            event = "some_event"
            track_custom_event(self.tracer, event, {"foo": "bar"})
            root_span = self.tracer.current_root_span()

            assert root_span.get_tag("%s.%s.foo" % (APPSEC.CUSTOM_EVENT_PREFIX, event)) == "bar"
            assert root_span.get_tag("%s.%s.track" % (APPSEC.CUSTOM_EVENT_PREFIX, event)) == "true"

    def test_set_user_blocked(self):
        tracer = self._tracer_appsec
        with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            tracer.configure(api_version="v0.4")
            with tracer.trace("fake_span", span_type=SpanTypes.WEB) as span:
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
                assert _context.get_item("http.request.blocked", span=span)

    def test_no_span_doesnt_raise(self):
        from ddtrace import tracer

        with self._caplog.at_level(logging.DEBUG):
            try:
                should_block_user(tracer, "111")
                block_request_if_user_blocked(tracer, "111")
                track_custom_event(tracer, "testevent", {})
                track_user_login_success_event(tracer, "111", {})
                track_user_login_failure_event(tracer, "111", {})
                set_user(tracer, "111")
            except Exception as e:
                pytest.fail("Should not raise but raised %s" % str(e))

            assert any("No root span" in record.message for record in self._caplog.records)
            assert any(record.levelno == logging.WARNING for record in self._caplog.records)
