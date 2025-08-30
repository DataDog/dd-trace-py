Debug Symbols Packaging
=======================

dd-trace-py is built with debug symbols by default, and packaged separately from the main wheel files to reduce the size of the primary distribution packages.

Debug Symbol Files
------------------

The project generates debug symbols during the build process:

- **Linux**: `.debug` files (using `objcopy --only-keep-debug`)
- **macOS**: `.dSYM` bundles (using `dsymutil`)

These debug symbols are extracted from the main wheels and packaged into separate `.zip` files with the naming convention:

```
{original-wheel-name}-debug-symbols.zip
```

For example:
- `ddtrace-1.20.0-cp39-cp39-linux_x86_64.whl` → `ddtrace-1.20.0-cp39-cp39-linux_x86_64-debug-symbols.zip`
- `ddtrace-1.20.0-cp39-cp39-macosx_10_9_x86_64.whl` → `ddtrace-1.20.0-cp39-cp39-macosx_10_9_x86_64-debug-symbols.zip`

Build Process
-------------

The debug symbols are handled automatically during the CI build process:

1. Wheels are built with debug symbols included
2. Debug symbols are extracted using the `scripts/extract_debug_symbols.py` script
3. Debug symbols are removed from the main wheel to reduce size
4. Separate debug symbol packages are created and uploaded as artifacts

Usage
-----

To use debug symbols for debugging or crash analysis:

1. Download the appropriate debug symbol package for your platform and Python version
2. Extract the debug symbol files to the same directory as the corresponding `.so` files.
   Typically, the site-packages directory where ddtrace is installed.
3. Your debugger or crash analysis tool should automatically find the debug symbols
