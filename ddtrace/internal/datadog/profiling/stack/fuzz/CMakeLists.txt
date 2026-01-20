cmake_minimum_required(VERSION 3.19)

# Fuzz targets are built only when stack/CMakeLists.txt has BUILD_FUZZING=ON. The caller is expected to provide
# compiler/linker flags for libFuzzer (e.g. -fsanitize=fuzzer,address,undefined).
option(STACK_USE_LIBFUZZER "Link fuzz targets with libFuzzer (-fsanitize=fuzzer)" OFF)

add_executable(
    fuzz_echion_remote_read
    ../src/echion/danger.cc
    ../src/echion/frame.cc
    ../src/echion/greenlets.cc
    ../src/echion/interp.cc
    ../src/echion/long.cc
    ../src/echion/mirrors.cc
    ../src/echion/stack_chunk.cc
    ../src/echion/stacks.cc
    ../src/echion/strings.cc
    ../src/echion/tasks.cc
    ../src/echion/threads.cc
    ../src/echion/timing.cc
    fuzz_echion_remote_read.cpp)

target_include_directories(fuzz_echion_remote_read PRIVATE ../include)
target_include_directories(fuzz_echion_remote_read SYSTEM PRIVATE ${Python3_INCLUDE_DIRS} ../echion ../include/vendored
                                                                  ../include/util)

# Ensure echion headers take the fuzz hook in vm.h
target_compile_definitions(fuzz_echion_remote_read PRIVATE ECHION_FUZZING)

# When building with libFuzzer, add the fuzzer runtime only for this target.
if(STACK_USE_LIBFUZZER)
    target_compile_definitions(fuzz_echion_remote_read PRIVATE FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION)
    target_compile_options(fuzz_echion_remote_read PRIVATE -fsanitize=fuzzer,address,undefined -fno-omit-frame-pointer)
    target_link_options(fuzz_echion_remote_read PRIVATE -fsanitize=fuzzer,address,undefined)
endif()

# Echion sources need to be given the current platform
if(APPLE)
    target_compile_definitions(fuzz_echion_remote_read PRIVATE PL_DARWIN)
elseif(UNIX)
    target_compile_definitions(fuzz_echion_remote_read PRIVATE PL_LINUX)
endif()

# Use the same ddup config helper for sanitizer/rpath defaults.
add_ddup_config(fuzz_echion_remote_read)

if(Python3_LIBRARIES)
    target_link_libraries(fuzz_echion_remote_read PRIVATE ${Python3_LIBRARIES})
endif()
