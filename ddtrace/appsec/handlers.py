import json

from six import BytesIO
import xmltodict

from ddtrace import config
from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


try:
    from json import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

log = get_logger(__name__)
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _on_set_request_tags(request):
    if _is_iast_enabled():
        from ddtrace.appsec.iast._taint_tracking import OriginType
        from ddtrace.appsec.iast._taint_utils import LazyTaintDict

        return LazyTaintDict(
            request.cookies,
            origins=(OriginType.COOKIE_NAME, OriginType.COOKIE),
            override_pyobject_tainted=True,
        )


def _on_request_span_modifier(request, environ, _HAS_JSON_MIXIN, exception_type):
    req_body = None
    if config._appsec_enabled and request.method in _BODY_METHODS:
        content_type = request.content_type
        wsgi_input = environ.get("wsgi.input", "")

        # Copy wsgi input if not seekable
        if wsgi_input:
            try:
                seekable = wsgi_input.seekable()
            except AttributeError:
                seekable = False
            if not seekable:
                content_length = int(environ.get("CONTENT_LENGTH", 0))
                body = wsgi_input.read(content_length) if content_length else wsgi_input.read()
                environ["wsgi.input"] = BytesIO(body)

        try:
            if content_type == "application/json" or content_type == "text/json":
                if _HAS_JSON_MIXIN and hasattr(request, "json") and request.json:
                    req_body = request.json
                else:
                    req_body = json.loads(request.data.decode("UTF-8"))
            elif content_type in ("application/xml", "text/xml"):
                req_body = xmltodict.parse(request.get_data())
            elif hasattr(request, "form"):
                req_body = request.form.to_dict()
            else:
                # no raw body
                req_body = None
        except (
            exception_type,
            AttributeError,
            RuntimeError,
            TypeError,
            ValueError,
            JSONDecodeError,
            xmltodict.expat.ExpatError,
            xmltodict.ParsingInterrupted,
        ):
            log.warning("Failed to parse request body", exc_info=True)
        finally:
            # Reset wsgi input to the beginning
            if wsgi_input:
                if seekable:
                    wsgi_input.seek(0)
                else:
                    environ["wsgi.input"] = BytesIO(body)
    return req_body


def listen():
    core.on("flask.set_request_tags", _on_set_request_tags)
    core.on("flask.request_span_modifier", _on_request_span_modifier)
