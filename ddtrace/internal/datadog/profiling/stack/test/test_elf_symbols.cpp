#include "elf_symbols.hpp"

#include <gtest/gtest.h>

#include <string>

namespace {

// libstdc++ renders nested closing angle brackets as "> >", libc++ as ">>".
std::string
collapse_angle_brackets(std::string text)
{
    size_t at = text.find("> >");
    while (at != std::string::npos) {
        text.erase(at + 1, 1);
        at = text.find("> >", at);
    }
    return text;
}

std::string
nested_rust_path(int depth)
{
    std::string mangled = "_R";
    for (int i = 0; i < depth; ++i) {
        mangled += "Nt";
    }
    mangled += "C4root";
    for (int i = 0; i < depth; ++i) {
        mangled += "4name";
    }
    return mangled;
}

} // namespace

TEST(DemangleSymbol, DecodesRustV0NestedPaths)
{
    // The name production this parser exists for: libdd_crashtracker's POSIX sigaction
    // entry point, which is what shows up when crashtracker owns SIGSEGV.
    EXPECT_EQ(demangle_symbol("_RNvNtNtCsg87oDJWqZI9_18libdd_crashtracker9collector13crash_handler22handle_posix_"
                              "sigaction"),
              "libdd_crashtracker::collector::crash_handler::handle_posix_sigaction");

    EXPECT_EQ(demangle_symbol(nested_rust_path(10)),
              "root::name::name::name::name::name::name::name::name::name::name");

    // A '_' after the length marks an identifier that itself starts with a digit.
    EXPECT_EQ(demangle_symbol("_RNvC4main4_0abc"), "main::0abc");
}

TEST(DemangleSymbol, DecodesItaniumAndLegacyRustNames)
{
    EXPECT_EQ(demangle_symbol("_ZN4absl20FailureSignalHandlerEiPvS0_"),
              "absl::FailureSignalHandler(int, void*, void*)");
    EXPECT_EQ(collapse_angle_brackets(demangle_symbol("_ZNSt6vectorIiSaIiEE9push_backERKi")),
              "std::vector<int, std::allocator<int>>::push_back(int const&)");
    EXPECT_EQ(demangle_symbol("_ZN4core3fmt9Formatter3pad17h0123456789abcdefE"),
              "core::fmt::Formatter::pad::h0123456789abcdef");
}

// A mangled name still identifies the offender, so anything the parser cannot fully
// decode must come back untouched rather than half-decoded or empty.
TEST(DemangleSymbol, ReturnsTheInputUnchangedForEverythingItCannotDecode)
{
    const char* const unchanged[] = {
        "",
        "foreign_handler",
        "_R",
        "_RNvB_totally bogus", // backreference
        "_R0NvC4main4func",    // format revision this parser predates
        "_RNvC",               // length prefix truncated away entirely
        "_RC10short",          // length prefix longer than what remains
        "_RC0_",               // zero-length identifier
        "_RCu8_foo_bar",       // punycode identifier
        "_Znot_a_real_symbol", // Itanium prefix, not an Itanium name
    };
    for (const char* name : unchanged) {
        EXPECT_EQ(demangle_symbol(name), name);
    }

    EXPECT_EQ(demangle_symbol(nested_rust_path(70)), nested_rust_path(70));
}

#if defined PL_LINUX

#include <cctype>
#include <cstdio>
#include <dlfcn.h>
#include <fstream>
#include <link.h>

