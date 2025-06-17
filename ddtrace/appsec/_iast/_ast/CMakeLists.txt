cmake_minimum_required(VERSION 3.10)

project(iastpatch C)

if(DEFINED ENV{DD_USE_SCCACHE} AND "$ENV{DD_USE_SCCACHE}" STREQUAL "1")
    # Optional: ccache integration
    find_program(CCACHE_PROGRAM sccache)
    if(CCACHE_PROGRAM)
        message(STATUS "Using ccache: ${CCACHE_PROGRAM}")
        set(CMAKE_C_COMPILER_LAUNCHER "${CCACHE_PROGRAM}")
        set(CMAKE_CXX_COMPILER_LAUNCHER "${CCACHE_PROGRAM}")
    endif()
endif()

find_package(Python3 REQUIRED COMPONENTS Development)

add_library(iastpatch MODULE iastpatch.c)

target_include_directories(iastpatch PRIVATE ${Python3_INCLUDE_DIRS})

target_link_libraries(iastpatch PRIVATE ${Python3_LIBRARIES})

set_target_properties(iastpatch PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}")
