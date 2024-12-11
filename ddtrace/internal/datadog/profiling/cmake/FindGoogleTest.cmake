if(NOT TARGET googletest)
  message(STATUS "Fetching GoogleTest")

    FetchContent_Declare(
        googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        GIT_TAG v1.15.2)
    set(gtest_force_shared_crt
        ON
        CACHE BOOL "" FORCE)
    set(INSTALL_GTEST
        OFF
        CACHE BOOL "" FORCE)
    
    if(SANITIZE_OPTIONS AND SANITIZE_OPTIONS STREQUAL "memory")
      # It's not great to add these globally, but 
      #    # Also need to propagate -fsanitize, -stdlib, and the libcxx include/link directories and libraries to gtest, gmock
      #    target_compile_options(gtest PRIVATE -fsanitize=${SANITIZE_OPTIONS} -fsanitize-memory-track-origins -fno-omit-frame-pointer -stdlib=libc++)
      #    target_include_directories(gtest PRIVATE ${INSTALLED_LIBCXX_INCLUDE_DIR})
      #    target_link_directories(gtest PRIVATE ${INSTALLED_LIBCXX_PATH})
      #    target_link_libraries(gtest PRIVATE c++)
      #
      #    target_compile_options(gmock PRIVATE -fsanitize=${SANITIZE_OPTIONS} -fsanitize-memory-track-origins -fno-omit-frame-pointer -stdlib=libc++)
      #    target_include_directories(gmock PRIVATE ${INSTALLED_LIBCXX_INCLUDE_DIR})
      #    target_link_directories(gmock PRIVATE ${INSTALLED_LIBCXX_PATH})
      #    target_link_libraries(gmock PRIVATE c++)
        add_compile_options(
            -fsanitize=memory
            -fsanitize-memory-track-origins
            -fno-omit-frame-pointer
            -stdlib=libc++
            -I${INSTALLED_LIBCXX_INCLUDE_DIR}
        )
        add_link_options(
            -fsanitize=memory
            -stdlib=libc++
        )
        link_directories(${INSTALLED_LIBCXX_PATH})
        list(APPEND CMAKE_PREFIX_PATH "${INSTALLED_LIBCXX_PATH}")
    endif()
    
    #    FetchContent_Populate(googletest)
    add_subdirectory(${googletest_SOURCE_DIR})
    add_subdirectory(${googletest_BINARY_DIR})
endif()
