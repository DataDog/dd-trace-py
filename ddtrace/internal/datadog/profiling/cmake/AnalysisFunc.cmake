include(CheckIPOSupported)

function(add_ddup_config target)
    # Profiling native extensions are built with C++17, even though underlying repo adheres to the manylinux 2014
    # standard. This isn't currently a problem, but if it becomes one, we may have to structure the library differently.
    target_compile_features(${target} PUBLIC cxx_std_17)

    if(CMAKE_SYSTEM_NAME STREQUAL "Windows")
        # Named struct initialization is supported in C++20, but clang/gcc also support it in C++17 MSVC doesn't support
        # it until C++20.
        target_compile_features(${target} PUBLIC cxx_std_20)
        target_compile_options(
            ${target}
            PRIVATE "$<$<CONFIG:Release>:/O1>" # Optimize for size (equivalent to -Os)
                    /Gy # Enable function-level linking (similar to -ffunction-sections)
                    /W4 # Highest warning level (equivalent to -Wall -Wextra)
                    /WX # Treat warnings as errors (equivalent to -Werror)
                    /w44265 # Warn on shadowed variables (equivalent to -Wshadow)
                    /w44287 # Warn on missing virtual destructors (equivalent to -Wnon-virtual-dtor)
                    /w44412 # Warn on old-style casts (equivalent to -Wold-style-cast)
                    /wd4201 # Don't treat the following as an error C4201: nonstandard extension used: nameless
                            # struct/union
                    /wd4267 # conversion from 'size_t' to 'unsigned short', possible loss of data
                    /wd4996 # 'getpid': The POSIX name for this item is deprecated. Instead, use the ISO C and C++
                            # conformant name: _getpid.
                    /wd4100 # '_unusedop': unreferenced formal parameter
                    /wd4245 # conversion from 'int' to 'uint64_t'
                    /wd4244 # conversion from 'int64_t' to 'int'
                    /wd4551)
    else()
        # Common compile options
        target_compile_options(
            ${target}
            PRIVATE "$<$<CONFIG:Release>:-Os>"
                    -ffunction-sections
                    -Wall
                    -Werror
                    -Wextra
                    -Wshadow
                    -Wnon-virtual-dtor
                    -Wold-style-cast)

        if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
            # macOS-specific options
            target_compile_options(${target} PRIVATE "$<$<CONFIG:Debug>:-Og;-g>" "$<$<CONFIG:RelWithDebInfo>:-Os;-g>")
        else()
            # Non-macOS (e.g., Linux) options
            target_compile_options(
                ${target} PRIVATE "$<$<CONFIG:Debug>:-Og;-ggdb3>" "$<$<CONFIG:RelWithDebInfo>:-Os;-ggdb3>"
                                  -fno-semantic-interposition)
        endif()
    endif()

    # Common link options
    target_link_options(${target} PRIVATE "$<$<CONFIG:RelWithDebInfo>:>")

    if(CMAKE_SYSTEM_NAME STREQUAL "Windows")
        target_link_options(
            ${target}
            PRIVATE
            "$<$<CONFIG:Release>:/OPT:REF>" # Equivalent to --gc-sections
            # Disable incremental linking mainly to reduce the binary size
            # https://learn.microsoft.com/en-us/cpp/build/reference/incremental-link-incrementally?view=msvc-170#remarks
            "$<$<CONFIG:Release>:/INCREMENTAL:NO>"
            "/NODEFAULTLIB:ALL" # Rough equivalent of --exclude-libs,ALL
        )
    elseif(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
        # macOS-specific linker options
        target_link_options(${target} PRIVATE "$<$<CONFIG:Release>:-Wl,-dead_strip>")
        target_link_options(${target} PRIVATE -ldl -undefined dynamic_lookup)
    else()
        # Linux/ELF-based linker options
        target_link_options(
            ${target}
            PRIVATE
            "$<$<CONFIG:Release>:-s>"
            -Wl,--as-needed
            -Wl,-Bsymbolic-functions
            -Wl,--gc-sections
            -Wl,-z,nodelete
            -Wl,--exclude-libs,ALL)
    endif()

    # Don't do any complications for Windows for now
    if(NOT CMAKE_SYSTEM_NAME STREQUAL "Windows")
        # If we can IPO, then do so
        check_ipo_supported(RESULT result)

        if(result)
            set_property(TARGET ${target} PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)
        endif()

        # Propagate sanitizers
        if(SANITIZE_OPTIONS)
            # Some sanitizers (or the analysis--such as symbolization--tooling thereof) work better with frame pointers,
            # so we include it here.
            target_compile_options(${target} PRIVATE -fsanitize=${SANITIZE_OPTIONS} -fno-omit-frame-pointer)
            target_link_options(${target} PRIVATE -fsanitize=${SANITIZE_OPTIONS} -shared-libsan)

            # Locate all directories containing relevant `.so` files
            execute_process(
                COMMAND bash -c
                        "find $(${CMAKE_CXX_COMPILER} -print-file-name=) -name '*.so' -exec dirname {} \; | uniq"
                OUTPUT_VARIABLE LIBSAN_LIB_PATHS
                OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)

            # Print for debugging
            message(STATUS "LIBSAN_LIB_PATHS: ${LIBSAN_LIB_PATHS}")

            # Split the paths into a semicolon-separated list for CMake
            string(REPLACE "\n" ";" LIBSAN_LIB_PATHS_LIST "${LIBSAN_LIB_PATHS}")

            # Set RPATH to include all identified paths
            set_target_properties(${target} PROPERTIES BUILD_RPATH "${LIBSAN_LIB_PATHS_LIST}"
                                                       INSTALL_RPATH "${LIBSAN_LIB_PATHS_LIST}")
        endif()

        # If DO_FANALYZER is specified and we're using gcc, then we can use -fanalyzer
        if(DO_FANALYZER AND CMAKE_CXX_COMPILER_ID MATCHES "GNU")
            target_compile_options(${target} PRIVATE -fanalyzer)
        endif()

        # The main targets, ddup, crashtracker, stack_v2, and dd_wrapper are built as dynamic libraries, so PIC is
        # required. And setting this is also fine for tests as they're loading those dynamic libraries.
        set_target_properties(${target} PROPERTIES POSITION_INDEPENDENT_CODE ON)
    endif()
endfunction()
