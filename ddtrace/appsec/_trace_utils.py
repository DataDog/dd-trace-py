from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._utils import _hash_user_id
import ddtrace.constants as constants
from ddtrace.contrib.internal.trace_utils import set_user
from ddtrace.ext import SpanTypes
from ddtrace.ext import user
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Span
from ddtrace.trace import Tracer


log = get_logger(__name__)


def _asm_manual_keep(span: Span) -> None:
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.sampling import SamplingMechanism

    span.set_tag(constants.MANUAL_KEEP_KEY)
    # set decision maker to ASM = -5
    span.set_tag_str(SAMPLING_DECISION_TRACE_TAG_KEY, "-%d" % SamplingMechanism.APPSEC)

    # set Security propagation tag
    span.set_tag_str(APPSEC.PROPAGATION_HEADER, "02")
    span.context._meta[APPSEC.PROPAGATION_HEADER] = "02"


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
        if login_events_mode in (LOGIN_EVENTS_MODE.SDK, LOGIN_EVENTS_MODE.AUTO):
            span.set_tag_str("%s.sdk" % tag_prefix, "true")
            reported_mode = asm_config._user_event_mode
        else:
            reported_mode = login_events_mode

        mode_tag = APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE if success else APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE
        span.set_tag_str(mode_tag, reported_mode)

        tag_metadata_prefix = "%s.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, success_str)
        if metadata is not None:
            for k, v in metadata.items():
                if isinstance(v, bool):
                    str_v = "true" if v else "false"
                else:
                    str_v = str(v)
                span.set_tag_str("%s.%s" % (tag_metadata_prefix, k), str_v)

        if login:
            span.set_tag_str(f"{APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC}.{success_str}.usr.login", login)
            if login_events_mode != LOGIN_EVENTS_MODE.SDK:
                span.set_tag_str(APPSEC.USER_LOGIN_USERNAME, login)
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
        login = None if login is None else _hash_user_id(str(login))
    span = _track_user_login_common(tracer, True, metadata, login_events_mode, login, name, email, span)
    if not span:
        return
    if real_mode == LOGIN_EVENTS_MODE.ANON and isinstance(user_id, str):
        user_id = _hash_user_id(user_id)
    span.set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, real_mode)
    if login_events_mode != LOGIN_EVENTS_MODE.SDK:
        span.set_tag_str(APPSEC.USER_LOGIN_USERID, str(user_id))
    set_user(tracer, user_id or "", name, email, scope, role, session_id, propagate, span, may_block=False)
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
        if res and any(action in [WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION] for action in res.actions):
            raise BlockingException(get_blocked())


