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

# Get the cmake modules for this project
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../cmake")

# Includes
include(FetchContent)
include(ExternalProject)
include(FindLibdatadog)

# Technically, this should be its own project which we `include()`, but I don't want to deal with that when so many
# things may yet be factored differently.
add_subdirectory(../dd_wrapper ${CMAKE_CURRENT_BINARY_DIR}/../dd_wrapper_build)

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

# We can't add common Profiling configuration because cython generates messy code, so we just setup some basic flags and
# features
target_link_options(${EXTENSION_NAME} PRIVATE "$<$<CONFIG:Release>:-Os>" -ffunction-sections)

if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
    # macOS-specific options
    target_compile_options(${EXTENSION_NAME} PRIVATE "$<$<CONFIG:Debug>:-Og;-g>")
    target_link_options(${EXTENSION_NAME} PRIVATE "$<$<CONFIG:Release>:-Wl,-dead_strip>")
else()
    # Linux/ELF-based options
    target_compile_options(${EXTENSION_NAME} PRIVATE "$<$<CONFIG:Debug>:-Og;-ggdb3>" -fno-semantic-interposition)
    target_link_options(${EXTENSION_NAME} PRIVATE "$<$<CONFIG:Release>:-s>" -Wl,--as-needed -Wl,-Bsymbolic-functions
        -Wl,--gc-sections)
endif()

set_property(TARGET ${EXTENSION_NAME} PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)

target_compile_features(${EXTENSION_NAME} PUBLIC cxx_std_17)

# cmake may mutate the name of the library (e.g., lib- and -.so for dynamic libraries). This suppresses that behavior,
# which is required to ensure all paths can be inferred correctly by setup.py.
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")

# RPATH is needed for sofile discovery at runtime, since Python packages are not installed in the system path. This is
# typical.
set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN/..")
target_include_directories(${EXTENSION_NAME} PRIVATE ../dd_wrapper/include ${Datadog_INCLUDE_DIRS}
    ${Python3_INCLUDE_DIRS})

if(Python3_LIBRARIES)
    target_link_libraries(${EXTENSION_NAME} PRIVATE dd_wrapper ${Python3_LIBRARIES})
else()
    target_link_libraries(${EXTENSION_NAME} PRIVATE dd_wrapper)
endif()

# Extensions are built as dynamic libraries, so PIC is required.
set_target_properties(${EXTENSION_NAME} PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Set the output directory for the built library
if(LIB_INSTALL_DIR)
    install(
        TARGETS ${EXTENSION_NAME}
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
        RUNTIME DESTINATION ${LIB_INSTALL_DIR})
endif()
