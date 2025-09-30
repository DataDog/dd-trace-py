"""
Class based views used for Django tests.
"""

import hashlib
from html import escape
import json
import os
from pathlib import Path
from pathlib import PosixPath
import pickle
import shlex
import subprocess
import time
from typing import Any
import urllib
from urllib.parse import quote

from django import VERSION as DJANGO_VERSION
from django.db import connection
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt
import requests
from requests.exceptions import ConnectionError  # noqa: A004
import urllib3
import yaml

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._trace_utils import block_request_if_user_blocked
from ddtrace.trace import tracer


if DJANGO_VERSION < (3, 2, 0):
    from unittest.mock import MagicMock

    url_has_allowed_host_and_scheme = MagicMock()
else:
    from django.utils.http import url_has_allowed_host_and_scheme


def assert_origin(parameter: Any, origin_type: Any) -> None:
    assert is_pyobject_tainted(parameter)
    sources, _ = IastSpanReporter.taint_ranges_as_evidence_info(parameter)
    assert sources[0].origin == origin_type


def _security_control_sanitizer(parameter):
    return parameter


def _security_control_validator(param1, param2, parameter_to_validate, param3):
    return None


def index(request):
    response = HttpResponse("Hello, test app.")
    response["my-response-header"] = "my_response_value"
    return response


def path_params_view(request, year, month):
    return JsonResponse({"year": year, "month": month})


def iast_enabled(request):
    """Return whether IAST request context is enabled, after an optional delay.

    This endpoint is used by concurrency tests to verify the Overhead Control Engine
    max concurrent requests behavior. It sleeps for the specified delay in ms and
    returns a JSON object with the enablement status.
    """
    try:
        delay_ms = int(request.GET.get("delay_ms", "200"))
    except ValueError:
        delay_ms = 200
    time.sleep(max(0, delay_ms) / 1000.0)
    return JsonResponse({"enabled": is_iast_request_enabled()})


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


def untrusted_serialization_yaml_load_view(request):
    """Endpoint to exercise UNTRUSTED_SERIALIZATION via yaml.load.

    Uses UnsafeLoader when available to match unsafe execution behavior.
    """
    user_input = request.GET.get("input", "")
    # label untrusted_serialization_yaml_load
    yaml.load(user_input, Loader=getattr(yaml, "UnsafeLoader", None))
    return HttpResponse("OK", status=200)


def untrusted_serialization_yaml_safe_load_view(request):
    """Endpoint using yaml.safe_load; should not report untrusted serialization."""
    user_input = request.GET.get("input", "")
    # label untrusted_serialization_yaml_safe_load
    yaml.safe_load(user_input)
    return HttpResponse("OK", status=200)


def block_callable_view(request):
    _asm_request_context.block_request()
    return HttpResponse("OK", status=200)


def checkuser_view(request, user_id):
    block_request_if_user_blocked(tracer, user_id)
    return HttpResponse(status=200)


def xss_http_request_parameter_mark_safe(request):
    user_input = request.GET.get("input", "")

    # label xss_http_request_parameter_mark_safe
    return render(request, "index.html", {"user_input": mark_safe(user_input)})


def xss_secure(request):
    user_input = request.GET.get("input", "")

    return render(request, "index.html", {"user_input": user_input})


def ospathjoin_propagation(request):
    user_input = request.GET.get("input", "")

    return HttpResponse(
        f"OK:{is_pyobject_tainted(os.path.join(user_input, user_input))}:"
        f"{is_pyobject_tainted(os.path.join(Path(user_input), Path(user_input)))}:"
        f"{is_pyobject_tainted(os.path.join(PosixPath(user_input), PosixPath(user_input)))}",
        status=200,
    )


def xss_http_request_parameter_template_safe(request):
    user_input = request.GET.get("input", "")

    # label xss_http_request_parameter_template_safe
    return render(request, "index_safe.html", {"user_input": user_input})


def xss_http_request_parameter_autoscape(request):
    user_input = request.GET.get("input", "")

    # label xss_http_request_parameter_autoscape
    return render(request, "index_autoescape.html", {"user_input": user_input})


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


def sqli_query_no_redacted(request):
    obj = request.GET["q"]
    with connection.cursor() as cursor:
        # label sqli_query_no_redacted
        cursor.execute(f"SELECT * FROM {obj} ORDER BY name")
    return HttpResponse("OK", status=200)


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


def iast_sampling(request):
    param_tainted = request.GET.get("param")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT '{param_tainted}', '1'  FROM sqlite_master")
    return HttpResponse(f"OK:{param_tainted}", status=200)


def iast_sampling_2(request):
    param_tainted = request.GET.get("param")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT '{param_tainted}', '1'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '2'  FROM sqlite_master")
    return HttpResponse(f"OK:{param_tainted}", status=200)


def iast_sampling_by_route_method(request, q_http_path_parameter):
    param_tainted = request.GET.get("param")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT '{param_tainted}', '1'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '2'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '3'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '4'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '5'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '6'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '7'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '8'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '9'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '10'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '11'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '12'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '13'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '14'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '15'  FROM sqlite_master")
        cursor.execute(f"SELECT '{param_tainted}', '16'  FROM sqlite_master")
    return HttpResponse(f"OK:{param_tainted}:{q_http_path_parameter}", status=200)


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

    # label test_django_insecure_cookie
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

    # label test_django_insecure_cookie_special_characters
    res.set_cookie("insecure", "cookie?()43jfM;;;===value", secure=False, httponly=True, samesite="Strict")
    return res


@csrf_exempt
def command_injection(request):
    # Extract value from multiple body formats to exercise taint from different sources
    # Prefer form fields, then JSON scalar, then raw body text.
    try:
        # Django >= 3 has request.headers
        if hasattr(request, "headers"):
            content_type = request.headers.get("Content-Type", "")
        else:
            content_type = request.META.get("CONTENT_TYPE", "")

        value = ""
        # Form-encoded bodies
        if content_type.startswith("application/x-www-form-urlencoded") or request.POST:
            # Use a known key if present, otherwise take the first value
            if "master_key" in request.POST:
                value = request.POST.get("master_key", "")
            elif request.POST:
                # Grab the first field's value
                first_key = next(iter(request.POST.keys()))
                value = request.POST.get(first_key, "")
        # JSON bodies
        if not value and content_type.startswith("application/json"):
            try:
                body_text = request.body.decode(errors="ignore")
                parsed = json.loads(body_text)
                if isinstance(parsed, str):
                    value = parsed
                elif isinstance(parsed, list) and parsed:
                    # Take the first value in the object if not a simple string
                    for val in parsed:
                        if val == "master":
                            value = val
                elif isinstance(parsed, dict) and parsed:
                    # Take the first value in the object if not a simple string
                    value = parsed.get("second")
                else:
                    value = body_text
            except Exception:
                value = request.body.decode(errors="ignore")
        # Raw bodies (text/plain or anything else)
        if not value:
            value = request.body.decode(errors="ignore")

    except Exception:
        value = request.body.decode(errors="ignore")

    # label iast_command_injection
    os.system("dir -l " + value)

    return HttpResponse("OK", status=200)


def command_injection_subprocess(request):
    cmd = request.POST.get("cmd", "")
    filename = "/"
    # label iast_command_injection_subprocess
    subp = subprocess.Popen(args=[cmd, "-la", filename], shell=True)
    subp.communicate()
    return HttpResponse("OK", status=200)


def return_headers(request):
    """Return all incoming request headers as JSON.

    Uses request.headers where available (Django >= 2.2), otherwise falls back to META.
    """
    headers = {}
    if hasattr(request, "headers"):
        for key, value in request.headers.items():
            headers[key] = value
    else:
        # Django < 2.2 compatibility: reconstruct headers from META
        for key, value in request.META.items():
            if key.startswith("HTTP_"):
                name = key[5:].replace("_", "-").title()
                headers[name] = value
            elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                name = key.replace("_", "-").title()
                headers[name] = value
    return JsonResponse(headers)


