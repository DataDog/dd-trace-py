import logging

import pytest

from ddtrace import constants
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec.trace_utils import block_request_if_user_blocked
from ddtrace.appsec.trace_utils import should_block_user
from ddtrace.appsec.trace_utils import track_custom_event
from ddtrace.appsec.trace_utils import track_user_login_failure_event
from ddtrace.appsec.trace_utils import track_user_login_success_event
from ddtrace.appsec.trace_utils import track_user_signup_event
from ddtrace.contrib.internal.trace_utils import set_user
from ddtrace.ext import user
import tests.appsec.rules as rules
from tests.appsec.utils import asm_context
from tests.appsec.utils import is_blocked
from tests.utils import TracerTestCase


config_asm = {"_asm_enabled": True}
config_good_rules = {"_asm_static_rule_file": rules.RULES_GOOD_PATH, "_asm_enabled": True}


class EventsSDKTestCase(TracerTestCase):
    _BLOCKED_USER = "123456"

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tracer, caplog):  # noqa: F811
        self._caplog = caplog
        self.tracer = tracer

    def test_track_user_login_event_success_without_metadata(self):
        with asm_context(tracer=self.tracer, span_name="test_success1", config=config_asm):
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
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX

            assert root_span.get_tag("appsec.events.users.login.success.track") == "true"
            assert root_span.get_tag("_dd.appsec.events.users.login.success.sdk") == "true"
            assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.IDENT
            assert not root_span.get_tag("%s.track" % failure_prefix)
            assert root_span.context.sampling_priority == constants.USER_KEEP
            # set_user tags
            assert root_span.get_tag(user.ID) == "1234"
            assert root_span.get_tag(user.NAME) == "John"
            assert root_span.get_tag(user.EMAIL) == "test@test.com"
            assert root_span.get_tag(user.SCOPE) == "test_scope"
            assert root_span.get_tag(user.ROLE) == "boss"
            assert root_span.get_tag(user.SESSION_ID) == "test_session_id"

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

    def test_track_user_login_event_success_auto_mode_safe(self):
        with asm_context(tracer=self.tracer, span_name="test_success1", config=config_asm):
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

            root_span = self.tracer.current_root_span()
            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            assert root_span.get_tag("%s.track" % success_prefix) == "true"
            assert not root_span.get_tag("_dd.appsec.events.users.login.success.sdk")
            assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == str(LOGIN_EVENTS_MODE.ANON)

    def test_track_user_login_event_success_auto_mode_extended(self):
        with asm_context(tracer=self.tracer, span_name="test_success1", config=config_asm):
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

            root_span = self.tracer.current_root_span()
            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            assert root_span.get_tag("%s.track" % success_prefix) == "true"
            assert not root_span.get_tag("_dd.appsec.events.users.login.success.sdk")
            assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == str(LOGIN_EVENTS_MODE.IDENT)

    def test_track_user_login_event_success_with_metadata(self):
        with asm_context(tracer=self.tracer, span_name="test_success2", config=config_asm):
            track_user_login_success_event(self.tracer, "1234", metadata={"foo": "bar"})
            root_span = self.tracer.current_root_span()
            assert root_span.get_tag("appsec.events.users.login.success.track") == "true"
            assert root_span.get_tag("_dd.appsec.events.users.login.success.sdk") == "true"
            assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.IDENT
            assert root_span.get_tag("%s.success.foo" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC) == "bar"
            assert root_span.context.sampling_priority == constants.USER_KEEP
            # set_user tags
            assert root_span.get_tag(user.ID) == "1234"
            assert not root_span.get_tag(user.NAME)
            assert not root_span.get_tag(user.EMAIL)
            assert not root_span.get_tag(user.SCOPE)
            assert not root_span.get_tag(user.ROLE)
            assert not root_span.get_tag(user.SESSION_ID)

    def test_track_user_login_event_failure_user_exists(self):
        with asm_context(tracer=self.tracer, span_name="test_failure", config=config_asm):
            track_user_login_failure_event(
                self.tracer,
                "1234",
                True,
                metadata={"foo": "bar"},
                login="johntest",
                name="John Test",
                email="john@test.net",
            )
            root_span = self.tracer.current_root_span()

            success_prefix = "%s.success" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC

            assert root_span.get_tag("%s.track" % failure_prefix) == "true"
            assert root_span.get_tag("_dd.appsec.events.users.login.failure.sdk") == "true"
            assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE) == LOGIN_EVENTS_MODE.IDENT
            assert not root_span.get_tag("%s.track" % success_prefix)
            assert not root_span.get_tag("_dd.appsec.events.users.login.success.sdk")
            assert not root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE)
            assert root_span.get_tag("%s.%s" % (failure_prefix, user.ID)) == "1234"
            assert root_span.get_tag("%s.%s" % (failure_prefix, user.EXISTS)) == "true"
            assert root_span.get_tag("%s.foo" % failure_prefix) == "bar"
            assert root_span.get_tag("%s.%s" % (failure_prefix, "login")) == "johntest"
            assert root_span.get_tag("%s.%s" % (failure_prefix, "username")) == "John Test"
            assert root_span.get_tag("%s.%s" % (failure_prefix, "email")) == "john@test.net"

            assert root_span.context.sampling_priority == constants.USER_KEEP
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
            failure_prefix = "%s.failure" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC
            assert root_span.get_tag("%s.%s" % (failure_prefix, user.EXISTS)) == "false"

    def test_track_user_signup_event_exists(self):
        with self.trace("test_signup_exists"):
            track_user_signup_event(self.tracer, "john", True)
            root_span = self.tracer.current_root_span()
            assert root_span.get_tag(APPSEC.USER_SIGNUP_EVENT) == "true"
            assert root_span.get_tag(user.ID) == "john"

    def test_custom_event(self):
        with self.trace("test_custom"):
            event = "some_event"
            track_custom_event(self.tracer, event, {"foo": "bar"})
            root_span = self.tracer.current_root_span()

            assert root_span.get_tag("%s.%s.foo" % (APPSEC.CUSTOM_EVENT_PREFIX, event)) == "bar"
            assert root_span.get_tag("%s.%s.track" % (APPSEC.CUSTOM_EVENT_PREFIX, event)) == "true"

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

    def test_no_span_doesnt_raise(self):
        from ddtrace.trace import tracer

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
