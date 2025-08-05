from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace.appsec._iast._handlers import _iast_on_wrapped_view
from ddtrace.appsec._iast._handlers import _on_asgi_finalize_response
from ddtrace.appsec._iast._handlers import _on_django_finalize_response_pre
from ddtrace.appsec._iast._handlers import _on_django_func_wrapped
from ddtrace.appsec._iast._handlers import _on_django_patch
from ddtrace.appsec._iast._handlers import _on_django_technical_500_response
from ddtrace.appsec._iast._handlers import _on_flask_finalize_request_post
from ddtrace.appsec._iast._handlers import _on_flask_patch
from ddtrace.appsec._iast._handlers import _on_grpc_response
from ddtrace.appsec._iast._handlers import _on_pre_tracedrequest_iast
from ddtrace.appsec._iast._handlers import _on_request_init
from ddtrace.appsec._iast._handlers import _on_set_request_tags_iast
from ddtrace.appsec._iast._handlers import _on_werkzeug_render_debugger_html
from ddtrace.appsec._iast._handlers import _on_wsgi_environ
from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from ddtrace.appsec._iast._langchain import langchain_listen
from ddtrace.appsec._iast.taint_sinks.sql_injection import _on_report_sqli
from ddtrace.internal import core


def iast_listen():
    def _iast_context_end(
        ctx: core.ExecutionContext,
        _exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ):
        _iast_end_request(ctx)

    core.on("grpc.client.response.message", _on_grpc_response)
    core.on("grpc.server.response.message", _on_grpc_server_response)

    core.on("django.patch", _on_django_patch)
    core.on("django.wsgi_environ", _on_wsgi_environ, "wrapped_result")
    core.on("django.finalize_response.pre", _on_django_finalize_response_pre)
    core.on("django.func.wrapped", _on_django_func_wrapped)
    core.on("django.technical_500_response", _on_django_technical_500_response)

    core.on("flask.patch", _on_flask_patch)
    core.on("flask.request_init", _on_request_init)
    core.on("flask.set_request_tags", _on_set_request_tags_iast)
    core.on("flask.wrapped_view", _iast_on_wrapped_view, "check_kwargs")
    core.on("flask._patched_request", _on_pre_tracedrequest_iast)
    core.on("asgi.finalize_response", _on_asgi_finalize_response)
    core.on("flask.finalize_request.post", _on_flask_finalize_request_post)
    core.on("werkzeug.render_debugger_html", _on_werkzeug_render_debugger_html)

    core.on("context.ended.wsgi.__call__", _iast_context_end)
    core.on("context.ended.asgi.__call__", _iast_context_end)

    # Sink points
    core.on("db_query_check", _on_report_sqli)

    langchain_listen(core)


def _on_grpc_server_response(message):
    _on_grpc_response(message)
