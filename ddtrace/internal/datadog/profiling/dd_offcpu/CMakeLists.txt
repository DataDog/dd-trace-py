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
# Toolchain: clang (BPF target), bpftool (vmlinux.h + skeleton), libelf, zlib.
#
# libbpf is *vendored and statically linked* (see below) rather than taken from
# the distro. Distros lag badly: Ubuntu 22.04 ships libbpf 0.5.0, which predates
# BTF_KIND_ENUM64 (libbpf 1.0) and cannot parse the BTF emitted by kernels >= 6.0
# — it fails at load with "failed to find valid kernel BTF". Pinning libbpf also
# keeps it in lockstep with the bpftool-generated skeleton. libelf and zlib are
# transitive dependencies of a statically linked libbpf.
# ------------------------------------------------------------------------------
find_program(CLANG_EXE clang REQUIRED)
find_program(BPFTOOL_EXE bpftool REQUIRED)

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBELF REQUIRED libelf)
find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

# ------------------------------------------------------------------------------
# Vendored libbpf: fetch a pinned release and build it as a static library at
# build time, then link it into dd_offcpu. Same approach as libbpf-tools.
# ------------------------------------------------------------------------------
include(ExternalProject)

set(LIBBPF_VERSION
    "v1.5.1"
    CACHE STRING "Pinned libbpf version to vendor and statically link")

set(LIBBPF_PREFIX "${CMAKE_CURRENT_BINARY_DIR}/libbpf")
set(LIBBPF_INSTALL "${LIBBPF_PREFIX}/usr")
set(LIBBPF_INCLUDE "${LIBBPF_INSTALL}/include")
set(LIBBPF_LIBDIR "${LIBBPF_INSTALL}/lib")
set(LIBBPF_A "${LIBBPF_LIBDIR}/libbpf.a")

# The imported include dir must exist at configure time, before libbpf is built.
file(MAKE_DIRECTORY ${LIBBPF_INCLUDE})

ExternalProject_Add(
    libbpf_vendored
    GIT_REPOSITORY https://github.com/libbpf/libbpf.git
    GIT_TAG ${LIBBPF_VERSION}
    GIT_SHALLOW TRUE
    UPDATE_DISCONNECTED TRUE
    PREFIX ${LIBBPF_PREFIX}
    CONFIGURE_COMMAND ""
    BUILD_IN_SOURCE TRUE
    # libbpf is a plain Makefile project; build static-only and install headers
    # (install pulls in install_uapi_headers) into our build-tree prefix.
    BUILD_COMMAND make -C src BUILD_STATIC_ONLY=1 PREFIX=${LIBBPF_INSTALL} LIBDIR=${LIBBPF_LIBDIR} install
    INSTALL_COMMAND ""
    BUILD_BYPRODUCTS ${LIBBPF_A})

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
            -I${CMAKE_CURRENT_SOURCE_DIR}/shared -I${LIBBPF_INCLUDE} -c ${BPF_SRC} -o ${BPF_OBJ}
    DEPENDS ${BPF_SRC} ${VMLINUX_H} ${CMAKE_CURRENT_SOURCE_DIR}/shared/offcpu.h ${LIBBPF_A}
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

# offcpu_skel (skeleton) and libbpf_vendored (static lib + headers) must exist
# before the daemon compiles/links.
add_dependencies(dd_offcpu offcpu_skel libbpf_vendored)

target_include_directories(dd_offcpu PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/shared ${CMAKE_CURRENT_SOURCE_DIR}/src
                                             ${GEN_DIR} ${LIBBPF_INCLUDE} ${LIBELF_INCLUDE_DIRS})

# Static libbpf first, then its transitive deps (libelf, zlib, pthread).
target_link_libraries(dd_offcpu PRIVATE ${LIBBPF_A} ${LIBELF_LIBRARIES} ZLIB::ZLIB Threads::Threads)
target_link_directories(dd_offcpu PRIVATE ${LIBELF_LIBRARY_DIRS})

if(LIB_INSTALL_DIR)
    install(TARGETS dd_offcpu RUNTIME DESTINATION ${LIB_INSTALL_DIR})
endif()
