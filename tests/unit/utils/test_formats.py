# -*- encoding: utf-8 -*-
import pytest

from ddtrace.utils import formats


@pytest.mark.parametrize(
    "key,result",
    (
        ("123abc123abc13321ab3219dwow02ewc", True),
        ("123abc123abc13321ab3219dwow02eCc", False),
        ("12345678910111213141516171819202", True),
        ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", True),
        ("zzzzz", False),
        ("é", False),
        ("aaaaaaaaaaaaaaaaaaaaaaaaaBaaaaaa", False),
        ("", False),
        ("dwqdq", False),
        ("a" * 33, False),
    ),
)
def test_validate_api_key(key, result):
    assert result == formats.validate_api_key(key)
