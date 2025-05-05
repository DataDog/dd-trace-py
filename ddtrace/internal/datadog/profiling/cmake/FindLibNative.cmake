set(SOURCE_INCLUDE_DIR ${CMAKE_SOURCE_DIR}/../../../../../src/native/target/include)
set(SOURCE_LIB_DIR ${CMAKE_SOURCE_DIR}/../../../../../src/native/target/release)

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR}/libnative)
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)
set(DEST_LIB_OUTPUT_DIR ${DEST_LIB_DIR}/lib)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/*.so" "${SOURCE_LIB_DIR}/*.lib" "${SOURCE_LIB_DIR}/*.dll")
file(COPY ${LIB_FILES} DESTINATION ${DEST_LIB_OUTPUT_DIR})

# Add imported library target
add_library(native SHARED IMPORTED)

set_target_properties(native PROPERTIES
    IMPORTED_LOCATION "${DEST_LIB_OUTPUT_DIR}/libnative.so"
    INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR}
)

