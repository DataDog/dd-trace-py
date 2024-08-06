# Only add this project if it doesn't already exist
if (TARGET cppcheck_project)
    return()
endif()

# Set the default value for the cppcheck option
option(DO_CPPCHECK "Enable cppcheck" OFF)
set(DD_CPPCHECK_VERSION "2.14.2" CACHE STRING "The version of cppcheck to use")
set(DD_CPPCHECK_VERSION_SHORT "2.14" CACHE STRING "The minor version of cppcheck to use")

if (DO_CPPCHECK)
  if (NOT DD_CPPCHECK_EXECUTABLE)
    # If we have a CPPCHECK_EXECUTABLE, then check that the version matches.  If it doesn't, then we need to rebuild it.
    if (CPPCHECK_EXECUTABLE AND EXISTS ${CPPCHECK_EXECUTABLE})
      execute_process(
        COMMAND ${CPPCHECK_EXECUTABLE} --version
        OUTPUT_VARIABLE CPPCHECK_VERSION
        OUTPUT_STRIP_TRAILING_WHITESPACE
      )
      string(REGEX REPLACE "Cppcheck ([0-9]+\\.[0-9]+).*" "\\1" CPPCHECK_VERSION ${CPPCHECK_VERSION})
      if (${CPPCHECK_VERSION} STREQUAL ${DD_CPPCHECK_VERSION_SHORT})
        message(STATUS "System cppcheck version is recent enough: ${CPPCHECK_VERSION}")
        set(DD_CPPCHECK_EXECUTABLE ${CPPCHECK_EXECUTABLE})
      else()
        message(STATUS "System cppcheck version mismatch: ${CPPCHECK_VERSION} != ${DD_CPPCHECK_VERSION_SHORT}")
      endif()
    endif()

    # If we're here and we still don't have a CPPCHECK_EXECUTABLE, then we need to build it
    if (NOT DD_CPPCHECK_EXECUTABLE)
      include(ExternalProject)
      ExternalProject_Add(cppcheck_project
        GIT_REPOSITORY https://github.com/danmar/cppcheck.git
        GIT_TAG ${DD_CPPCHECK_VERSION}
        CMAKE_ARGS -DCMAKE_INSTALL_PREFIX=${CMAKE_BINARY_DIR}/cppcheck
        BUILD_IN_SOURCE 1
        UPDATE_COMMAND ""
      )
      set(DD_CPPCHECK_EXECUTABLE ${CMAKE_BINARY_DIR}/cppcheck/bin/cppcheck CACHE FILEPATH "Path to cppcheck executable")
      ExternalProject_Get_Property(cppcheck_project install_dir)
      add_custom_target(check-cppcheck ALL DEPENDS cppcheck_project)
    endif()
  endif()
endif()

# This function will add a cppcheck target for a given directory
# unless DO_CPPCHECK is set to false, then it will add a target that does nothing
function(add_cppcheck_target)
  # Parse additional arguments as lists
  set(options)
  set(oneValueArgs TARGET)
  set(multiValueArgs INCLUDE SRC)
  cmake_parse_arguments(PARSE_ARGV 0 ARG "${options}" "${oneValueArgs}" "${multiValueArgs}")

  # Automatically generate the cppcheck target name
  set(NAME "cppcheck_${ARGV0}")

  if (DO_CPPCHECK)
    # Initialize command variable
    set(cppcheck_cmd
      ${DD_CPPCHECK_EXECUTABLE}
      --enable=all
      --addon=threadsafety.py
      --addon=misc
      --check-level=exhaustive
      --template="cppcheck:{id}:{file}:{line}:{severity}:{message}"
      --library=googletest
      --std=c++17
      --language=c++
      --suppress=missingIncludeSystem
      --inline-suppr
      --error-exitcode=1
    )

    # Append include directories to the command
    foreach(INCLUDE_DIR ${ARG_INCLUDE})
      message(STATUS "Adding include directory to cppcheck: ${INCLUDE_DIR}")
      list(APPEND cppcheck_cmd -I ${INCLUDE_DIR})
    endforeach()

    # Append source directories/files to the command
    foreach(SRC_FILE ${ARG_SRC})
      list(APPEND cppcheck_cmd ${SRC_FILE})
    endforeach()

    # Define the custom target with the constructed command
    add_custom_target(${NAME}
      COMMAND ${cppcheck_cmd}
      COMMENT "Running cppcheck on ${ARGV0}"
    )

    # Make the cppcheck target a dependent of the specified target
    add_dependencies(${ARGV0} ${NAME})
  else()
    # Define a do-nothing target if cppcheck is disabled
    add_custom_target(${NAME}
      COMMAND echo "Cppcheck target ${NAME} is disabled."
      COMMENT "cppcheck is disabled for ${ARGV0}"
    )
  endif()

  # Create a standard target to run everything
  if (NOT TARGET cppcheck)
    add_custom_target(cppcheck COMMENT "Runs cppcheck on all projects")
  endif()

  if (DO_CPPCHECK)
    add_dependencies(cppcheck ${NAME})
  endif()
endfunction()
