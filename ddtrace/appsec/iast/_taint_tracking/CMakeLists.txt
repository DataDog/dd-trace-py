cmake_minimum_required(VERSION 3.15...3.22)
set(APP_NAME _native)

project(${APP_NAME})

set(CMAKE_CXX_FLAGS_DEBUG_INIT "-Wall")
set(CMAKE_CONFIGURATION_TYPES "Debug" CACHE STRING "" FORCE)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(CMAKE_VERBOSE_MAKEFILE ON)

# Python Libraries
find_package(PythonLibs REQUIRED)
include_directories(${PYTHON_INCLUDE_DIRS})

# Debug messages
message(STATUS "PYTHON_LIBRARIES = ${PYTHON_LIBRARIES}")
message(STATUS "PYTHON_EXECUTABLE = ${PYTHON_EXECUTABLE}")
message(STATUS "PYTHON_INCLUDE_DIRS = ${PYTHON_INCLUDE_DIRS}")


get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
file(GLOB SOURCE_FILES "*.cpp" "TaintedObject/*.cpp" "Source/*.cpp" "TaintRange/*.cpp")
file(GLOB HEADER_FILES "*.h" "TaintedObject/*.h" "Source/*.h" "TaintRange/*.h")


add_library(${APP_NAME} SHARED ${SOURCE_FILES} ${HEADER_FILES} _native.cpp Constants.h)

set_target_properties(
        ${APP_NAME}
        PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${PROJECT_BINARY_DIR}/../"
        PREFIX ""
)

target_link_libraries(${APP_NAME} ${PYTHON_LIBRARIES})
