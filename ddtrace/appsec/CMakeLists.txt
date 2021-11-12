cmake_minimum_required(VERSION 3.14)
project(ddwaf)
include(ExternalProject)

ExternalProject_Add(libddwaf
    GIT_REPOSITORY https://github.com/DataDog/libddwaf.git
    GIT_TAG 841cbf703375ae0561b1e9c272a40734830b22fd
    INSTALL_DIR ${CMAKE_SOURCE_DIR}
    CMAKE_ARGS -DLIBDDWAF_BUILD_SHARED=OFF -DLIBDDWAF_BUILD_STATIC=ON -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_PREFIX:PATH=<INSTALL_DIR>
)
