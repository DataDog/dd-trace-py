import os

import django
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.internal.schema.span_attribute_schema import _DEFAULT_SPAN_SERVICE_NAMES
from tests.utils import assert_span_http_status_code


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


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.django_db
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_service_names(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_service_name = {None: "django", "v0": "django", "v1": _DEFAULT_SPAN_SERVICE_NAMES["v1"]}[schema_version]
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
