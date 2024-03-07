# Only add this project if it doesn't already exist
if (TARGET cppcheck_project)
    return()
endif()

include(ExternalProject)

# Build cppcheck from sources
if (DO_CPPCHECK)
  ExternalProject_Add(cppcheck_project
    GIT_REPOSITORY https://github.com/danmar/cppcheck.git
    GIT_TAG "2.13.3"
    CMAKE_ARGS -DCMAKE_INSTALL_PREFIX=${CMAKE_BINARY_DIR}/cppcheck
  )
  set(CPPCHECK_EXECUTABLE ${CMAKE_BINARY_DIR}/cppcheck/bin/cppcheck)

  # The function we use to register targets for cppcheck would require us to run separate
  # commands for each target, which is annoying.  Instead we'll consolidate all the targets
  # as dependencies of a single target, and then run that target.
  add_custom_target(cppcheck_runner ALL COMMENT "Runs cppcheck on all defined targets")
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
  set(NAME "cppcheck_dd_${ARGV0}")

  if (DO_CPPCHECK)
    # Initialize command variable
    set(cppcheck_cmd
      ${CPPCHECK_EXECUTABLE}
      --enable=all
      --addon=threadsafety.py
      --addon=misc
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
endfunction()
