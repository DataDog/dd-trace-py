cmake_minimum_required(VERSION 3.14)
include(FetchContent)

FetchContent_Populate(
  libcpp
  URL https://github.com/DataDog/libddwaf/releases/download/1.5.1/libc++-static-x86_64-linux.tar.gz
  SOURCE_DIR ${CMAKE_SOURCE_DIR}/libcpp
)

FetchContent_Populate(
  ddwaf
  URL https://github.com/DataDog/libddwaf/releases/download/1.5.1/libddwaf-1.5.1-linux-x86_64.tar.gz
  SOURCE_DIR ${CMAKE_SOURCE_DIR}/libddwaf
)
