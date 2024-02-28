# Only add this project if Infer is not defined
if(TARGET Infer)
    return()
endif()

include(ExternalProject)
set(TAG_INFER
    "v1.1.0"
    CACHE STRING "infer github tag")

set(Infer_BUILD_DIR ${CMAKE_BINARY_DIR}/infer)
set(Infer_ROOT ${Infer_BUILD_DIR}/infer-${TAG_LIBDATADOG})

message(STATUS "${CMAKE_CURRENT_LIST_DIR}/tools/fetch_infer.sh ${TAG_INFER} ${Infer_ROOT}")
execute_process(
  COMMAND "${CMAKE_CURRENT_LIST_DIR}/tools/fetch_infer.sh" ${TAG_INFER} ${Infer_ROOT}
  WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
  COMMAND_ERROR_IS_FATAL ANY
)

set(Infer_DIR "${Infer_ROOT}/infer")
