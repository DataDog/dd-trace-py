# Compile extension with Cmake

```bash
cmake -DPYTHON_EXECUTABLE:FILEPATH=FILEPATH=/usr/bin/python3.11 . && \
 make -j _taint_tracking && \
 mv lib_taint_tracking.so _taint_tracking.so
```

## Verify compilation was correctly

```bash
python3.11
```
```python
from _taint_tracking import setup
setup(bytes.join, bytearray.join)
```

## Clean Cmake folders

```bash
./clean.sh
```