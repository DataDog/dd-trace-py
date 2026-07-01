// Unit tests for the native ELF symbolizer (src/symbolize.c).
//
// These are pure-logic tests: maps-line parsing, vaddr->file-offset mapping,
// and an end-to-end resolve against the test binary's own /proc/self/maps. None
// of them need a live kernel, eBPF, or elevated capabilities, so they run in
// ordinary CI. The symbolizer C sources are compiled into this target as C
// (see test/CMakeLists.txt); we call them here with C linkage.

#include <gtest/gtest.h>

extern "C" {
#include "symbolize.h"
}
#include "symbolize_internal.h" // self-contained extern "C" guards

#include <cstdint>
#include <cstring>
#include <gelf.h>
#include <unistd.h>

// A named function in this binary that the self-resolve test looks up by
// address. extern "C" keeps the symbol name unmangled ("marker_func"); used +
// noinline keep it present in .symtab as a real STT_FUNC at a stable address.
extern "C" __attribute__((used, noinline)) void
marker_func(void)
{
    asm volatile(""); // defeat folding/eliding
}

TEST(MapsLine, ExecutableFileBackedAccepted)
{
    uint64_t start = 0, end = 0, offset = 0;
    char path[4096] = { 0 };
    const char* line = "7f8b8c000000-7f8b8c021000 r-xp 00001000 fd:01 1234 /usr/lib/libc.so.6\n";
    ASSERT_EQ(ddoffcpu_parse_maps_line(line, &start, &end, &offset, path, sizeof(path)), 1);
    EXPECT_EQ(start, 0x7f8b8c000000ULL);
    EXPECT_EQ(end, 0x7f8b8c021000ULL);
    EXPECT_EQ(offset, 0x1000ULL);
    EXPECT_STREQ(path, "/usr/lib/libc.so.6");
}

TEST(MapsLine, NonExecutableRejected)
{
    uint64_t start = 0, end = 0, offset = 0;
    char path[4096] = { 0 };
    const char* line = "7f8b8c000000-7f8b8c021000 r--p 00000000 fd:01 1234 /usr/lib/libc.so.6\n";
    EXPECT_EQ(ddoffcpu_parse_maps_line(line, &start, &end, &offset, path, sizeof(path)), 0);
}

TEST(MapsLine, AnonymousExecutableRejected)
{
    // Executable but no file path (e.g. a JIT mapping): must be rejected.
    uint64_t start = 0, end = 0, offset = 0;
    char path[4096] = { 0 };
    const char* line = "7f8b8c000000-7f8b8c021000 r-xp 00000000 00:00 0 \n";
    EXPECT_EQ(ddoffcpu_parse_maps_line(line, &start, &end, &offset, path, sizeof(path)), 0);
}

TEST(MapsLine, PseudoPathHeapRejected)
{
    // Executable mapping whose "path" is a pseudo name like [heap]: rejected
    // because it does not start with '/'.
    uint64_t start = 0, end = 0, offset = 0;
    char path[4096] = { 0 };
    const char* line = "00400000-00401000 r-xp 00000000 00:00 0 [heap]\n";
    EXPECT_EQ(ddoffcpu_parse_maps_line(line, &start, &end, &offset, path, sizeof(path)), 0);
}

TEST(MapsLine, PathWithSpacesPreserved)
{
    uint64_t start = 0, end = 0, offset = 0;
    char path[4096] = { 0 };
    const char* line = "00400000-00420000 r-xp 00000000 fd:01 99 /opt/my app/lib.so\n";
    ASSERT_EQ(ddoffcpu_parse_maps_line(line, &start, &end, &offset, path, sizeof(path)), 1);
    EXPECT_STREQ(path, "/opt/my app/lib.so");
}

TEST(VaddrToFoff, InRangeAndOutOfRange)
{
    GElf_Phdr loads[2];
    std::memset(loads, 0, sizeof(loads));
    loads[0].p_type = PT_LOAD;
    loads[0].p_vaddr = 0x1000;
    loads[0].p_memsz = 0x1000;
    loads[0].p_offset = 0x0;
    loads[1].p_type = PT_LOAD;
    loads[1].p_vaddr = 0x200000;
    loads[1].p_memsz = 0x1000;
    loads[1].p_offset = 0x2000;

    EXPECT_EQ(ddoffcpu_vaddr_to_foff(loads, 2, 0x1500), 0x500ULL);
    EXPECT_EQ(ddoffcpu_vaddr_to_foff(loads, 2, 0x200010), 0x2010ULL);
    EXPECT_EQ(ddoffcpu_vaddr_to_foff(loads, 2, 0x999999), UINT64_MAX);
}

TEST(Resolve, SelfResolvesKnownFunction)
{
    struct symbolizer* s = symbolizer_new(getpid());
    ASSERT_NE(s, nullptr);

    char buf[256] = { 0 };
    uint64_t addr = (uint64_t)(uintptr_t)&marker_func;
    int rc = symbolizer_resolve(s, addr, buf, sizeof(buf));
    EXPECT_EQ(rc, 0);
    EXPECT_NE(std::strstr(buf, "marker_func"), nullptr) << "resolved to: " << buf;

    symbolizer_free(s);
}

TEST(Resolve, AddressOutsideAnyMapping)
{
    struct symbolizer* s = symbolizer_new(getpid());
    ASSERT_NE(s, nullptr);

    char buf[256] = { 0 };
    int rc = symbolizer_resolve(s, 0x1, buf, sizeof(buf));
    EXPECT_EQ(rc, -1);
    EXPECT_STREQ(buf, "0x1");

    symbolizer_free(s);
}