namespace {

// Internal linkage on purpose: dladdr resolves only .dynsym, so this is exactly the
// kind of symbol describe_elf_address exists to name.
void
fixture_local_function(volatile int* sink)
{
    *sink = *sink + 1;
}

void (*volatile g_fixture_local_function)(volatile int*) = fixture_local_function;

struct ObjectLocation
{
    uintptr_t address = 0;
    std::string path;
    uintptr_t offset = 0;
    bool found = false;
};

// dladdr's dli_fbase is not the load bias for a non-PIE main executable, and st_value
// in the symbol table is relative to the bias, so derive the bias from the program
// headers instead.
int
locate_object(struct dl_phdr_info* info, size_t, void* data)
{
    auto* out = static_cast<ObjectLocation*>(data);
    for (int i = 0; i < info->dlpi_phnum; ++i) {
        const ElfW(Phdr)& header = info->dlpi_phdr[i];
        if (header.p_type != PT_LOAD) {
            continue;
        }
        const uintptr_t start = static_cast<uintptr_t>(info->dlpi_addr) + static_cast<uintptr_t>(header.p_vaddr);
        if (out->address < start || out->address >= start + static_cast<uintptr_t>(header.p_memsz)) {
            continue;
        }
        out->path = (info->dlpi_name != nullptr && info->dlpi_name[0] != '\0') ? info->dlpi_name : "/proc/self/exe";
        out->offset = out->address - static_cast<uintptr_t>(info->dlpi_addr);
        out->found = true;
        return 1;
    }
    return 0;
}

ObjectLocation
locate(const void* address)
{
    ObjectLocation out;
    out.address = reinterpret_cast<uintptr_t>(address);
    dl_iterate_phdr(locate_object, &out);
    return out;
}

bool
is_lowercase_hex(const std::string& text)
{
    for (const char c : text) {
        if (!std::isxdigit(static_cast<unsigned char>(c)) || std::isupper(static_cast<unsigned char>(c))) {
            return false;
        }
    }
    return true;
}

std::string
write_temp_file(const char* name, const std::string& contents)
{
    const std::string path = ::testing::TempDir() + name;
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(contents.data(), static_cast<std::streamsize>(contents.size()));
    out.close();
    return path;
}

} // namespace

TEST(DescribeElfAddress, NamesALocalSymbolThatDladdrCannotResolve)
{
    const void* address = reinterpret_cast<const void*>(g_fixture_local_function);

    Dl_info dl{};
    ASSERT_NE(dladdr(address, &dl), 0);
    ASSERT_NE(dl.dli_fname, nullptr);
    // The premise of the whole lookup: the name we want is not in .dynsym.
    EXPECT_EQ(dl.dli_sname, nullptr);

    const ObjectLocation location = locate(address);
    ASSERT_TRUE(location.found);

    const ElfAddressInfo info = describe_elf_address(location.path.c_str(), location.offset);
    EXPECT_NE(info.symbol.find("fixture_local_function"), std::string::npos) << "symbol was: " << info.symbol;
    EXPECT_FALSE(info.build_id.empty());
    EXPECT_TRUE(is_lowercase_hex(info.build_id)) << "build id was: " << info.build_id;
}

TEST(DescribeElfAddress, ReportsNoSymbolForAnOffsetOutsideEveryFunction)
{
    const ObjectLocation location = locate(reinterpret_cast<const void*>(g_fixture_local_function));
    ASSERT_TRUE(location.found);

    // Far past the end of the mapped image, so no symbol can cover it. The build ID
    // still resolves: the note scan does not depend on the offset.
    const ElfAddressInfo info = describe_elf_address(location.path.c_str(), uintptr_t{ 1 } << 40);
    EXPECT_TRUE(info.symbol.empty()) << "symbol was: " << info.symbol;
    EXPECT_FALSE(info.build_id.empty());
}

TEST(DescribeElfAddress, ReturnsEmptyFieldsForInputItCannotRead)
{
    for (const char* path : { static_cast<const char*>(nullptr), "", "/nonexistent/definitely/not/here.so" }) {
        const ElfAddressInfo info = describe_elf_address(path, 0);
        EXPECT_TRUE(info.symbol.empty());
        EXPECT_TRUE(info.build_id.empty());
    }
}

TEST(DescribeElfAddress, ReturnsEmptyFieldsForAFileThatIsNotAnElfImage)
{
    const std::string text = write_temp_file("dd_elf_symbols_not_elf.txt", "this is not an ELF file\n");
    const ElfAddressInfo from_text = describe_elf_address(text.c_str(), 0);
    EXPECT_TRUE(from_text.symbol.empty());
    EXPECT_TRUE(from_text.build_id.empty());

    // Right magic, nothing behind it: the header checks must reject before any offset
    // derived from it is used to index the mapping.
    std::string header(64, '\0');
    header[0] = 0x7f;
    header[1] = 'E';
    header[2] = 'L';
    header[3] = 'F';
    const std::string truncated = write_temp_file("dd_elf_symbols_truncated.so", header);
    const ElfAddressInfo from_truncated = describe_elf_address(truncated.c_str(), 0);
    EXPECT_TRUE(from_truncated.symbol.empty());
    EXPECT_TRUE(from_truncated.build_id.empty());

    std::remove(text.c_str());
    std::remove(truncated.c_str());
}

#endif
