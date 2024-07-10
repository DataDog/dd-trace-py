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

set(BOOST_ENABLE_CMAKE ON)
set(BOOST_RUNTIME_LINK static)

if(WIN32)
    set(BOOST_URL "https://archives.boost.io/release/1.85.0/source/boost_1_85_0.zip")
else()
    set(BOOST_URL "https://archives.boost.io/release/1.85.0/source/boost_1_85_0.tar.bz2")
endif()


# Configure FetchContent to download Boost
FetchContent_Declare(
    Boost
    URL ${BOOST_URL}
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)

# Make boost available
FetchContent_MakeAvailable(Boost)

message("boost_SOURCE_DIR: ${boost_SOURCE_DIR}")

add_library(Boost_lockfree INTERFACE)
add_library(Boost::lockfree ALIAS Boost_lockfree)
target_include_directories(Boost_lockfree SYSTEM INTERFACE ${boost_SOURCE_DIR})

# Library sources
add_library(dd_wrapper SHARED
    src/uploader_builder.cpp
    src/sample_manager.cpp
    src/profile.cpp
    src/uploader.cpp
    src/sample.cpp
    src/ddup_interface.cpp
    src/crashtracker.cpp
    src/crashtracker_interface.cpp
    src/receiver_interface.cpp
    src/synchronized_sample_pool.cpp
)

# Add common configuration flags
add_ddup_config(dd_wrapper)

target_compile_features(dd_wrapper PUBLIC cxx_std_17)

target_include_directories(dd_wrapper PRIVATE
    include
    ${Datadog_INCLUDE_DIRS} ${boost_INCLUDE_DIRS}
)
target_link_libraries(dd_wrapper PRIVATE
    ${Datadog_LIBRARIES} Boost::lockfree
)
set_target_properties(dd_wrapper PROPERTIES POSITION_INDEPENDENT_CODE ON)

# For a regular build, the LIB_INSTALL_DIR represents the final location of the library, so nothing special is needed.
# However, for an inplace build, setup.py will pass a temporary path as the extension output directory, so while it
# will handle the extension artifacts themselves, supplementary files like this one will be left uncopied.
# One way around this is to propagate the original source dir of the extension, which can be used to deduce the
# ideal install directory.
if(INPLACE_LIB_INSTALL_DIR)
    set(LIB_INSTALL_DIR "${INPLACE_LIB_INSTALL_DIR}")
endif()

# If LIB_INSTALL_DIR is set, install the library.
# Install one directory up--ddup, crashtracker, and stackv2 are set to the same relative level.
if(LIB_INSTALL_DIR)
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

# Static analysis
add_infer_target(dd_wrapper)
add_clangtidy_target(dd_wrapper)

# Add the tests
if(BUILD_TESTING)
    enable_testing()
    add_subdirectory(test)
endif()
