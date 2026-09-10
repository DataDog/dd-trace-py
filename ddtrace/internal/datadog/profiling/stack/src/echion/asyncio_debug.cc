#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif

#include <echion/cpython/asyncio_debug.h>
#include <echion/vm.h>

#include "dd_wrapper/include/defer.hpp"

#include <array>
#include <bit>
#include <cerrno>
#include <cstring>
#include <limits>

#if defined(__linux__)
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <sys/stat.h>
#include <unistd.h>
#elif defined(__APPLE__)
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#endif

std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets* offsets)
{
    if (offsets == nullptr) {
        return std::nullopt;
    }

    constexpr uint64_t node_size = 2 * sizeof(uintptr_t);
    constexpr uint64_t max_size = std::numeric_limits<size_t>::max();
    const auto valid_field = [](uint64_t size, uint64_t field, uint64_t width, uint64_t alignment) {
        return size >= width && field <= size - width && field % alignment == 0 && field <= max_size;
    };

    // AsyncioDebug has no cookie, so validate the complete schema to reject false-positive section matches.
    if (offsets->interpreter.asyncio_tasks_head == 0 || offsets->thread.asyncio_tasks_head == 0 ||
        !valid_field(offsets->task.size, offsets->task.task_name, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->task.size, offsets->task.task_awaited_by, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->task.size, offsets->task.task_is_task, sizeof(char), alignof(char)) ||
        !valid_field(offsets->task.size, offsets->task.task_awaited_by_is_set, sizeof(char), alignof(char)) ||
        !valid_field(offsets->task.size, offsets->task.task_coro, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->task.size, offsets->task.task_node, node_size, alignof(uintptr_t)) ||
        !valid_field(
          offsets->interpreter.size, offsets->interpreter.asyncio_tasks_head, node_size, alignof(uintptr_t)) ||
        !valid_field(
          offsets->thread.size, offsets->thread.asyncio_running_loop, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(
          offsets->thread.size, offsets->thread.asyncio_running_task, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->thread.size, offsets->thread.asyncio_tasks_head, node_size, alignof(uintptr_t))) {
        return std::nullopt;
    }

    return AsyncioOffsets{ static_cast<size_t>(offsets->interpreter.asyncio_tasks_head),
                           static_cast<size_t>(offsets->thread.asyncio_tasks_head) };
}

namespace {

std::optional<AsyncioOffsets>
read_asyncio_debug_table(const PyAsyncioDebugOffsets* debug_offsets)
{
    if (debug_offsets == nullptr) {
        return std::nullopt;
    }

    PyAsyncioDebugOffsets table;
    return copy_type(debug_offsets, table) == 0 ? parse_asyncio_debug_offsets(&table) : std::nullopt;
}

bool
is_asyncio_binary(const char* path)
{
    if (path == nullptr || path[0] == '\0') {
        return true;
    }
    const char* filename = std::strrchr(path, '/');
    filename = filename == nullptr ? path : filename + 1;
    return std::strstr(filename, "_asyncio") != nullptr || std::strncmp(filename, "libpython", 9) == 0 ||
           std::strncmp(filename, "python", 6) == 0 || std::strncmp(filename, "Python", 6) == 0;
}

#if defined(__linux__)

bool
contains_span(uint64_t size, uint64_t offset, uint64_t length)
{
    return offset <= size && length <= size - offset;
}

bool
read_at(int fd, uint64_t file_size, uint64_t offset, void* buffer, size_t size)
{
    if (!contains_span(file_size, offset, size)) {
        return false;
    }
    // Reads into owned memory cannot SIGBUS if the file is truncated. Bound EINTR retries as well as read sizes.
    for (unsigned int attempt = 0; attempt < 3; ++attempt) {
        const ssize_t count = pread(fd, buffer, size, static_cast<off_t>(offset));
        if (count >= 0) {
            return static_cast<size_t>(count) == size;
        }
        if (errno != EINTR) {
            break;
        }
    }
    return false;
}

bool
section_is_loaded(const dl_phdr_info& binary, uint64_t address, uint64_t size)
{
    if (address > std::numeric_limits<uintptr_t>::max() - binary.dlpi_addr ||
        size > std::numeric_limits<uintptr_t>::max() - (binary.dlpi_addr + address)) {
        return false;
    }
    for (ElfW(Half) i = 0; i < binary.dlpi_phnum; ++i) {
        const auto& segment = binary.dlpi_phdr[i];
        if (segment.p_type == PT_LOAD && (segment.p_flags & PF_R) != 0 && address >= segment.p_vaddr &&
            contains_span(segment.p_memsz, address - segment.p_vaddr, size)) {
            return true;
        }
    }
    return false;
}

bool
has_gnu_build_id(const unsigned char* notes, size_t size)
{
    size_t offset = 0;
    while (contains_span(size, offset, sizeof(ElfW(Nhdr)))) {
        ElfW(Nhdr) note;
        std::memcpy(&note, notes + offset, sizeof(note));
        offset += sizeof(note);
        const uint64_t name_size = (static_cast<uint64_t>(note.n_namesz) + 3) & ~uint64_t{ 3 };
        const uint64_t data_size = (static_cast<uint64_t>(note.n_descsz) + 3) & ~uint64_t{ 3 };
        if (!contains_span(size, offset, name_size + data_size)) {
            return false;
        }
        if (note.n_type == NT_GNU_BUILD_ID && note.n_namesz == 4 && note.n_descsz != 0 &&
            std::memcmp(notes + offset, "GNU", 4) == 0) {
            return true;
        }
        offset += static_cast<size_t>(name_size + data_size);
    }
    return false;
}

bool
is_process_executable(int fd, const dl_phdr_info& binary)
{
    if (binary.dlpi_name != nullptr && binary.dlpi_name[0] != '\0') {
        return false;
    }

    struct stat file_info;
    struct stat executable_info;
    return fstat(fd, &file_info) == 0 && stat("/proc/self/exe", &executable_info) == 0 &&
           file_info.st_dev == executable_info.st_dev && file_info.st_ino == executable_info.st_ino;
}

bool
matches_loaded_binary(int fd, uint64_t file_size, const ElfW(Ehdr) & header, const dl_phdr_info& binary)
{
    bool matched_build_id = false;
    for (ElfW(Half) i = 0; i < header.e_phnum; ++i) {
        ElfW(Phdr) segment;
        if (!read_at(fd, file_size, header.e_phoff + i * sizeof(segment), &segment, sizeof(segment)) ||
            std::memcmp(&segment, &binary.dlpi_phdr[i], sizeof(segment)) != 0) {
            return false;
        }
        // ELF notes are small, immutable metadata. Cap scratch space and reject binaries without a matching build ID
        // rather than applying section addresses from a replacement file to an older loaded library.
        std::array<unsigned char, 4096> file_notes;
        std::array<unsigned char, 4096> loaded_notes;
        if (segment.p_type != PT_NOTE || segment.p_filesz > file_notes.size() ||
            !section_is_loaded(binary, segment.p_vaddr, segment.p_filesz)) {
            continue;
        }
        const size_t size = static_cast<size_t>(segment.p_filesz);
        // The loader exposes virtual addresses as integers.
        // NOLINTNEXTLINE(performance-no-int-to-ptr)
        const auto* address = reinterpret_cast<const void*>(binary.dlpi_addr + segment.p_vaddr);
        if (read_at(fd, file_size, segment.p_offset, file_notes.data(), size) &&
            copy_generic(address, loaded_notes.data(), size) == 0 &&
            std::memcmp(file_notes.data(), loaded_notes.data(), size) == 0 &&
            has_gnu_build_id(file_notes.data(), size)) {
            matched_build_id = true;
        }
    }
    // /proc/self/exe is a kernel reference to the exact executable backing this process, including after unlink or
    // replacement. Other paths can race with replacement and therefore still require a matching GNU build ID.
    return matched_build_id || is_process_executable(fd, binary);
}

int
find_asyncio_debug_section(dl_phdr_info* binary, size_t, void* data)
{
    if (!is_asyncio_binary(binary->dlpi_name)) {
        return 0;
    }

    const char* path = binary->dlpi_name[0] == '\0' ? "/proc/self/exe" : binary->dlpi_name;
    // O_NONBLOCK also avoids hanging on a FIFO substituted for a previously loaded library.
    const int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) {
        return 0;
    }
    defer
    {
        close(fd);
    };
    auto* result = static_cast<std::optional<AsyncioOffsets>*>(data);
    *result = read_asyncio_debug_offsets_from_elf(fd, *binary);
    return result->has_value() ? 1 : 0;
}

#endif

} // namespace

