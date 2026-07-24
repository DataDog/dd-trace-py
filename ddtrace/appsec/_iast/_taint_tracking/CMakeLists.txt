cmake_minimum_required(VERSION 3.19)
set(APP_NAME _native)
option(BUILD_MACOS "Build for MacOS" OFF)
if(DEFINED ENV{CIBUILDWHEEL} OR DEFINED ENV{CIBW_BUILD})
    option(NATIVE_TESTING "Disable test subdirectories and targets" OFF)
else()
    option(NATIVE_TESTING "Load test subdirectories and targets" ON)
endif()

project(${APP_NAME})

set(CMAKE_CXX_STANDARD 17)

# Direct CMake Debug builds use sanitizers by default. setup.py passes this option explicitly because DD_FAST_BUILD
# keeps the release CMake build type.
set(IAST_SANITIZERS_DEFAULT OFF)
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    set(IAST_SANITIZERS_DEFAULT ON)
endif()
option(IAST_ENABLE_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviorSanitizer" ${IAST_SANITIZERS_DEFAULT})
option(IAST_ENABLE_HARDENING "Enable production-safe native hardening" ON)

include(CheckCXXCompilerFlag)
include(CheckCXXSourceCompiles)
include(CheckLinkerFlag)
include(CMakePushCheckState)

# AIDEV-NOTE: Sanitizers provide fail-fast diagnostics in Debug/fast builds, while production hardening must not
# introduce a sanitizer runtime dependency. Keep sanitizer and hardening flags target-scoped so vendored dependencies
# retain their own build configuration.
function(iast_add_supported_compile_option target option check_name)
    check_cxx_compiler_flag("${option}" ${check_name})
    if(${check_name})
        target_compile_options(${target} PRIVATE "${option}")
    endif()
endfunction()

function(iast_add_supported_link_option target option check_name)
    check_linker_flag(CXX "${option}" ${check_name})
    if(${check_name})
        target_link_options(${target} PRIVATE "${option}")
    endif()
endfunction()

function(iast_configure_native_target target)
    if(IAST_ENABLE_SANITIZERS)
        if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
            message(FATAL_ERROR "IAST sanitizers require a Clang or GCC-compatible compiler")
        endif()

        cmake_push_check_state(RESET)
        set(CMAKE_REQUIRED_FLAGS "-fsanitize=address,undefined")
        list(APPEND CMAKE_REQUIRED_LINK_OPTIONS "-fsanitize=address,undefined")
        check_cxx_source_compiles("int main() { return 0; }" IAST_HAS_ADDRESS_UNDEFINED_SANITIZERS)
        cmake_pop_check_state()
        if(NOT IAST_HAS_ADDRESS_UNDEFINED_SANITIZERS)
            message(FATAL_ERROR "The C++ compiler does not support the requested IAST ASan/UBSan profile")
        endif()

        target_compile_options(${target} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer
                                                 -fno-sanitize-recover=all)
        target_link_options(${target} PRIVATE -fsanitize=address,undefined -fno-sanitize-recover=all)
    endif()

    if(IAST_ENABLE_HARDENING)
        iast_add_supported_compile_option(${target} "-fstack-protector-strong" IAST_HAS_STACK_PROTECTOR_STRONG)
        iast_add_supported_compile_option(${target} "-fno-omit-frame-pointer" IAST_HAS_FRAME_POINTERS)

        if(NOT IAST_ENABLE_SANITIZERS)
            iast_add_supported_compile_option(${target} "-ftrivial-auto-var-init=zero" IAST_HAS_AUTO_VAR_INIT_ZERO)
        endif()

        if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
            target_compile_definitions(${target} PRIVATE _GLIBCXX_ASSERTIONS)
            if(NOT IAST_ENABLE_SANITIZERS)
                target_compile_definitions(${target} PRIVATE _FORTIFY_SOURCE=2)
            endif()
            iast_add_supported_compile_option(${target} "-fstack-clash-protection" IAST_HAS_STACK_CLASH_PROTECTION)
            iast_add_supported_compile_option(${target} "-fcf-protection=full" IAST_HAS_CONTROL_FLOW_PROTECTION)
            iast_add_supported_link_option(${target} "-Wl,-z,relro" IAST_HAS_RELRO)
            iast_add_supported_link_option(${target} "-Wl,-z,now" IAST_HAS_NOW)
            iast_add_supported_link_option(${target} "-Wl,-z,noexecstack" IAST_HAS_NOEXECSTACK)
        endif()
    endif()
endfunction()

add_compile_options(
    -fPIC
    -fexceptions
    -fvisibility=hidden
    -pthread
    -Wall
    -Wno-unknown-pragmas
    -g) # Add debug symbols always, it will be stripped in release/minsizerel by cmake

if(BUILD_MACOS)
    # https://pybind11.readthedocs.io/en/stable/compiling.html#building-manually
    message(STATUS "Compile options for MacOS")
    add_link_options(-ldl -undefined dynamic_lookup)
else()
    message(STATUS "Compile options for Linux/Win")
endif(BUILD_MACOS)
unset(BUILD_MACOS CACHE)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../../../../cmake")
include(AbseilDep)

include_directories(".")

file(
    GLOB
    SOURCE_FILES
    "*.cpp"
    "api/*.cpp"
    "context/*.cpp"
    "aspects/*.cpp"
    "initializer/*.cpp"
    "tainted_ops/*.cpp"
    "taint_tracking/*.cpp"
    "utils/*.cpp")
file(
    GLOB
    HEADER_FILES
    "*.h"
    "api/*.h"
    "context/*.h"
    "aspects/*.h"
    "initializer/*.h"
    "tainted_ops/*.h"
    "taint_tracking/*.h"
    "utils/*.h")

# Find ICU library (not needed for now) find_package(ICU REQUIRED COMPONENTS uc i18n)  # 'uc' for the common library,
# 'i18n' for the internationalization library include_directories(${ICU_INCLUDE_DIRS}) list(APPEND ICU_LIBS
# ${ICU_LIBRARIES})

# Debug messages
message(STATUS "PYTHON_LIBRARIES = ${PYTHON_LIBRARIES}")
message(STATUS "PYTHON_EXECUTABLE = ${PYTHON_EXECUTABLE}")
message(STATUS "PYTHON_INCLUDE_DIRS = ${PYTHON_INCLUDE_DIRS}")
# message(STATUS "ICU_LIBRARIES = ${ICU_LIBRARIES}") message(STATUS "ICU_INCLUDE_DIRS = ${ICU_INCLUDE_DIRS}")

# For pybind11 3.0: Use legacy Python detection (compatible with wheel building)
set(PYBIND11_FINDPYTHON OFF)

add_subdirectory(_vendor/pybind11)
if(NATIVE_TESTING)
    find_package(
        Python3
        COMPONENTS Interpreter Development
        REQUIRED)
    # Legacy variables expected elsewhere in the project/tests
    set(PYTHON_EXECUTABLE ${Python3_EXECUTABLE})
    set(PYTHON_INCLUDE_DIRS ${Python3_INCLUDE_DIRS})
    set(PYTHON_LIBRARIES ${Python3_LIBRARIES})

    add_subdirectory(tests EXCLUDE_FROM_ALL)
endif()

# Set verbose mode so compiler and args are shown
set(CMAKE_VERBOSE_MAKEFILE ON)

pybind11_add_module(_native SHARED ${SOURCE_FILES} ${HEADER_FILES})
get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
set_target_properties(_native PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}")
iast_configure_native_target(_native)

if(IAST_ENABLE_SANITIZERS)
    message(STATUS "IAST native profile: ASan + UBSan with production hardening")
else()
    message(STATUS "IAST native profile: production hardening")
endif()

# target_link_libraries(_native PRIVATE ${ICU_LIBS})

if(NOT (CMAKE_BUILD_TYPE STREQUAL "Debug")
   AND NOT (DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}"
                                                                                          STREQUAL "false")))
    target_link_libraries(${APP_NAME} PRIVATE absl::node_hash_map)
endif()

install(
    TARGETS _native
    DESTINATION LIBRARY
    DESTINATION ${LIB_INSTALL_DIR}
    ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
    RUNTIME DESTINATION ${LIB_INSTALL_DIR})
