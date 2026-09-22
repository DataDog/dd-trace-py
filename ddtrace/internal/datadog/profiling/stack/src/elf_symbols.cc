#include "elf_symbols.hpp"

#include <cstdlib>
#include <cxxabi.h>

namespace {

// Rust v0 (RFC 2603) covers generics, backreferences and punycode identifiers. Decoding
// all of it is not worth it here, so this handles only the nested-path shape a function
// symbol takes and refuses anything else, leaving the caller with the mangled name.
class RustV0PathParser
{
  public:
    RustV0PathParser(const std::string& mangled, size_t start) noexcept
      : mangled_(mangled)
      , pos_(start)
    {
    }

    bool parse_path(std::string& out) noexcept
    {
        if (++depth_ > kMaxDepth) {
            return false;
        }
        if (pos_ >= mangled_.size()) {
            return false;
        }
        switch (mangled_[pos_++]) {
            case 'C': // crate root
                return parse_identifier(out);
            case 'N': { // <namespace> <parent path> <identifier>
                if (pos_ >= mangled_.size()) {
                    return false;
                }
                ++pos_; // namespace tag: not rendered
                std::string parent;
                if (!parse_path(parent)) {
                    return false;
                }
                std::string name;
                if (!parse_identifier(name)) {
                    return false;
                }
                out = parent + "::" + name;
                return true;
            }
            default:
                return false;
        }
    }

  private:
    static constexpr int kMaxDepth = 64;

    static bool is_base62(char c) noexcept
    {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
    }

    bool parse_identifier(std::string& out) noexcept
    {
        // Optional disambiguator 's' <base-62> '_'. An identifier otherwise starts with
        // a digit or 'u', so a leading 's' is unambiguous.
        if (pos_ < mangled_.size() && mangled_[pos_] == 's') {
            ++pos_;
            while (pos_ < mangled_.size() && mangled_[pos_] != '_') {
                if (!is_base62(mangled_[pos_])) {
                    return false;
                }
                ++pos_;
            }
            if (pos_ >= mangled_.size()) {
                return false;
            }
            ++pos_;
        }

        if (pos_ < mangled_.size() && mangled_[pos_] == 'u') {
            return false; // punycode
        }

        size_t length = 0;
        size_t digits = 0;
        while (pos_ < mangled_.size() && mangled_[pos_] >= '0' && mangled_[pos_] <= '9') {
            if (length > (kMaxIdentifier - 9) / 10) {
                return false;
            }
            length = length * 10 + static_cast<size_t>(mangled_[pos_] - '0');
            ++pos_;
            ++digits;
        }
        if (digits == 0 || length == 0) {
            return false;
        }

        // A '_' separates the length from an identifier that itself starts with a digit.
        if (pos_ < mangled_.size() && mangled_[pos_] == '_') {
            ++pos_;
        }
        if (length > mangled_.size() - pos_) {
            return false;
        }
        out.assign(mangled_, pos_, length);
        pos_ += length;
        return true;
    }

    static constexpr size_t kMaxIdentifier = 1u << 20;

    const std::string& mangled_;
    size_t pos_;
    int depth_ = 0;
};

} // namespace

std::string
demangle_symbol(const std::string& name) noexcept
{
    try {
        if (name.size() > 2 && name[0] == '_' && name[1] == 'R') {
            // A decimal right after _R marks a format revision this parser predates.
            if (name[2] < '0' || name[2] > '9') {
                RustV0PathParser parser(name, 2);
                std::string path;
                if (parser.parse_path(path)) {
                    return path;
                }
            }
            return name;
        }

        if (name.compare(0, 2, "_Z") == 0 || name.compare(0, 3, "__Z") == 0) {
            int status = 0;
            char* buffer = abi::__cxa_demangle(name.c_str(), nullptr, nullptr, &status);
            if (buffer == nullptr) {
                return name;
            }
            std::string out = (status == 0) ? std::string(buffer) : name;
            std::free(buffer);
            return out;
        }
    } catch (...) {
        // Naming the offender is a convenience; never let it throw into the sampler.
    }
    return name;
}

