#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif

#include <echion/cpython/asyncio_debug.h>

#include "dd_wrapper/include/defer.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <limits>
#include <memory>
#include <new>

#if defined(__linux__)
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/uio.h>
#include <unistd.h>
#elif defined(__APPLE__)
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#endif

std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets& offsets)
{
    constexpr uint64_t node_size = 2 * sizeof(uintptr_t);
    constexpr uint64_t max_size = std::numeric_limits<size_t>::max();
    const auto valid_field = [](uint64_t size, uint64_t field, uint64_t width, uint64_t alignment) {
        return size >= width && field <= size - width && field % alignment == 0 && field <= max_size;
    };

    // AsyncioDebug has no cookie, so validate the complete schema to reject false-positive section matches.
    if (offsets.interpreter.asyncio_tasks_head == 0 || offsets.thread.asyncio_tasks_head == 0 ||
        !valid_field(offsets.task.size, offsets.task.task_name, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.task.size, offsets.task.task_awaited_by, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.task.size, offsets.task.task_is_task, sizeof(char), alignof(char)) ||
        !valid_field(offsets.task.size, offsets.task.task_awaited_by_is_set, sizeof(char), alignof(char)) ||
        !valid_field(offsets.task.size, offsets.task.task_coro, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.task.size, offsets.task.task_node, node_size, alignof(uintptr_t)) ||
        !valid_field(offsets.interpreter.size, offsets.interpreter.asyncio_tasks_head, node_size, alignof(uintptr_t)) ||
        !valid_field(offsets.thread.size, offsets.thread.asyncio_running_loop, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.thread.size, offsets.thread.asyncio_running_task, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.thread.size, offsets.thread.asyncio_tasks_head, node_size, alignof(uintptr_t))) {
        return std::nullopt;
    }

    return AsyncioOffsets{ static_cast<size_t>(offsets.interpreter.asyncio_tasks_head),
                           static_cast<size_t>(offsets.thread.asyncio_tasks_head) };
}

