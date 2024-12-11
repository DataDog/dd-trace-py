include(ExternalProject)

if(NOT TARGET llvm-libcxx-msan)
    ExternalProject_Add(llvm-libcxx-msan
        PREFIX            ${CMAKE_CURRENT_BINARY_DIR}/llvm-libcxx-msan
        GIT_REPOSITORY    https://github.com/llvm/llvm-project.git
        GIT_TAG           llvmorg-19.1.5 # chosen to make msan build straightforward
        GIT_SHALLOW       TRUE
        GIT_PROGRESS      TRUE
        SOURCE_SUBDIR     runtimes
        CMAKE_ARGS
            -DCMAKE_BUILD_TYPE=Release
            -DLLVM_ENABLE_RUNTIMES=libcxx$<SEMICOLON>libcxxabi$<SEMICOLON>libunwind
            -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
            -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
            -DLLVM_USE_SANITIZER=MemoryWithOrigins
        BUILD_COMMAND     cmake --build .
        INSTALL_COMMAND   ""
    )

  set(INSTALLED_LIBCXX_ROOT "${CMAKE_CURRENT_BINARY_DIR}/llvm-libcxx-msan/src/llvm-libcxx-msan-build")
  set(INSTALLED_LIBCXX_PATH "${INSTALLED_LIBCXX_ROOT}/lib")
endif()