def track_user_login_failure_event(
    tracer: Tracer,
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
    if real_mode == LOGIN_EVENTS_MODE.ANON and isinstance(login, str):
        login = _hash_user_id(login)
    span = _track_user_login_common(tracer, False, metadata, login_events_mode, login)
    if not span:
        return
    if exists is not None:
        exists_str = "true" if exists else "false"
        span.set_tag_str("%s.failure.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, user.EXISTS), exists_str)
    if user_id:
        if real_mode == LOGIN_EVENTS_MODE.ANON and isinstance(user_id, str):
            user_id = _hash_user_id(user_id)
        if login_events_mode != LOGIN_EVENTS_MODE.SDK:
            span.set_tag_str(APPSEC.USER_LOGIN_USERID, str(user_id))
    span.set_tag_str("%s.failure.%s" % (APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, user.ID), str(user_id))
    span.set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, real_mode)
    # if called from the SDK, set the login, email and name
    if login_events_mode in (LOGIN_EVENTS_MODE.SDK, LOGIN_EVENTS_MODE.AUTO):
        if login:
            span.set_tag_str("%s.failure.login" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, login)
        if email:
            span.set_tag_str("%s.failure.email" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, email)
        if name:
            span.set_tag_str("%s.failure.username" % APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC, name)
    if in_asm_context():
        call_waf_callback(custom_data={"LOGIN_FAILURE": None})


def track_user_signup_event(
    tracer: Tracer,
    user_id: Optional[str],
    success: bool,
    login: Optional[str] = None,
    login_events_mode: str = LOGIN_EVENTS_MODE.SDK,
) -> None:
    span = tracer.current_root_span()
    if span:
        success_str = "true" if success else "false"
        span.set_tag_str(APPSEC.USER_SIGNUP_EVENT, success_str)
        if user_id:
            if login_events_mode == LOGIN_EVENTS_MODE.ANON and isinstance(user_id, str):
                user_id = _hash_user_id(user_id)
            span.set_tag_str(user.ID, str(user_id))
            span.set_tag_str(APPSEC.USER_SIGNUP_EVENT_USERID, str(user_id))
            span.set_tag_str(APPSEC.USER_LOGIN_USERID, str(user_id))
        if login:
            if login_events_mode == LOGIN_EVENTS_MODE.ANON and isinstance(login, str):
                login = _hash_user_id(login)
            span.set_tag_str(APPSEC.USER_SIGNUP_EVENT_USERNAME, str(login))
            span.set_tag_str(APPSEC.USER_LOGIN_USERNAME, str(login))
        _asm_manual_keep(span)

        # This is used to mark if the call was done from the SDK of the automatic login events
        if login_events_mode == LOGIN_EVENTS_MODE.SDK:
            span.set_tag_str("%s.sdk" % APPSEC.USER_SIGNUP_EVENT, "true")
        else:
            span.set_tag_str("%s.auto.mode" % APPSEC.USER_SIGNUP_EVENT_MODE, str(login_events_mode))

        return
    else:
        log.warning(
            "No root span in the current execution. Skipping track_user_signup tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )


def track_custom_event(tracer: Tracer, event_name: str, metadata: Dict[str, Any]) -> None:
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
        if isinstance(v, bool):
            str_v = "true" if v else "false"
        else:
            str_v = str(v)
        span.set_tag_str("%s.%s.%s" % (APPSEC.CUSTOM_EVENT_PREFIX, event_name, k), str_v)
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

    # Early check to avoid calling the WAF if the request is already blockedxw
    if get_blocked():
        return True

    _asm_request_context.call_waf_callback(custom_data={"REQUEST_USER_ID": str(userid)}, force_sent=True)
    return bool(get_blocked())


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


def block_request_if_user_blocked(tracer: Tracer, userid: str, mode: str = "sdk") -> None:
    """
    Check if the specified User ID should be blocked and if positive
    block the current request using `block_request`.

    This should only be called with set_user from the sdk API

    :param tracer: tracer instance to use
    :param userid: the ID of the user as registered by `set_user`
    :param mode: the mode of the login event ("sdk" by default, "auto" to simulate auto instrumentation)
    """
    if not asm_config._asm_enabled:
        log.warning("should_block_user call requires ASM to be enabled")
        return
    span = tracer.current_root_span()
    if span:
        root_span = span._local_root or span
        if mode == LOGIN_EVENTS_MODE.SDK:
            root_span.set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, LOGIN_EVENTS_MODE.SDK)
        else:
            if mode == LOGIN_EVENTS_MODE.AUTO:
                mode = asm_config._user_event_mode
            if mode == LOGIN_EVENTS_MODE.DISABLED:
                return
            if mode == LOGIN_EVENTS_MODE.ANON:
                userid = _hash_user_id(str(userid))
            root_span.set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, mode)
            root_span.set_tag_str(APPSEC.USER_LOGIN_USERID, str(userid))
        root_span.set_tag_str(user.ID, str(userid))
    if should_block_user(tracer, userid):
        _asm_request_context.block_request()


def _on_django_login(pin, request, user, mode, info_retriever, django_config):
    if user:
        from ddtrace.contrib.internal.django.compat import user_is_authenticated

        user_id, user_extra = info_retriever.get_user_info(
            login=django_config.include_user_login,
            email=django_config.include_user_email,
            name=django_config.include_user_realname,
        )
        if user_is_authenticated(user):
            with pin.tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
                session_key = getattr(getattr(request, "session", None), "session_key", None)
                track_user_login_success_event(
                    pin.tracer,
                    user_id=user_id,
                    session_id=session_key,
                    propagate=True,
                    login_events_mode=mode,
                    **user_extra,
                )
        else:
            # Login failed and the user is unknown (may exist or not)
            # DEV: DEAD CODE?
            track_user_login_failure_event(
                pin.tracer, user_id=user_id, login_events_mode=mode, login=user_extra.get("login", None)
            )


def _on_django_auth(result_user, mode, kwargs, pin, info_retriever, django_config):
    if not asm_config._asm_enabled:
        return True, result_user

    userid_list = info_retriever.possible_user_id_fields + info_retriever.possible_login_fields

    for possible_key in userid_list:
        if possible_key in kwargs:
            user_id = kwargs[possible_key]
            break
    else:
        user_id = None

    if not result_user:
        with pin.tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
            exists = info_retriever.user_exists()
            user_id_found, user_extra = info_retriever.get_user_info(
                login=django_config.include_user_login,
                email=django_config.include_user_email,
                name=django_config.include_user_realname,
            )
            if user_extra.get("login") is None:
                user_extra["login"] = user_id
            user_id = user_id_found or user_id

            track_user_login_failure_event(
                pin.tracer, user_id=user_id, login_events_mode=mode, exists=exists, **user_extra
            )

    return False, None


