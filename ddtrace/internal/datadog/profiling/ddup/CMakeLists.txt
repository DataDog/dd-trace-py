cmake_minimum_required(VERSION 3.19)

# This builds a C-extension for Python using Cython and a native dependency.  The exact name of the extension has
# consequences for the build path, which is in turn used by setup.py to build the wheel. Otherwise, we have to propagate
# a lot of state all around. Thus, we use the same name as the Python package (i.e., the caller sets EXTENSION_NAME)
# ddup is used by a default for standalone/test builds.
set(EXTENSION_NAME
    "_ddup.so"
    CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})
message(STATUS "Building extension: ${EXTENSION_NAME}")

# Set verbose mode so compiler and args are shown
set(CMAKE_VERBOSE_MAKEFILE ON)

# Get the cmake modules for this project
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../cmake")
include(AnalysisFunc)

# Link against the pre-built libdd_wrapper shared library The library is built by setup.py and its location is passed
# via NATIVE_EXTENSION_LOCATION
set(DD_WRAPPER_LIB "${NATIVE_EXTENSION_LOCATION}/../datadog/profiling/libdd_wrapper${EXTENSION_SUFFIX}")

# Import the pre-built library as an imported target
add_library(dd_wrapper SHARED IMPORTED)
set_target_properties(
    dd_wrapper PROPERTIES IMPORTED_LOCATION "${DD_WRAPPER_LIB}" INTERFACE_INCLUDE_DIRECTORIES
                                                                "${CMAKE_CURRENT_SOURCE_DIR}/../dd_wrapper/include")

find_package(Python3 COMPONENTS Interpreter Development)

# Make sure we have necessary Python variables
if(NOT Python3_INCLUDE_DIRS)
    message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

# If we still don't have a Python executable, we can't continue
if(NOT Python3_EXECUTABLE)
    message(FATAL_ERROR "Python executable not found")
endif()

# This sets some parameters for the target build
set(ENV{PY_MAJOR_VERSION} ${Python3_VERSION_MAJOR})
set(ENV{PY_MINOR_VERSION} ${Python3_VERSION_MINOR})
set(ENV{PY_MICRO_VERSION} ${Python3_VERSION_PATCH})

# Cythonize the .pyx file
set(DDUP_CPP_SRC ${CMAKE_CURRENT_BINARY_DIR}/_ddup.cpp)
add_custom_command(
    OUTPUT ${DDUP_CPP_SRC}
    COMMAND ${Python3_EXECUTABLE} -m cython ${CMAKE_CURRENT_LIST_DIR}/_ddup.pyx -o ${DDUP_CPP_SRC}
    DEPENDS ${CMAKE_CURRENT_LIST_DIR}/_ddup.pyx)

# Specify the target C-extension that we want to build
add_library(${EXTENSION_NAME} SHARED ${DDUP_CPP_SRC})

add_ddup_config(${EXTENSION_NAME})
# Cython generates code that produces errors for the following, so relax compile options
target_compile_options(${EXTENSION_NAME} PRIVATE -Wno-old-style-cast -Wno-shadow -Wno-address)
# tp_print is marked deprecated in Python 3.8, but cython still generates code using it
if("${Python3_VERSION_MINOR}" STREQUAL "8")
    target_compile_options(${EXTENSION_NAME} PRIVATE -Wno-deprecated-declarations)
endif()

# cmake may mutate the name of the library (e.g., lib- and -.so for dynamic libraries). This suppresses that behavior,
# which is required to ensure all paths can be inferred correctly by setup.py.
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")

# RPATH is needed for sofile discovery at runtime, since Python packages are not installed in the system path. This is
# typical.
if(APPLE)
    set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "@loader_path/..")
elseif(UNIX)
    set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN/..")
endif()
target_include_directories(
    ${EXTENSION_NAME}
    PRIVATE ../dd_wrapper/include
            ../../../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include/
            ${Datadog_INCLUDE_DIRS} ${Python3_INCLUDE_DIRS})

target_link_libraries(${EXTENSION_NAME} PRIVATE dd_wrapper)

# Set the output directory for the built library
if(LIB_INSTALL_DIR)
    install(
        TARGETS ${EXTENSION_NAME}
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
        RUNTIME DESTINATION ${LIB_INSTALL_DIR})
endif()
