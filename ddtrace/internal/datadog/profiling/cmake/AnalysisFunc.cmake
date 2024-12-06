include(CheckIPOSupported)

include(FindGoogleTest)
include(GoogleTest)

function(add_ddup_config target)
    # Profiling native extensions are built with C++17, even though underlying
    # repo adheres to the manylinux 2014 standard. This isn't currently a
    # problem, but if it becomes one, we may have to structure the library
    # differently.
    target_compile_features(${target} PUBLIC cxx_std_17)

    # Common compile options
    target_compile_options(${target} PRIVATE "$<$<CONFIG:Release>:-Os>" -ffunction-sections
        -Wall
        -Werror
        -Wextra
        -Wshadow
        -Wnon-virtual-dtor
        -Wold-style-cast)

    if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
        # macOS-specific options
        target_compile_options(
            ${target}
            PRIVATE "$<$<CONFIG:Debug>:-Og;-g>"
            "$<$<CONFIG:RelWithDebInfo>:-Os;-g>"
        )
    else()
        # Non-macOS (e.g., Linux) options
        target_compile_options(
            ${target}
            PRIVATE "$<$<CONFIG:Debug>:-Og;-ggdb3>"
            "$<$<CONFIG:RelWithDebInfo>:-Os;-ggdb3>"
            -fno-semantic-interposition
        )
    endif()

    # Common link options
    target_link_options(${target} PRIVATE "$<$<CONFIG:RelWithDebInfo>:>")

    if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
        # macOS-specific linker options
        target_link_options(${target} PRIVATE "$<$<CONFIG:Release>:-Wl,-dead_strip>")
    else()
        # Linux/ELF-based linker options
        target_link_options(
            ${target}
            PRIVATE
          "$<$<CONFIG:Release>:-s>"
        )

        # We treat the binary delicately around sanitizers, but the gloves come off for distributable builds
        if(NOT SANITIZE_OPTIONS)
            target_link_options(
                ${target}
                PRIVATE
                -Wl,--as-needed
                -Wl,-Bsymbolic-functions
                -Wl,--gc-sections
                -Wl,-z,nodelete
                -Wl,--exclude-libs,ALL
            )
        endif()
    endif()

    # If we can IPO, then do so
    check_ipo_supported(RESULT result)

    if(result)
        set_property(TARGET ${target} PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)
    endif()

    # Propagate sanitizers
    if(SANITIZE_OPTIONS)
        # Some sanitizers (or the analysis--such as symbolization--tooling thereof) work better with frame pointers, so
        # we include it here.
        target_compile_options(${target} PRIVATE -fsanitize=${SANITIZE_OPTIONS} -fno-omit-frame-pointer)
        target_link_options(${target} PRIVATE -fsanitize=${SANITIZE_OPTIONS})

    # If msan was chosen, also enable memory track origins, which helps in diagnostics
    if(SANITIZE_OPTIONS STREQUAL "memory")
        target_compile_options(${target} PRIVATE -fsanitize-memory-track-origins)

        # That was the easy part. Now we have to include an entire custom clang toolchain which has the msan runtime.
        # Selecting a version is hard, so just build the latest one.
        include(FindCxxMsan)

        add_dependencies(${target} llvm-libcxx-msan)
        target_compile_options(${target} PRIVATE
           -stdlib=libc++,
           -nostdinc++,
           -isystem ${CMAKE_CURRENT_BINARY_DIR}/llvm-libcxx-msan/install/include/c++/v1
        )

        target_link_options(${target} PRIVATE
            -stdlib=libc++
            -L${INSTALLED_LIBCXX_PATH}
            -lc++abi
        )
    endif()

    # Locate all directories containing relevant `.so` files
    execute_process(
        COMMAND bash -c "find $(${CMAKE_CXX_COMPILER} -print-file-name=) -name '*.so' -exec dirname {} \; | uniq"
        OUTPUT_VARIABLE LIBSAN_LIB_PATHS
        OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)

    # Print for debugging
    message(STATUS "LIBSAN_LIB_PATHS: ${LIBSAN_LIB_PATHS}")

    # Split the paths into a semicolon-separated list for CMake
    string(REPLACE "\n" ";" LIBSAN_LIB_PATHS_LIST "${LIBSAN_LIB_PATHS}")

    # Set RPATH to include all identified paths
    set_target_properties(${target} PROPERTIES
        BUILD_RPATH "${LIBSAN_LIB_PATHS_LIST}"
        INSTALL_RPATH "${LIBSAN_LIB_PATHS_LIST}")
    endif()

    # If DO_FANALYZER is specified and we're using gcc, then we can use -fanalyzer
    if(DO_FANALYZER AND CMAKE_CXX_COMPILER_ID MATCHES "GNU")
        target_compile_options(${target} PRIVATE -fanalyzer)
    endif()

    # The main targets, ddup, crashtracker, stack_v2, and dd_wrapper are built
    # as dynamic libraries, so PIC is required. And setting this is also fine
    # for tests as they're loading those dynamic libraries.
    set_target_properties(${target} PROPERTIES POSITION_INDEPENDENT_CODE ON)
endfunction()

set(INSTALLED_LIBCXX_PATH "${CMAKE_CURRENT_BINARY_DIR}/llvm-libcxx-msan/install/lib")
