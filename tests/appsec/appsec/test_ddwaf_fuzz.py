import sys

from hypothesis import given
from hypothesis import strategies as st
import pytest

from ddtrace.appsec._ddwaf.ddwaf_types import _observator
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_object


SCALAR_OBJECTS = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.characters())

PYTHON_OBJECTS = st.recursive(
    base=SCALAR_OBJECTS,
    extend=lambda inner: st.lists(inner) | st.dictionaries(SCALAR_OBJECTS, inner),
)

WRAPPER_KWARGS = dict(
    max_objects=st.integers(min_value=0, max_value=(1 << 63) - 1),
)


@given(obj=PYTHON_OBJECTS, kwargs=st.fixed_dictionaries(WRAPPER_KWARGS))
def test_ddwaf_objects_wrapper(obj, kwargs):
    obj = ddwaf_object(obj, **kwargs)
    repr(obj)
    del obj


class _AnyObject:
    cst = "1048A9B04F0EDC"

    def __str__(self):
        return self.cst


@pytest.mark.parametrize(
    "obj, res",
    [
        (32, 32),
        (True, True),
        ("test", "test"),
        (b"test", "test"),
        (1.0, 1.0),
        ([1, 2], [1, 2]),
        ([1, "a", 3.14, -3], [1, "a", 3.14, -3]),
        ({"test": "truc"}, {"test": "truc"}),
        (None, None),
        (_AnyObject(), _AnyObject.cst),
        ((1 << 64) - 1, -1),  # integers are now on 64 signed bits into the waf
        ((1 << 63) - 1, (1 << 63) - 1),
        (float("inf"), float("inf")),
    ],
)
def test_small_objects(obj, res):
    dd_obj = ddwaf_object(obj)
    assert dd_obj.struct == res


@pytest.mark.parametrize(
    "obj, res, trunc",
    [
        (324, 324, (None, None, None)),  # integers are no more formatted into strings by libddwaf and are not truncated
        (True, True, (None, None, None)),
        ("toast", "to", (5, None, None)),
        (b"toast", "to", (5, None, None)),
        (1.034, 1.034, (None, None, None)),
        ([1, 2], [1], (None, 2, None)),
        ({"toast": "touch", "tomato": "tommy"}, {"to": "to"}, (5, 2, None)),
        (None, None, (None, None, None)),
        (_AnyObject(), _AnyObject.cst[:2], (14, None, None)),
        ([[[1, 2], 3], 4], [[]], (None, 2, 20)),
    ],
)
def test_limits(obj, res, trunc):
    # truncation of max_string_length takes the last C null byte into account
    obs = _observator()
    dd_obj = ddwaf_object(obj, observator=obs, max_objects=1, max_depth=1, max_string_length=2)
    assert dd_obj.struct == res
    assert (obs.string_length, obs.container_size, obs.container_depth) == trunc


def test_vendorized_xmltodict():
    # This test ensures that the vendored xmltodict generates the same structure as package xmltodict.
    import ddtrace.vendor.xmltodict as xmltodict

    test_file = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Sample XML file for testing xmltodict -->
<root xmlns:ns="http://example.com/ns" xmlns="http://example.com/default">
    <!-- Attributes -->
    <elementWithAttributes id="123" type="example">
        Attribute testing
    </elementWithAttributes>

    <!-- Nested elements -->
    <nestedElements>
        <level1>
            <level2>
                <level3>Deeply nested content</level3>
            </level2>
        </level1>
    </nestedElements>

    <!-- Namespaces -->
    <ns:namespaceElement>
        <ns:child>Namespace content</ns:child>
    </ns:namespaceElement>

    <!-- CDATA -->
    <cdataExample><![CDATA[This is some <CDATA> content]]></cdataExample>

    <!-- Mixed content -->
    <mixedContent>
        Text before <child>child element</child> text after.
    </mixedContent>

    <!-- Empty element -->
    <emptyElement />

    <!-- List-like structure -->
    <items>
        <item>Item 1</item>
        <item>Item 2</item>
        <item>Item 3</item>
    </items>
</root>
"""
    parsed = xmltodict.parse(test_file)
    expected = {
        "root": {
            "@xmlns:ns": "http://example.com/ns",
            "@xmlns": "http://example.com/default",
            "elementWithAttributes": {"@id": "123", "@type": "example", "#text": "Attribute testing"},
            "nestedElements": {"level1": {"level2": {"level3": "Deeply nested content"}}},
            "ns:namespaceElement": {"ns:child": "Namespace content"},
            "cdataExample": "This is some <CDATA> content",
            "mixedContent": {"child": "child element", "#text": "Text before  text after."},
            "emptyElement": None,
            "items": {"item": ["Item 1", "Item 2", "Item 3"]},
        }
    }
    assert parsed == expected, f"Parsed XML does not match expected structure: {parsed} != {expected}"


if __name__ == "__main__":
    import atheris

    atheris.Setup(sys.argv, atheris.instrument_func(test_ddwaf_objects_wrapper.hypothesis.fuzz_one_input))
    atheris.Fuzz()
