# Compile extension with Cmake

```bash
sh clean.sh
cmake -DPYTHON_EXECUTABLE:FILEPATH=FILEPATH=/usr/bin/python3.11 . && \
 make -j _native && \
 mv lib_native.so _native.so
```

## Verify compilation was correctly

```bash
python3.11
```
```python
from _native import setup, Source, TaintRange
source = Source(name="aaa", value="bbbb", origin="ccc")
source = Source("aaa", "bbbb", "ccc")
setup(bytes.join, bytearray.join)
```

## Clean Cmake folders

```bash
./clean.sh
```
