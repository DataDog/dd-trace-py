cmake_minimum_required(VERSION 3.19)

# We're building a Python extension and expect to be called from setup.py
set(EXTENSION_NAME "" CACHE STRING "Name of the extension")
project(${EXTENSION_NAME})

# Set C++17 as required standard for the whole project.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} -Wextra")

# Features
option(UNWIND_NATIVE_ENABLE "Enable native unwinding" OFF)

# Includes
include(FetchContent)
include(ExternalProject)
include(FindPython3)
add_subdirectory(../dd_wrapper "${LIB_INSTALL_DIR}")

# Find the Python interpreter
find_package(Python3 COMPONENTS Interpreter REQUIRED)
if (NOT Python3_INCLUDE_DIRS)
  message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
endif()

# Add echion
FetchContent_Declare(
    echion
    GIT_REPOSITORY "https://github.com/sanchda/echion.git"
    GIT_TAG "sanchda/more_rendering"
)
FetchContent_GetProperties(echion)
if(NOT echion_POPULATED)
  FetchContent_Populate(echion)
endif()

# Get libunwind
if(UNWIND_NATIVE_ENABLE)
  ExternalProject_Add(
      libunwind_ext
      PREFIX libunwind
      GIT_REPOSITORY https://github.com/libunwind/libunwind.git
      GIT_TAG "v1.7.2"
      CONFIGURE_COMMAND autoreconf -i && ./configure --disable-minidebuginfo --enable-fast-unwind --disable-shared --enable-static CFLAGS=-fPIC
      BUILD_COMMAND make
      INSTALL_COMMAND ""
      BUILD_IN_SOURCE TRUE
  )
  set(LIBUNWIND_BUILD_PATH "${CMAKE_BINARY_DIR}/libunwind/src/libunwind_ext")
  set(LIBUNWIND_LIB "${LIBUNWIND_BUILD_PATH}/src/.libs/libunwind.a")
  set(LIBUNWIND_ARCH_LIB "${LIBUNWIND_BUILD_PATH}/src/.libs/libunwind-x86_64.a")
endif()

# echion needs to know the underlying platform; if it's given to us, use that
# else try to infer it
set(PLATFORM "Unknown" CACHE STRING "Target platform")
if(PLATFORM STREQUAL "Unknown")
    if(WIN32)
        set(PLATFORM "Windows")
    elseif(APPLE)
        set(PLATFORM "Darwin")
    elseif(UNIX)
        set(PLATFORM "Linux")
    endif()
endif()
if(PLATFORM STREQUAL "Windows")
    add_definitions(-DPL_WINDOWS)
elseif(PLATFORM STREQUAL "Darwin")
    add_definitions(-DPL_DARWIN)
elseif(PLATFORM STREQUAL "Linux")
    add_definitions(-DPL_LINUX)
endif()

if (NOT UNWIND_NATIVE_ENABLE)
  add_definitions(-DUNWIND_NATIVE_DISABLE)
endif()

# Specify the target C-extension that we want to build
add_library(${EXTENSION_NAME} SHARED
    src/stack.cpp
)

target_include_directories(${EXTENSION_NAME} PRIVATE
    ${Python3_INCLUDE_DIRS}
    ${echion_SOURCE_DIR}
    ../dd_wrapper/include
)

# We want the extension name to be literal
set_target_properties(${EXTENSION_NAME} PROPERTIES PREFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES SUFFIX "")
set_target_properties(${EXTENSION_NAME} PROPERTIES INSTALL_RPATH "$ORIGIN") # NB., gets installed to parent!

# Add stuff we might need for native unwinding
if (UNWIND_NATIVE_ENABLE)
  target_include_directories(${EXTENSION_NAME} PRIVATE
      ${libunwind_ext_SOURCE_DIR}/include
  add_dependencies(${EXTENSION_NAME} libunwind_ext)
  target_link_libraries(${EXTENSION_NAME} PRIVATE ${LIBUNWIND_LIB} ${LIBUNWIND_ARCH_LIB})
  )
endif()

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
