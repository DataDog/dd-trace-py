from inspect import unwrap
import weakref

import flask
import werkzeug
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import NotFound

from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.packages import get_version_for_package
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_blocked


# Not all versions of flask/werkzeug have this mixin
try:
    from werkzeug.wrappers.json import JSONMixin

    _HAS_JSON_MIXIN = True
except ImportError:
    _HAS_JSON_MIXIN = False

# DispatcherMiddleware has shipped since werkzeug 1.0 (below our floor); guard the import for safety only.
try:
    from werkzeug.middleware.dispatcher import DispatcherMiddleware as _DispatcherMiddleware
except ImportError:
    _DispatcherMiddleware = None  # type: ignore[assignment,misc]

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.contrib.internal.wsgi.wsgi import _DDWSGIMiddlewareBase
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.utils.version import parse_version

from .wrappers import _wrap_call_with_tracing_check
from .wrappers import simple_call_wrapper
from .wrappers import with_tracing_enabled
from .wrappers import wrap_function
from .wrappers import wrap_view


log = get_logger(__name__)

FLASK_VERSION = "flask.version"
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Configure default configuration
config._add(
    "flask",
    dict(
        # Flask service configuration
        _default_service=schematize_service_name("flask"),
        collect_view_args=True,
        distributed_tracing_enabled=True,
        template_default_name="<memory>",
        trace_signals=True,
    ),
)


def get_version() -> str:
    return get_version_for_package("flask")


def _supported_versions() -> dict[str, str]:
    return {"flask": ">=1.1.4"}


def get_werkzeug_version() -> str:
    return get_version_for_package("werkzeug")


if _HAS_JSON_MIXIN:

    class RequestWithJson(werkzeug.Request, JSONMixin):
        pass

    _RequestType = RequestWithJson
else:
    _RequestType = werkzeug.Request

# Extract flask version into a tuple e.g. (0, 12, 1) or (1, 0, 2)
# DEV: This makes it so we can do `if flask_version >= (0, 12, 0):`
# DEV: Example tests:
#      (0, 10, 0) > (0, 10)
#      (0, 10, 0) >= (0, 10, 0)
#      (0, 10, 1) >= (0, 10)
#      (0, 11, 1) >= (0, 10)
#      (0, 11, 1) >= (0, 10, 2)
#      (1, 0, 0) >= (0, 10)
#      (0, 9) == (0, 9)
#      (0, 9, 0) != (0, 9)
#      (0, 8, 5) <= (0, 9)
flask_version_str = get_version()
flask_version = parse_version(flask_version_str)

werkzeug_version_str = get_werkzeug_version()
werkzeug_version = parse_version(werkzeug_version_str)


class _FlaskWSGIMiddleware(_DDWSGIMiddlewareBase):
    _request_call_name = schematize_url_operation("flask.request", protocol="http", direction=SpanDirection.INBOUND)
    _application_call_name = "flask.application"
    _response_call_name = "flask.response"

    def _wrapped_start_response(self, start_response, ctx, status_code, headers, exc_info=None):
        core.dispatch("flask.start_response.pre", (flask.request, ctx, config.flask, status_code, headers))
        if not get_blocked():
            core.dispatch("flask.start_response", ("Flask",))
            if block_config := get_blocked():
                # response code must be set here, or it will be too late
                result_content = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                    "flask.block.request.content", ()
                ).block_requested
                if result_content:
                    _, status, response_headers = result_content.value
                    result = start_response