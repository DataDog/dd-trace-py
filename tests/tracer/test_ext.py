from ddtrace.ext import aws


def test_flatten_dict():
    """Ensure that flattening of a nested dict results in a normalized, 1-level dict"""
    d = dict(A=1, B=2, C=dict(A=3, B=4, C=dict(A=5, B=6)))
    e = dict(A=1, B=2, C_A=3, C_B=4, C_C_A=5, C_C_B=6)
    assert aws._flatten_dict(d, sep="_") == e
