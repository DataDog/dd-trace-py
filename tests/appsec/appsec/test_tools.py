# -*- coding: utf-8 -*-
import pytest

from ddtrace.internal.utils.http import parse_form_params


@pytest.mark.parametrize(
    "body, res",
    [
        ("", {}),
        ("x=1", {"x": "1"}),
        ("x=1&y=2", {"x": "1", "y": "2"}),
        ("x=1&y=2&x=3", {"x": ["1", "3"], "y": "2"}),
        ("x+x=1&y=2&x=3", {"x x": "1", "x": "3", "y": "2"}),
        ("3%2B3%3D6%20toto=%C3%A9l%C3%A8phant%C3%B8%F0%9F%98%80", {"3+3=6 toto": "élèphantø😀"}),
    ],
)
def test_parse_form_params(body, res):
    form_params = parse_form_params(body)
    assert form_params == res


def test_used_id_hashing():
    from ddtrace.appsec._utils import _hash_user_id

    assert _hash_user_id("zouzou@sansgluten.com") == "anon_0c76692372ebf01a7da6e9570fb7d0a1"
