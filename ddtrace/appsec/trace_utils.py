from typing import Optional
from typing import TYPE_CHECKING

from ddtrace.contrib.trace_utils import set_user


if TYPE_CHECKING:
    from ddtrace import Span
    from ddtrace import Tracer

from ddtrace import constants
from ddtrace.appsec._constants import APPSEC
from ddtrace.ext import user
from ddtrace.internal.compat import six
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _track_user_login_common(tracer, user_id, success, metadata=None):
    # type: (Tracer, str, bool, Optional[dict]) -> Optional[Span]

    span = tracer.current_root_span()
    if span:
        success_str = "success" if success else "failure"
        span.set_tag_str("%s.%s.track" % (APPSEC.USER_LOGIN_EVENT_PREFIX, success_str), "true")
        if metadata is not None:
            for k, v in six.iteritems(metadata):
                span.set_tag_str("%s.%s.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX, success_str, k), str(v))
        span.set_tag_str(constants.MANUAL_KEEP_KEY, "true")
        return span
    else:
        log.warning(
            "No root span in the current execution. Skipping track_user_success_login tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )
    return None


def track_user_login_success_event(
    tracer,
    user_id,
    metadata=None,
    name=None,
    email=None,
    scope=None,
    role=None,
    session_id=None,
    propagate=False,
):
    # type: (Tracer, str, Optional[dict], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], bool) -> None # noqa: E501

    span = _track_user_login_common(tracer, user_id, True, metadata)
    if not span:
        return

    # usr.id will be set by set_user
    set_user(tracer, user_id, name, email, scope, role, session_id, propagate)


def track_user_login_failure_event(tracer, user_id, exists, metadata=None):
    # type: (Tracer, str, bool, Optional[dict]) -> None

    span = _track_user_login_common(tracer, user_id, False, metadata)
    if not span:
        return

    span.set_tag_str("%s.failure.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX, user.ID), str(user_id))
    exists_str = "true" if exists else "false"
    span.set_tag_str("%s.failure.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX, user.EXISTS), exists_str)


def track_custom_event(tracer, event_name, metadata):
    # type: (Tracer, str, dict) -> None

    if not event_name:
        log.warning("Empty event name given to track_custom_event. Skipping setting tags.")
        return

    if not metadata:
        log.warning("Empty metadata given to track_custom_event. Skipping setting tags.")
        return

    span = tracer.current_root_span()
    if span:
        for k, v in six.iteritems(metadata):
            span.set_tag_str("%s.%s.%s" % (APPSEC.CUSTOM_EVENT_PREFIX, event_name, k), str(v))
            span.set_tag_str(constants.MANUAL_KEEP_KEY, "true")
    else:
        log.warning(
            "No root span in the current execution. Skipping track_custom_event tags. "
            "See https://docs.datadoghq.com/security_platform/application_security"
            "/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )
