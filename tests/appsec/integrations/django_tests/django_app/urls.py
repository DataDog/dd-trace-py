import django
from django.http import HttpResponse
from django.urls import path

from ddtrace import tracer
from tests.appsec.integrations.django_tests.django_app import views


# django.conf.urls.url was deprecated in django 3 and removed in django 4
if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler


def shutdown(request):
    # Endpoint used to flush traces to the agent when doing snapshots.
    tracer.shutdown()
    return HttpResponse(status=200)


urlpatterns = [
    handler(r"^$", views.index),
    # This must precede composed-view.
    handler("appsec/response-header/$", views.magic_header_key, name="response-header"),
    handler("appsec/body/$", views.body_view, name="body_view"),
    handler("appsec/view_with_exception/$", views.view_with_exception, name="view_with_exception"),
    handler("appsec/weak-hash/$", views.weak_hash_view, name="weak_hash"),
    handler("appsec/block/$", views.block_callable_view, name="block"),
    handler("appsec/command-injection/$", views.command_injection, name="command_injection"),
    handler("appsec/header-injection/$", views.header_injection, name="header_injection"),
    handler("appsec/taint-checking-enabled/$", views.taint_checking_enabled_view, name="taint_checking_enabled_view"),
    handler(
        "appsec/taint-checking-disabled/$", views.taint_checking_disabled_view, name="taint_checking_disabled_view"
    ),
    handler(
        "appsec/sqli_http_request_parameter/$", views.sqli_http_request_parameter, name="sqli_http_request_parameter"
    ),
    handler(
        "appsec/sqli_http_request_parameter_name_get/$",
        views.sqli_http_request_parameter_name_get,
        name="sqli_http_request_parameter_name_get",
    ),
    handler(
        "appsec/sqli_http_request_parameter_name_post/$",
        views.sqli_http_request_parameter_name_post,
        name="sqli_http_request_parameter_name_post",
    ),
    handler(
        "appsec/sqli_http_request_header_name/$",
        views.sqli_http_request_header_name,
        name="sqli_http_request_header_name",
    ),
    handler(
        "appsec/sqli_http_request_header_value/$",
        views.sqli_http_request_header_value,
        name="sqli_http_request_header_value",
    ),
    handler(
        "appsec/sqli_http_request_cookie_name/$",
        views.sqli_http_request_cookie_name,
        name="sqli_http_request_cookie_name",
    ),
    handler(
        "appsec/sqli_http_request_cookie_value/$",
        views.sqli_http_request_cookie_value,
        name="sqli_http_request_cookie_value",
    ),
    handler("appsec/sqli_http_request_body/$", views.sqli_http_request_body, name="sqli_http_request_body"),
    handler("appsec/source/body/$", views.source_body_view, name="source_body"),
    handler("appsec/insecure-cookie/test_insecure_2_1/$", views.view_insecure_cookies_two_insecure_one_secure),
    handler("appsec/insecure-cookie/test_insecure_special/$", views.view_insecure_cookies_insecure_special_chars),
    handler("appsec/insecure-cookie/test_insecure/$", views.view_insecure_cookies_insecure),
    handler("appsec/insecure-cookie/test_secure/$", views.view_insecure_cookies_secure),
    handler("appsec/insecure-cookie/test_empty_cookie/$", views.view_insecure_cookies_empty),
    path(
        "appsec/sqli_http_path_parameter/<str:q_http_path_parameter>/",
        views.sqli_http_path_parameter,
        name="sqli_http_path_parameter",
    ),
    handler("appsec/validate_querydict/$", views.validate_querydict, name="validate_querydict"),
    path("appsec/path-params/<int:year>/<str:month>/", views.path_params_view, name="path-params-view"),
    path("appsec/checkuser/<str:user_id>/", views.checkuser_view, name="checkuser"),
]
