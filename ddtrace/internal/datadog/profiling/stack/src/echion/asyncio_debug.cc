#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif

#include <echion/cpython/asyncio_debug.h>

#if defined(__linux__) || defined(__APPLE__)
#include <echion/vm.h>
#endif

#include <cstring>
#include <limits>

#if defined(__linux__)
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#elif defined(__APPLE__)
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#endif

namespace {

#if defined(__linux__) || defined(__APPLE__)

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
is_asyncio_image(const char* path)
{
    if (path == nullptr || path[0] == '\0') {
        return true;
    }
    const char* filename = std::strrchr(path, '/');
    filename = filename == nullptr ? path : filename + 1;
    return std::strstr(filename, "_asyncio") != nullptr || std::strncmp(filename, "libpython", 9) == 0 ||
           std::strncmp(filename, "python", 6) == 0 || std::strncmp(filename, "Python", 6) == 0;
}

#endif

#if defined(__linux__)

bool
contains_span(size_t size, uint64_t offset, uint64_t length)
{
    return offset <= size && length <= size - offset;
}

bool
section_is_loaded(const dl_phdr_info& image, uint64_t address, uint64_t size)
{
    for (ElfW(Half) i = 0; i < image.dlpi_phnum; ++i) {
        const auto& segment = image.dlpi_phdr[i];
        if (segment.p_type == PT_LOAD && address >= segment.p_vaddr && size <= segment.p_memsz &&
            address - segment.p_vaddr <= segment.p_memsz - size) {
            return true;
        }
    }
    return false;
}

const PyAsyncioDebugOffsets*
find_elf_section(const char* path, const dl_phdr_info& image)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        return nullptr;
    }

    struct stat file_info;
    if (fstat(fd, &file_info) != 0 || file_info.st_size < static_cast<off_t>(sizeof(ElfW(Ehdr))) ||
        static_cast<uintmax_t>(file_info.st_size) > std::numeric_limits<size_t>::max()) {
        close(fd);
        return nullptr;
    }

    const size_t file_size = static_cast<size_t>(file_info.st_size);
    void* mapping = mmap(nullptr, file_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (mapping == MAP_FAILED) {
        return nullptr;
    }

    const PyAsyncioDebugOffsets* result = nullptr;
    const auto* bytes = static_cast<const unsigned char*>(mapping);
    const auto* header = reinterpret_cast<const ElfW(Ehdr)*>(bytes);

    do {
        if (std::memcmp(header->e_ident, ELFMAG, SELFMAG) != 0 || header->e_ident[EI_VERSION] != EV_CURRENT ||
            header->e_ident[EI_CLASS] != (sizeof(void*) == 8 ? ELFCLASS64 : ELFCLASS32) ||
            header->e_version != EV_CURRENT || (header->e_type != ET_DYN && header->e_type != ET_EXEC) ||
            header->e_shentsize != sizeof(ElfW(Shdr)) ||
            !contains_span(file_size, header->e_shoff, sizeof(ElfW(Shdr)))) {
            break;
        }

        const auto* sections = reinterpret_cast<const ElfW(Shdr)*>(bytes + header->e_shoff);
        const uint64_t section_count = header->e_shnum == 0 ? sections[0].sh_size : header->e_shnum;
        const uint64_t names_index = header->e_shstrndx == SHN_XINDEX ? sections[0].sh_link : header->e_shstrndx;
        if (section_count == 0 || names_index >= section_count ||
            section_count > (file_size - header->e_shoff) / sizeof(ElfW(Shdr))) {
            break;
        }

        const auto& names_section = sections[names_index];
        if (names_section.sh_type != SHT_STRTAB ||
            !contains_span(file_size, names_section.sh_offset, names_section.sh_size)) {
            break;
        }
        const char* names = reinterpret_cast<const char*>(bytes + names_section.sh_offset);

        for (uint64_t i = 0; i < section_count; ++i) {
            const auto& section = sections[i];
            if (section.sh_name >= names_section.sh_size) {
                continue;
            }
            const char* name = names + section.sh_name;
            const size_t remaining = static_cast<size_t>(names_section.sh_size - section.sh_name);
            if (std::memchr(name, '\0', remaining) == nullptr || std::strcmp(name, ".AsyncioDebug") != 0 ||
                section.sh_type != SHT_PROGBITS || (section.sh_flags & SHF_ALLOC) == 0 ||
                section.sh_size < sizeof(PyAsyncioDebugOffsets) ||
                !section_is_loaded(image, section.sh_addr, section.sh_size) ||
                section.sh_addr > std::numeric_limits<uintptr_t>::max() - image.dlpi_addr) {
                continue;
            }
            result = reinterpret_cast<const PyAsyncioDebugOffsets*>(image.dlpi_addr + section.sh_addr);
            break;
        }
    } while (false);

    munmap(mapping, file_size);
    return result;
}