#if defined PL_LINUX

#include <cstring>
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {

using Ehdr = ElfW(Ehdr);
using Shdr = ElfW(Shdr);
using Sym = ElfW(Sym);
using Nhdr = ElfW(Nhdr);

// Diagnostics are not worth an unbounded read of whatever dladdr handed us.
constexpr size_t kMaxFileSize = 512u * 1024u * 1024u;
constexpr unsigned char kNativeClass = (sizeof(void*) == 8) ? ELFCLASS64 : ELFCLASS32;

class MappedFile
{
  public:
    explicit MappedFile(const char* path) noexcept
    {
        const int fd = ::open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) {
            return;
        }
        struct stat st
        {};
        if (::fstat(fd, &st) == 0 && st.st_size > 0 && static_cast<size_t>(st.st_size) <= kMaxFileSize) {
            const size_t size = static_cast<size_t>(st.st_size);
            void* mapping = ::mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
            if (mapping != MAP_FAILED) {
                data_ = static_cast<const unsigned char*>(mapping);
                size_ = size;
            }
        }
        ::close(fd);
    }

    ~MappedFile()
    {
        if (data_ != nullptr) {
            ::munmap(const_cast<unsigned char*>(data_), size_);
        }
    }

    MappedFile(const MappedFile&) = delete;
    MappedFile& operator=(const MappedFile&) = delete;

    bool valid() const noexcept { return data_ != nullptr; }

    // Bounds-checked view of `count` bytes at `offset`, or nullptr if out of range.
    // Written to avoid overflow on hostile offsets.
    const unsigned char* view(size_t offset, size_t count) const noexcept
    {
        if (data_ == nullptr || count > size_ || offset > size_ - count) {
            return nullptr;
        }
        return data_ + offset;
    }

  private:
    const unsigned char* data_ = nullptr;
    size_t size_ = 0;
};

template<typename T>
bool
read_at(const MappedFile& file, size_t offset, T& out) noexcept
{
    const unsigned char* bytes = file.view(offset, sizeof(T));
    if (bytes == nullptr) {
        return false;
    }
    std::memcpy(&out, bytes, sizeof(T));
    return true;
}

std::string
to_hex(const unsigned char* bytes, size_t count)
{
    static constexpr char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(count * 2);
    for (size_t i = 0; i < count; ++i) {
        out.push_back(digits[bytes[i] >> 4]);
        out.push_back(digits[bytes[i] & 0x0f]);
    }
    return out;
}

// Scans a SHT_NOTE section for NT_GNU_BUILD_ID. Note entries are 4-byte aligned
// name/description pairs following a fixed header.
std::string
read_build_id(const MappedFile& file, const Shdr& section)
{
    size_t cursor = static_cast<size_t>(section.sh_offset);
    const size_t end = cursor + static_cast<size_t>(section.sh_size);
    while (cursor + sizeof(Nhdr) <= end) {
        Nhdr note{};
        if (!read_at(file, cursor, note)) {
            return {};
        }
        const size_t name_size = (static_cast<size_t>(note.n_namesz) + 3u) & ~size_t{ 3u };
        const size_t desc_size = (static_cast<size_t>(note.n_descsz) + 3u) & ~size_t{ 3u };
        const size_t name_at = cursor + sizeof(Nhdr);
        const size_t desc_at = name_at + name_size;

        if (note.n_type == NT_GNU_BUILD_ID && note.n_namesz == 4) {
            const unsigned char* name = file.view(name_at, 4);
            const unsigned char* desc = file.view(desc_at, note.n_descsz);
            if (name != nullptr && desc != nullptr && std::memcmp(name, "GNU", 4) == 0) {
                return to_hex(desc, note.n_descsz);
            }
        }

        const size_t next = desc_at + desc_size;
        if (next <= cursor) { // malformed: no forward progress
            return {};
        }
        cursor = next;
    }
    return {};
}

