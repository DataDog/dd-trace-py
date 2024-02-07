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

# Custom modules are in the parent directory
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

# Setup state for FindLibdatadog
set(Datadog_BUILD_DIR "${dd_wrapper_BUILD_PARENT}/libdatadog")

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

# Library sources
add_library(dd_wrapper SHARED
    src/uploader_builder.cpp
    src/sample_builder.cpp
    src/profile.cpp
    src/uploader.cpp
    src/sample.cpp
    src/interface.cpp
)

# If we were given flags to do sanitization or static analyis, use them
if (SANITIZE_OPTIONS)
  if (SANITIZE_OPTIONS MATCHES "fanalyzer")
    target_compile_options(dd_wrapper PRIVATE -fanalyzer)
  elseif (SANITIZE_OPTIONS MATCHES "cppcheck")
    set(DO_CPPCHECK ON)
  else()
    target_compile_options(dd_wrapper PRIVATE -fsanitize=${SANITIZE_OPTIONS})
    target_link_options(dd_wrapper PRIVATE -fsanitize=${SANITIZE_OPTIONS})
  endif()
endif()

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

# if "DO_CPPCHECK" is set, then we'll run cppcheck
if (DO_CPPCHECK)
  message(STATUS "Running cppcheck")
  include(FindCppcheck)
  if (CPPCHECK_EXECUTABLE)
    message(STATUS "cppcheck found: ${CPPCHECK_EXECUTABLE}")
    add_custom_target(cppcheck_run
      COMMAND ${CPPCHECK_EXECUTABLE}
      --enable=warning,performance,portability,information,style,missingInclude
      --template="cppcheck:{id}:{file}:{line}:{severity}:{message}"
      --library=googletest
      --std=c++17
      --language=c++
      --suppress=missingIncludeSystem
      --inline-suppr
      --error-exitcode=1
      -I ${CMAKE_SOURCE_DIR}/include
      -I ${Datadog_INCLUDE_DIRS}
      ${CMAKE_SOURCE_DIR}/src
      WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
      COMMENT "Running cppcheck"
    )
  else()
    message(WARNING "cppcheck not found")
  endif()
endif()

# Add the tests
enable_testing()
add_subdirectory(test)
