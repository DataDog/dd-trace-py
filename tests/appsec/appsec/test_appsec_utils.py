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


def test_block_config_get_existing_attribute():
    """
    Test that get() returns existing attribute values.

    Regression test for PR #15042 which changed Block_config from dict to class,
    breaking Lambda integration that expects .get() method for dictionary-like access.
    """
    from ddtrace.appsec._utils import Block_config

    config = Block_config(status_code=403, type="auto", grpc_status_code=10)

    assert config.get("status_code") == 403
    assert config.get("type") == "auto"
    assert config.get("grpc_status_code") == 10


def test_block_config_get_nonexistent_attribute_default_none():
    """Test that get() returns None for nonexistent attributes when no default is provided."""
    from ddtrace.appsec._utils import Block_config

    config = Block_config()

    assert config.get("nonexistent_field") is None
    assert config.get("missing_attribute") is None


def test_block_config_get_nonexistent_attribute_with_custom_default():
    """Test that get() returns custom default for nonexistent attributes."""
    from ddtrace.appsec._utils import Block_config

    config = Block_config()

    assert config.get("nonexistent_field", "default_value") == "default_value"
    assert config.get("missing_attribute", 123) == 123
    assert config.get("another_missing", False) is False


def test_block_config_get_all_standard_attributes():
    """Test that get() works for all standard Block_config attributes."""
    from ddtrace.appsec._utils import Block_config

    config = Block_config(
        type="json",
        status_code=404,
        grpc_status_code=5,
        security_response_id="custom-block-id",
        location="/custom/location",
    )

    # Test all standard attributes
    assert config.get("block_id") == "custom-block-id"
    assert config.get("status_code") == 404
    assert config.get("grpc_status_code") == 5
    assert config.get("type") == "json"
    assert config.get("content_type") == "application/json"
    # Location should have the security_response_id replaced
    assert "/custom/location" in config.get("location")


def test_block_config_get_method_lambda_compatibility():
    """
    Test Lambda integration compatibility scenario.

    This simulates the actual error from Lambda where code expects
    dictionary-like access: block_config.get("key", default)

    Reproduces the error:
    AttributeError: 'Block_config' object has no attribute 'get'
    """
    from ddtrace.appsec._utils import Block_config

    # Simulate Lambda's get_asm_blocked_response usage
    block_config = Block_config(
        type="auto",
        status_code=403,
        grpc_status_code=10,
        security_response_id="block-001",
    )

    # Lambda code does things like: block_config.get("status_code", 403)
    status = block_config.get("status_code", 403)
    assert status == 403

    # Lambda code might check for optional fields
    custom_field = block_config.get("custom_field", "default")
    assert custom_field == "default"

    # Verify block_id is accessible
    block_id = block_config.get("block_id")
    assert block_id == "block-001"


def test_block_config_get_preserves_attribute_access():
    """Test that adding get() doesn't break normal attribute access."""
    from ddtrace.appsec._utils import Block_config

    config = Block_config(status_code=500, type="html")

    # Normal attribute access should still work
    assert config.status_code == 500
    assert config.type == "html"

    # get() method should return the same values
    assert config.get("status_code") == 500
    assert config.get("type") == "html"

    # Both access methods should return identical values
    assert config.status_code == config.get("status_code")
    assert config.type == config.get("type")
