// Mach-O discovery of the AsyncioDebug table. See asyncio_debug.cc for the overall design.
//
// find_asyncio_debug_offsets walks dyld's image list. For each image, read_asyncio_debug_offsets_from_macho:
//   1. copies the Mach-O header and load commands into an owned snapshot with guarded memory reads,
//   2. finds the segment that maps the header (to compute the slide) and the __DATA,AsyncioDebug section in that
//      snapshot, validating every command against the snapshot bounds, and
//   3. reads the table from the loaded image and validates it.
// dyld does not retain images during enumeration, so no dyld-provided pointer, including image names, is ever
// dereferenced directly. An image unloaded mid-read only loses attribution.

#if defined(__APPLE__)

#include <echion/asyncio_debug_bounds.h>
#include <echion/cpython/asyncio_debug.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstring>
#include <limits>
#include <memory>
#include <new>

#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>

namespace {

using asyncio_debug::contains_span;

bool
read_loaded_memory(const void* address, void* buffer, size_t size)
{
    // Discovery runs without the GIL on an initialization thread. Use mach_vm_read_overwrite directly, not copy_memory:
    // the sampler can change its copy strategy concurrently. Fail closed if the call fails.
    mach_vm_size_t copied = 0;
    return mach_vm_read_overwrite(mach_task_self(),
                                  reinterpret_cast<mach_vm_address_t>(address),
                                  size,
                                  reinterpret_cast<mach_vm_address_t>(buffer),
                                  &copied) == KERN_SUCCESS &&
           copied == size;
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

} // namespace

std::optional<AsyncioOffsets>
find_asyncio_debug_offsets()
{
    auto snapshot = std::unique_ptr<MachOSnapshot>(new (std::nothrow) MachOSnapshot);
    if (!snapshot) {
        return std::nullopt;
    }
    // A bounded pass may miss an image when another thread changes the list; later initialization can retry.
    // Scan by section rather than filename so no borrowed dyld string needs to be dereferenced.
    const uint32_t count = std::min(_dyld_image_count(), uint32_t{ 4096 });
    for (uint32_t index = 0; index < count; ++index) {
        // First match wins; see find_asyncio_debug_offsets in asyncio_debug.h for the single-build assumption.
        if (auto offsets = read_asyncio_debug_offsets_from_macho(_dyld_get_image_header(index), *snapshot)) {
            return offsets;
        }
    }
    return std::nullopt;
}

std::optional<uint64_t>
loaded_binaries_generation()
{
    // dyld has no change counter. The image count misses a load paired with an unload, which only delays a retry
    // until the next change.
    return _dyld_image_count();
}

#endif // defined(__APPLE__)
