import falcon
import wrapt

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version

from .middleware import TraceMiddleware


log = get_logger(__name__)

FALCON_VERSION = parse_version(falcon.__version__)

# Position of `middleware` in the App/API initializer, after media_type, request_type and response_type.
_MIDDLEWARE_ARG_POS = 3

config._add(
    "falcon",
    dict(
        distributed_tracing=asbool(env.get("DD_FALCON_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return getattr(falcon, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"falcon": ">=3.0"}


def patch():
    """
    Patch falcon.App to include contrib.falcon.TraceMiddleware
    by default
    """
    if getattr(falcon, "_datadog_patch", False):
        return

    falcon._datadog_patch = True
    # falcon.API and falcon.asgi.App delegate to App.__init__, so wrapping it alone
    # instruments every application exactly once.
    if FALCON_VERSION >= (3, 0, 0):
        wrapt.wrap_function_wrapper("falcon", "App.__init__", traced_init)
    else:
        wrapt.wrap_function_wrapper("falcon", "API.__init__", traced_init)


def _as_middleware_list(middleware):
    # Same normalization as Falcon: falsy means no middleware, a non-iterable is a single component.
    if not middleware:
        return []
    try:
        return list(middleware)
    except TypeError:
        return [middleware]


def traced_init(wrapped, instance, args, kwargs):
    if getattr(instance, "_ASGI", False):
        # falcon.asgi.App rejects middleware whose methods are not coroutines, which
        # TraceMiddleware's are not, so leave ASGI apps uninstrumented instead of failing to start.
        log.debug("Falcon ASGI applications are not instrumented by the falcon integration")
        return wrapped(*args, **kwargs)

    middleware = get_argument_value(args, kwargs, _MIDDLEWARE_ARG_POS, "middleware", optional=True)
    # Build a new list rather than inserting into the caller's, which may be shared between apps.
    middleware = [TraceMiddleware(), *_as_middleware_list(middleware)]
    args, kwargs = set_argument_value(args, kwargs, _MIDDLEWARE_ARG_POS, "middleware", middleware, override_unset=True)
    return wrapped(*args, **kwargs)
