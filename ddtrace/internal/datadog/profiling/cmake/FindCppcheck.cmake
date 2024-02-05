# Unless explicitly stated otherwise all files in this repository are licensed under the Apache
# License Version 2.0. This product includes software developed at Datadog
# (https://www.datadoghq.com/). Copyright 2021-Present Datadog, Inc.

include(ExternalProject)

ExternalProject_Add(cppcheck_project
  GIT_REPOSITORY https://github.com/danmar/cppcheck.git
  GIT_TAG "2.13.3"
  CMAKE_ARGS -DCMAKE_INSTALL_PREFIX=${CMAKE_BINARY_DIR}/cppcheck
)
set(CPPCHECK_EXECUTABLE ${CMAKE_BINARY_DIR}/cppcheck/bin/cppcheck)
