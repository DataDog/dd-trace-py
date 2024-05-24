import hashlib
import os
from typing import TYPE_CHECKING  # noqa:F401

import django
from django.db import connection
from django.http import HttpResponse
from django.http import JsonResponse

from ddtrace import tracer
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._trace_utils import block_request_if_user_blocked
from tests.utils import override_env


try:
    with override_env({"DD_IAST_ENABLED": "True"}):
        from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
        from ddtrace.appsec._iast._taint_tracking.aspects import decode_aspect
except ImportError:
    # Python 2 compatibility
    from operator import add as add_aspect

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
    from typing import Any  # noqa:F401


def include_view(request):
    return HttpResponse(status=200)


def path_params_view(request, year, month):
    return JsonResponse({"year": year, "month": month})


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
        # label iast_enabled_sqli_http_request_parameter
        cursor.execute(request.GET["q"])

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def sqli_http_request_header_name(request):
    key = [x for x in request.META.keys() if x == "master"][0]

    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_request_header_name
        cursor.execute(add_aspect("SELECT 1 FROM sqlite_", key))

    return HttpResponse(request.META["master"], status=200)


def sqli_http_request_header_value(request):
    value = [x for x in request.META.values() if x == "master"][0]
    with connection.cursor() as cursor:
        query = add_aspect("SELECT 1 FROM sqlite_", value)
        # label iast_enabled_sqli_http_request_header_value
        cursor.execute(query)

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def sqli_http_path_parameter(request, q_http_path_parameter):
    with override_env({"DD_IAST_ENABLED": "True"}):
        from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

    with connection.cursor() as cursor:
        query = add_aspect("SELECT 1 from ", q_http_path_parameter)
        # label iast_enabled_full_sqli_http_path_parameter
        cursor.execute(query)

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def taint_checking_enabled_view(request):
    if python_supported_by_iast():
        with override_env({"DD_IAST_ENABLED": "True"}):
            from ddtrace.appsec._iast._taint_tracking import OriginType
            from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
            from ddtrace.appsec._iast.reporter import IastSpanReporter

        def assert_origin_path(path):  # type: (Any) -> None
            assert is_pyobject_tainted(path)
            sources, tainted_ranges_to_dict = IastSpanReporter.taint_ranges_as_evidence_info(path)
            assert sources[0].origin == OriginType.PATH

    else:

        def assert_origin_path(pyobject):  # type: (Any) -> bool
            return True

        def is_pyobject_tainted(pyobject):  # type: (Any) -> bool
            return True

    # TODO: Taint request body
    # assert is_pyobject_tainted(request.body)
    assert is_pyobject_tainted(request.GET["q"])
    assert is_pyobject_tainted(request.META["QUERY_STRING"])
    assert is_pyobject_tainted(request.META["HTTP_USER_AGENT"])
    # TODO: Taint request headers
    # assert is_pyobject_tainted(request.headers["User-Agent"])
    assert_origin_path(request.path_info)
    assert_origin_path(request.path)
    assert_origin_path(request.META["PATH_INFO"])
    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def taint_checking_disabled_view(request):
    if python_supported_by_iast():
        with override_env({"DD_IAST_ENABLED": "True"}):
            from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
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


def sqli_http_request_cookie_name(request):
    key = [x for x in request.COOKIES.keys() if x == "master"][0]

    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_cookies_name
        cursor.execute(add_aspect("SELECT 1 FROM sqlite_", key))

    return HttpResponse(request.COOKIES["master"], status=200)


def sqli_http_request_cookie_value(request):
    value = [x for x in request.COOKIES.values() if x == "master"][0]

    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_cookies_value
        cursor.execute(add_aspect("SELECT 1 FROM sqlite_", value))

    return HttpResponse(request.COOKIES["master"], status=200)


def sqli_http_request_body(request):
    key = "master_key"
    if key in request.POST:
        value = request.POST[key]
    else:
        value = decode_aspect(bytes.decode, 1, request.body)
    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_body
        cursor.execute(add_aspect("SELECT 1 FROM sqlite_", value))

    return HttpResponse(value, status=200)