#if defined(__linux__)

std::optional<AsyncioOffsets>
read_asyncio_debug_offsets_from_elf(int fd, const dl_phdr_info& binary)
{
    struct stat file_info;
    if (fstat(fd, &file_info) != 0 || !S_ISREG(file_info.st_mode) || file_info.st_size < 0) {
        return std::nullopt;
    }
    const auto file_size = static_cast<uint64_t>(file_info.st_size);
    ElfW(Ehdr) header;
    if (!read_at(fd, file_size, 0, &header, sizeof(header)) || std::memcmp(header.e_ident, ELFMAG, SELFMAG) != 0 ||
        header.e_ident[EI_VERSION] != EV_CURRENT ||
        header.e_ident[EI_CLASS] != (sizeof(void*) == 8 ? ELFCLASS64 : ELFCLASS32) ||
        header.e_ident[EI_DATA] != (std::endian::native == std::endian::little ? ELFDATA2LSB : ELFDATA2MSB) ||
        header.e_version != EV_CURRENT || (header.e_type != ET_DYN && header.e_type != ET_EXEC) ||
        header.e_ehsize != sizeof(header) || header.e_phentsize != sizeof(ElfW(Phdr)) || header.e_phnum == 0 ||
        header.e_phnum > 256 || header.e_phnum != binary.dlpi_phnum ||
        !contains_span(file_size, header.e_phoff, header.e_phnum * sizeof(ElfW(Phdr))) ||
        header.e_shentsize != sizeof(ElfW(Shdr)) || !contains_span(file_size, header.e_shoff, sizeof(ElfW(Shdr))) ||
        !matches_loaded_binary(fd, file_size, header, binary)) {
        return std::nullopt;
    }

    ElfW(Shdr) first_section;
    if (!read_at(fd, file_size, header.e_shoff, &first_section, sizeof(first_section))) {
        return std::nullopt;
    }
    const uint64_t section_count = header.e_shnum == 0 ? first_section.sh_size : header.e_shnum;
    const uint64_t names_index = header.e_shstrndx == SHN_XINDEX ? first_section.sh_link : header.e_shstrndx;
    // Bound initialization work independently of file size, including extended ELF section counts.
    if (section_count == 0 || section_count > 4096 || names_index >= section_count ||
        !contains_span(file_size, header.e_shoff, section_count * sizeof(ElfW(Shdr)))) {
        return std::nullopt;
    }

    ElfW(Shdr) names_section;
    if (!read_at(
          fd, file_size, header.e_shoff + names_index * sizeof(names_section), &names_section, sizeof(names_section)) ||
        names_section.sh_type != SHT_STRTAB ||
        !contains_span(file_size, names_section.sh_offset, names_section.sh_size)) {
        return std::nullopt;
    }

    for (uint64_t i = 0; i < section_count; ++i) {
        ElfW(Shdr) section;
        if (!read_at(fd, file_size, header.e_shoff + i * sizeof(section), &section, sizeof(section))) {
            return std::nullopt;
        }
        constexpr char section_name[] = ".AsyncioDebug";
        char name[sizeof(section_name)];
        if (section.sh_type != SHT_PROGBITS || (section.sh_flags & SHF_ALLOC) == 0 ||
            section.sh_size < sizeof(PyAsyncioDebugOffsets) ||
            !contains_span(names_section.sh_size, section.sh_name, sizeof(name)) ||
            !read_at(fd, file_size, names_section.sh_offset + section.sh_name, name, sizeof(name)) ||
            std::memcmp(name, section_name, sizeof(name)) != 0 ||
            !section_is_loaded(binary, section.sh_addr, section.sh_size)) {
            continue;
        }
        // The loader exposes the load bias and section address as integers.
        // NOLINTNEXTLINE(performance-no-int-to-ptr)
        const auto* table = reinterpret_cast<const PyAsyncioDebugOffsets*>(binary.dlpi_addr + section.sh_addr);
        if (auto offsets = read_asyncio_debug_table(table)) {
            return offsets;
        }
    }
    return std::nullopt;
}

#endif

std::optional<AsyncioOffsets>
find_asyncio_debug_offsets()
{
#if defined(__linux__)
    std::optional<AsyncioOffsets> result;
    dl_iterate_phdr(find_asyncio_debug_section, &result);
    return result;
#elif defined(__APPLE__)
    for (uint32_t index = 0; index < _dyld_image_count(); ++index) {
        const mach_header* binary = _dyld_get_image_header(index);
        if (!is_asyncio_binary(_dyld_get_image_name(index)) || binary == nullptr || binary->magic != MH_MAGIC_64) {
            continue;
        }
        unsigned long size = 0;
        const auto* section =
          getsectiondata(reinterpret_cast<const mach_header_64*>(binary), SEG_DATA, "AsyncioDebug", &size);
        if (size >= sizeof(PyAsyncioDebugOffsets)) {
            if (auto offsets = read_asyncio_debug_table(reinterpret_cast<const PyAsyncioDebugOffsets*>(section))) {
                return offsets;
            }
        }
    }
    return std::nullopt;
#endif
}
