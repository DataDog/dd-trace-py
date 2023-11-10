import flask
import flask_login

from ddtrace import Pin
from ddtrace.appsec.trace_utils import track_user_login_failure_event
from ddtrace.appsec.trace_utils import track_user_login_success_event
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...appsec._utils import _UserInfoRetriever
from ...ext import SpanTypes
from ...internal.utils import get_argument_value
from .. import trace_utils
from ..flask.wrappers import get_current_app


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return flask_login.__version__


class _FlaskLoginUserInfoRetriever(_UserInfoRetriever):
    def get_userid(self):
        if hasattr(self.user, "get_id") and not asm_config._user_model_login_field:
            return self.user.get_id()

        return super(_FlaskLoginUserInfoRetriever, self).get_userid()


def traced_login_user(func, instance, args, kwargs):
    pin = Pin._find(func, instance, get_current_app())
    ret = func(*args, **kwargs)

    try:
        mode = asm_config._automatic_login_events_mode
        if not asm_config._asm_enabled or mode == "disabled":
            return ret

        user = get_argument_value(args, kwargs, 0, "user")
        if not user:
            track_user_login_failure_event(pin.tracer, user_id=None, exists=False, login_events_mode=mode)
            return ret

        if hasattr(user, "is_anonymous") and user.is_anonymous:
            return ret

        if not isinstance(user, flask_login.UserMixin):
            log.debug(
                "Automatic Login Events Tracking: flask_login User models not inheriting from UserMixin not supported",
            )
            return ret

        info_retriever = _FlaskLoginUserInfoRetriever(user)
        user_id, user_extra = info_retriever.get_user_info()
        if user_id == -1:
            with pin.tracer.trace("flask_login.login_user", span_type=SpanTypes.AUTH):
                track_user_login_failure_event(pin.tracer, user_id="missing", exists=False, login_events_mode=mode)
            return ret
        if not user_id:
            track_user_login_failure_event(pin.tracer, user_id=None, exists=False, login_events_mode=mode)
            log.debug(
                "Automatic Login Events Tracking: Could not determine user id field user for the %s user Model; "
                "set DD_USER_MODEL_LOGIN_FIELD to the name of the field used for the user id or implement the "
                "get_id method for your model",
                type(user),
            )
            return ret

        with pin.tracer.trace("flask_login.login_user", span_type=SpanTypes.AUTH):
            session_key = flask.session.get("_id", None)
            track_user_login_success_event(
                pin.tracer,
                user_id=user_id,
                session_id=session_key,
                propagate=True,
                login_events_mode=mode,
                **user_extra,
            )
    except Exception:
        log.debug("Error while trying to trace flask_login.login_user", exc_info=True)

    return ret


def patch():
    if not asm_config._asm_enabled:
        return

    if getattr(flask_login, "_datadog_patch", False):
        return

    Pin().onto(flask_login)
    _w("flask_login", "login_user", traced_login_user)
    flask_login._datadog_patch = True


def unpatch():
    import flask_login

    if not getattr(flask_login, "_datadog_patch", False):
        return

    trace_utils.unwrap(flask_login, "login_user")
    flask_login._datadog_patch = False