def vulnerable_request_downstream(request):
    """Trigger a weak-hash vulnerability, then call downstream return-headers endpoint.

    Mirrors Flask's /vulnerablerequestdownstream behavior to validate header propagation
    and IAST instrumentation under Django.
    """
    # Trigger weak hash for IAST
    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    _ = m.digest()

    port = request.GET.get("port", "8050")
    http_poolmanager = urllib3.PoolManager(num_pools=1)
    # Sending a GET request and getting back response as HTTPResponse object.
    response = http_poolmanager.request("GET", f"http://localhost:{port}/appsec/returnheaders")
    http_poolmanager.clear()

    return HttpResponse(response.data, status=200, content_type="application/json")


def untrusted_serialization_yaml_view(request):
    """Endpoint to exercise UNTRUSTED_SERIALIZATION via YAML loaders.

    Uses a tainted query parameter and yaml.unsafe_load to trigger the sink.
    """
    user_input = request.GET.get("input", "")
    # label untrusted_serialization_yaml_view
    yaml.unsafe_load(user_input)
    return HttpResponse("OK", status=200)


def untrusted_serialization_pickle_view(request):
    """Endpoint to exercise pickle.loads with user input.

    Note: We convert the string to bytes. Current IAST may not propagate taint
    through encode, so Django integration test is a smoke test (no vuln expected).
    """
    user_input = request.GET.get("input", "")
    data = user_input.encode("utf-8", "ignore")
    try:
        # label untrusted_serialization_pickle
        pickle.loads(data)
    except Exception:
        pass
    return HttpResponse("OK", status=200)


def untrusted_serialization_dill_view(request):
    """Endpoint to exercise dill.loads with user input.

    Dill is optional; if not installed, we handle gracefully. As with pickle,
    encode may drop taint, so treat as smoke test in integration.
    """
    import dill  # type: ignore

    user_input = request.GET.get("input", "")
    data = user_input.encode("utf-8", "ignore")

    try:
        # label untrusted_serialization_dill
        dill.loads(data)
    except Exception:
        pass
    return HttpResponse("OK", status=200)


def command_injection_secure_mark(request):
    value = request.body.decode()
    # label iast_command_injection
    os.system("dir -l " + shlex.quote(value))

    return HttpResponse("OK", status=200)


def command_injection_security_control(request):
    value = request.body.decode()
    _security_control_validator(None, None, value, None)
    # label iast_command_injection
    os.system("dir -l " + _security_control_sanitizer(value))

    return HttpResponse("OK", status=200)


def xss_secure_mark(request):
    value = request.body.decode()

    value_secure = escape(value)

    return render(request, "index.html", {"user_input": mark_safe(value_secure)})


@csrf_exempt
def header_injection(request):
    value = request.body.decode()

    response = HttpResponse(f"OK:{value}", status=200)
    if DJANGO_VERSION < (3, 2, 0):
        # label iast_header_injection
        response._headers["Header-Injection".lower()] = ("Header-Injection", value)

    else:
        # label iast_header_injection
        response.headers._store["Header-Injection".lower()] = ("Header-Injection", value)
    return response


def header_injection_secure(request):
    value = request.body.decode()

    response = HttpResponse("OK", status=200)
    if DJANGO_VERSION < (3, 2, 0):
        response["Header-Injection"] = value
    else:
        response.headers["Header-Injection"] = value
    return response


def unvalidated_redirect_url(request):
    value = request.GET.get("url")
    # label unvalidated_redirect_url
    return redirect(value)


def unvalidated_redirect_url_validator(request):
    value = request.GET.get("url")
    if url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}):
        return redirect(value)
    return redirect(value)


def unvalidated_redirect_path(request):
    value = request.GET.get("url")
    # label unvalidated_redirect_path
    return redirect("http://localhost:8080/" + value)


def unvalidated_redirect_safe_source_cookie(request):
    value = request.COOKIES["url"]
    # label unvalidated_redirect_safe_source_cookie
    return redirect(value)


def unvalidated_redirect_safe_source_header(request):
    value = request.META["url"]
    # label unvalidated_redirect_safe_source_header
    return redirect("http://localhost:8080/" + value)


def unvalidated_redirect_path_multiple_sources(request):
    value1 = request.GET.get("url")
    value2 = request.META["url"]
    # label unvalidated_redirect_path_multiple_sources
    return redirect(value1 + value2)


def unvalidated_redirect_url_header(request):
    value = request.GET.get("url")
    response = HttpResponse("OK", status=200)
    if DJANGO_VERSION < (3, 2, 0):
        response["Location"] = value
    else:
        response.headers["Location"] = value
    return response


