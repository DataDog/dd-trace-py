cmake_minimum_required(VERSION 3.14)
project(Echion LANGUAGES CXX)

find_package(Python3 COMPONENTS Interpreter Development REQUIRED)

set(CMAKE_REMOVE_ON_FAILURE OFF)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Use glob to get all the source files
file(GLOB_RECURSE SOURCE_FILES "echion/*.cc")

# Add source files
add_library(echion SHARED ${SOURCE_FILES})
target_include_directories(echion PUBLIC include)
target_include_directories(echion PUBLIC ${CMAKE_HOME_DIRECTORY})

if(${CMAKE_SYSTEM_NAME} STREQUAL "Linux")
  target_compile_definitions(echion PUBLIC PL_LINUX)
elseif(${CMAKE_SYSTEM_NAME} STREQUAL "Darwin")
  target_compile_definitions(echion PUBLIC PL_DARWIN)
else()
  message(FATAL_ERROR "Unsupported platform: ${CMAKE_SYSTEM_NAME}")
endif()

include_directories(SYSTEM ${Python3_INCLUDE_DIRS})
target_link_libraries(echion PUBLIC Python3::Python lzma)

if(${CMAKE_SYSTEM_NAME} STREQUAL "Linux")
  target_link_libraries(echion PUBLIC unwind)
endif()

target_compile_options(echion PRIVATE -Wno-unused-function -Wno-unused-parameter -Wno-macro-redefined -Wno-reorder -Wextra -Wall)
if(${CMAKE_SYSTEM_NAME} STREQUAL "Linux")
  target_compile_options(echion PRIVATE -Wno-changes-meaning)
endif()
