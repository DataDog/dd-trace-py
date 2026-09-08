include(FetchContent)
set(FETCHCONTENT_UPDATES_DISCONNECTED
    ON
    CACHE BOOL "" FORCE)
FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.15.2
    GIT_SHALLOW TRUE
    GIT_PROGRESS TRUE)
set(gtest_force_shared_crt
    ON
    CACHE BOOL "" FORCE)
set(INSTALL_GTEST
    OFF
    CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)
include(GoogleTest)
include(AnalysisFunc)

find_package(Python3 COMPONENTS Interpreter Development)

if(DO_VALGRIND)
    find_program(
        VALGRIND_EXECUTABLE
        NAMES valgrind
        PATHS /usr/bin /usr/local/bin)

    if(VALGRIND_EXECUTABLE)
        set(MEMORYCHECK_COMMAND "${VALGRIND_EXECUTABLE}")
        set(MEMORYCHECK_COMMAND_OPTIONS
            "--leak-check=full --show-leak-kinds=definite --errors-for-leak-kinds=definite --trace-children=yes --error-exitcode=1 --log-fd=1 --suppressions=${CMAKE_CURRENT_SOURCE_DIR}/valgrind.supp"
        )
    else()
        message(FATAL_ERROR "Valgrind not found")
    endif()

    include(CTest)
endif()

function(dd_wrapper_add_test name)
    add_executable(${name} ${ARGN})
    target_include_directories(${name} PRIVATE ../include $<TARGET_PROPERTY:_native,INTERFACE_INCLUDE_DIRECTORIES>)
    target_link_libraries(${name} PRIVATE gmock gtest_main dd_wrapper _native ${Python3_LIBRARIES})
    add_ddup_config(${name})
    if(NOT APPLE)
        target_link_options(${name} PRIVATE -Wl,--no-as-needed)
    endif()

    # Test executable needs to find libdd_wrapper in parent directory Append to INSTALL_RPATH instead of replacing to
    # preserve sanitizer library paths set by add_ddup_config
    if(APPLE)
        set(_rpath_prefix "@loader_path")
    else()
        set(_rpath_prefix "$ORIGIN")
    endif()

    get_target_property(_native_location _native IMPORTED_LOCATION)
    get_filename_component(_native_dir "${_native_location}" DIRECTORY)
    get_target_property(EXISTING_INSTALL_RPATH ${name} INSTALL_RPATH)
    if(EXISTING_INSTALL_RPATH)
        set_target_properties(
            ${name} PROPERTIES INSTALL_RPATH "${EXISTING_INSTALL_RPATH};${_rpath_prefix}/..;${_native_dir}"
                               BUILD_WITH_INSTALL_RPATH TRUE)
    else()
        set_target_properties(${name} PROPERTIES INSTALL_RPATH "${_rpath_prefix}/..;${_native_dir}"
                                                 BUILD_WITH_INSTALL_RPATH TRUE)
    endif()

    gtest_discover_tests(
        ${name} DISCOVERY_MODE PRE_TEST # Delay test discovery until test execution to avoid running sanitizer-built
                                        # executables during build
        PROPERTIES # We start new threads after fork(), and we want to continue running the tests after that instead of
                   # dying.
                   ENVIRONMENT "TSAN_OPTIONS=die_after_fork=0:suppressions=${CMAKE_CURRENT_SOURCE_DIR}/TSan.supp")

    if(LIB_INSTALL_DIR)
        get_filename_component(_lib_install_name "${LIB_INSTALL_DIR}" NAME)
        if(_lib_install_name STREQUAL "lib")
            get_filename_component(_test_install_root "${LIB_INSTALL_DIR}" DIRECTORY)
        else()
            set(_test_install_root "${LIB_INSTALL_DIR}")
        endif()
        install(TARGETS ${name} RUNTIME DESTINATION "${_test_install_root}/test")
    endif()
endfunction()

# Add the tests
dd_wrapper_add_test(test_initialization test_initialization.cpp)
dd_wrapper_add_test(test_api test_api.cpp)
dd_wrapper_add_test(test_threading test_threading.cpp)
dd_wrapper_add_test(test_forking test_forking.cpp)
dd_wrapper_add_test(test_cleanup_race test_cleanup_race.cpp)

if(EXISTS "${LIBDD_PROFILING_CXX_SOURCE}" AND EXISTS "${LIBDD_PROFILING_CXXBRIDGE_LIB}")
    set_source_files_properties(test_cxx_bridge.cpp PROPERTIES COMPILE_FLAGS "-Wno-old-style-cast")
    dd_wrapper_add_test(test_cxx_bridge test_cxx_bridge.cpp)
    target_link_libraries(test_cxx_bridge PRIVATE "${LIBDD_PROFILING_CXXBRIDGE_LIB}")
else()
    message(
        FATAL_ERROR
            "Generated libdd-profiling CXX bridge artifacts not found: ${LIBDD_PROFILING_CXX_SOURCE}; ${LIBDD_PROFILING_CXXBRIDGE_LIB}"
    )
endif()
