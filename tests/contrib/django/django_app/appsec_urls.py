import hashlib
from typing import TYPE_CHECKING

import django
from django.db import connection
from django.http import HttpResponse

from ddtrace import tracer
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec.iast._util import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec.trace_utils import block_request_if_user_blocked


# django.conf.urls.url was deprecated in django 3 and removed in django 4
if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler

if django.VERSION >= (2, 0, 0):
    from django.urls import path
else:
    from django.conf.urls import url as path


if TYPE_CHECKING:
    from typing import Any


def include_view(request):
    return HttpResponse(status=200)


def path_params_view(request, year, month):
    return HttpResponse(status=200)


def body_view(request):
    # Django >= 3
    if hasattr(request, "headers"):
        content_type = request.headers["Content-Type"]
    else:
        # Django < 3
        content_type = request.META["CONTENT_TYPE"]
    if content_type in ("application/json", "application/xml", "text/xml"):
        data = request.body
        return HttpResponse(data, status=200)
    else:
        data = request.POST
        return HttpResponse(str(dict(data)), status=200)


def weak_hash_view(request):
    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    return HttpResponse("OK", status=200)


def block_callable_view(request):
    _asm_request_context.block_request()
    return HttpResponse("OK", status=200)


def checkuser_view(request, user_id):
    block_request_if_user_blocked(tracer, user_id)
    return HttpResponse(status=200)


def sqli_http_request_parameter(request):
    with connection.cursor() as cursor:
        cursor.execute(request.GET["q"])

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def taint_checking_enabled_view(request):
    if python_supported_by_iast():
        from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    else:

        def is_pyobject_tainted(pyobject):  # type: (Any) -> bool
            return True

    # TODO: Taint request body
    # assert is_pyobject_tainted(request.body)
    assert is_pyobject_tainted(request.GET["q"])
    assert is_pyobject_tainted(request.META["QUERY_STRING"])
    assert is_pyobject_tainted(request.META["HTTP_USER_AGENT"])
    assert is_pyobject_tainted(request.headers["User-Agent"])

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def taint_checking_disabled_view(request):
    if python_supported_by_iast():
        from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    else:

        def is_pyobject_tainted(pyobject):  # type: (Any) -> bool
            return False

    assert not is_pyobject_tainted(request.body)
    assert not is_pyobject_tainted(request.GET["q"])
    assert not is_pyobject_tainted(request.META["QUERY_STRING"])
    assert not is_pyobject_tainted(request.META["HTTP_USER_AGENT"])
    assert not is_pyobject_tainted(request.headers["User-Agent"])

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def magic_header_key(request):
    # Endpoint used to block request on response headers
    res = HttpResponse(status=200)
    res["Content-Disposition"] = 'attachment; filename="MagicKey_Al4h7iCFep9s1"'
    return res


urlpatterns = [
    handler("response-header/$", magic_header_key, name="response-header"),
    handler("body/$", body_view, name="body_view"),
    handler("weak-hash/$", weak_hash_view, name="weak_hash"),
    handler("block/$", block_callable_view, name="block"),
    handler("taint-checking-enabled/$", taint_checking_enabled_view, name="taint_checking_enabled_view"),
    handler("taint-checking-disabled/$", taint_checking_disabled_view, name="taint_checking_disabled_view"),
    handler("sqli_http_request_parameter/$", sqli_http_request_parameter, name="sqli_http_request_parameter"),
]

if django.VERSION >= (2, 0, 0):
    urlpatterns += [
        path("path-params/<int:year>/<str:month>/", path_params_view, name="path-params-view"),
        path("checkuser/<str:user_id>/", checkuser_view, name="checkuser"),
    ]
else:
    urlpatterns += [
        path(r"path-params/(?P<year>[0-9]{4})/(?P<month>\w+)/$", path_params_view, name="path-params-view"),
        path(r"checkuser/(?P<user_id>\w+)/$", checkuser_view, name="checkuser"),
    ]
