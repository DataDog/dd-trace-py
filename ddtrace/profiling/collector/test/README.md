# memalloc_heap_map Unit Tests

This directory contains unit tests for the `memalloc_heap_map` C++ class.

## Prerequisites

- CMake (version 3.10 or higher)
- C++ compiler with C++11 support
- Python development headers (Python.h)
- Google Test (will be downloaded automatically by CMake)

## Building and Running the Tests

### Option 1: Using CMake directly

```bash
# Navigate to the test directory
cd ddtrace/profiling/collector/test

# Create a build directory
mkdir build
cd build

# Configure CMake
cmake ..

# Build the test executable
cmake --build .

# Run the tests
ctest --output-on-failure

# Or run the test executable directly
./test_memalloc_heap_map
```

### Option 2: Using the build script (if integrated)

If this test is integrated into the main build system, you can run:

```bash
# From the repository root
./ddtrace/internal/datadog/profiling/build_standalone.sh -t RelWithDebInfo memalloc_heap_map_test
```

## Test Coverage

The tests cover:

- **Constructor/Destructor**: Default construction and cleanup
- **size()**: Empty map and after insertions
- **insert()**: New insertions and replacing existing values
- **contains()**: Key existence checks
- **remove()**: Removing entries and handling non-existent keys
- **Iterator operations**: begin(), end(), iteration, post-increment
- **destructive_copy_from()**: Copying between maps and clearing source

## Notes

- The tests require Python to be initialized since `traceback_t` uses Python objects
- The test framework automatically initializes Python and the traceback module in `SetUp()`
- Mock traceback objects are created using the actual `traceback_t::get_traceback()` method

