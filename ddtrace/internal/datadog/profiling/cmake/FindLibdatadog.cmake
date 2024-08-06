# Only add this project if Datadog::Profiling is not already defined
if(TARGET Datadog::Profiling)
    return()
endif()

include(ExternalProject)
set(TAG_LIBDATADOG
    "v12.0.0"
    CACHE STRING "libdatadog github tag")

set(Datadog_BUILD_DIR ${CMAKE_BINARY_DIR}/libdatadog)
set(Datadog_ROOT ${Datadog_BUILD_DIR}/libdatadog-${TAG_LIBDATADOG})

message(STATUS "${CMAKE_CURRENT_LIST_DIR}/tools/fetch_libdatadog.sh ${TAG_LIBDATADOG} ${Datadog_ROOT}")
execute_process(
  COMMAND "${CMAKE_CURRENT_LIST_DIR}/tools/fetch_libdatadog.sh" ${TAG_LIBDATADOG} ${Datadog_ROOT}
  WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
  COMMAND_ERROR_IS_FATAL ANY
)

set(Datadog_DIR "${Datadog_ROOT}/cmake")

# Prefer static library to shared library
set(CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP ${CMAKE_FIND_LIBRARY_SUFFIXES})
set(CMAKE_FIND_LIBRARY_SUFFIXES .a)

find_package(Datadog REQUIRED)

# Restore CMAKE_FIND_LIBRARY_SUFFIXES
set(CMAKE_FIND_LIBRARY_SUFFIXES ${CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP})
