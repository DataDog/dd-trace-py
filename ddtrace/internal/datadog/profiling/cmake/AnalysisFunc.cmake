include(CheckIPOSupported)

function(add_ddup_config target)
    target_compile_options(${target} PRIVATE
      "$<$<CONFIG:Debug>:-Og;-ggdb3>"
      "$<$<CONFIG:Release>:-Os>"
      -ffunction-sections -fno-semantic-interposition -Wall -Werror -Wextra -Wshadow -Wnon-virtual-dtor -Wold-style-cast
    )
    target_link_options(${target} PRIVATE
      "$<$<CONFIG:Release>:-s>"
      -Wl,--as-needed -Wl,-Bsymbolic-functions -Wl,--gc-sections
    )
    set_property(TARGET ${target} PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)
    check_ipo_supported(RESULT result)
    if (result)
      set_property(TARGET ${target} PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)
    endif()

    # Propagate sanitizers
    if (SANITIZE_OPTIONS)
        # Some sanitizers (or the analysis--such as symbolization--tooling thereof) work better with frame
        # pointers, so we include it here.
        target_compile_options(${target} PRIVATE -fsanitize=${SANITIZE_OPTIONS} -fno-omit-frame-pointer)
        target_link_options(${target} PRIVATE -fsanitize=${SANITIZE_OPTIONS})
    endif()

    # If DO_FANALYZER is specified and we're using gcc, then we can use -fanalyzer
    if (DO_FANALYZER AND CMAKE_CXX_COMPILER_ID MATCHES "GNU")
      target_compile_options(${target} PRIVATE -fanalyzer)
    endif()
endfunction()
