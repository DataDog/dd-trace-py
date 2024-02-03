cmake_minimum_required(VERSION 3.19)
project(dd_wrapper
    VERSION 0.1.0
    LANGUAGES CXX
)

# C++17
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Infer some basic properties about the build.  This is necessary because multiple
# extensions reuse this cmake file, so we need its artifacts to go in a consistent
# place
get_filename_component(dd_wrapper_BUILD_PARENT "${CMAKE_BINARY_DIR}/../.." ABSOLUTE)
set(dd_wrapper_BUILD_DIR "${dd_wrapper_BUILD_PARENT}/ddtrace.internal.datadog.profiling")

# Setup state for FindLibdatadog
set(Datadog_BUILD_DIR "${dd_wrapper_BUILD_PARENT}/libdatadog")
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

# Includes
include(CMakePackageConfigHelpers) # For generating the config file
include(FetchContent)
include(ExternalProject)
include(FindLibdatadog)

# Send some of these vars back up to the parent
if (DD_WRAPPER_IS_SUBPROJECT)
  set(Datadog_INCLUDE_DIRS "${Datadog_INCLUDE_DIRS}" PARENT_SCOPE)
  set(dd_wrapper_BUILD_DIR "${dd_wrapper_BUILD_PARENT}/ddtrace.internal.datadog.profiling" PARENT_SCOPE)
endif()

# Set some additional parameters
add_compile_options(
  "$<$<CONFIG:Debug>:-Og;-ggdb3;-fno-omit-frame-pointer>"
  "$<$<CONFIG:Release>:-O3>"
  -Wall -Werror -Wextra -Wshadow -Wnon-virtual-dtor -Wold-style-cast
)

# If we were given flags to do sanitization or static analyis, use them
if (SANITIZE_OPTIONS)
  if (SANITIZE_OPTIONS MATCHES "fanalyzer")
    add_compile_options(-fanalyzer)
  else()
    add_compile_options(-fsanitize=${SANITIZE_OPTIONS})
    add_link_options(-fsanitize=${SANITIZE_OPTIONS})
  endif()
endif()

# Library sources
add_library(dd_wrapper SHARED
    src/uploader_builder.cpp
    src/profile_builder.cpp
    src/global_cache.cpp
    src/profile_shared.cpp
    src/uploader.cpp
    src/profile.cpp
    src/interface.cpp
)

target_include_directories(dd_wrapper PRIVATE
    include
    ${Datadog_INCLUDE_DIRS}
)
target_link_libraries(dd_wrapper PRIVATE
    ${Datadog_LIBRARIES}
)
set_target_properties(dd_wrapper PROPERTIES POSITION_INDEPENDENT_CODE ON)

set_target_properties(dd_wrapper
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${dd_wrapper_BUILD_DIR}"
)

# Assumed dd_wrapper_INSTALL_DIR is propagated from the parent
if (NOT dd_wrapper_INSTALL_DIR)
  # If not, then just keep it in the build directory
  set(dd_wrapper_INSTALL_DIR "${CMAKE_BINARY_DIR}/lib")
endif()
message(STATUS "dd_wrapper_INSTALL_DIR: ${dd_wrapper_INSTALL_DIR}")
install(TARGETS dd_wrapper
    DESTINATION ${dd_wrapper_INSTALL_DIR}
)

### Testing
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG release-1.11.0
)
FetchContent_MakeAvailable(googletest)

# Add the tests
add_executable(test_init
  test/initialization.cpp
)
target_include_directories(test_init PRIVATE
  include
)
target_link_libraries(test_init PRIVATE
  gtest_main
  dd_wrapper
)

# Register
include(GoogleTest)
gtest_discover_tests(test_init)
