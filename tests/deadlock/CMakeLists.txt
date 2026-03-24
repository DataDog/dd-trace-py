cmake_minimum_required(VERSION 3.19)

# The exact name of this extension determines the installation path, which must be kept in sync with setup.py. Default
# to "_deadlock" if not set.
set(EXTENSION_NAME
    "_deadlock"
    CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})

message(STATUS "Building extension: ${EXTENSION_NAME}")
set(CMAKE_VERBOSE_MAKEFILE ON)

find_package(
    Python3
    COMPONENTS Interpreter Development.Module
    REQUIRED)

if(NOT Python3_INCLUDE_DIRS)
    message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)
if(NOT Threads_FOUND OR NOT CMAKE_USE_PTHREADS_INIT)
    message(FATAL_ERROR "pthread-compatible library not found")
endif()

# Build the extension shared library
add_library(${EXTENSION_NAME} SHARED src/deadlock.cpp)

target_compile_features(${EXTENSION_NAME} PRIVATE cxx_std_17)

# Python headers (including internal headers like internal/pycore_frame.h) are all under Python3_INCLUDE_DIRS when the
# -dev package is installed. Mark as SYSTEM to suppress warnings from CPython internals.
target_include_directories(${EXTENSION_NAME} SYSTEM PRIVATE ${Python3_INCLUDE_DIRS})

# Platform detection (mirrors the pattern used by the _stack extension)
if(APPLE)
    target_compile_definitions(${EXTENSION_NAME} PRIVATE PL_DARWIN)
elseif(UNIX)
    target_compile_definitions(${EXTENSION_NAME} PRIVATE PL_LINUX)
endif()

# Python extensions must not have a lib- prefix or .so/.dylib suffix; the Python build system appends the ABI tag and
# correct extension.
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")

# RPATH so the extension can find its sibling shared libraries at runtime (not strictly needed here since we have no
# shared-library deps beyond libpython, but mirrors the pattern used across the project).
if(APPLE)
    set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "@loader_path" BUILD_WITH_INSTALL_RPATH TRUE)
elseif(UNIX)
    set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN" BUILD_WITH_INSTALL_RPATH TRUE)
endif()

# Link against Python3::Module and pthreads. Python3::Module provides the correct link flags per platform: - macOS:
# -undefined dynamic_lookup (do NOT link libpython directly) - Linux: no -lpython (symbols resolved at load time by the
# interpreter)
target_link_libraries(${EXTENSION_NAME} PRIVATE Python3::Module Threads::Threads)

# Install target (setup.py passes LIB_INSTALL_DIR)
if(LIB_INSTALL_DIR)
    install(
        TARGETS ${EXTENSION_NAME}
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
        RUNTIME DESTINATION ${LIB_INSTALL_DIR})
endif()
