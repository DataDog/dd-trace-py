cmake_minimum_required(VERSION 3.10)
project(iastpatch C)

find_package(Python3 REQUIRED COMPONENTS Development)

add_library(iastpatch MODULE iastpatch.c)

target_include_directories(iastpatch PRIVATE ${Python3_INCLUDE_DIRS})

target_link_libraries(iastpatch PRIVATE ${Python3_LIBRARIES})

set_target_properties(iastpatch PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}")
