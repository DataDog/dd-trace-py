import json

from pylons import request
from pylons import response
from pylons.controllers import WSGIController

from ddtrace import tracer
from ddtrace.contrib.trace_utils import set_user

from ..lib.helpers import ExceptionWithCodeMethod
from ..lib.helpers import get_render_fn


class BaseController(WSGIController):
    def __call__(self, environ, start_response):
        """Invoke the Controller"""
        # WSGIController.__call__ dispatches to the Controller method
        # the request is routed to. This routing information is
        # available in environ['pylons.routes_dict']
        return WSGIController.__call__(self, environ, start_response)


class RootController(BaseController):
    """Controller used for most tests"""

    def index(self):
        return "Hello World"

    def body(self):
        result = str(request.body)
        content_type = getattr(request, "content_type", request.headers.environ.get("CONTENT_TYPE"))
        if content_type in ("application/json"):
            if hasattr(request, "json"):
                result = json.dumps(request.json)
            else:
                result = str(request.body)
        elif content_type in ("application/x-www-form-urlencoded"):
            result = json.dumps(dict(request.POST))
        return result

    def raise_exception(self):
        raise Exception("Ouch!")

    def raise_wrong_code(self):
        e = Exception("Ouch!")
        e.code = "wrong formatted code"
        raise e

    def raise_code_method(self):
        raise ExceptionWithCodeMethod("Ouch!")

    def raise_custom_code(self):
        e = Exception("Ouch!")
        e.code = "512"
        raise e

    def path_params(self, year, month):
        return "Hello World"

    def render(self):
        render = get_render_fn()
        return render("/template.mako")

    def render_exception(self):
        render = get_render_fn()
        return render("/exception.mako")

    def response_headers(self):
        response.headers["custom-header"] = "value"
        return "hi"

    def identify(self):
        set_user(
            tracer,
            user_id="usr.id",
            email="usr.email",
            name="usr.name",
            session_id="usr.session_id",
            role="usr.role",
            scope="usr.scope",
        )
        return "ok"
