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

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/*.so" "${SOURCE_LIB_DIR}/*.lib" "${SOURCE_LIB_DIR}/*.pyd")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILES}")

if(WIN32)
    if(NOT DEFINED NATIVE_IMPLIB)
        set(NATIVE_IMPLIB ${CMAKE_SOURCE_DIR}/../../../../../src/native/target/release/_native.lib)
    endif()

    if(EXISTS "${NATIVE_IMPLIB}")
        message(WARNING "NATIVE_IMPLIB: ${NATIVE_IMPLIB}")
        add_library(_native SHARED IMPORTED GLOBAL)
        set_target_properties(_native PROPERTIES
            IMPORTED_LOCATION_RELEASE "${SOURCE_LIB_DIR}/${LIBRARY_NAME}"
            IMPORTED_IMPLIB_RELEASE "${NATIVE_IMPLIB}"
            IMPORTED_LOCATION_DEBUG "${SOURCE_LIB_DIR}/${LIBRARY_NAME}"
            IMPORTED_IMPLIB_DEBUG "${NATIVE_IMPLIB}"
            IMPORTED_LOCATION_RELWITHDEBINFO "${SOURCE_LIB_DIR}/${LIBRARY_NAME}"
            IMPORTED_IMPLIB_RELWITHDEBINFO "${NATIVE_IMPLIB}"
            IMPORTED_LOCATION_MINSIZEREL "${SOURCE_LIB_DIR}/${LIBRARY_NAME}"
            IMPORTED_IMPLIB_MINSIZEREL "${NATIVE_IMPLIB}"
            INTERFACE_INCLUDE_DIRECTORIES "${DEST_INCLUDE_DIR}"
        )
    else()
        message(WARNING "No .lib found — falling back to dummy interface target")
        add_library(_native INTERFACE)
        target_include_directories(_native INTERFACE "${DEST_INCLUDE_DIR}")
        set(_native_DLL_PATH "${SOURCE_LIB_DIR}/${LIBRARY_NAME}")
        # add_library(_native SHARED IMPORTED GLOBAL)
        # set_target_properties(_native PROPERTIES
        #     IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
        #     INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR}
        # )
    endif()
else()
    add_library(_native SHARED IMPORTED GLOBAL)
    set_target_properties(_native PROPERTIES
        IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
        INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR}
    )
endif()

