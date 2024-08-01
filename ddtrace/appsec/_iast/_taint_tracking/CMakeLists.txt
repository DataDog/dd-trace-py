cmake_minimum_required(VERSION 3.19)
include(FetchContent)

set(APP_NAME _native)
option(BUILD_MACOS "Build for MacOS" OFF)

project(${APP_NAME})

set(CMAKE_CXX_STANDARD 17)

# -U_FORTIFY_SOURCE to fix a bug in alpine and pybind11
# https://github.com/pybind/pybind11/issues/1650
# https://gitlab.alpinelinux.org/alpine/aports/-/issues/8626
add_compile_options(-fPIC -fexceptions -fvisibility=hidden -fpermissive -pthread -Wall -Wno-unknown-pragmas -U_FORTIFY_SOURCE)

# Debug-specific compile options
set(CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} -g -O0")
set(CMAKE_C_FLAGS_DEBUG "${CMAKE_C_FLAGS_DEBUG} -g -O0")

# RelWithDebInfo-specific compile options
set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "${CMAKE_CXX_FLAGS_RELWITHDEBINFO} -g -O2")
set(CMAKE_C_FLAGS_RELWITHDEBINFO "${CMAKE_C_FLAGS_RELWITHDEBINFO} -g -O2")

if(BUILD_MACOS)
    # https://pybind11.readthedocs.io/en/stable/compiling.html#building-manually
    message(STATUS "Compile options for MacOS")
    add_link_options(-ldl -undefined dynamic_lookup)
else()
    message(STATUS "Compile options for Linux/Win")
endif(BUILD_MACOS)
unset(BUILD_MACOS CACHE)

# Abseil setup
if(CMAKE_BUILD_TYPE STREQUAL "Release" OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo")
    message(STATUS "Release or RelWithDebInfo mode: using abseil")
    FetchContent_Declare(
        absl
        URL "https://github.com/abseil/abseil-cpp/archive/refs/tags/20230802.1.zip"
    )
    FetchContent_MakeAvailable(absl)
else()
    message(STATUS "Debug mode: not using abseil")
endif()

include_directories(".")
file(GLOB SOURCE_FILES "*.cpp"
        "Aspects/*.cpp"
        "Initializer/*.cpp"
        "TaintedOps/*.cpp"
        "TaintTracking/*.cpp"
        "Utils/*.cpp")
file(GLOB HEADER_FILES "*.h"
        "Aspects/*.h"
        "Initializer/*.h"
        "TaintedOps/*.h"
        "TaintTracking/*.h"
        "Utils/*.h"
        )

# Debug messages
message(STATUS "PYTHON_LIBRARIES = ${Python_LIBRARIES}")
message(STATUS "PYTHON_EXECUTABLE = ${Python_EXECUTABLE}")
message(STATUS "PYTHON_INCLUDE_DIRS = ${Python_INCLUDE_DIRS}")
message(STATUS "Python_EXECUTABLE = ${Python_EXECUTABLE}")

add_subdirectory(_vendor/pybind11)
pybind11_add_module(${APP_NAME} SHARED ${SOURCE_FILES} ${HEADER_FILES})

get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
set_target_properties(
    ${APP_NAME}
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
)

# Debug and RelWithDebInfo-specific link options
if(CMAKE_BUILD_TYPE STREQUAL "Debug" OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo")
    if(UNIX AND NOT APPLE)
        target_link_options(${APP_NAME} PRIVATE -rdynamic)
    endif()
endif()

# Link abseil for Release and RelWithDebInfo
if(CMAKE_BUILD_TYPE STREQUAL "Release" OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo")
    target_link_libraries(${APP_NAME} PRIVATE absl::node_hash_map)
endif()

install(TARGETS ${APP_NAME} 
    LIBRARY DESTINATION ${LIB_INSTALL_DIR}
    ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
    RUNTIME DESTINATION ${LIB_INSTALL_DIR}
)
