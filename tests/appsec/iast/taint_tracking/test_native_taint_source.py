import gc

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import str_to_origin


def test_all_params_and_tostring():
    s = Source(name="name", value="val", origin=OriginType.COOKIE)
    tostr = s.to_string()
    assert tostr == repr(s)
    assert tostr == s.__repr__()
    assert tostr == str(s)
    assert tostr.startswith("Source at ")
    assert tostr.endswith(" [name=name, value=val, origin=http.request.cookie.value]")
    assert s.name == "name"
    assert s.value == "val"
    assert s.origin == OriginType.COOKIE


def test_partial_params():
    s = Source()
    assert s.name == ""
    assert s.value == ""
    assert s.origin == OriginType.PARAMETER
    assert s.to_string().endswith(" [name=, value=, origin=http.request.parameter]")

    s = Source("name")
    assert s.name == "name"
    assert s.value == ""
    assert s.origin == OriginType.PARAMETER
    assert s.to_string().endswith(" [name=name, value=, origin=http.request.parameter]")

    s = Source("name", "value")
    assert s.name == "name"
    assert s.value == "value"
    assert s.origin == OriginType.PARAMETER
    assert s.to_string().endswith(" [name=name, value=value, origin=http.request.parameter]")

    s = Source("name", origin=OriginType.BODY)
    assert s.name == "name"
    assert s.value == ""
    assert s.origin == OriginType.BODY
    assert s.to_string().endswith(" [name=name, value=, origin=http.request.body]")


def test_equals():
    s1 = Source("name", "value", OriginType.PARAMETER)
    also_s1 = Source("name", "value", OriginType.PARAMETER)
    s2 = Source("name", origin=OriginType.PARAMETER)
    s3 = Source(value="value")
    assert s1.__eq__(also_s1)
    assert s1 == also_s1

    assert s1 != s2
    assert s1 != s3
    assert s2 != s3


def test_origin_to_str_returns_valid_string():
    """
    Regression test for PyBind11 return_value_policy::reference misuse.

    CONTEXT:
    The origin_to_str() function returns std::string by VALUE (a temporary).
    Using py::return_value_policy::reference tells PyBind11 to return a reference
    to this temporary, which is undefined behavior and can cause segfaults.

    THE BUG:
    While PyBind11 may make defensive copies in some cases, using
    return_value_policy::reference for a function returning by value is:
    1. Semantically incorrect - violates PyBind11 API contract
    2. Undefined behavior - can crash depending on compiler, Python version, etc.
    3. Non-deterministic - may work in tests but fail in production

    WHY THIS TEST MAY PASS EVEN WITH BUGGY CODE:
    - PyBind11 might make defensive copies for std::string
    - Modern compilers/allocators might not reuse freed memory immediately
    - Debug builds have different memory patterns than release builds
    - The crash may only occur under memory pressure or specific timing

    WHAT WE'RE TESTING:
    This test verifies strings are properly copied and remain valid, which is
    the CORRECT behavior. The test validates that after removing the incorrect
    return policy, strings work reliably in all scenarios.
    """
    # Test all OriginType values
    origin_types = [
        (OriginType.PARAMETER, "http.request.parameter"),
        (OriginType.PARAMETER_NAME, "http.request.parameter.name"),
        (OriginType.HEADER, "http.request.header"),
        (OriginType.HEADER_NAME, "http.request.header.name"),
        (OriginType.PATH, "http.request.path"),
        (OriginType.BODY, "http.request.body"),
        (OriginType.QUERY, "http.request.query"),
        (OriginType.PATH_PARAMETER, "http.request.path.parameter"),
        (OriginType.COOKIE, "http.request.cookie.value"),
        (OriginType.COOKIE_NAME, "http.request.cookie.name"),
        (OriginType.GRPC_BODY, "http.request.grpc_body"),
    ]

    for origin_type, expected_str in origin_types:
        result = origin_to_str(origin_type)
        # Verify the string is correct
        assert result == expected_str, f"Expected {expected_str}, got {result}"
        # Verify we can perform string operations (would crash if memory was freed)
        assert len(result) > 0
        assert result.startswith("http.request.")
        # Store in a list to verify string persists
        stored_results = [origin_to_str(origin_type) for _ in range(5)]
        assert all(s == expected_str for s in stored_results)


def test_origin_to_str_multiple_calls_with_gc():
    """
    Regression test that triggers garbage collection between calls.

    This would expose use-after-free bugs if strings were returned by reference,
    as the temporary strings would be collected and accessing them would crash.
    """
    results = []
    for _ in range(100):
        # Call origin_to_str for each origin type
        for origin_type in [
            OriginType.PARAMETER,
            OriginType.HEADER,
            OriginType.BODY,
            OriginType.COOKIE,
        ]:
            result = origin_to_str(origin_type)
            results.append(result)
            # Force garbage collection to expose memory issues
            gc.collect()

    # Verify all results are still valid
    expected_results = [
        "http.request.parameter",
        "http.request.header",
        "http.request.body",
        "http.request.cookie.value",
    ] * 100

    assert results == expected_results
    # Verify we can still access the strings after GC
    for result in results:
        assert len(result) > 0
        assert "http.request." in result


