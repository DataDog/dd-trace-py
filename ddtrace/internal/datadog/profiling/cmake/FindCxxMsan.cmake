include(ExternalProject)

if(NOT TARGET llvm-libcxx-msan)
    ExternalProject_Add(llvm-libcxx-msan
        PREFIX            ${CMAKE_CURRENT_BINARY_DIR}/llvm-libcxx-msan
        GIT_REPOSITORY    https://github.com/llvm/llvm-project.git
        GIT_TAG           llvmorg-17.0.6 # should probably reflect toolchain version!!!
        GIT_SHALLOW       TRUE
        GIT_PROGRESS      TRUE
        SOURCE_SUBDIR     runtimes
        CMAKE_ARGS
            -DCMAKE_BUILD_TYPE=Release
            -DLLVM_ENABLE_RUNTIMES=libcxx;libcxxabi;libunwind
            -DLIBCXX_CXX_ABI=libcxxabi
            -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
            -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
            -DLLVM_USE_SANITIZER=MemoryWithOrigins
            -DCMAKE_INSTALL_PREFIX=${CMAKE_CURRENT_BINARY_DIR}/llvm-libcxx-msan/install
        BUILD_COMMAND     cmake --build . -- cxx cxxabi unwind
        INSTALL_COMMAND   cmake --install .
    )
endif()
