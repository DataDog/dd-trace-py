"""
Class based views used for Django tests.
"""
import hashlib
import os
from typing import Any

from django.db import connection
from django.http import HttpResponse
from django.http import JsonResponse

from ddtrace import tracer
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._trace_utils import block_request_if_user_blocked


def assert_origin(parameter: Any, origin_type: Any) -> None:
    assert is_pyobject_tainted(parameter)
    sources, _ = IastSpanReporter.taint_ranges_as_evidence_info(parameter)
    assert sources[0].origin == origin_type


def index(request):
    response = HttpResponse("Hello, test app.")
    response["my-response-header"] = "my_response_value"
    return response


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
        first_post_key = list(request.POST.keys())[0]
        assert_origin(first_post_key, OriginType.PARAMETER_NAME)
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
    import bcrypt
    from django.contrib.auth.hashers import BCryptSHA256PasswordHasher

    password_django = BCryptSHA256PasswordHasher()
    obj = password_django.encode("i'm a password", bcrypt.gensalt())
    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_request_parameter
        cursor.execute(request.GET["q"] + obj + "'")

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def sqli_http_request_parameter_name_get(request):
    obj = " 1"
    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_request_parameter_name_get
        cursor.execute(list(request.GET.keys())[0] + obj)

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def sqli_http_request_parameter_name_post(request):
    obj = " 1"
    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_request_parameter_name_post
        cursor.execute(list(request.POST.keys())[0] + obj)

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def sqli_http_request_header_name(request):
    key = [x for x in request.META.keys() if x == "master"][0]

    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_request_header_name
        cursor.execute("SELECT 1 FROM sqlite_" + key)

    return HttpResponse(request.META["master"], status=200)


def sqli_http_request_header_value(request):
    value = [x for x in request.META.values() if x == "master"][0]
    with connection.cursor() as cursor:
        query = "SELECT 1 FROM sqlite_" + value
        # label iast_enabled_sqli_http_request_header_value
        cursor.execute(query)

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def sqli_http_path_parameter(request, q_http_path_parameter):
    with connection.cursor() as cursor:
        query = "SELECT 1 from " + q_http_path_parameter
        # label iast_enabled_full_sqli_http_path_parameter
        cursor.execute(query)

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def taint_checking_enabled_view(request):
    # TODO: Taint request body
    # assert is_pyobject_tainted(request.body)
    first_get_key = list(request.GET.keys())[0]
    assert is_pyobject_tainted(request.GET["q"])
    assert is_pyobject_tainted(first_get_key)
    assert is_pyobject_tainted(request.META["QUERY_STRING"])
    assert is_pyobject_tainted(request.META["HTTP_USER_AGENT"])
    # TODO: Taint request headers
    # assert is_pyobject_tainted(request.headers["User-Agent"])
    assert_origin(request.path_info, OriginType.PATH)
    assert_origin(request.path, OriginType.PATH)
    assert_origin(request.META["PATH_INFO"], OriginType.PATH)
    assert_origin(request.GET["q"], OriginType.PARAMETER)
    assert_origin(first_get_key, OriginType.PARAMETER_NAME)

    return HttpResponse(request.META["HTTP_USER_AGENT"], status=200)


def taint_checking_disabled_view(request):
    assert not is_pyobject_tainted(request.body)
    assert not is_pyobject_tainted(request.GET["q"])
    assert not is_pyobject_tainted(list(request.GET.keys())[0])
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
        cursor.execute("SELECT 1 FROM sqlite_" + key)

    return HttpResponse(request.COOKIES["master"], status=200)


def sqli_http_request_cookie_value(request):
    value = [x for x in request.COOKIES.values() if x == "master"][0]

    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_cookies_value
        cursor.execute("SELECT 1 FROM sqlite_" + value)

    return HttpResponse(request.COOKIES["master"], status=200)


def sqli_http_request_body(request):
    key = "master_key"
    if key in request.POST:
        value = request.POST[key]
    else:
        value = request.body.decode()
    with connection.cursor() as cursor:
        # label iast_enabled_sqli_http_body
        cursor.execute("SELECT 1 FROM sqlite_" + value)

    return HttpResponse(value, status=200)


def source_body_view(request):
    value = request.body.decode()
    with connection.cursor() as cursor:
        # label source_body_view
        cursor.execute("SELECT 1 FROM sqlite_master WHERE type='1'" + value)
    return HttpResponse(value, status=200)


def view_with_exception(request):
    value = request.GET["q"]
    from time import sleep_not_exists  # noqa:F401

    with connection.cursor() as cursor:
        # label value
        cursor.execute(value)
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
    value = request.body.decode()
    # label iast_command_injection
    os.system("dir -l " + value)

    return HttpResponse("OK", status=200)


def header_injection(request):
    value = request.body.decode()

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
