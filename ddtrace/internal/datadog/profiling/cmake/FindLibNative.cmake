find_package(Python3)

if(DEFINED NATIVE_EXTENSION_LOCATION)
    set(SOURCE_LIB_DIR ${NATIVE_EXTENSION_LOCATION})
else()
    set(SOURCE_LIB_DIR ${CMAKE_SOURCE_DIR}/../../../../../src/native/target/release)
endif()

if(DEFINED EXTENSION_SUFFIX)
    set(LIBRARY_NAME _native${EXTENSION_SUFFIX})
else()
    set(LIBRARY_NAME lib_native.so)
endif()

message(WARNING "SOURCE_LIB_DIR: ${SOURCE_LIB_DIR}")
message(WARNING "LIBRARY_NAME: ${LIBRARY_NAME}")

set(SOURCE_INCLUDE_DIR ${CMAKE_SOURCE_DIR}/../../../../../src/native/target/include)

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/${LIBRARY_NAME}")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILES}")

add_library(_native SHARED IMPORTED GLOBAL)
set_target_properties(_native PROPERTIES IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
                                         INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR})
