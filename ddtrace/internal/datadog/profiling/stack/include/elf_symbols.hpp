#pragma once

#include <cstdint>
#include <string>

struct ElfAddressInfo
{
    // Mangled name of the function containing the address, empty if unresolved.
    std::string symbol;
    // Lowercase hex GNU build ID, empty if the file carries no build-id note.
    std::string build_id;
};

// Names the function containing `offset` in the ELF file at `path`, where `offset` is
// relative to the object's load base.
//
// This exists because dladdr resolves only .dynsym, and signal handlers are usually
// local symbols that appear only in .symtab - so dli_sname comes back null for exactly
// the addresses we most want named. Reading the file from disk sees the full table.
// When the binary is stripped the build ID still allows offline resolution.
//
// Reads and maps a file: not async-signal-safe, and must not be called from a handler.
// Returns empty fields rather than throwing on any malformed or unreadable input.
ElfAddressInfo
describe_elf_address(const char* path, uintptr_t offset) noexcept;

// Human-readable form of a linkage name: Itanium C++ and legacy Rust via the platform
// demangler, Rust v0 via a parser for the nested-path shape that function symbols take.
// Returns the input unchanged for anything it cannot fully decode, since a mangled name
// still identifies the offender.
std::string
demangle_symbol(const std::string& name) noexcept;
