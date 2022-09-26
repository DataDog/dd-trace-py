cmake_minimum_required(VERSION 3.14)
project(ddwaf)
include(ExternalProject)

ExternalProject_Add(libddwaf
    GIT_REPOSITORY https://github.com/DataDog/libddwaf.git
    GIT_TAG 6609da85ceb8850c2dd430da72f53b0cceafb326
    INSTALL_DIR ${CMAKE_SOURCE_DIR}
    CMAKE_ARGS
        -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
        -DCMAKE_MAKE_PROGRAM=${CMAKE_MAKE_PROGRAM}
        -DLIBDDWAF_BUILD_SHARED=OFF
        -DLIBDDWAF_BUILD_STATIC=ON
        -DLIBDDWAF_TESTING=OFF
        -DLIBDDWAF_MSVC_RUNTIME_LIBRARY=/MD
        -DCMAKE_INSTALL_LIBDIR=lib
        -DCMAKE_INSTALL_PREFIX:PATH=<INSTALL_DIR>
)
