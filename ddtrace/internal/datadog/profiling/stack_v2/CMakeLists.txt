cmake_minimum_required(VERSION 3.19)

# The exact name of this extension determines aspects of the installation and build paths, which need to be kept in sync
# with setup.py. Accordingly, take the name passed in by the caller, defaulting to "stack_v2" if needed.
set(EXTENSION_NAME
    "_stack_v2"
    CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})
message(STATUS "Building extension: ${EXTENSION_NAME}")

# Set verbose mode so compiler and args are shown
set(CMAKE_VERBOSE_MAKEFILE ON)

# Custom cmake modules are in the parent directory
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

# Link against the pre-built libdd_wrapper shared library The library is built by setup.py and its location is passed
# via NATIVE_EXTENSION_LOCATION. For standalone builds, check LIB_INSTALL_DIR first.
if(LIB_INSTALL_DIR AND EXISTS "${LIB_INSTALL_DIR}/libdd_wrapper${EXTENSION_SUFFIX}")
    set(DD_WRAPPER_LIB "${LIB_INSTALL_DIR}/libdd_wrapper${EXTENSION_SUFFIX}")
else()
    set(DD_WRAPPER_LIB "${NATIVE_EXTENSION_LOCATION}/../datadog/profiling/libdd_wrapper${EXTENSION_SUFFIX}")
endif()

# Import the pre-built library as an imported target
add_library(dd_wrapper SHARED IMPORTED)
set_target_properties(
    dd_wrapper PROPERTIES IMPORTED_LOCATION "${DD_WRAPPER_LIB}" INTERFACE_INCLUDE_DIRECTORIES
                                                                "${CMAKE_CURRENT_SOURCE_DIR}/../dd_wrapper/include")

# Includes
include(FetchContent)
include(AnalysisFunc)
include(FindCppcheck)

find_package(Python3 COMPONENTS Interpreter Development)

# Make sure we have necessary Python variables
if(NOT Python3_INCLUDE_DIRS)
    message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

if(NOT Threads_FOUND OR NOT CMAKE_USE_PTHREADS_INIT)
    message(FATAL_ERROR "pthread compatible library not found")
endif()

# Add echion
set(ECHION_COMMIT
    "1b02a8fcccaa4b748b494f3c3177b78be6503967" # https://github.com/P403n1x87/echion/commit/1b02a8fcccaa4b748b494f3c3177b78be6503967
    CACHE STRING "Commit hash of echion to use")
FetchContent_Declare(
    echion
    GIT_REPOSITORY "https://github.com/P403n1x87/echion.git"
    GIT_TAG ${ECHION_COMMIT})
FetchContent_GetProperties(echion)

if(NOT echion_POPULATED)
    FetchContent_Populate(echion)
endif()

# Specify the target C-extension that we want to build
add_library(${EXTENSION_NAME} SHARED ${echion_SOURCE_DIR}/echion/frame.cc ${echion_SOURCE_DIR}/echion/render.cc
                                     src/sampler.cpp src/stack_renderer.cpp src/stack_v2.cpp src/thread_span_links.cpp)

# Add common config
add_ddup_config(${EXTENSION_NAME})
add_cppcheck_target(
    ${EXTENSION_NAME}
    DIRECTORY
    ${CMAKE_CURRENT_SOURCE_DIR}
    INCLUDE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/include/util
    ${CMAKE_CURRENT_SOURCE_DIR}/..
    ${echion_SOURCE_DIR}
    SRC
    ${CMAKE_CURRENT_SOURCE_DIR}/src)

# Never build with native unwinding, since this is not currently used
target_compile_definitions(${EXTENSION_NAME} PRIVATE UNWIND_NATIVE_DISABLE)

# Includes; echion and python are marked "system" to suppress warnings
target_include_directories(
    ${EXTENSION_NAME} PRIVATE .. # include dd_wrapper from the root in order to make its paths transparent in the code
                              include)
target_include_directories(${EXTENSION_NAME} SYSTEM PRIVATE ${echion_SOURCE_DIR} ${Python3_INCLUDE_DIRS}
                                                            include/vendored include/util)

# Echion sources need to be given the current platform
if(APPLE)
    target_compile_definitions(${EXTENSION_NAME} PRIVATE PL_DARWIN)
elseif(UNIX)
    target_compile_definitions(${EXTENSION_NAME} PRIVATE PL_LINUX)
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

target_link_libraries(${EXTENSION_NAME} PRIVATE dd_wrapper Threads::Threads)

# Set the output directory for the built library
if(LIB_INSTALL_DIR)
    install(
        TARGETS ${EXTENSION_NAME}
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
        RUNTIME DESTINATION ${LIB_INSTALL_DIR})
endif()

if(BUILD_TESTING)
    enable_testing()
    add_subdirectory(test)
endif()