// Returns the name of the STT_FUNC symbol covering `offset`, preferring the closest
// symbol at or below it. st_size of zero means the extent is unknown, so such symbols
// only match an exact hit.
std::string
lookup_symbol(const MappedFile& file, const Shdr& symtab, const Shdr& strtab, uintptr_t offset)
{
    if (symtab.sh_entsize != sizeof(Sym) || symtab.sh_entsize == 0) {
        return {};
    }
    const size_t count = static_cast<size_t>(symtab.sh_size) / sizeof(Sym);

    uintptr_t best_value = 0;
    uint32_t best_name = 0;
    bool found = false;
    for (size_t i = 0; i < count; ++i) {
        Sym sym{};
        if (!read_at(file, static_cast<size_t>(symtab.sh_offset) + i * sizeof(Sym), sym)) {
            break;
        }
        if (ELF32_ST_TYPE(sym.st_info) != STT_FUNC || sym.st_value == 0 || sym.st_name == 0) {
            continue;
        }
        const uintptr_t value = static_cast<uintptr_t>(sym.st_value);
        if (value > offset) {
            continue;
        }
        if (sym.st_size != 0 && offset >= value + static_cast<uintptr_t>(sym.st_size)) {
            continue;
        }
        if (sym.st_size == 0 && offset != value) {
            continue;
        }
        if (!found || value > best_value) {
            best_value = value;
            best_name = sym.st_name;
            found = true;
        }
    }
    if (!found) {
        return {};
    }

    // Names are NUL-terminated inside the string table; clamp to the section.
    const size_t name_at = static_cast<size_t>(strtab.sh_offset) + best_name;
    if (best_name >= strtab.sh_size) {
        return {};
    }
    const size_t max_len = static_cast<size_t>(strtab.sh_size) - best_name;
    const unsigned char* name = file.view(name_at, 1);
    if (name == nullptr) {
        return {};
    }
    const size_t len = ::strnlen(reinterpret_cast<const char*>(name), max_len);
    if (file.view(name_at, len) == nullptr) {
        return {};
    }
    return demangle_symbol(std::string(reinterpret_cast<const char*>(name), len));
}

} // namespace

ElfAddressInfo
describe_elf_address(const char* path, uintptr_t offset) noexcept
{
    ElfAddressInfo out;
    if (path == nullptr || path[0] == '\0') {
        return out;
    }

    try {
        MappedFile file(path);
        if (!file.valid()) {
            return out;
        }

        Ehdr ehdr{};
        if (!read_at(file, 0, ehdr) || std::memcmp(ehdr.e_ident, ELFMAG, SELFMAG) != 0 ||
            ehdr.e_ident[EI_CLASS] != kNativeClass || ehdr.e_shentsize != sizeof(Shdr) || ehdr.e_shnum == 0) {
            return out;
        }

        Shdr symtab{};
        Shdr strtab{};
        bool have_symtab = false;
        for (size_t i = 0; i < ehdr.e_shnum; ++i) {
            Shdr section{};
            if (!read_at(file, static_cast<size_t>(ehdr.e_shoff) + i * sizeof(Shdr), section)) {
                return out;
            }
            if (section.sh_type == SHT_NOTE && out.build_id.empty()) {
                out.build_id = read_build_id(file, section);
            } else if (section.sh_type == SHT_SYMTAB && !have_symtab && section.sh_link < ehdr.e_shnum) {
                if (read_at(file, static_cast<size_t>(ehdr.e_shoff) + section.sh_link * sizeof(Shdr), strtab)) {
                    symtab = section;
                    have_symtab = true;
                }
            }
        }

        if (have_symtab) {
            out.symbol = lookup_symbol(file, symtab, strtab, offset);
        }
    } catch (...) {
        // Diagnostics must never take down the sampler.
        return {};
    }
    return out;
}

#else

ElfAddressInfo
describe_elf_address(const char*, uintptr_t) noexcept
{
    return {};
}

#endif
