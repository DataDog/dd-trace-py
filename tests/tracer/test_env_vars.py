import os
import subprocess

import pytest


@pytest.mark.parametrize(
    "env_var_name,env_var_value,expected_obfuscation_config,expected_global_query_string_disabled",
    [
        ("DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN", "", None, False),
        (
            "DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN",
            "(?i)(?:p(?:ass)?w(?:or))",
            "re.compile('(?i)(?:p(?:ass)?w(?:or))'.encode('ascii'))",
            False,
        ),
        (
            "DD_WRONG_ENV_NAME",
            "(?i)(?:p(?:ass)?w(?:or))",
            "re.compile(DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN_DEFAULT.encode('ascii'))",
            False,
        ),
        (
            "DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN",
            "(",
            None,
            True,
        ),
    ],
)
def test_obfuscation_querystring_pattern_env_var(
    env_var_name, env_var_value, expected_obfuscation_config, expected_global_query_string_disabled
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
assert config._obfuscation_query_string_pattern == %s and config.global_trace_query_string_disabled == %s
"""
                % (expected_obfuscation_config, expected_global_query_string_disabled)
            ),
        ],
        env=env,
    )
    assert b"AssertionError" not in out
