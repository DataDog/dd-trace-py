cmake_minimum_required(VERSION 3.19)

# We're building a Python extension and expect to be called from setup.py
set(EXTENSION_NAME "" CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})

# Set C++17 as required standard for the whole project.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Includes
include(FetchContent)
include(ExternalProject)
include(FindPython3)
add_subdirectory(dd_wrapper "${LIB_INSTALL_DIR}")

# Find the Python interpreter
find_package(Python3 COMPONENTS Interpreter REQUIRED)
if (NOT Python3_INCLUDE_DIRS)
  message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

# Cythonize the .pyx file
set (ENV{PY_MAJOR_VERSION} ${PY_MAJOR_VERSION})
set (ENV{PY_MINOR_VERSION} ${PY_MINOR_VERSION})
set (ENV{PY_MICRO_VERSION} ${PY_MICRO_VERSION})

add_custom_command(
    OUTPUT ${CMAKE_CURRENT_LIST_DIR}/_ddup.cpp
    COMMAND ${PYTHON_EXECUTABLE} -m cython ${CMAKE_CURRENT_LIST_DIR}/_ddup.pyx -o ${CMAKE_CURRENT_LIST_DIR}/_ddup.cpp
    DEPENDS ${CMAKE_CURRENT_LIST_DIR}/_ddup.pyx
)

# Specify the target C-extension that we want to build
# prefix/suffix stuff is to ensure the extension name is literal
add_library(${EXTENSION_NAME} SHARED
    _ddup.cpp
)
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN")

target_include_directories(${EXTENSION_NAME} PRIVATE
    include
    dd_wrapper/include
    ${Datadog_INCLUDE_DIRS}
    ${Python3_INCLUDE_DIRS}
)

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
