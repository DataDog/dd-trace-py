cmake_minimum_required(VERSION 3.19)

# Fuzz targets are built only when stack/CMakeLists.txt has BUILD_FUZZING=ON. The caller is expected to provide
# compiler/linker flags for libFuzzer (e.g. -fsanitize=fuzzer,address,undefined).
option(STACK_USE_LIBFUZZER "Link fuzz targets with libFuzzer (-fsanitize=fuzzer)" OFF)

# Echion + profiling sources shared by all fuzz targets (everything except vm.cc,
# which conflicts with the fuzz copy_memory hook in vm.h).
set(FUZZ_ECHION_SOURCES
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
    ../src/stack_renderer.cpp
    ../src/sampler.cpp
    ../src/thread_span_links.cpp)

# Helper function: register a single fuzz target with all shared build settings.
function(add_fuzz_target TARGET_NAME)
    add_executable(${TARGET_NAME} ${FUZZ_ECHION_SOURCES} ${TARGET_NAME}.cpp)

    # Include paths: ../.. is the profiling root (for "dd_wrapper/include/..." paths), ../include is for stack headers.
    target_include_directories(${TARGET_NAME} PRIVATE ../.. ../include)
    target_include_directories(
        ${TARGET_NAME} SYSTEM PRIVATE ${Python3_INCLUDE_DIRS} ../echion ../include/vendored ../include/util
        ../../../../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include/)

    # Ensure echion headers take the fuzz hook in vm.h
    target_compile_definitions(${TARGET_NAME} PRIVATE ECHION_FUZZING)

    # When building with libFuzzer, add the fuzzer runtime only for this target.
    if(STACK_USE_LIBFUZZER)
        target_compile_definitions(${TARGET_NAME} PRIVATE FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION)
        target_compile_options(${TARGET_NAME} PRIVATE -fsanitize=fuzzer,address,undefined -fno-omit-frame-pointer)
        target_link_options(${TARGET_NAME} PRIVATE -fsanitize=fuzzer,address,undefined)
    endif()

    # Echion sources need to be given the current platform
    if(APPLE)
        target_compile_definitions(${TARGET_NAME} PRIVATE PL_DARWIN)
    elseif(UNIX)
        target_compile_definitions(${TARGET_NAME} PRIVATE PL_LINUX)
    endif()

    # Use the same ddup config helper for sanitizer/rpath defaults.
    add_ddup_config(${TARGET_NAME})

    # Link against dd_wrapper (imported target defined by the parent stack/CMakeLists.txt)
    target_link_libraries(${TARGET_NAME} PRIVATE dd_wrapper)

    if(Python3_LIBRARIES)
        target_link_libraries(${TARGET_NAME} PRIVATE ${Python3_LIBRARIES})
    endif()

    # Set RPATH so the fuzz binary can find dd_wrapper and _native at runtime.
    if(LIB_INSTALL_DIR)
        set_target_properties(${TARGET_NAME} PROPERTIES BUILD_RPATH "${LIB_INSTALL_DIR}" INSTALL_RPATH
                                                                                          "${LIB_INSTALL_DIR}")
    endif()
endfunction()

# Register all fuzz targets
add_fuzz_target(fuzz_echion_remote_read)
add_fuzz_target(fuzz_echion_strings)
add_fuzz_target(fuzz_echion_mirrors)
add_fuzz_target(fuzz_echion_stacks)
