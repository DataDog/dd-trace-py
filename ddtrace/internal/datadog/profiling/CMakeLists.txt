cmake_minimum_required(VERSION 3.19)

# This builds a C-extension for Python using Cython and a native dependency.  The exact
# name of the extension has consequences for the build path, which is in turn used by
# setup.py to build the wheel. Otherwise, we have to propagate a lot of state all around.
# Thus, we use the same name as the Python package (i.e., the caller sets EXTENSION_NAME)
# ddup is used by a default for standalone/test builds.
set(EXTENSION_NAME "ddup" CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})

# Set C++17 as required standard for the whole project.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Includes
include(FetchContent)
include(ExternalProject)
include(FindPython3)

# Build the dd_wrapper
# Need to specify that we're calling it via add_subdirectory, so that it knows to
# propagate the right variables to its parent scope
set(DD_WRAPPER_IS_SUBPROJECT ON)
add_subdirectory(dd_wrapper "${LIB_INSTALL_DIR}")

# Find the Python interpreter
find_package(Python3 COMPONENTS Interpreter REQUIRED)
if (NOT Python3_INCLUDE_DIRS)
  message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

# This sets some parameters for the target build, which can only be defined by setup.py
set (ENV{PY_MAJOR_VERSION} ${PY_MAJOR_VERSION})
set (ENV{PY_MINOR_VERSION} ${PY_MINOR_VERSION})
set (ENV{PY_MICRO_VERSION} ${PY_MICRO_VERSION})

# Cythonize the .pyx file
add_custom_command(
    OUTPUT ${CMAKE_CURRENT_LIST_DIR}/_ddup.cpp
    COMMAND ${PYTHON_EXECUTABLE} -m cython ${CMAKE_CURRENT_LIST_DIR}/_ddup.pyx -o ${CMAKE_CURRENT_LIST_DIR}/_ddup.cpp
    DEPENDS ${CMAKE_CURRENT_LIST_DIR}/_ddup.pyx
)

# Specify the target C-extension that we want to build
add_library(${EXTENSION_NAME} SHARED
    _ddup.cpp
)

# cmake may mutate the name of the library (e.g., lib- and -.so for dynamic libraries).
# This suppresses that behavior, which is required to ensure all paths can be inferred
# correctly by setup.py.
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")

# RPATH is needed for sofile discovery at runtime, since Python packages are not
# installed in the system path. This is typical.
set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN")

target_include_directories(${EXTENSION_NAME} PRIVATE
    include
    dd_wrapper/include
    ${Datadog_INCLUDE_DIRS}
    ${Python3_INCLUDE_DIRS}
)

target_link_libraries(${EXTENSION_NAME} PRIVATE
    dd_wrapper
)

# Extensions are built as dynamic libraries, so PIC is required.
set_target_properties(${EXTENSION_NAME} PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Set the output directory for the built library
set_target_properties(${EXTENSION_NAME}
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
)

install(TARGETS ${EXTENSION_NAME} DESTINATION ${LIB_INSTALL_DIR})
