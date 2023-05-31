# from ddtrace.appsec.iast._taint_tracking._native import setup, add_aspect
from ddtrace.appsec.iast._taint_tracking import setup
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec.iast._taint_tracking import taint_pyobject
from ddtrace.appsec.iast._taint_tracking import Source
from ddtrace.appsec.iast._taint_tracking import OriginType
from ddtrace.appsec.iast._taint_tracking._native import aspects  # noqa: F401

# Setup
setup(bytes.join, bytearray.join)


def do_join(s, iterable):
    return aspects.join_aspect(s, iterable)


# string_input = taint_pyobject(
#     b"-joiner-", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 3
# )
# it = [b"a", b"b", b"c"]
string_input = taint_pyobject(
    bytearray(b"-joiner-"), Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 3
)
it = [bytearray(b"a"), bytearray(b"b"), bytearray(b"c")]

result = do_join(string_input, it)


assert result == b"a-joiner-b-joiner-c"
ranges = get_tainted_ranges(result)
print(ranges)
assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == b"joi"
assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == b"joi"
# # print(source)
# # print(source.name)
# # print(source.value)
# # print(source.origin)
# # source = Source("aaa", "bbbb", "ccc")
# source = Source(name="aaa", value="bbbb", origin="ccc")
# print(source)
# print(source.name)
# print(source.value)
# print(source.origin)
# print(source.to_string())
# range = TaintRange(start=10, length=33, source=source)
# print(range)
# print(range.start)
# print(range.length)
# print(range.source)
# print(range.to_string())
# # range = TaintRange(start=10, length=33, source=source)
