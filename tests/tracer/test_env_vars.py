import os
import subprocess

import pytest


@pytest.mark.parametrize(
    "env_var_name,env_var_value,expected_obfuscation_config,expected_global_query_string_obfuscation_disabled,"
    "expected_http_tag_query_string",
    [
        ("DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP", "", None, True, True),
        (
            "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP",
            "(?i)(?:p(?:ass)?w(?:or))",
            "re.compile('(?i)(?:p(?:ass)?w(?:or))'.encode('ascii'))",
            False,
            True,
        ),
        (
            "DD_WRONG_ENV_NAME",
            "(?i)(?:p(?:ass)?w(?:or))",
            "re.compile(DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP_DEFAULT.encode('ascii'))",
            False,
            True,
        ),
        (
            "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP",
            "(",
            None,
            False,
            False,
        ),
    ],
)
def test_obfuscation_querystring_pattern_env_var(
    env_var_name,
    env_var_value,
    expected_obfuscation_config,
    expected_global_query_string_obfuscation_disabled,
    expected_http_tag_query_string,
):
    """
    Test that obfuscation config is properly configured from env vars
    """
    env = os.environ.copy()
    env[env_var_name] = env_var_value
    out = subprocess.check_output(
        [
            "python",
            "-c",
            (
                """import re;from ddtrace import config;
from ddtrace.settings.config import DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP_DEFAULT;
assert config._obfuscation_query_string_pattern == %s;
assert config.global_query_string_obfuscation_disabled == %s;
assert config.http_tag_query_string == %s
"""
                % (
                    expected_obfuscation_config,
                    expected_global_query_string_obfuscation_disabled,
                    expected_http_tag_query_string,
                )
            ),
        ],
        env=env,
    )
    assert b"AssertionError" not in out


@pytest.mark.parametrize(
    "server_tag_query_string,client_tag_query_string,"
    "django_expected_http_tag_query_string,requests_expected_http_tag_query_string",
    [
        ("True", "true", True, True),
        (None, None, True, True),
        ("", None, True, True),
        (None, "", True, True),
        ("invalid", None, True, True),
        (None, "invalid", True, True),
        ("1", None, True, True),
        ("True", None, True, True),
        ("0", None, False, True),
        ("False", None, False, True),
        (None, "0", True, False),
        (None, "FALSE", True, False),
    ],
)
def test_tag_querystring_env_var(
    server_tag_query_string,
    client_tag_query_string,
    django_expected_http_tag_query_string,
    requests_expected_http_tag_query_string,
):
    """
    Test that query string tagging config is properly configured from env vars
    """
    env = os.environ.copy()
    if server_tag_query_string is not None:
        env["DD_HTTP_SERVER_TAG_QUERY_STRING"] = server_tag_query_string
    if client_tag_query_string is not None:
        env["DD_HTTP_CLIENT_TAG_QUERY_STRING"] = client_tag_query_string
    out = subprocess.check_output(
        [
            "python",
            "-c",
            (
                """
import os;
from ddtrace import config;
config._add(
    "requests",
    {
        "default_http_tag_query_string": os.getenv("DD_HTTP_CLIENT_TAG_QUERY_STRING", "true"),
    },
);
assert config.django.http_tag_query_string == %s;
assert config.requests.http_tag_query_string == %s
"""
                % (
                    django_expected_http_tag_query_string,
                    requests_expected_http_tag_query_string,
                )
            ),
        ],
        env=env,
    )
    assert b"AssertionError" not in out
