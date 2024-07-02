cmake_minimum_required(VERSION 3.19)

# See ../ddup/CMakeLists.txt for a more detailed explanation of why we do what we do.
set(EXTENSION_NAME "_crashtracker.so" CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})
message(STATUS "Building extension: ${EXTENSION_NAME}")

# Get the cmake modules for this project
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../cmake")

# Includes
include(FetchContent)
include(ExternalProject)
include(FindLibdatadog)

add_subdirectory(../dd_wrapper ${CMAKE_CURRENT_BINARY_DIR}/../dd_wrapper_build)

# Make sure we have necessary Python variables
if (NOT Python3_INCLUDE_DIRS)
  message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

# This sets some parameters for the target build, which can only be defined by setup.py
set(ENV{PY_MAJOR_VERSION} ${PY_MAJOR_VERSION})
set(ENV{PY_MINOR_VERSION} ${PY_MINOR_VERSION})
set(ENV{PY_MICRO_VERSION} ${PY_MICRO_VERSION})

# if PYTHON_EXECUTABLE is unset or empty, but Python3_EXECUTABLE is set, use that
if (NOT PYTHON_EXECUTABLE AND Python3_EXECUTABLE)
  set(PYTHON_EXECUTABLE ${Python3_EXECUTABLE})
endif()

# If we still don't have a Python executable, we can't continue
if (NOT PYTHON_EXECUTABLE)
  message(FATAL_ERROR "Python executable not found")
endif()

# Cythonize the .pyx file
set(CRASHTRACKER_CPP_SRC ${CMAKE_CURRENT_BINARY_DIR}/_crashtracker.cpp)
add_custom_command(
    OUTPUT ${CRASHTRACKER_CPP_SRC}
    COMMAND ${PYTHON_EXECUTABLE} -m cython ${CMAKE_CURRENT_LIST_DIR}/_crashtracker.pyx -o ${CRASHTRACKER_CPP_SRC}
    DEPENDS ${CMAKE_CURRENT_LIST_DIR}/_crashtracker.pyx
)

# Specify the target C-extension that we want to build
add_library(${EXTENSION_NAME} SHARED
    ${CRASHTRACKER_CPP_SRC}
)

# We can't add common Profiling configuration because cython generates messy code, so we just setup some
# basic flags and features
target_compile_options(${EXTENSION_NAME} PRIVATE
  "$<$<CONFIG:Debug>:-Og;-ggdb3>"
  "$<$<CONFIG:Release>:-Os>"
  -ffunction-sections -fno-semantic-interposition
)
target_link_options(${EXTENSION_NAME} PRIVATE
  "$<$<CONFIG:Release>:-s>"
  -Wl,--as-needed -Wl,-Bsymbolic-functions -Wl,--gc-sections
)
set_property(TARGET ${EXTENSION_NAME} PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)

target_compile_features(${EXTENSION_NAME} PUBLIC cxx_std_17)

# cmake may mutate the name of the library (e.g., lib- and -.so for dynamic libraries).
# This suppresses that behavior, which is required to ensure all paths can be inferred
# correctly by setup.py.
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")

# RPATH is needed for sofile discovery at runtime, since Python packages are not
# installed in the system path. This is typical.
set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN/..")
target_include_directories(${EXTENSION_NAME} PRIVATE
    ../dd_wrapper/include
    ${Datadog_INCLUDE_DIRS}
    ${Python3_INCLUDE_DIRS}
)

target_link_libraries(${EXTENSION_NAME} PRIVATE
    dd_wrapper
)

# Extensions are built as dynamic libraries, so PIC is required.
set_target_properties(${EXTENSION_NAME} PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Set the output directory for the built library
if (LIB_INSTALL_DIR)
    install(TARGETS ${EXTENSION_NAME}
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
        RUNTIME DESTINATION ${LIB_INSTALL_DIR}
    )
endif()

# Crashtracker receiver binary
add_executable(crashtracker_exe
    src/crashtracker.cpp
)
target_include_directories(crashtracker_exe PRIVATE
    ..
    ${Datadog_INCLUDE_DIRS}
)
set_target_properties(crashtracker_exe PROPERTIES INSTALL_RPATH "$ORIGIN/..")
target_link_libraries(crashtracker_exe PRIVATE
    dd_wrapper
)

# See the dd_wrapper CMakeLists.txt for a more detailed explanation of why we do what we do.
if (INPLACE_LIB_INSTALL_DIR)
    set(LIB_INSTALL_DIR "${INPLACE_LIB_INSTALL_DIR}")
endif()

if (LIB_INSTALL_DIR)
    install(TARGETS crashtracker_exe
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
        RUNTIME DESTINATION ${LIB_INSTALL_DIR}
    )
endif()