namespace {

bool
contains_span(uint64_t size, uint64_t offset, uint64_t length)
{
    return offset <= size && length <= size - offset;
}

bool
read_loaded_memory(const void* address, void* buffer, size_t size)
{
    // Discovery runs without the GIL on an initialization thread. Use process_vm_readv directly (and
    // mach_vm_read_overwrite on macOS), not copy_memory: the sampler can change safe_copy concurrently, and signal
    // handler swaps pause only the sampler, not this thread. Fail closed if the syscall is unavailable or fails;
    // falling back to signal-based memcpy would reintroduce those races.
#if defined(__linux__)
    const iovec local{ buffer, size };
    const iovec remote{ const_cast<void*>(address), size };
    return process_vm_readv(getpid(), &local, 1, &remote, 1, 0) == static_cast<ssize_t>(size);
#elif defined(__APPLE__)
    mach_vm_size_t copied = 0;
    return mach_vm_read_overwrite(mach_task_self(),
                                  reinterpret_cast<mach_vm_address_t>(address),
                                  size,
                                  reinterpret_cast<mach_vm_address_t>(buffer),
                                  &copied) == KERN_SUCCESS &&
           copied == size;
#endif
}

std::optional<AsyncioOffsets>
read_asyncio_debug_table(const PyAsyncioDebugOffsets* debug_offsets)
{
    if (debug_offsets == nullptr) {
        return std::nullopt;
    }

    PyAsyncioDebugOffsets table;
    return read_loaded_memory(debug_offsets, &table, sizeof(table)) ? parse_asyncio_debug_offsets(table) : std::nullopt;
}

#if defined(__linux__)

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

constexpr size_t max_program_headers = 256;

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

std::optional<ElfW(Ehdr)>
read_and_validate_elf_header(int fd, uint64_t file_size)
{
    ElfW(Ehdr) header;
    if (!read_at(fd, file_size, 0, &header, sizeof(header))) {
        return std::nullopt;
    }

    // e_ident starts with the ELF magic bytes and records the file's word size, byte order, and format version. The
    // loaded binary has the same word size and byte order as this process, so no cross-architecture decoding is needed.
    if (std::memcmp(header.e_ident, ELFMAG, SELFMAG) != 0 || header.e_ident[EI_VERSION] != EV_CURRENT ||
        header.e_ident[EI_CLASS] != (sizeof(void*) == 8 ? ELFCLASS64 : ELFCLASS32) ||
        header.e_ident[EI_DATA] != (std::endian::native == std::endian::little ? ELFDATA2LSB : ELFDATA2MSB)) {
        return std::nullopt;
    }

    // ET_EXEC covers traditional executables. ET_DYN covers shared libraries and position-independent executables.
    if (header.e_version != EV_CURRENT || (header.e_type != ET_DYN && header.e_type != ET_EXEC) ||
        header.e_ehsize != sizeof(header)) {
        return std::nullopt;
    }

    // Program headers describe the segments mapped by the dynamic loader. We later compare this table with
    // dl_iterate_phdr's in-memory table to ensure that the file still represents the loaded binary.
    if (header.e_phentsize != sizeof(ElfW(Phdr)) || header.e_phnum == 0 || header.e_phnum > max_program_headers ||
        !contains_span(file_size, header.e_phoff, header.e_phnum * sizeof(ElfW(Phdr)))) {
        return std::nullopt;
    }

    // Section headers locate named sections such as .AsyncioDebug. At least the first header must be readable because
    // ELF stores extended section counts and string-table indexes there when they do not fit in the main header.
    if (header.e_shentsize != sizeof(ElfW(Shdr)) || !contains_span(file_size, header.e_shoff, sizeof(ElfW(Shdr)))) {
        return std::nullopt;
    }
    return header;
}

struct ElfSectionTable
{
    uint64_t section_headers_offset;
    uint64_t section_count;
    ElfW(Shdr) names_section;
};

std::optional<ElfSectionTable>
read_elf_section_table(int fd, uint64_t file_size, const ElfW(Ehdr) & header)
{
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
    return ElfSectionTable{ header.e_shoff, section_count, names_section };
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
is_process_executable(const struct stat& file_info, const dl_phdr_info& binary)
{
    if (binary.dlpi_name != nullptr && binary.dlpi_name[0] != '\0') {
        return false;
    }

    struct stat executable_info;
    return stat("/proc/self/exe", &executable_info) == 0 && file_info.st_dev == executable_info.st_dev &&
           file_info.st_ino == executable_info.st_ino;
}

bool
mapping_contains_segment(uint64_t start,
                         uint64_t end,
                         uint64_t file_offset,
                         const dl_phdr_info& binary,
                         const ElfW(Phdr) & segment)
{
    if (segment.p_type != PT_LOAD || segment.p_filesz == 0 ||
        segment.p_vaddr > std::numeric_limits<uintptr_t>::max() - binary.dlpi_addr) {
        return false;
    }

    const uint64_t segment_address = binary.dlpi_addr + segment.p_vaddr;
    if (segment_address < start || segment_address >= end) {
        return false;
    }
    const uint64_t bytes_before_segment = segment_address - start;
    return contains_span(segment.p_offset, file_offset, bytes_before_segment) &&
           file_offset + bytes_before_segment == segment.p_offset;
}

bool
matches_loaded_mapping(const struct stat& file_info, const dl_phdr_info& binary)
{
    FILE* maps = std::fopen("/proc/self/maps", "r");
    if (maps == nullptr) {
        return false;
    }
    defer
    {
        std::fclose(maps);
    };

    // A maps entry identifies the mapped file by device and inode. Match its address and file offset to a PT_LOAD
    // segment first, then compare that identity with the file opened through dlpi_name.
    std::array<char, 4096> line;
    while (std::fgets(line.data(), line.size(), maps) != nullptr) {
        unsigned long long start = 0;
        unsigned long long end = 0;
        unsigned long long file_offset = 0;
        unsigned int device_major = 0;
        unsigned int device_minor = 0;
        unsigned long long inode = 0;
        if (std::sscanf(line.data(),
                        "%llx-%llx %*4s %llx %x:%x %llu",
                        &start,
                        &end,
                        &file_offset,
                        &device_major,
                        &device_minor,
                        &inode) != 6 ||
            device_major != major(file_info.st_dev) || device_minor != minor(file_info.st_dev) ||
            inode != static_cast<unsigned long long>(file_info.st_ino)) {
            continue;
        }

        for (ElfW(Half) i = 0; i < binary.dlpi_phnum; ++i) {
            if (mapping_contains_segment(start, end, file_offset, binary, binary.dlpi_phdr[i])) {
                return true;
            }
        }
    }
    return false;
}

// Section addresses come from the open file, but the load bias and section contents come from the loaded binary. The
// library path may have been replaced since the loader mapped it, so combining metadata from different files could
// produce a valid-looking address into unrelated memory. Match the file against loader-owned metadata before using it.
bool
matches_loaded_binary(int fd,
                      uint64_t file_size,
                      const struct stat& file_info,
                      const ElfW(Ehdr) & header,
                      const dl_phdr_info& binary)
{
    // Program headers define the loaded segment layout. Require the file's complete table to equal the table retained
    // by the dynamic loader before comparing identity metadata within those segments.
    if (header.e_phnum != binary.dlpi_phnum) {
        return false;
    }

    bool loaded_has_build_id = false;
    bool matched_build_id = false;
    bool inspected_all_notes = true;
    for (ElfW(Half) i = 0; i < header.e_phnum; ++i) {
        ElfW(Phdr) segment;
        if (!read_at(fd, file_size, header.e_phoff + i * sizeof(segment), &segment, sizeof(segment)) ||
            std::memcmp(&segment, &binary.dlpi_phdr[i], sizeof(segment)) != 0) {
            return false;
        }
        // Matching layouts alone do not prove file identity because two builds can have identical program headers.
        // Compare the GNU build ID note from the file with the copy mapped in memory. ELF notes are small, immutable
        // metadata, so cap scratch space and avoid allocating while inspecting them.
        if (segment.p_type != PT_NOTE) {
            continue;
        }

        std::array<unsigned char, 4096> file_notes;
        std::array<unsigned char, 4096> loaded_notes;
        if (segment.p_filesz > file_notes.size() || !section_is_loaded(binary, segment.p_vaddr, segment.p_filesz)) {
            inspected_all_notes = false;
            continue;
        }
        const size_t size = static_cast<size_t>(segment.p_filesz);
        // The loader exposes virtual addresses as integers.
        // NOLINTNEXTLINE(performance-no-int-to-ptr)
        const auto* address = reinterpret_cast<const void*>(binary.dlpi_addr + segment.p_vaddr);
        if (!read_loaded_memory(address, loaded_notes.data(), size)) {
            inspected_all_notes = false;
            continue;
        }
        if (!has_gnu_build_id(loaded_notes.data(), size)) {
            continue;
        }

        loaded_has_build_id = true;
        if (read_at(fd, file_size, segment.p_offset, file_notes.data(), size) &&
            std::memcmp(file_notes.data(), loaded_notes.data(), size) == 0) {
            matched_build_id = true;
        }
    }

    if (loaded_has_build_id) {
        return matched_build_id;
    }
    if (!inspected_all_notes) {
        return false;
    }

    // /proc/self/exe refers to the executable backing this process even after unlink or replacement. For other
    // build-ID-free objects, /proc/self/maps retains the loaded file's device and inode.
    return is_process_executable(file_info, binary) || matches_loaded_mapping(file_info, binary);
}

std::optional<AsyncioOffsets>
find_asyncio_debug_offsets_in_sections(int fd,
                                       uint64_t file_size,
                                       const ElfSectionTable& table,
                                       const dl_phdr_info& binary)
{
    constexpr char section_name[] = ".AsyncioDebug";
    for (uint64_t i = 0; i < table.section_count; ++i) {
        ElfW(Shdr) section;
        if (!read_at(fd, file_size, table.section_headers_offset + i * sizeof(section), &section, sizeof(section))) {
            return std::nullopt;
        }
        if (section.sh_type != SHT_PROGBITS || (section.sh_flags & SHF_ALLOC) == 0 ||
            section.sh_size < sizeof(PyAsyncioDebugOffsets)) {
            continue;
        }

        char name[sizeof(section_name)];
        if (!contains_span(table.names_section.sh_size, section.sh_name, sizeof(name)) ||
            !read_at(fd, file_size, table.names_section.sh_offset + section.sh_name, name, sizeof(name)) ||
            std::memcmp(name, section_name, sizeof(name)) != 0 ||
            !section_is_loaded(binary, section.sh_addr, section.sh_size)) {
            continue;
        }
        // The loader exposes the load bias and section address as integers.
        // NOLINTNEXTLINE(performance-no-int-to-ptr)
        const auto* debug_table = reinterpret_cast<const PyAsyncioDebugOffsets*>(binary.dlpi_addr + section.sh_addr);
        if (auto offsets = read_asyncio_debug_table(debug_table)) {
            return offsets;
        }
    }
    return std::nullopt;
}

constexpr size_t max_asyncio_binaries = 16;
constexpr size_t max_binary_path = 4096;

struct LoadedBinarySnapshot
{
    ElfW(Addr) load_bias = 0;
    ElfW(Half) program_header_count = 0;
    bool is_process_executable = false;
    std::array<char, max_binary_path> path{};
    std::array<ElfW(Phdr), max_program_headers> program_headers{};

    dl_phdr_info binary() const
    {
        dl_phdr_info result{};
        result.dlpi_addr = load_bias;
        result.dlpi_name = is_process_executable ? "" : path.data();
        result.dlpi_phdr = program_headers.data();
        result.dlpi_phnum = program_header_count;
        return result;
    }

    const char* file_path() const { return is_process_executable ? "/proc/self/exe" : path.data(); }
};

struct LoadedBinarySnapshots
{
    std::array<LoadedBinarySnapshot, max_asyncio_binaries> binaries{};
    size_t count = 0;
};

int
snapshot_asyncio_binary(dl_phdr_info* binary, size_t, void* data)
{
    if (!is_asyncio_binary(binary->dlpi_name)) {
        return 0;
    }

    auto* snapshots = static_cast<LoadedBinarySnapshots*>(data);
    if (snapshots->count == snapshots->binaries.size()) {
        return 1;
    }
    if (binary->dlpi_phdr == nullptr || binary->dlpi_phnum == 0 || binary->dlpi_phnum > max_program_headers) {
        return 0;
    }

    const bool is_process_executable = binary->dlpi_name == nullptr || binary->dlpi_name[0] == '\0';
    size_t path_length = 0;
    if (!is_process_executable) {
        const auto* path_end = static_cast<const char*>(std::memchr(binary->dlpi_name, '\0', max_binary_path));
        if (path_end == nullptr) {
            return 0;
        }
        path_length = static_cast<size_t>(path_end - binary->dlpi_name);
    }

    auto& snapshot = snapshots->binaries[snapshots->count++];
    snapshot.load_bias = binary->dlpi_addr;
    snapshot.program_header_count = binary->dlpi_phnum;
    snapshot.is_process_executable = is_process_executable;
    if (!is_process_executable) {
        std::memcpy(snapshot.path.data(), binary->dlpi_name, path_length + 1);
    }
    std::memcpy(snapshot.program_headers.data(), binary->dlpi_phdr, binary->dlpi_phnum * sizeof(ElfW(Phdr)));
    return 0;
}

#elif defined(__APPLE__)

struct MachOSnapshot
{
    mach_header_64 header;
    std::array<unsigned char, 64 * 1024> commands;
};

static_assert(offsetof(MachOSnapshot, commands) == sizeof(mach_header_64));

std::optional<AsyncioOffsets>
read_asyncio_debug_offsets_from_macho(const mach_header* binary, MachOSnapshot& snapshot)
{
    // dyld's indexed enumeration does not retain images. Never dereference its pointers, including image names:
    // another thread may dlclose an image between any two calls. Snapshot all metadata with guarded syscalls and
    // validate command bounds before parsing owned bytes. Unloading during a read must only lose attribution.
    mach_header_64 header;
    if (binary == nullptr || !read_loaded_memory(binary, &header, sizeof(header)) || header.magic != MH_MAGIC_64 ||
        (header.filetype != MH_EXECUTE && header.filetype != MH_DYLIB && header.filetype != MH_BUNDLE) ||
        header.ncmds == 0 || header.ncmds > 256 || header.sizeofcmds > snapshot.commands.size()) {
        return std::nullopt;
    }
    const size_t metadata_size = sizeof(header) + header.sizeofcmds;
    if (!read_loaded_memory(binary, &snapshot, metadata_size) ||
        std::memcmp(&header, &snapshot.header, sizeof(header)) != 0) {
        return std::nullopt;
    }

    std::optional<uint64_t> header_vmaddr;
    std::optional<uint64_t> debug_vmaddr;
    size_t offset = 0;
    for (uint32_t i = 0; i < header.ncmds; ++i) {
        load_command command;
        if (!contains_span(header.sizeofcmds, offset, sizeof(command))) {
            return std::nullopt;
        }
        std::memcpy(&command, snapshot.commands.data() + offset, sizeof(command));
        if (command.cmdsize < sizeof(command) || command.cmdsize % 8 != 0 ||
            !contains_span(header.sizeofcmds, offset, command.cmdsize)) {
            return std::nullopt;
        }
        if (command.cmd == LC_SEGMENT_64) {
            segment_command_64 segment;
            if (command.cmdsize < sizeof(segment)) {
                return std::nullopt;
            }
            std::memcpy(&segment, snapshot.commands.data() + offset, sizeof(segment));
            if (segment.nsects > (command.cmdsize - sizeof(segment)) / sizeof(section_64)) {
                return std::nullopt;
            }
            if ((segment.initprot & VM_PROT_READ) != 0) {
                // The segment containing the file header determines the slide, without another racy dyld lookup.
                if (segment.fileoff == 0 && segment.filesize >= metadata_size && segment.vmsize >= metadata_size) {
                    if (header_vmaddr) {
                        return std::nullopt;
                    }
                    header_vmaddr = segment.vmaddr;
                }
                if (std::strncmp(segment.segname, SEG_DATA, sizeof(segment.segname)) == 0) {
                    for (uint32_t j = 0; j < segment.nsects; ++j) {
                        section_64 section;
                        std::memcpy(&section,
                                    snapshot.commands.data() + offset + sizeof(segment) + j * sizeof(section),
                                    sizeof(section));
                        if (std::strncmp(section.sectname, "AsyncioDebug", sizeof(section.sectname)) == 0 &&
                            std::strncmp(section.segname, SEG_DATA, sizeof(section.segname)) == 0 &&
                            (section.flags & SECTION_TYPE) == S_REGULAR &&
                            section.size >= sizeof(PyAsyncioDebugOffsets) && section.addr >= segment.vmaddr &&
                            contains_span(segment.vmsize, section.addr - segment.vmaddr, section.size)) {
                            if (debug_vmaddr) {
                                return std::nullopt;
                            }
                            debug_vmaddr = section.addr;
                        }
                    }
                }
            }
        }
        offset += command.cmdsize;
    }

    if (offset != header.sizeofcmds || !header_vmaddr || !debug_vmaddr || *debug_vmaddr < *header_vmaddr) {
        return std::nullopt;
    }
    const uintptr_t base = reinterpret_cast<uintptr_t>(binary);
    const uint64_t debug_offset = *debug_vmaddr - *header_vmaddr;
    if (debug_offset > std::numeric_limits<uintptr_t>::max() - base) {
        return std::nullopt;
    }
    // The address refers to the loaded image, not the metadata snapshot. This final read is guarded too.
    // NOLINTNEXTLINE(performance-no-int-to-ptr)
    return read_asyncio_debug_table(reinterpret_cast<const PyAsyncioDebugOffsets*>(base + debug_offset));
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

    auto header = read_and_validate_elf_header(fd, file_size);
    if (!header || !matches_loaded_binary(fd, file_size, file_info, *header, binary)) {
        return std::nullopt;
    }

    auto section_table = read_elf_section_table(fd, file_size, *header);
    if (!section_table) {
        return std::nullopt;
    }

    return find_asyncio_debug_offsets_in_sections(fd, file_size, *section_table, binary);
}

#endif

std::optional<AsyncioOffsets>
find_asyncio_debug_offsets()
{
#if defined(__linux__)
    // glibc invokes dl_iterate_phdr callbacks while holding the dynamic loader lock. Copy the candidate metadata in the
    // callbacks, then perform all filesystem and guarded-memory reads after iteration releases that lock.
    auto snapshots = std::unique_ptr<LoadedBinarySnapshots>(new (std::nothrow) LoadedBinarySnapshots{});
    if (!snapshots) {
        return std::nullopt;
    }
    dl_iterate_phdr(snapshot_asyncio_binary, snapshots.get());

    for (size_t i = 0; i < snapshots->count; ++i) {
        const auto& snapshot = snapshots->binaries[i];
        // O_NONBLOCK avoids hanging on a FIFO substituted for a previously loaded library. Regular files can still
        // block, but no dynamic loader lock is held while opening or reading them.
        const int fd = open(snapshot.file_path(), O_RDONLY | O_CLOEXEC | O_NONBLOCK);
        if (fd < 0) {
            continue;
        }
        auto binary = snapshot.binary();
        auto offsets = read_asyncio_debug_offsets_from_elf(fd, binary);
        close(fd);
        if (offsets) {
            return offsets;
        }
    }
    return std::nullopt;
#elif defined(__APPLE__)
    auto snapshot = std::unique_ptr<MachOSnapshot>(new (std::nothrow) MachOSnapshot);
    if (!snapshot) {
        return std::nullopt;
    }
    // A bounded pass may miss an image when another thread changes the list; later initialization can retry.
    // Scan by section rather than filename so no borrowed dyld string needs to be dereferenced.
    const uint32_t count = std::min(_dyld_image_count(), uint32_t{ 4096 });
    for (uint32_t index = 0; index < count; ++index) {
        if (auto offsets = read_asyncio_debug_offsets_from_macho(_dyld_get_image_header(index), *snapshot)) {
            return offsets;
        }
    }
    return std::nullopt;
#endif
}
