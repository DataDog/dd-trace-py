from typing import Any
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import _hash_user_id
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.trace_utils_base import set_user
from ddtrace.ext import user
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

_NO_ROOT_SPAN_WARNING = (
    "No root span in the current execution. Skipping %s tags. "
    "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
    "?tab=set_user&code-lang=python for more information."
)

_BLOCKING_ACTIONS = frozenset({WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION})


def _is_blocking(res: Optional[DDWaf_result]) -> bool:
    return res is not None and not _BLOCKING_ACTIONS.isdisjoint(res.actions)


def _maybe_hash(value: Optional[str], mode: str) -> Optional[str]:
    if value is not None and mode == LOGIN_EVENTS_MODE.ANON and isinstance(value, str):
        return _hash_user_id(value)
    return value


def _asm_manual_keep(span: Span) -> None:
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.sampling import SamplingMechanism

    span._override_sampling_decision(USER_KEEP)
    # set decision maker to ASM = -5
    span._set_attribute(SAMPLING_DECISION_TRACE_TAG_KEY, f"-{SamplingMechanism.APPSEC}")

    # set Security propagation tag
    span._set_attribute(APPSEC.PROPAGATION_HEADER, "02")
    span.context._meta[APPSEC.PROPAGATION_HEADER] = "02"


def _aiguard_manual_keep(span: Span) -> None:
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.sampling import SamplingMechanism

    span._override_sampling_decision(USER_KEEP)
    # set decision maker to AI_GUARD = -13
    span._set_attribute(SAMPLING_DECISION_TRACE_TAG_KEY, f"-{SamplingMechanism.AI_GUARD}")


def _handle_metadata(entry_span: Span, prefix: str, metadata: dict) -> None:
    MAX_DEPTH = 6
    stack = [(prefix, metadata, 1)]
    while stack:
        current_prefix, data, level = stack.pop()
        if isinstance(data, list):
            if level < MAX_DEPTH:
                for i, v in enumerate(data):
                    stack.append((f"{current_prefix}.{i}", v, level + 1))
        elif isinstance(data, dict):
            if level < MAX_DEPTH:
                for k, v in data.items():
                    stack.append((f"{current_prefix}.{k}", v, level + 1))
        else:
            if isinstance(data, bool):
                data = "true" if data else "false"
            entry_span._set_attribute(current_prefix, str(data))


