include(CheckIPOSupported)

function(add_ddup_config target)
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
            -Wl,--as-needed
            -Wl,-Bsymbolic-functions
            -Wl,--gc-sections
            -Wl,-z,nodelete
            -Wl,--exclude-libs,ALL)
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

        # We need to do a little bit of work in order to ensure the dynamic *san libraries can be linked Procedure
        # adapted from datadog/ddprof :)
        execute_process(
            COMMAND ${CMAKE_CXX_COMPILER} -print-file-name=
            OUTPUT_VARIABLE LIBSAN_LIB_PATH
            OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
        set_target_properties(${target} PROPERTIES INSTALL_RPATH ${LIBSAN_LIB_PATH} BUILD_RPATH ${LIBSAN_LIB_PATH})
    endif()

    # If DO_FANALYZER is specified and we're using gcc, then we can use -fanalyzer
    if(DO_FANALYZER AND CMAKE_CXX_COMPILER_ID MATCHES "GNU")
        target_compile_options(${target} PRIVATE -fanalyzer)
    endif()
endfunction()