def test_str_to_origin_returns_valid_enum():
    """
    Regression test for str_to_origin return value policy.

    While OriginType is an integral type and wouldn't crash with reference policy,
    this test ensures the return by value works correctly.
    """
    origin_strings = [
        ("http.request.parameter", OriginType.PARAMETER),
        ("http.request.parameter.name", OriginType.PARAMETER_NAME),
        ("http.request.header", OriginType.HEADER),
        ("http.request.header.name", OriginType.HEADER_NAME),
        ("http.request.path", OriginType.PATH),
        ("http.request.body", OriginType.BODY),
        ("http.request.query", OriginType.QUERY),
        ("http.request.path.parameter", OriginType.PATH_PARAMETER),
        ("http.request.cookie.value", OriginType.COOKIE),
        ("http.request.cookie.name", OriginType.COOKIE_NAME),
        ("http.request.grpc_body", OriginType.GRPC_BODY),
    ]

    for origin_str, expected_type in origin_strings:
        result = str_to_origin(origin_str)
        assert result == expected_type, f"Expected {expected_type}, got {result}"
        # Verify the enum value is usable
        assert isinstance(result, OriginType)


def test_origin_to_str_and_str_to_origin_roundtrip():
    """
    Regression test verifying roundtrip conversion works correctly.

    This would crash if origin_to_str returned references to temporary strings,
    because the string would be freed before str_to_origin could use it.
    """
    origin_types = [
        OriginType.PARAMETER,
        OriginType.PARAMETER_NAME,
        OriginType.HEADER,
        OriginType.HEADER_NAME,
        OriginType.PATH,
        OriginType.BODY,
        OriginType.QUERY,
        OriginType.PATH_PARAMETER,
        OriginType.COOKIE,
        OriginType.COOKIE_NAME,
        OriginType.GRPC_BODY,
    ]

    for original_type in origin_types:
        # Convert to string
        str_value = origin_to_str(original_type)
        # Force garbage collection
        gc.collect()
        # Convert back to OriginType
        converted_type = str_to_origin(str_value)
        # Verify roundtrip
        assert converted_type == original_type, f"Roundtrip failed for {original_type}"

    # Test multiple roundtrips in a loop
    for _ in range(50):
        for origin_type in origin_types:
            assert str_to_origin(origin_to_str(origin_type)) == origin_type


def test_origin_to_str_string_concatenation():
    """
    Regression test using string concatenation.

    This would crash if the returned string was a reference to freed memory,
    as concatenation would try to access the freed memory.
    """
    for origin_type in [OriginType.PARAMETER, OriginType.HEADER, OriginType.BODY]:
        result = origin_to_str(origin_type)
        # Perform string operations that would crash with invalid memory
        concatenated = "source_type:" + result
        assert "source_type:http.request." in concatenated

        # Test in f-string
        formatted = f"tag={result}"
        assert formatted.startswith("tag=http.request.")

        # Test string methods
        assert result.upper().startswith("HTTP.REQUEST.")
        assert result.split(".") == result.split(".")
        assert len(result.encode("utf-8")) > 0


def test_origin_to_str_in_tuple_construction():
    """
    Regression test for the specific usage pattern in production code.

    REAL-WORLD USAGE (from ddtrace/appsec/_iast/_metrics.py):
        telemetry.telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.IAST,
            "instrumented.source",
            1,
            (("source_type", origin_to_str(source_type)),)
        )

    This pattern creates a tuple with the string inline. With
    py::return_value_policy::reference, the temporary std::string would be
    destroyed before the tuple could copy it, leading to:
    - Segmentation faults (accessing freed memory)
    - Data corruption (string contains garbage)
    - Non-deterministic crashes (depends on memory allocator state)

    This test validates the tuple contains valid, accessible strings.
    """
    # Test the exact pattern from _metrics.py
    for _ in range(100):
        tags = (("source_type", origin_to_str(OriginType.PARAMETER)),)
        # Access the string from the tuple
        source_type = tags[0][1]
        assert source_type == "http.request.parameter"
        # Verify we can perform operations on it
        assert len(source_type) > 0
        assert source_type.startswith("http")

    # Test with multiple tags
    for _ in range(100):
        tags = (
            ("type1", origin_to_str(OriginType.PARAMETER)),
            ("type2", origin_to_str(OriginType.HEADER)),
            ("type3", origin_to_str(OriginType.COOKIE)),
        )
        # Verify all strings are valid
        assert tags[0][1] == "http.request.parameter"
        assert tags[1][1] == "http.request.header"
        assert tags[2][1] == "http.request.cookie.value"

        # Perform operations on each
        for _key, value in tags:
            assert value.startswith("http.request.")
            assert len(value.encode("utf-8")) > 0

    # Force garbage collection to expose any memory issues
    gc.collect()

    # Test dict construction pattern (also used in codebase)
    for _ in range(100):
        data = {
            "origin": origin_to_str(OriginType.BODY),
            "type": origin_to_str(OriginType.QUERY),
        }
        assert data["origin"] == "http.request.body"
        assert data["type"] == "http.request.query"

    gc.collect()
