# Rust + C++ Integration for Memory Profiler

This document describes the initial setup for migrating the memory profiler from C++ to Rust using the `cxx` crate.

## Overview

As a first step in the migration, we've added a trivial Rust function that is called from C++ via the `cxx` bridge. This demonstrates the integration pattern that will be used for the full migration.

## Changes Made

### 1. Rust Dependencies (`src/native/Cargo.toml`)
- Added `cxx = "1.0"` to dependencies
- Added `cxx-build = "1.0"` to build-dependencies

### 2. New Rust Module (`src/native/memalloc.rs`)
- Created a new module with a `cxx::bridge` that exposes a trivial function `rust_multiply_by_two()`
- This function multiplies an integer by 2 (for testing the integration)
- Uses the `ddtrace::profiling` namespace for organization

### 3. Build Script (`src/native/build.rs`)
- Added `cxx_build::bridge()` call to generate C++ headers and bridge code
- The bridge compiles `src/native/memalloc.rs` and generates:
  - C++ headers in `target/*/cxxbridge/ddtrace-native/src/native/memalloc.rs.h`
  - Bridge code compiled into the Rust library

### 4. Rust Library (`src/native/lib.rs`)
- Added `mod memalloc;` to include the new module

### 5. C++ Integration (`ddtrace/profiling/collector/_memalloc.cpp`)
- Included the generated cxx bridge header: `ddtrace-native/src/native/memalloc.rs.h`
- Added a test call to `rust_multiply_by_two()` in the `memalloc_start()` function
- The call is made with value 21, expecting result 42

### 6. CMake Build (`ddtrace/profiling/collector/CMakeLists.txt`)
- Configured paths to find cxx-generated headers
- Added logic to link against the Rust native library (`_native.so`/`_native.dylib`)
- The cxx bridge code is compiled into the Rust library, so we just need to link against it

## How the Integration Works

1. **Rust Side**: The `#[cxx::bridge]` macro in `memalloc.rs` declares functions to expose to C++
2. **Build Time**: The `cxx-build` crate generates C++ header files and a static library during the Rust build
3. **C++ Side**: The generated headers provide C++ function declarations that call into Rust
4. **Linking**: 
   - The _memalloc extension links against the cxx bridge static library
   - **macOS**: Also links directly against the Rust `_native.dylib` library
   - **Linux**: Uses `--allow-shlib-undefined` to allow undefined Rust symbols at link time; these are resolved at runtime by Python's dynamic loader (since `_native.so` is always loaded first as a Python extension)

## Building

The existing build process should handle everything automatically:

```bash
# Install/build the package (this will build Rust first, then C++ extensions)
pip install -e .
```

Or for development:

```bash
# Build just the Rust component
cd src/native
cargo build --release --features profiling

# Then build the C++ extensions
python setup.py build_ext --inplace
```

## Testing

To verify the integration works:

1. Build the package
2. Import and start the memory profiler:
   ```python
   from ddtrace.profiling.collector import _memalloc
   _memalloc.start(max_nframe=64, heap_sample_interval=1024*1024)
   ```
3. If the Rust function is called successfully, the profiler will start without errors

## Potential Build Issues

### Issue: "memalloc.rs.h: No such file or directory"

**Cause**: The cxx headers haven't been generated yet or are in a different location.

**Solutions**:
- Ensure Rust is built first: `cargo build --release --features profiling` in `src/native/`
- Check that the path in `_memalloc.cpp` matches the generated header location
- The path structure is: `cxxbridge/{crate_name}/src/native/memalloc.rs.h`

### Issue: "undefined reference to rust_multiply_by_two"

**Cause**: The cxx bridge static library or Rust symbols aren't being linked properly.

**Solutions**:
- Verify the cxx bridge static library exists: `find src/native/target*/release/build -name "libmemalloc_cxx_bridge.a"`
- Check CMake output for "Found cxx bridge static library" message
- **macOS only**: Check for "Found Rust native library" message
- **Linux**: Symbols are resolved at runtime, so check that `_native.so` is installed in `ddtrace/internal/native/`

### Issue: "lib_native.so: cannot open shared object file" (Linux only)

**Cause**: This error should NOT occur with the current implementation. If you see it, the CMakeLists.txt is incorrectly linking against the Rust library on Linux.

**Solution**: Ensure the Linux-specific code in CMakeLists.txt uses `--allow-shlib-undefined` and does NOT link against the Rust library (symbols are resolved at runtime instead).

### Platform Differences

The integration works differently on macOS vs Linux:

- **macOS**: Links directly against `_native.dylib` at build time, uses rpath for runtime loading
- **Linux**: Does NOT link against `lib_native.so` at build time; relies on Python's dynamic loader to resolve Rust symbols at runtime (since `_native.so` is always loaded first as a Python extension)

This difference is necessary because on Linux, the Rust library is renamed during installation (e.g., `_native.cpython-313-x86_64-linux-gnu.so`), making direct linking problematic.

## Next Steps

1. **Verify the build works**: Try building and running the test above
2. **Expand functionality**: Start migrating actual memory profiling logic to Rust
3. **Performance testing**: Ensure the FFI overhead is acceptable
4. **Error handling**: Add proper error propagation across the FFI boundary

## Architecture Notes

- The `cxx` crate provides zero-cost abstractions for C++/Rust interop
- Data is passed by value (int32) in this example; more complex types will need careful design
- The namespace `ddtrace::profiling` helps organize the C++ side
- Future migrations should follow this same pattern: define interface in Rust, call from C++

