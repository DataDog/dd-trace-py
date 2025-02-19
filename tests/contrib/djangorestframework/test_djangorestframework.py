import os

import django
import pytest

from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import USER_KEEP
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.django_db
def test_trace_exceptions(client, test_spans):  # noqa flake8 complains about shadowing test_spans
    response = client.get("/users/")

    # Our custom exception handler is setting the status code to 500
    assert response.status_code == 500

    sp = test_spans.get_root_span()
    assert sp.name == "django.request"
    if django.VERSION >= (2, 2, 0):
        assert sp.resource == "GET ^users/$"
    else:
        assert sp.resource == "GET tests.contrib.djangorestframework.app.views.UserViewSet"
    assert sp.error == 1
    assert sp.span_type == "web"
    assert_span_http_status_code(sp, 500)
    assert sp.get_tag("http.method") == "GET"
    assert sp.get_tag("component") == "django"
    assert sp.get_tag("span.kind") == "server"

    # the DRF integration should set the traceback on the django.view.dispatch span
    # (as it's the current span when the exception info is set)
    view_dispatch_spans = list(test_spans.filter_spans(name="django.view.dispatch"))
    assert len(view_dispatch_spans) == 1
    err_span = view_dispatch_spans[0]
    assert err_span.error == 1
    assert err_span.get_tag(ERROR_MSG) == "Authentication credentials were not provided."
    assert "NotAuthenticated" in err_span.get_tag("error.stack")
    assert err_span.get_tag("component") == "django"


@pytest.mark.parametrize(
    "test_endpoint",
    [
        {"endpoint": "/users/", "status_code": "500", "resource_name": "GET ^users/$"},
        {"endpoint": "/other", "status_code": "404", "resource_name": "GET 404"},
    ],
)
@pytest.mark.parametrize("inferred_proxy_enabled", [False, True])
def test_inferred_spans_api_gateway_default(client, test_spans, test_endpoint, inferred_proxy_enabled):
    with override_global_config(dict(_inferred_proxy_services_enabled=inferred_proxy_enabled)):
        # must be in this form to override headers
        default_headers = {
            "HTTP_X_DD_PROXY": "aws-apigateway",
            "HTTP_X_DD_PROXY_REQUEST_TIME_MS": "1736973768000",
            "HTTP_X_DD_PROXY_PATH": "/",
            "HTTP_X_DD_PROXY_HTTPMETHOD": "GET",
            "HTTP_X_DD_PROXY_DOMAIN_NAME": "local",
            "HTTP_X_DD_PROXY_STAGE": "stage",
        }

        distributed_headers = {
            "HTTP_X_DD_PROXY": "aws-apigateway",
            "HTTP_X_DD_PROXY_REQUEST_TIME_MS": "1736973768000",
            "HTTP_X_DD_PROXY_PATH": "/",
            "HTTP_X_DD_PROXY_HTTPMETHOD": "GET",
            "HTTP_X_DD_PROXY_DOMAIN_NAME": "local",
            "HTTP_X_DD_PROXY_STAGE": "stage",
            "HTTP_X_DATADOG_TRACE_ID": "1",
            "HTTP_X_DATADOG_PARENT_ID": "2",
            "HTTP_X_DATADOG_ORIGIN": "rum",
            "HTTP_X_DATADOG_SAMPLING_PRIORITY": "2",
        }

        for headers in [default_headers, distributed_headers]:
            test_spans.reset()
            client.get(test_endpoint["endpoint"], **headers)
            traces = test_spans.spans
            if inferred_proxy_enabled is False:
                web_span = traces[0]
                assert web_span._parent is None
                if headers == distributed_headers:
                    web_span.assert_matches(
                        name="django.request",
                        trace_id=1,
                        parent_id=2,
                        metrics={
                            _SAMPLING_PRIORITY_KEY: USER_KEEP,
                        },
                    )
            else:
                aws_gateway_span = traces[0]
                web_span = traces[1]
                assert_web_and_inferred_aws_api_gateway_span_data(
                    aws_gateway_span,
                    web_span,
                    web_span_name="django.request",
                    web_span_component="django",
                    web_span_service_name="django",
                    web_span_resource=test_endpoint["resource_name"],
                    api_gateway_service_name="local",
                    api_gateway_resource="GET /",
                    method="GET",
                    status_code=test_endpoint["status_code"],
                    url="local/",
                    start=1736973768,
                    is_distributed=headers == distributed_headers,
                    distributed_trace_id=1,
                    distributed_parent_id=2,
                    distributed_sampling_priority=USER_KEEP,
                )


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.django_db
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_service_names(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_service_name = {None: "django", "v0": "django", "v1": DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME}[
        schema_version
    ]
    code = """
import pytest
import sys
from tests.contrib.djangorestframework.conftest import *

def test(client, test_spans):
    response = client.get("/users/")

    # Our custom exception handler is setting the status code to 500
    assert response.status_code == 500

    sp = test_spans.get_root_span()
    assert sp.service == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service_name
    )
    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.django_db
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_operation_names(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_operation_name = {None: "django.request", "v0": "django.request", "v1": "http.server.request"}[
        schema_version
    ]
    code = """
import pytest
import sys
from tests.contrib.djangorestframework.conftest import *

def test(client, test_spans):
    response = client.get("/users/")

    # Our custom exception handler is setting the status code to 500
    assert response.status_code == 500

    sp = test_spans.get_root_span()
    assert sp.name == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_operation_name
    )
    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)