def view_insecure_cookies_insecure(request):
    res = HttpResponse("OK")
    res.set_cookie("insecure", "cookie", secure=False, httponly=True, samesite="Strict")
    return res


def view_insecure_cookies_secure(request):
    res = HttpResponse("OK")
    res.set_cookie("secure2", "value", secure=True, httponly=True, samesite="Strict")
    return res


def view_insecure_cookies_empty(request):
    res = HttpResponse("OK")
    res.set_cookie("insecure", "", secure=False, httponly=True, samesite="Strict")
    return res


def view_insecure_cookies_two_insecure_one_secure(request):
    res = HttpResponse("OK")
    res.set_cookie("insecure1", "cookie1", secure=False, httponly=True, samesite="Strict")
    res.set_cookie("insecure2", "cookie2", secure=True, httponly=False, samesite="Strict")
    res.set_cookie("secure3", "cookie3", secure=True, httponly=True, samesite="Strict")
    return res


def view_insecure_cookies_insecure_special_chars(request):
    res = HttpResponse("OK")
    res.set_cookie("insecure", "cookie?()43jfM;;;===value", secure=False, httponly=True, samesite="Strict")
    return res


def command_injection(request):
    value = decode_aspect(bytes.decode, 1, request.body)
    # label iast_command_injection
    os.system(add_aspect("dir -l ", value))

    return HttpResponse("OK", status=200)


def header_injection(request):
    value = decode_aspect(bytes.decode, 1, request.body)

    response = HttpResponse("OK", status=200)
    # label iast_header_injection
    response.headers["Header-Injection"] = value
    return response


def validate_querydict(request):
    qd = request.GET
    res = qd.getlist("x")
    lres = list(qd.lists())
    keys = list(qd.dict().keys())
    return HttpResponse(
        "x=%s, all=%s, keys=%s, urlencode=%s" % (str(res), str(lres), str(keys), qd.urlencode()), status=200
    )


urlpatterns = [
    handler("response-header/$", magic_header_key, name="response-header"),
    handler("body/$", body_view, name="body_view"),
    handler("weak-hash/$", weak_hash_view, name="weak_hash"),
    handler("block/$", block_callable_view, name="block"),
    handler("command-injection/$", command_injection, name="command_injection"),
    handler("header-injection/$", header_injection, name="header_injection"),
    handler("taint-checking-enabled/$", taint_checking_enabled_view, name="taint_checking_enabled_view"),
    handler("taint-checking-disabled/$", taint_checking_disabled_view, name="taint_checking_disabled_view"),
    handler("sqli_http_request_parameter/$", sqli_http_request_parameter, name="sqli_http_request_parameter"),
    handler("sqli_http_request_header_name/$", sqli_http_request_header_name, name="sqli_http_request_header_name"),
    handler("sqli_http_request_header_value/$", sqli_http_request_header_value, name="sqli_http_request_header_value"),
    handler("sqli_http_request_cookie_name/$", sqli_http_request_cookie_name, name="sqli_http_request_cookie_name"),
    handler("sqli_http_request_cookie_value/$", sqli_http_request_cookie_value, name="sqli_http_request_cookie_value"),
    handler("sqli_http_request_body/$", sqli_http_request_body, name="sqli_http_request_body"),
    handler("insecure-cookie/test_insecure_2_1/$", view_insecure_cookies_two_insecure_one_secure),
    handler("insecure-cookie/test_insecure_special/$", view_insecure_cookies_insecure_special_chars),
    handler("insecure-cookie/test_insecure/$", view_insecure_cookies_insecure),
    handler("insecure-cookie/test_secure/$", view_insecure_cookies_secure),
    handler("insecure-cookie/test_empty_cookie/$", view_insecure_cookies_empty),
    path(
        "sqli_http_path_parameter/<str:q_http_path_parameter>/",
        sqli_http_path_parameter,
        name="sqli_http_path_parameter",
    ),
    handler("validate_querydict/$", validate_querydict, name="validate_querydict"),
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
