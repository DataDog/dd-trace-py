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

# At present, C++17 is chosen as the minimum standard. This conflicts with the
# manylinux2014 standard upon which we must rely. We'll have to statically link
# libstdc++ to avoid this conflict, but need to remain mindful of symbol visibility
# and overall binary size.
target_compile_features(dd_wrapper PUBLIC cxx_std_17)

# Statically link libstdc++ to avoid manylinux2014 conflicts
target_link_options(dd_wrapper PRIVATE -static-libstdc++)

target_include_directories(dd_wrapper PRIVATE
    include
    ${Datadog_INCLUDE_DIRS}
)
target_link_libraries(dd_wrapper PRIVATE
    ${Datadog_LIBRARIES}
)
set_target_properties(dd_wrapper PROPERTIES POSITION_INDEPENDENT_CODE ON)

set_target_properties(dd_wrapper
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${dd_wrapper_BUILD_DIR}"
)

# If LIB_INSTALL_DIR is set, install the library
if (LIB_INSTALL_DIR)
    install(TARGETS dd_wrapper
        DESTINATION "${LIB_INSTALL_DIR}"
    )
endif()

# Add the tests
if (BUILD_TESTING)
    enable_testing()
    add_subdirectory(test)
endif()
