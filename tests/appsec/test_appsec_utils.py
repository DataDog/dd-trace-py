import os

import pytest

from ddtrace.internal.constants import BLOCKED_RESPONSE_HTML
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
import ddtrace.internal.utils.http as utils
from tests.utils import override_env


@pytest.fixture(autouse=True)
def reset_template_caches():
    utils._HTML_BLOCKED_TEMPLATE_CACHE = None
    utils._JSON_BLOCKED_TEMPLATE_CACHE = None
    try:
        yield
    finally:
        utils._HTML_BLOCKED_TEMPLATE_CACHE = None
        utils._JSON_BLOCKED_TEMPLATE_CACHE = None


def test_get_blocked_template_no_env_var_html():
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML="", DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON="")):
        assert utils._get_blocked_template("text/html") == BLOCKED_RESPONSE_HTML


def test_get_blocked_template_no_env_var_json():
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML="", DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON="")):
        assert utils._get_blocked_template("other") == BLOCKED_RESPONSE_JSON
        assert utils._get_blocked_template("application/json") == BLOCKED_RESPONSE_JSON
        assert utils._get_blocked_template("") == BLOCKED_RESPONSE_JSON
        assert utils._get_blocked_template(None) == BLOCKED_RESPONSE_JSON


def test_get_blocked_template_user_file_missing_html():
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML="missing.html")):
        assert utils._get_blocked_template("text/html") == BLOCKED_RESPONSE_HTML
        assert utils._get_blocked_template("") == BLOCKED_RESPONSE_JSON
        assert utils._get_blocked_template("application/json") == BLOCKED_RESPONSE_JSON


def test_get_blocked_template_user_file_missing_json():
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON="missing.json")):
        assert utils._get_blocked_template("") == BLOCKED_RESPONSE_JSON
        assert utils._get_blocked_template("application/json") == BLOCKED_RESPONSE_JSON
        assert utils._get_blocked_template("text/html") == BLOCKED_RESPONSE_HTML


def test_get_blocked_template_user_file_exists_html():
    template_path = os.path.join(os.path.dirname(__file__), "blocking_template_html.html")
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=template_path)):
        with open(template_path, "r") as test_template_html:
            html_content = test_template_html.read()
        assert utils._get_blocked_template("text/html") == html_content
        assert utils._get_blocked_template("") == BLOCKED_RESPONSE_JSON
        assert utils._get_blocked_template("application/json") == BLOCKED_RESPONSE_JSON


def test_get_blocked_template_user_file_exists_json():
    template_path = os.path.join(os.path.dirname(__file__), "blocking_template_json.json")
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=template_path)):
        with open(template_path, "r") as test_template_json:
            json_content = test_template_json.read()
        assert utils._get_blocked_template("") == json_content
        assert utils._get_blocked_template("application/json") == json_content
        assert utils._get_blocked_template("text/html") == BLOCKED_RESPONSE_HTML
