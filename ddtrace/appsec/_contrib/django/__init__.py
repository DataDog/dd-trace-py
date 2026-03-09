from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._asm_request_context import _call_waf
from ddtrace.appsec._asm_request_context import _call_waf_first
from ddtrace.appsec._asm_request_context import _get_headers_if_appsec
from ddtrace.appsec._asm_request_context import _on_context_ended
from ddtrace.appsec._asm_request_context import _set_headers_and_response
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._asm_request_context import set_block_request_callable
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._trace_utils import _asm_manual_keep
from ddtrace.appsec._trace_utils import track_user_login_failure_event
from ddtrace.appsec._trace_utils import track_user_login_success_event
from ddtrace.appsec._utils import _hash_user_id
from ddtrace.contrib.internal.trace_utils_base import set_user
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.trace import tracer


log = get_logger(__name__)


def _on_django_login(pin, request, user_obj, mode, info_retriever, django_config):
    if user_obj:
        from ddtrace.contrib.internal.django.compat import user_is_authenticated

        user_id, user_extra = info_retriever.get_user_info(
            login=django_config.include_user_login,
            email=django_config.include_user_email,
            name=django_config.include_user_realname,
        )
        if user_is_authenticated(user_obj):
            with tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
                session_key = getattr(getattr(request, "session", None), "session_key", None)
                track_user_login_success_event(
                    None,
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
                None, user_id=user_id, login_events_mode=mode, login=user_extra.get("login", None)
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
        with tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
            exists = info_retriever.user_exists()
            user_id_found, user_extra = info_retriever.get_user_info(
                login=django_config.include_user_login,
                email=django_config.include_user_email,
                name=django_config.include_user_realname,
            )
            if user_extra.get("login") is None:
                user_extra["login"] = user_id
            user_id = user_id_found or user_id

            track_user_login_failure_event(None, user_id=user_id, login_events_mode=mode, exists=exists, **user_extra)

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


def _on_django_process(result_user, session_key, mode, kwargs, info_retriever, django_config):
    if (not asm_config._asm_enabled) or mode == LOGIN_EVENTS_MODE.DISABLED:
        return
    user_id, user_extra = get_user_info(info_retriever, django_config, kwargs)
    user_login = user_extra.get("login")
    res = None
    if result_user and result_user.is_authenticated:
        span = _asm_request_context.get_entry_span()
        if span is None:
            return
        if mode == LOGIN_EVENTS_MODE.ANON:
            hash_id = ""
            if isinstance(user_id, str):
                hash_id = _hash_user_id(user_id)
                span._set_tag_str(APPSEC.USER_LOGIN_USERID, hash_id)
            if isinstance(user_login, str):
                hash_login = _hash_user_id(user_login)
                span._set_tag_str(APPSEC.USER_LOGIN_USERNAME, hash_login)
            span._set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, mode)
            set_user(None, hash_id, propagate=True, may_block=False, span=span)
        elif mode == LOGIN_EVENTS_MODE.IDENT:
            if user_id:
                span._set_tag_str(APPSEC.USER_LOGIN_USERID, str(user_id))
            if user_login:
                span._set_tag_str(APPSEC.USER_LOGIN_USERNAME, str(user_login))
            span._set_tag_str(APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, mode)
            set_user(
                None,
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


def _on_django_signup_user(django_config, pin, func, instance, args, kwargs, user_obj, info_retriever):
    if (not asm_config._asm_enabled) or asm_config._user_event_mode == LOGIN_EVENTS_MODE.DISABLED:
        return
    user_id, user_extra = get_user_info(info_retriever, django_config)
    if user_obj:
        span = _asm_request_context.get_entry_span()
        if span is None:
            return
        _asm_manual_keep(span)
        span._set_tag_str(APPSEC.USER_SIGNUP_EVENT_MODE, str(asm_config._user_event_mode))
        span._set_tag_str(APPSEC.USER_SIGNUP_EVENT, "true")
        if "login" in user_extra:
            login = user_extra["login"]
            if asm_config._user_event_mode == LOGIN_EVENTS_MODE.ANON:
                login = _hash_user_id(login)
            span._set_tag_str(APPSEC.USER_SIGNUP_EVENT_USERNAME, login)
            span._set_tag_str(APPSEC.USER_LOGIN_USERNAME, login)
        if user_id:
            user_id = str(user_id)
            if asm_config._user_event_mode == LOGIN_EVENTS_MODE.ANON:
                user_id = _hash_user_id(str(user_id))
            span._set_tag_str(APPSEC.USER_SIGNUP_EVENT_USERID, user_id)
            span._set_tag_str(APPSEC.USER_LOGIN_USERID, user_id)


def listen():
    core.on("django.login", _on_django_login)
    core.on("django.auth", _on_django_auth, "user")
    core.on("django.process_request", _on_django_process)
    core.on("django.create_user", _on_django_signup_user)

    core.on("django.start_response.post", _call_waf_first)
    core.on("django.finalize_response", _call_waf)
    core.on("django.after_request_headers", _get_headers_if_appsec, "headers")
    core.on("django.extract_body", _get_headers_if_appsec, "headers")
    core.on("django.after_request_headers.finalize", _set_headers_and_response)

    core.on("context.ended.django.traced_get_response", _on_context_ended)
    core.on("django.traced_get_response.pre", lambda block_callable, *_: set_block_request_callable(block_callable))
