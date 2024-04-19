Crashtracker Tests
===

These are offline tests for the crashtracker module.

Symbolization Tests
===

1. Python with ctypes
2. Python calling a .so module
3. From C
4. Python calling a .so which calls into the Python API
5. Python calling a .so which calls glibc, then the handler calls the Python API, which calls back into the module
6. From C, but without framepointers
7. From C, but it's been stripped
8. From C, but it's been stripped and without framepointers