def get_user_info(info_retriever, django_config, kwargs={}):
    userid_list = info_retriever.possible_user_id_fields + info_retriever.possible_login_fields

    for possible_key in userid_list:
        if possible_key in kwargs:
            user_id = kwargs[possible_key]
            break
    else:
        user_id = None

    user_id_found, user_extra = info_retriever.get_user_info(
        login=True,
        email=django_config.include_user_email,
        name=django_config.include_user_realname,
    )
    if user_extra.get("login") is None and user_id:
        user_extra["login"] = user_id
    return user_id_found or user_id, user_extra


def _on_django_process(result_user, session_key, mode, kwargs, pin, info_retriever, django_config):
    if (not asm_config._asm_enabled) or mode == LOGIN_EVENTS_MODE.DISABLED:
        return
    user_id, user_extra = get_user_info(info_retriever, django_config, kwargs)
    user_login = user_extra.get("login")
    res = None
    if result_user and result_user.is_authenticated:
        span = pin.tracer.current_root_span()
        if mode == LOGIN_EVENTS_MODE.ANON:
            hash_id = ""
            if isinstance(user_id, str):
                hash_id = _hash_user_id(user_id)
                span.set_tag_str(APPSEC.USER_LOGIN_USERID, hash_id)
            if isinstance(user_login, str):
                hash_login = _hash_user_id(user_login)
                span.set_tag_str(APPSEC.USER_LOGIN_USERNAME, hash_login)
            span.set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, mode)
            set_user(pin.tracer, hash_id, propagate=True, may_block=False, span=span)
        elif mode == LOGIN_EVENTS_MODE.IDENT:
            if user_id:
                span.set_tag_str(APPSEC.USER_LOGIN_USERID, str(user_id))
            if user_login:
                span.set_tag_str(APPSEC.USER_LOGIN_USERNAME, str(user_login))
            span.set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, mode)
            set_user(
                pin.tracer,
                str(user_id),
                propagate=True,
                email=user_extra.get("email"),
                name=user_extra.get("name"),
                may_block=False,
                span=span,
            )
        if in_asm_context():
            real_mode = mode if mode != LOGIN_EVENTS_MODE.AUTO else asm_config._user_event_mode
            custom_data = {
                "REQUEST_USER_ID": str(user_id) if user_id else None,
                "REQUEST_USERNAME": user_login,
                "LOGIN_SUCCESS": real_mode,
            }
            if session_key:
                custom_data["REQUEST_SESSION_ID"] = session_key
            res = call_waf_callback(custom_data=custom_data, force_sent=True)
    elif in_asm_context() and session_key:
        res = call_waf_callback(custom_data={"REQUEST_SESSION_ID": session_key})
    if res and any(action in [WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION] for action in res.actions):
        raise BlockingException(get_blocked())


def _on_django_signup_user(django_config, pin, func, instance, args, kwargs, user, info_retriever):
    if (not asm_config._asm_enabled) or asm_config._user_event_mode == LOGIN_EVENTS_MODE.DISABLED:
        return
    user_id, user_extra = get_user_info(info_retriever, django_config)
    if user:
        span = pin.tracer.current_root_span()
        _asm_manual_keep(span)
        span.set_tag_str(APPSEC.USER_SIGNUP_EVENT_MODE, str(asm_config._user_event_mode))
        span.set_tag_str(APPSEC.USER_SIGNUP_EVENT, "true")
        if "login" in user_extra:
            login = user_extra["login"]
            if asm_config._user_event_mode == LOGIN_EVENTS_MODE.ANON:
                login = _hash_user_id(login)
            span.set_tag_str(APPSEC.USER_SIGNUP_EVENT_USERNAME, login)
            span.set_tag_str(APPSEC.USER_LOGIN_USERNAME, login)
        if user_id:
            user_id = str(user_id)
            if asm_config._user_event_mode == LOGIN_EVENTS_MODE.ANON:
                user_id = _hash_user_id(str(user_id))
            span.set_tag_str(APPSEC.USER_SIGNUP_EVENT_USERID, user_id)
            span.set_tag_str(APPSEC.USER_LOGIN_USERID, user_id)


def listen():
    core.on("django.login", _on_django_login)
    core.on("django.auth", _on_django_auth, "user")
    core.on("django.process_request", _on_django_process)
    core.on("django.create_user", _on_django_signup_user)
