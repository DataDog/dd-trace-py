include(ExternalProject)

# Build cppcheck from sources
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

# This function will add a cppcheck target for a given directory
# unless DO_CPPCHECK is set to false, then it will add a target that does nothing
function(add_cppcheck_target NAME DIRECTORY)
  if (DO_CPPCHECK)
    add_custom_target("${NAME}"
      COMMAND ${CPPCHECK_EXECUTABLE}
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
      -I ${CMAKE_SOURCE_DIR}/include
      -I ${Datadog_INCLUDE_DIRS}
      ${CMAKE_SOURCE_DIR}/src
      WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
      COMMENT "Running cppcheck"
    )
  else()
    # Explicitly:  target does not depend on cppcheck, so cppcheck will not be built
    add_custom_target("${NAME}"
      COMMAND echo "building ${NAME} without cppcheck"
      COMMENT "cppcheck is disabled"
    )
  endif()

  add_dependencies(cppcheck_runner "${NAME}")
endfunction()
