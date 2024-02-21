# The exact name of this extension determines aspects of the installation and build paths,
# which need to be kept in sync with setup.py. Accordingly, take the name passed in by the
# caller, defaulting to "stack_v2" if needed.
cmake_minimum_required(VERSION 3.19)
set(EXTENSION_NAME "stack_v2" CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})

# Custom cmake modules are in the parent directory
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../cmake")

# Includes
include(FetchContent)
include(ExternalProject)
include(FindPython3)
include(AnalysisFunc)
add_subdirectory(../dd_wrapper "${LIB_INSTALL_DIR}")

# Find the Python interpreter
find_package(Python3 COMPONENTS Interpreter REQUIRED)
if (NOT Python3_INCLUDE_DIRS)
  message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

# Add echion
FetchContent_Declare(
    echion
    GIT_REPOSITORY "https://github.com/sanchda/echion.git"
    GIT_TAG "sanchda/more_rendering"
)
FetchContent_GetProperties(echion)
if(NOT echion_POPULATED)
  FetchContent_Populate(echion)
endif()

# Specify the target C-extension that we want to build
add_library(${EXTENSION_NAME} SHARED
    src/sampler.cpp
    src/stack_renderer.cpp
    src/stack_v2.cpp
)

# Add common config
add_ddup_config(${EXTENSION_NAME})

# This project is build with C++17, even though the underlying repo adheres to the manylinux 2014 standard.
# This isn't currently a problem, but if it becomes one, we may have to structure the library differently.
target_compile_features(${EXTENSION_NAME} PUBLIC cxx_std_17)

# Never build with native unwinding, since this is not currently used
target_compile_definitions(${EXTENSION_NAME} PRIVATE UNWIND_NATIVE_DISABLE)

# Includes; echion and python are marked "system" to suppress warnings,
# but note in MSVC we'll have to #pragma warning(push, 0 then pop for the same effect.
message(ERROR "echion_SOURCE_DIR: ${echion_SOURCE_DIR}")
target_include_directories(${EXTENSION_NAME} PRIVATE
    ..
    include
)
target_include_directories(${EXTENSION_NAME} SYSTEM PRIVATE
    ${echion_SOURCE_DIR}
    ${Python3_INCLUDE_DIRS}
    include/vendored
    include/util
)

# Echion sources need to be given the current platform
if(WIN32)
    target_compile_definitions(${EXTENSION_NAME} PRIVATE PL_WINDOWS)
elseif(APPLE)
    target_compile_definitions(${EXTENSION_NAME} PRIVATE PL_DARWIN)
elseif(UNIX)
    target_compile_definitions(${EXTENSION_NAME} PRIVATE PL_LINUX)
endif()

# cmake may mutate the name of the library (e.g., lib- and -.so for dynamic libraries).
# This suppresses that behavior, which is required to ensure all paths can be inferred
# correctly by setup.py.
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN") # NB., gets installed to parent!

target_link_libraries(${EXTENSION_NAME} PRIVATE
    dd_wrapper
)

# We want the extension to be built with position-independent code
set_target_properties(${EXTENSION_NAME} PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Set the output directory for the built library
set_target_properties(${EXTENSION_NAME}
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
)

install(TARGETS ${EXTENSION_NAME} DESTINATION ${LIB_INSTALL_DIR})
