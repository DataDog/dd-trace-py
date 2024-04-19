cmake_minimum_required(VERSION 3.19)
project(dd_wrapper
    VERSION 0.1.1
    LANGUAGES CXX
)

# Build in a predictable location.  This is needed for setup.py
get_filename_component(dd_wrapper_BUILD_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../ddtrace.internal.datadog.profiling" ABSOLUTE)

# Custom modules are in the parent directory
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

# Includes
include(FetchContent)
include(ExternalProject)
include(FindLibdatadog)
include(AnalysisFunc)
include(FindClangtidy)
include(FindCppcheck)
include(FindInfer)

# Library sources
add_library(dd_wrapper SHARED
    src/uploader_builder.cpp
    src/sample_manager.cpp
    src/profile.cpp
    src/uploader.cpp
    src/sample.cpp
    src/interface.cpp
)

# Add common configuration flags
add_ddup_config(dd_wrapper)

target_compile_features(dd_wrapper PUBLIC cxx_std_17)

target_include_directories(dd_wrapper PRIVATE
    include
    ${Datadog_INCLUDE_DIRS}
)
target_link_libraries(dd_wrapper PRIVATE
    ${Datadog_LIBRARIES}
)
set_target_properties(dd_wrapper PROPERTIES POSITION_INDEPENDENT_CODE ON)

# If LIB_INSTALL_DIR is set, install the library.
# Install one directory up--both ddup and stackv2 are set to the same relative level.
if (LIB_INSTALL_DIR)
    install(TARGETS dd_wrapper
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}/..
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}/..
        RUNTIME DESTINATION ${LIB_INSTALL_DIR}/..
    )
endif()

# Configure cppcheck
add_cppcheck_target(dd_wrapper
    DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
    INCLUDE ${CMAKE_CURRENT_SOURCE_DIR}/include
            ${Datadog_INCLUDE_DIRS}
    SRC ${CMAKE_CURRENT_SOURCE_DIR}/src
)

add_infer_target(dd_wrapper)
add_clangtidy_target(dd_wrapper)

# Add the tests
if (BUILD_TESTING)
    enable_testing()
    add_subdirectory(test)
endif()
