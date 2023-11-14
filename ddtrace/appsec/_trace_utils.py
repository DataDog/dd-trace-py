from typing import Optional

from ddtrace import Span
from ddtrace import Tracer
from ddtrace import config
from ddtrace import constants
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec._constants import WAF_CONTEXT_NAMES
from ddtrace.appsec._utils import _safe_userid
from ddtrace.contrib.trace_utils import set_user
from ddtrace.ext import SpanTypes
from ddtrace.ext import user
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def _asm_manual_keep(span: Span) -> None:
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.sampling import SamplingMechanism

    span.set_tag(constants.MANUAL_KEEP_KEY)
    # set decision maker to ASM = -5
    span.set_tag_str(SAMPLING_DECISION_TRACE_TAG_KEY, "-%d" % SamplingMechanism.APPSEC)


def _track_user_login_common(
    tracer: Tracer,
    success: bool,
    metadata: Optional[dict] = None,
    login_events_mode: str = LOGIN_EVENTS_MODE.SDK,
    login: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
    span: Optional[Span] = None,
) -> Optional[Span]:
    if span is None:
        span = tracer.current_root_span()
    if span:
        success_str = "success" if success else "failure"
        tag_prefix = "%s.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX, success_str)

        if success:
            span.set_tag_str(APPSEC.USER_LOGIN_EVENT_SUCCESS_TRACK, "true")
        else:
            span.set_tag_str(APPSEC.USER_LOGIN_EVENT_FAILURE_TRACK, "true")

        # This is used to mark if the call was done from the SDK of the automatic login events
        if login_events_mode == LOGIN_EVENTS_MODE.SDK:
            span.set_tag_str("%s.sdk" % tag_prefix, "true")

        mode_tag = APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE if success else APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE
        auto_tag_mode = (
            login_events_mode if login_events_mode != LOGIN_EVENTS_MODE.SDK else asm_config._automatic_login_events_mode
        )
        span.set_tag_str(mode_tag, auto_tag_mode)

        tag_metadata_prefix = "%s.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, success_str)
        if metadata is not None:
            for k, v in metadata.items():
                span.set_tag_str("%s.%s" % (tag_metadata_prefix, k), str(v))

        if login:
            span.set_tag_str("%s.login" % tag_prefix, login)

        if email:
            span.set_tag_str("%s.email" % tag_prefix, email)

        if name:
            span.set_tag_str("%s.username" % tag_prefix, name)

        _asm_manual_keep(span)
        return span
    else:
        log.warning(
            "No root span in the current execution. Skipping track_user_success_login tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )
    return None


def track_user_login_success_event(
    tracer: Tracer,
    user_id: str,
    metadata: Optional[dict] = None,
    login: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
    scope: Optional[str] = None,
    role: Optional[str] = None,
    session_id: Optional[str] = None,
    propagate: bool = False,
    login_events_mode: str = LOGIN_EVENTS_MODE.SDK,
    span: Optional[Span] = None,
) -> None:
    """
    Add a new login success tracking event. The parameters after metadata (name, email,
    scope, role, session_id, propagate) will be passed to the `set_user` function that will be called
    by this one, see:
    https://docs.datadoghq.com/logs/log_configuration/attributes_naming_convention/#user-related-attributes
    https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/?tab=set_tag&code-lang=python

    :param tracer: tracer instance to use
    :param user_id: a string with the UserId
    :param metadata: a dictionary with additional metadata information to be stored with the event
    """

    span = _track_user_login_common(tracer, True, metadata, login_events_mode, login, name, email, span)
    if not span:
        return

    if (
        user_id
        and (login_events_mode not in (LOGIN_EVENTS_MODE.SDK, LOGIN_EVENTS_MODE.EXTENDED))
        and not asm_config._user_model_login_field
    ):
        user_id = _safe_userid(user_id)

    set_user(tracer, user_id, name, email, scope, role, session_id, propagate, span)


def track_user_login_failure_event(
    tracer: Tracer,
    user_id: Optional[str],
    exists: bool,
    metadata: Optional[dict] = None,
    login_events_mode: str = LOGIN_EVENTS_MODE.SDK,
) -> None:
    """
    Add a new login failure tracking event.
    :param tracer: tracer instance to use
    :param user_id: a string with the UserId if exists=True or the username if not
    :param exists: a boolean indicating if the user exists in the system
    :param metadata: a dictionary with additional metadata information to be stored with the event
    """

    if (
        user_id
        and (login_events_mode not in (LOGIN_EVENTS_MODE.SDK, LOGIN_EVENTS_MODE.EXTENDED))
        and not asm_config._user_model_login_field
    ):
        user_id = _safe_userid(user_id)

    span = _track_user_login_common(tracer, False, metadata, login_events_mode)
    if not span:
        return

    if user_id:
        span.set_tag_str("%s.failure.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, user.ID), str(user_id))
    exists_str = "true" if exists else "false"
    span.set_tag_str("%s.failure.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, user.EXISTS), exists_str)


def track_user_signup_event(
    tracer: Tracer, user_id: str, success: bool, login_events_mode: str = LOGIN_EVENTS_MODE.SDK
) -> None:
    span = tracer.current_root_span()
    if span:
        success_str = "true" if success else "false"
        span.set_tag_str(APPSEC.USER_SIGNUP_EVENT, success_str)
        span.set_tag_str(user.ID, user_id)
        _asm_manual_keep(span)

        # This is used to mark if the call was done from the SDK of the automatic login events
        if login_events_mode == LOGIN_EVENTS_MODE.SDK:
            span.set_tag_str("%s.sdk" % APPSEC.USER_SIGNUP_EVENT, "true")
        else:
            span.set_tag_str("%s.auto.mode" % APPSEC.USER_SIGNUP_EVENT, str(login_events_mode))

        return
    else:
        log.warning(
            "No root span in the current execution. Skipping track_user_signup tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )


def track_custom_event(tracer: Tracer, event_name: str, metadata: dict) -> None:
    """
    Add a new custom tracking event.

    :param tracer: tracer instance to use
    :param event_name: the name of the custom event
    :param metadata: a dictionary with additional metadata information to be stored with the event
    """

    if not event_name:
        log.warning("Empty event name given to track_custom_event. Skipping setting tags.")
        return

    if not metadata:
        log.warning("Empty metadata given to track_custom_event. Skipping setting tags.")
        return

    span = tracer.current_root_span()
    if not span:
        log.warning(
            "No root span in the current execution. Skipping track_custom_event tags. "
            "See https://docs.datadoghq.com/security_platform/application_security"
            "/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )
        return

    span.set_tag_str("%s.%s.track" % (APPSEC.CUSTOM_EVENT_PREFIX, event_name), "true")

    for k, v in metadata.items():
        span.set_tag_str("%s.%s.%s" % (APPSEC.CUSTOM_EVENT_PREFIX, event_name, k), str(v))
        _asm_manual_keep(span)


def should_block_user(tracer: Tracer, userid: str) -> bool:
    """
    Return true if the specified User ID should be blocked.

    :param tracer: tracer instance to use
    :param userid: the ID of the user as registered by `set_user`
    """

    if not asm_config._asm_enabled:
        log.warning(
            "One click blocking of user ids is disabled. To use this feature please enable "
            "Application Security Monitoring"
        )
        return False

    # Early check to avoid calling the WAF if the request is already blocked
    span = tracer.current_root_span()
    if not span:
        log.warning(
            "No root span in the current execution. should_block_user returning False"
            "See https://docs.datadoghq.com/security_platform/application_security"
            "/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )
        return False

    if core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span):
        return True

    _asm_request_context.call_waf_callback(custom_data={"REQUEST_USER_ID": str(userid)})
    return bool(core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span))


def block_request() -> None:
    """
    Block the current request and return a 403 Unauthorized response. If the response
    has already been started to be sent this could not work. The behaviour of this function
    could be different among frameworks, but it usually involves raising some kind of internal Exception,
    meaning that if you capture the exception the request blocking could not work.
    """
    if not asm_config._asm_enabled:
        log.warning("block_request() is disabled. To use this feature please enable" "Application Security Monitoring")
        return

    _asm_request_context.block_request()


def block_request_if_user_blocked(tracer: Tracer, userid: str) -> None:
    """
    Check if the specified User ID should be blocked and if positive
    block the current request using `block_request`.

    :param tracer: tracer instance to use
    :param userid: the ID of the user as registered by `set_user`
    """
    if not asm_config._asm_enabled:
        log.warning("should_block_user call requires ASM to be enabled")
        return

    if should_block_user(tracer, userid):
        span = tracer.current_root_span()
        if span:
            span.set_tag_str(user.ID, str(userid))
        _asm_request_context.block_request()


def _on_django_login(
    pin,
    request,
    user,
    mode,
    info_retriever,
):
    if not asm_config._asm_enabled:
        return

    if user:
        if str(user) != "AnonymousUser":
            user_id, user_extra = info_retriever.get_user_info()

            with pin.tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
                from ddtrace.contrib.django.compat import user_is_authenticated

                if user_is_authenticated(user):
                    session_key = getattr(request, "session_key", None)
                    track_user_login_success_event(
                        pin.tracer,
                        user_id=user_id,
                        session_id=session_key,
                        propagate=True,
                        login_events_mode=mode,
                        **user_extra,
                    )
                else:
                    # Login failed but the user exists
                    track_user_login_failure_event(pin.tracer, user_id=user_id, exists=True, login_events_mode=mode)
        else:
            # Login failed and the user is unknown
            user_id = info_retriever.get_userid()
            track_user_login_failure_event(pin.tracer, user_id=user_id, exists=False, login_events_mode=mode)


def _on_django_auth(result_user, mode, kwargs, pin, info_retriever):
    if not asm_config._asm_enabled:
        return True, result_user

    extended_userid_fields = info_retriever.possible_user_id_fields + info_retriever.possible_login_fields
    userid_list = info_retriever.possible_user_id_fields if mode == "safe" else extended_userid_fields

    for possible_key in userid_list:
        if possible_key in kwargs:
            user_id = kwargs[possible_key]
            break
    else:
        user_id = None

    if not result_user:
        with pin.tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
            track_user_login_failure_event(pin.tracer, user_id=user_id, exists=False, login_events_mode=mode)

    return False, None


core.on("django.login", _on_django_login)
core.on("django.auth", _on_django_auth)