def validate_querydict(request):
    qd = request.GET
    res = qd.getlist("x")
    lres = list(qd.lists())
    keys = list(qd.dict().keys())
    return HttpResponse(
        "x=%s, all=%s, keys=%s, urlencode=%s" % (str(res), str(lres), str(keys), qd.urlencode()), status=200
    )


def stacktrace_leak_view(request):
    from tests.appsec.iast.taint_sinks.test_stacktrace_leak import _load_html_django_stacktrace

    return HttpResponse(_load_html_django_stacktrace())


def stacktrace_leak_500_view(request):
    try:
        raise Exception("FooBar Exception")
    except Exception:
        import sys

        from django.views.debug import technical_500_response

        return technical_500_response(request, *sys.exc_info())


def signup(request):
    from django.contrib.auth.models import User

    login = request.GET.get("login")
    passwd = request.GET.get("pwd")
    if login and passwd:
        User.objects.create_user(username=login, password=passwd)
        return HttpResponse("OK", status=200)
    return HttpResponse("Error", status=400)


def ssrf_requests(request):
    value = request.GET.get("url")
    option = request.GET.get("option")
    try:
        if option == "path":
            # label ssrf_requests_path
            _ = requests.get(f"http://localhost:8080/{value}", timeout=1)
        elif option == "protocol":
            # label ssrf_requests_protocol
            _ = requests.get(f"{value}://localhost:8080/", timeout=1)
        elif option == "host":
            # label ssrf_requests_host
            _ = requests.get(f"http://{value}:8080/", timeout=1)
        elif option == "query":
            # label ssrf_requests_query
            _ = requests.get(f"http://localhost:8080/?{value}", timeout=1)
        elif option == "query_with_fragment":
            # label ssrf_requests_query_with_fragment
            _ = requests.get(f"http://localhost:8080/?{value}", timeout=1)
        elif option == "port":
            # label ssrf_requests_port
            _ = requests.get(f"http://localhost:{value}/", timeout=1)
        elif option == "fragment1":
            _ = requests.get(f"http://localhost:8080/#section1={value}", timeout=1)
        elif option == "fragment2":
            _ = requests.get(f"http://localhost:8080/?param1=value1&param2=value2#section2={value}", timeout=1)
        elif option == "fragment3":
            _ = requests.get(
                "http://localhost:8080/path-to-something/object_identifier?"
                f"param1=value1&param2=value2#section3={value}",
                timeout=1,
            )
        elif option == "query_param":
            _ = requests.get("http://localhost:8080/", params={"param1": value}, timeout=1)
        elif option == "urlencode_single":
            params = urllib.parse.urlencode({"key1": value})
            _ = requests.get(f"http://localhost:8080/?{params}", timeout=1)
        elif option == "urlencode_multiple":
            params = urllib.parse.urlencode({"key1": value, "key2": "static_value", "key3": "another_value"})
            _ = requests.get(f"http://localhost:8080/?{params}", timeout=1)
        elif option == "urlencode_nested":
            nested_data = {"user": value, "filters": {"type": "report", "format": "json"}}
            params = urllib.parse.urlencode({"data": json.dumps(nested_data)})
            _ = requests.get(f"http://localhost:8080/?{params}", timeout=1)
        elif option == "urlencode_with_fragment":
            params = urllib.parse.urlencode({"search": value})
            _ = requests.get(f"http://localhost:8080/?{params}#results", timeout=1)
        elif option == "urlencode_doseq":
            params = urllib.parse.urlencode({"ids": [value, "id2", "id3"]}, doseq=True)
            _ = requests.get(f"http://localhost:8080/?{params}", timeout=1)
        elif option == "safe_host":
            if url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}):
                # label ssrf_requests_safe_host
                _ = requests.get(f"http://{value}:8080/", timeout=1)
            _ = requests.get(f"http://{value}:8080/", timeout=1)
        elif option == "safe_path":
            safe_path = quote(value)
            _ = requests.get(f"http://localhost:8080/{safe_path}", timeout=1)
    except ConnectionError:
        pass
    return HttpResponse("OK", status=200)
