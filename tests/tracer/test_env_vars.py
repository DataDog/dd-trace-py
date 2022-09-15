import os
import subprocess

import pytest


@pytest.mark.parametrize(
    "env_var_name,env_var_value,expected_obfuscation_config,expected_global_query_string_obfuscation_disabled,"
    "expected_http_tag_query_string",
    [
        ("DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN", "", None, True, True),
        (
            "DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN",
            "(?i)(?:p(?:ass)?w(?:or))",
            "re.compile('(?i)(?:p(?:ass)?w(?:or))'.encode('ascii'))",
            False,
            True,
        ),
        (
            "DD_WRONG_ENV_NAME",
            "(?i)(?:p(?:ass)?w(?:or))",
            "re.compile(DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN_DEFAULT.encode('ascii'))",
            False,
            True,
        ),
        (
            "DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN",
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
from ddtrace.settings.config import DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN_DEFAULT;
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
