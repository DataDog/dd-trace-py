from ddtrace import constants
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec.trace_utils import track_custom_event
from ddtrace.appsec.trace_utils import track_user_login_failure_event
from ddtrace.appsec.trace_utils import track_user_login_success_event
from ddtrace.ext import user
from tests.utils import TracerTestCase


class EventsSDKTestCase(TracerTestCase):
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
