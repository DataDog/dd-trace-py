from _native import setup, Source, TaintRange

# Setup
setup(bytes.join, bytearray.join)

# Source

# print(source)
# print(source.name)
# print(source.value)
# print(source.origin)
# source = Source("aaa", "bbbb", "ccc")
source = Source(name="aaa", value="bbbb", origin="ccc")
print(source)
print(source.name)
print(source.value)
print(source.origin)
range = TaintRange(start=10, length=33, source=source)
print(range)
print(range.start)
print(range.length)
print(range.source)
# range = TaintRange(start=10, length=33, source=source)
