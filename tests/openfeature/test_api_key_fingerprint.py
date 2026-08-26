import pytest

from ddtrace.internal.openfeature._agentless import api_key_fingerprint


@pytest.mark.parametrize(
    ("api_key", "expected"),
    [
        ("", "rijn_RZwTDmWjELXeEmMEb0eIIegKayGGUPNsuJweEPhlXi5"),
        ("padding-171", "rijn_053ybBRXypQt9AC6UIlqH1YCFYSV1rQl8HCDIcBZs3D"),
        ("!@#$%^𐍈한€हИ£", "rijn_eFLHeyLxwaiNs2hY16pjkjNjVSHWRgf2rlveKc8YA1K"),
        ("secret", "rijn_amLaG4Pd6h6t9VtJna81k744P1DYxGHzIJ6ECO3OOMj"),
    ],
)
def test_api_key_fingerprint_matches_clifford_v1(api_key, expected):
    assert api_key_fingerprint(api_key) == expected
    assert len(api_key_fingerprint(api_key)) == 48
