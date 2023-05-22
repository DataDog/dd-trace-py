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
print(source.to_string())
range = TaintRange(start=10, length=33, source=source)
print(range)
print(range.start)
print(range.length)
print(range.source)
print(range.to_string())
# range = TaintRange(start=10, length=33, source=source)
