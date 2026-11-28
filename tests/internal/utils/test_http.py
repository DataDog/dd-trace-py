from ddtrace.internal.utils.http import w3c_tracestate_add_p


def test_w3c_tracestate_add_p_enforces_dd_member_256_limit():
    """``w3c_tracestate_add_p`` must not push the ``dd=`` list-member past the
    W3C 256-character limit when the parent-id field is appended. Optional
    ``t.*`` fields are dropped as whole fields so the remaining trace state
    stays parseable instead of being rejected by W3C consumers.

    Regression test for https://github.com/DataDog/dd-trace-py/issues/20810
    """
    before = "dd=s:2;t.example:" + "x" * 240
    after = w3c_tracestate_add_p(before, 0x1C8)
    value = after[len("dd=") :]
    assert len(value) <= 256, value
    # mandatory datadog fields are retained
    assert value.startswith("p:00000000000001c8"), value
    assert "s:2" in value, value
    # optional propagated field dropped as a whole field to fit the limit
    assert "t.example" not in value, value
    # remaining value stays a parseable semicolon-separated key:value list
    assert all(":" in field for field in value.split(";")), value


def test_w3c_tracestate_add_p_short_value_unchanged():
    """When there is room, the parent-id field is added without dropping anything."""
    before = "dd=s:2;o:rag"
    after = w3c_tracestate_add_p(before, 0x1C8)
    assert after == "dd=p:00000000000001c8;s:2;o:rag", after
