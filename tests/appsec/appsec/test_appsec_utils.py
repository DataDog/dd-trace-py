import os

import pytest

from ddtrace.internal.constants import BLOCKED_RESPONSE_HTML
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
import ddtrace.internal.utils.http as utils
from tests.utils import override_env


SECID: str = "[security_response_id]"
BLOCK_ID: str = "012038019238-123213"


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
        assert utils._get_blocked_template("text/html", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_HTML, BLOCK_ID
        )


def test_get_blocked_template_no_env_var_json():
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML="", DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON="")):
        assert utils._get_blocked_template("other", BLOCK_ID) == utils._format_template(BLOCKED_RESPONSE_JSON, BLOCK_ID)
        assert utils._get_blocked_template("application/json", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_JSON, BLOCK_ID
        )
        assert utils._get_blocked_template("", BLOCK_ID) == utils._format_template(BLOCKED_RESPONSE_JSON, BLOCK_ID)
        assert utils._get_blocked_template(None, BLOCK_ID) == utils._format_template(BLOCKED_RESPONSE_JSON, BLOCK_ID)


def test_get_blocked_template_user_file_missing_html():
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML="missing.html")):
        assert utils._get_blocked_template("text/html", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_HTML, BLOCK_ID
        )
        assert utils._get_blocked_template("", BLOCK_ID) == utils._format_template(BLOCKED_RESPONSE_JSON, BLOCK_ID)
        assert utils._get_blocked_template("application/json", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_JSON, BLOCK_ID
        )


def test_get_blocked_template_user_file_missing_json():
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON="missing.json")):
        assert utils._get_blocked_template("", BLOCK_ID) == utils._format_template(BLOCKED_RESPONSE_JSON, BLOCK_ID)
        assert utils._get_blocked_template("application/json", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_JSON, BLOCK_ID
        )
        assert utils._get_blocked_template("text/html", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_HTML, BLOCK_ID
        )


def test_get_blocked_template_user_file_exists_html():
    template_path = os.path.join(os.path.dirname(__file__), "blocking_template_html.html")
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=template_path)):
        with open(template_path, "r") as test_template_html:
            html_content = test_template_html.read()
        assert utils._get_blocked_template("text/html", BLOCK_ID) == html_content
        assert utils._get_blocked_template("", BLOCK_ID) == utils._format_template(BLOCKED_RESPONSE_JSON, BLOCK_ID)
        assert utils._get_blocked_template("application/json", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_JSON, BLOCK_ID
        )


def test_get_blocked_template_user_file_exists_json():
    template_path = os.path.join(os.path.dirname(__file__), "blocking_template_json.json")
    with override_env(dict(DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=template_path)):
        with open(template_path, "r") as test_template_json:
            json_content = utils._format_template(test_template_json.read(), BLOCK_ID)
        assert utils._get_blocked_template("", BLOCK_ID) == json_content
        assert utils._get_blocked_template("application/json", BLOCK_ID) == json_content
        assert utils._get_blocked_template("text/html", BLOCK_ID) == utils._format_template(
            BLOCKED_RESPONSE_HTML, BLOCK_ID
        )
