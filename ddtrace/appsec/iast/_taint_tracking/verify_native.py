# from ddtrace.appsec.iast._taint_tracking._native import setup, add_aspect
from ddtrace.appsec.iast._taint_tracking import setup
from ddtrace.appsec.iast._taint_tracking.aspects import add_aspect  # noqa: F401

# Setup
setup(bytes.join, bytearray.join)
#
# # Source
print(add_aspect("a", "b"))
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