def _track_user_login_common(
    tracer: Any,
    success: bool,
    metadata: Optional[dict] = None,
    login_events_mode: str = LOGIN_EVENTS_MODE.SDK,
    login: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
    span: Optional[Span] = None,
) -> Optional[Span]:
    if span is None:
        span = _asm_request_context.get_entry_span()
    if not span and (current_span := core.get_span()):
        span = current_span._service_entry_span
    if span:
        success_str = "success" if success else "failure"
        tag_prefix = f"{APPSEC.USER_LOGIN_EVENT_PREFIX}.{success_str}"

        if success:
            span._set_attribute(APPSEC.USER_LOGIN_EVENT_SUCCESS_TRACK, "true")
        else:
            span._set_attribute(APPSEC.USER_LOGIN_EVENT_FAILURE_TRACK, "true")

        # This is used to mark if the call was done from the SDK of the automatic login events
        if login_events_mode in (LOGIN_EVENTS_MODE.SDK, LOGIN_EVENTS_MODE.AUTO):
            span._set_attribute(f"{tag_prefix}.sdk", "true")
            reported_mode = asm_config._user_event_mode
        else:
            reported_mode = login_events_mode

        mode_tag = APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE if success else APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE
        span._set_attribute(mode_tag, reported_mode)

        tag_metadata_prefix = f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.{success_str}"
        if metadata is not None:
            _handle_metadata(span, tag_metadata_prefix, metadata)

        if login:
            span._set_attribute(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.{success_str}.usr.login", login)
            if login_events_mode != LOGIN_EVENTS_MODE.SDK:
                span._set_attribute(APPSEC.USER_LOGIN_USERNAME, login)
            span._set_attribute(f"{tag_prefix}.login", login)

        if email:
            span._set_attribute(f"{tag_prefix}.email", email)

        if name:
            span._set_attribute(f"{tag_prefix}.username", name)

        _asm_manual_keep(span)
        return span
    else:
        log.warning(_NO_ROOT_SPAN_WARNING, "track_user_login")
    return None


def track_user_login_success_event(
    tracer: Any,
    user_id: Optional[str],
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
    real_mode = login_events_mode if login_events_mode != LOGIN_EVENTS_MODE.AUTO else asm_config._user_event_mode
    if real_mode == LOGIN_EVENTS_MODE.DISABLED:
        return
    initial_login = login
    initial_user_id = user_id
    if real_mode == LOGIN_EVENTS_MODE.ANON:
        name = email = None
        login = _maybe_hash(login, real_mode)
    span = _track_user_login_common(None, True, metadata, login_events_mode, login, name, email, span)
    if not span:
        return
    user_id = _maybe_hash(user_id, real_mode)
    span._set_attribute(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, real_mode)
    if user_id:
        if login_events_mode != LOGIN_EVENTS_MODE.SDK:
            span._set_attribute(APPSEC.USER_LOGIN_USERID, str(user_id))
        else:
            span._set_attribute(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.success.usr.id", str(user_id))
    set_user(None, user_id or "", name, email, scope, role, session_id, propagate, span, may_block=False)
    if in_asm_context():
        custom_data = {
            "REQUEST_USER_ID": str(initial_user_id) if initial_user_id else None,
            "REQUEST_USERNAME": initial_login,
            "LOGIN_SUCCESS": real_mode,
        }
        if session_id:
            custom_data["REQUEST_SESSION_ID"] = session_id
        res = call_waf_callback(
            custom_data=custom_data,
            force_sent=True,
        )
        if _is_blocking(res):
            raise BlockingException(get_blocked())


def track_user_login_failure_event(
    tracer: Any,
    user_id: Optional[str],
    exists: Optional[bool] = None,
    metadata: Optional[dict] = None,
    login_events_mode: str = LOGIN_EVENTS_MODE.SDK,
    login: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> None:
    """
    Add a new login failure tracking event.
    :param tracer: tracer instance to use
    :param user_id: a string with the UserId if exists=True or the username if not
    :param exists: a boolean indicating if the user exists in the system
    :param metadata: a dictionary with additional metadata information to be stored with the event
    """

    real_mode = login_events_mode if login_events_mode != LOGIN_EVENTS_MODE.AUTO else asm_config._user_event_mode
    if real_mode == LOGIN_EVENTS_MODE.DISABLED:
        return
    login = _maybe_hash(login, real_mode)
    span = _track_user_login_common(None, False, metadata, login_events_mode, login)
    if not span:
        return
    if exists is not None:
        exists_str = "true" if exists else "false"
        span._set_attribute(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.failure.{user.EXISTS}", exists_str)
    if user_id:
        user_id = _maybe_hash(user_id, real_mode)
        if login_events_mode != LOGIN_EVENTS_MODE.SDK:
            span._set_attribute(APPSEC.USER_LOGIN_USERID, str(user_id))
        span._set_attribute(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.failure.{user.ID}", str(user_id))
    span._set_attribute(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, real_mode)
    # if called from the SDK, set the login, email and name
    if login_events_mode in (LOGIN_EVENTS_MODE.SDK, LOGIN_EVENTS_MODE.AUTO):
        if login:
            span._set_attribute(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.failure.login", login)
        if email:
            span._set_attribute(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.failure.email", email)
        if name:
            span._set_attribute(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.failure.username", name)
    if in_asm_context():
        custom_data: dict[str, Any] = {"LOGIN_FAILURE": None}
        if login:
            custom_data["REQUEST_USERNAME"] = login
        res = call_waf_callback(custom_data=custom_data)
        if _is_blocking(res):
            raise BlockingException(get_blocked())


def track_user_signup_event(
    tracer: Any,
    user_id: Optional[str],
    success: bool,
    login: Optional[str] = None,
    login_events_mode: str = LOGIN_EVENTS_MODE.SDK,
) -> None:
    span = _asm_request_context.get_entry_span()
    if span:
        success_str = "true" if success else "false"
        span._set_attribute(APPSEC.USER_SIGNUP_EVENT, success_str)
        if user_id:
            user_id = _maybe_hash(user_id, login_events_mode)
            span._set_attribute(user.ID, str(user_id))
            span._set_attribute(APPSEC.USER_SIGNUP_EVENT_USERID, str(user_id))
            span._set_attribute(APPSEC.USER_LOGIN_USERID, str(user_id))
        if login:
            login = _maybe_hash(login, login_events_mode)
            span._set_attribute(APPSEC.USER_SIGNUP_EVENT_USERNAME, str(login))
            span._set_attribute(APPSEC.USER_LOGIN_USERNAME, str(login))
        _asm_manual_keep(span)

        # This is used to mark if the call was done from the SDK of the automatic login events
        if login_events_mode == LOGIN_EVENTS_MODE.SDK:
            span._set_attribute(f"{APPSEC.USER_SIGNUP_EVENT}.sdk", "true")
        else:
            span._set_attribute(f"{APPSEC.USER_SIGNUP_EVENT_MODE}.auto.mode", str(login_events_mode))

        return
    else:
        log.warning(_NO_ROOT_SPAN_WARNING, "track_user_signup")


def track_custom_event(tracer: Any, event_name: str, metadata: dict[str, Any]) -> None:
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

    span = _asm_request_context.get_entry_span()
    if not span:
        log.warning(_NO_ROOT_SPAN_WARNING, "track_custom_event")
        return

    span._set_attribute(f"{APPSEC.CUSTOM_EVENT_PREFIX}.{event_name}.track", "true")
    _handle_metadata(span, f"{APPSEC.CUSTOM_EVENT_PREFIX}.{event_name}", metadata)
    _asm_manual_keep(span)


def should_block_user(tracer: Any, userid: str, session_id: Optional[str] = None) -> bool:
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
    if get_blocked():
        return True
    custom_data: dict[str, Any] = {}
    if userid is not None:
        custom_data["REQUEST_USER_ID"] = str(userid)
    if session_id is not None:
        custom_data["REQUEST_SESSION_ID"] = str(session_id)
    _asm_request_context.call_waf_callback(custom_data=custom_data, force_sent=True)
    return bool(get_blocked())


def block_request() -> None:
    """
    Block the current request and return a 403 Unauthorized response. If the response
    has already been started to be sent this could not work. The behaviour of this function
    could be different among frameworks, but it usually involves raising some kind of internal Exception,
    meaning that if you capture the exception the request blocking could not work.
    """
    if not asm_config._asm_enabled:
        log.warning("block_request() is disabled. To use this feature please enable, Application Security Monitoring")
        return

    _asm_request_context.block_request()


def block_request_if_user_blocked(userid: str, mode: str = "sdk", session_id: Optional[str] = None) -> None:
    """
    Check if the specified User ID should be blocked and if positive
    block the current request using `block_request`.

    This should only be called with set_user from the sdk API

    :param userid: the ID of the user as registered by `set_user`
    :param mode: the mode of the login event ("sdk" by default, "auto" to simulate auto instrumentation)
    """
    if not asm_config._asm_enabled:
        if mode != LOGIN_EVENTS_MODE.AUTO:
            log.warning("should_block_user call requires ASM to be enabled")
        return
    if mode == LOGIN_EVENTS_MODE.AUTO:
        mode = asm_config._user_event_mode
    if mode == LOGIN_EVENTS_MODE.DISABLED:
        return
    entry_span = _asm_request_context.get_entry_span()
    if entry_span:
        entry_span._set_attribute(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, mode)
        if userid:
            if mode == LOGIN_EVENTS_MODE.ANON:
                userid = _hash_user_id(str(userid))
            if mode != LOGIN_EVENTS_MODE.SDK:
                entry_span._set_attribute(APPSEC.USER_LOGIN_USERID, str(userid))
            entry_span._set_attribute(user.ID, str(userid))
    if should_block_user(None, userid, session_id):
        _asm_request_context.block_request()