int
find_asyncio_debug_section(dl_phdr_info* image, size_t, void* data)
{
    if (!is_asyncio_image(image->dlpi_name)) {
        return 0;
    }

    const char* path = image->dlpi_name[0] == '\0' ? "/proc/self/exe" : image->dlpi_name;
    const auto* table = find_elf_section(path, *image);
    auto* result = static_cast<std::optional<AsyncioOffsets>*>(data);
    *result = read_asyncio_debug_table(table);
    return result->has_value() ? 1 : 0;
}

std::optional<AsyncioOffsets>
find_asyncio_debug_table()
{
    std::optional<AsyncioOffsets> result;
    dl_iterate_phdr(find_asyncio_debug_section, &result);
    return result;
}

#elif defined(__APPLE__)

std::optional<AsyncioOffsets>
find_asyncio_debug_table()
{
    for (uint32_t image_index = 0; image_index < _dyld_image_count(); ++image_index) {
        const mach_header* image = _dyld_get_image_header(image_index);
        if (!is_asyncio_image(_dyld_get_image_name(image_index)) || image == nullptr || image->magic != MH_MAGIC_64) {
            continue;
        }

        const auto* header = reinterpret_cast<const mach_header_64*>(image);
        const char* command_data = reinterpret_cast<const char*>(header + 1);
        const char* command_end = command_data + header->sizeofcmds;
        for (uint32_t command_index = 0; command_index < header->ncmds; ++command_index) {
            if (command_data + sizeof(load_command) > command_end) {
                break;
            }
            const auto* command = reinterpret_cast<const load_command*>(command_data);
            if (command->cmdsize < sizeof(load_command) || command_data + command->cmdsize > command_end) {
                break;
            }

            if (command->cmd == LC_SEGMENT_64 && command->cmdsize >= sizeof(segment_command_64)) {
                const auto* segment = reinterpret_cast<const segment_command_64*>(command);
                const uint64_t section_bytes = static_cast<uint64_t>(segment->nsects) * sizeof(section_64);
                if (std::strncmp(segment->segname, SEG_DATA, sizeof(segment->segname)) == 0 &&
                    section_bytes <= command->cmdsize - sizeof(segment_command_64)) {
                    const auto* sections = reinterpret_cast<const section_64*>(segment + 1);
                    for (uint32_t section_index = 0; section_index < segment->nsects; ++section_index) {
                        const auto& section = sections[section_index];
                        if (std::strncmp(section.sectname, "AsyncioDebug", sizeof(section.sectname)) == 0 &&
                            section.size >= sizeof(PyAsyncioDebugOffsets)) {
                            const auto* table = reinterpret_cast<const PyAsyncioDebugOffsets*>(
                              static_cast<uintptr_t>(_dyld_get_image_vmaddr_slide(image_index)) + section.addr);
                            if (auto result = read_asyncio_debug_table(table)) {
                                return result;
                            }
                        }
                    }
                }
            }
            command_data += command->cmdsize;
        }
    }
    return std::nullopt;
}

#else

std::optional<AsyncioOffsets>
find_asyncio_debug_table()
{
    return std::nullopt;
}

#endif

} // namespace

std::optional<AsyncioOffsets>
find_asyncio_debug_offsets()
{
    // Asyncio is loaded before initialization and its layout cannot change during the process lifetime, so cache both
    // successful discovery and fail-closed absence.
    static const auto offsets = find_asyncio_debug_table();
    return offsets;
}
