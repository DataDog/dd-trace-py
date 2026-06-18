cmake_minimum_required(VERSION 3.19)

project(
    dd_offcpu
    VERSION 0.0.1
    LANGUAGES C)

# This is a Linux + eBPF-only component. Fail early and clearly elsewhere.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "dd_offcpu requires Linux (eBPF); current system is ${CMAKE_SYSTEM_NAME}")
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# ------------------------------------------------------------------------------
# Toolchain: clang (BPF target), bpftool (vmlinux.h + skeleton), libbpf, libelf.
# ------------------------------------------------------------------------------
find_program(CLANG_EXE clang REQUIRED)
find_program(BPFTOOL_EXE bpftool REQUIRED)

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBBPF REQUIRED libbpf)
pkg_check_modules(LIBELF REQUIRED libelf)

# Map the host processor onto clang's __TARGET_ARCH_* macro used by CO-RE.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64")
    set(BPF_TARGET_ARCH "x86")
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    set(BPF_TARGET_ARCH "arm64")
else()
    message(FATAL_ERROR "Unsupported processor for BPF build: ${CMAKE_SYSTEM_PROCESSOR}")
endif()

set(GEN_DIR "${CMAKE_CURRENT_BINARY_DIR}/gen")
file(MAKE_DIRECTORY ${GEN_DIR})

# ------------------------------------------------------------------------------
# Step 1: vmlinux.h from the running kernel's BTF (CO-RE type definitions).
# ------------------------------------------------------------------------------
set(VMLINUX_H "${GEN_DIR}/vmlinux.h")
add_custom_command(
    OUTPUT ${VMLINUX_H}
    COMMAND ${BPFTOOL_EXE} btf dump file /sys/kernel/btf/vmlinux format c > ${VMLINUX_H}
    COMMENT "Generating vmlinux.h from kernel BTF"
    VERBATIM)

# ------------------------------------------------------------------------------
# Step 2: compile the BPF program to a CO-RE object.
# ------------------------------------------------------------------------------
set(BPF_SRC "${CMAKE_CURRENT_SOURCE_DIR}/bpf/offcpu.bpf.c")
set(BPF_OBJ "${GEN_DIR}/offcpu.bpf.o")
add_custom_command(
    OUTPUT ${BPF_OBJ}
    COMMAND ${CLANG_EXE} -g -O2 -target bpf -D__TARGET_ARCH_${BPF_TARGET_ARCH} -I${GEN_DIR}
            -I${CMAKE_CURRENT_SOURCE_DIR}/include ${LIBBPF_INCLUDE_DIRS} -c ${BPF_SRC} -o ${BPF_OBJ}
    DEPENDS ${BPF_SRC} ${VMLINUX_H} ${CMAKE_CURRENT_SOURCE_DIR}/include/offcpu.h
    COMMENT "Compiling BPF program offcpu.bpf.c"
    VERBATIM)

# ------------------------------------------------------------------------------
# Step 3: generate the libbpf skeleton header from the BPF object.
# ------------------------------------------------------------------------------
set(SKEL_H "${GEN_DIR}/offcpu.skel.h")
add_custom_command(
    OUTPUT ${SKEL_H}
    COMMAND ${BPFTOOL_EXE} gen skeleton ${BPF_OBJ} > ${SKEL_H}
    DEPENDS ${BPF_OBJ}
    COMMENT "Generating BPF skeleton offcpu.skel.h"
    VERBATIM)

add_custom_target(offcpu_skel DEPENDS ${SKEL_H})

# ------------------------------------------------------------------------------
# Step 4: the userspace daemon.
# ------------------------------------------------------------------------------
add_executable(dd_offcpu src/main.c src/pysym.c src/symbolize.c)

add_dependencies(dd_offcpu offcpu_skel)

target_include_directories(dd_offcpu PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include ${CMAKE_CURRENT_SOURCE_DIR}/src
                                             ${GEN_DIR} ${LIBBPF_INCLUDE_DIRS} ${LIBELF_INCLUDE_DIRS})

target_link_libraries(dd_offcpu PRIVATE ${LIBBPF_LIBRARIES} ${LIBELF_LIBRARIES})
target_link_directories(dd_offcpu PRIVATE ${LIBBPF_LIBRARY_DIRS} ${LIBELF_LIBRARY_DIRS})

if(LIB_INSTALL_DIR)
    install(TARGETS dd_offcpu RUNTIME DESTINATION ${LIB_INSTALL_DIR})
endif()
