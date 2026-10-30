// Only the production reader's call is redirected; this wrapper forwards to the real Mach syscall.
#undef mach_vm_read_overwrite

#include <echion/cpython/asyncio_debug.h>

#include <gtest/gtest.h>

#include <array>
#include <cstring>
#include <dlfcn.h>
#include <functional>
#include <limits>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <new>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>

namespace {

// Restrict enumeration to fixture images so an installed or built-in _asyncio cannot hide a discovery failure.
std::vector<const mach_header*> images;
size_t visited_images = 0;
size_t memory_reads = 0;
std::function<void()> after_header_lookup;
std::function<void(mach_vm_address_t, mach_vm_size_t)> before_memory_read;

} // namespace

// Keep real fixture metadata and syscall reads, with hooks for deterministic image disappearance.
extern "C" uint32_t
test_asyncio_dyld_image_count()
{
    return static_cast<uint32_t>(images.size());
}

extern "C" const mach_header*
test_asyncio_dyld_get_image_header(uint32_t index)
{
    ++visited_images;
    const auto* header = images.at(index);
    if (after_header_lookup) {
        after_header_lookup();
    }
    return header;
}

extern "C" kern_return_t
test_asyncio_mach_vm_read_overwrite(vm_map_read_t task,
                                    mach_vm_address_t address,
                                    mach_vm_size_t size,
                                    mach_vm_address_t buffer,
                                    mach_vm_size_t* copied)
{
    ++memory_reads;
    if (before_memory_read) {
        before_memory_read(address, size);
    }
    return mach_vm_read_overwrite(task, address, size, buffer, copied);
}

class AsyncioMachODiscovery : public ::testing::Test
{
  protected:
    enum Fixture : size_t
    {
        Missing,
        Undersized,
        Invalid,
        Valid,
    };
    std::array<void*, 4> handles{};
    std::array<const mach_header*, 4> fixtures{};

    void SetUp() override
    {
        images.clear();
        visited_images = 0;
        memory_reads = 0;
        after_header_lookup = {};
        before_memory_read = {};
        constexpr std::array paths{ ASYNCIO_MISSING_FIXTURE_PATH,
                                    ASYNCIO_UNDERSIZED_FIXTURE_PATH,
                                    ASYNCIO_INVALID_FIXTURE_PATH,
                                    ASYNCIO_VALID_FIXTURE_PATH };
        for (size_t i = 0; i < paths.size(); ++i) {
            handles[i] = dlopen(paths[i], RTLD_NOW | RTLD_LOCAL);
            ASSERT_NE(handles[i], nullptr) << dlerror();
            void* anchor = dlsym(handles[i], "asyncio_fixture_anchor");
            ASSERT_NE(anchor, nullptr) << dlerror();
            Dl_info info{};
            ASSERT_NE(dladdr(anchor, &info), 0);
            fixtures[i] = static_cast<const mach_header*>(info.dli_fbase);
        }
    }

    void TearDown() override
    {
        images.clear();
        for (auto* handle : handles) {
            if (handle != nullptr) {
                EXPECT_EQ(dlclose(handle), 0);
            }
        }
    }

    void expect_valid_offsets()
    {
        auto offsets = find_asyncio_debug_offsets();
        ASSERT_TRUE(offsets);
        EXPECT_EQ(offsets->interpreter_tasks_head, 128);
        EXPECT_EQ(offsets->thread_tasks_head, 256);
    }
};

TEST_F(AsyncioMachODiscovery, RejectsMissingSection)
{
    images = { fixtures[Missing] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(visited_images, 1);
}

TEST_F(AsyncioMachODiscovery, RejectsUndersizedSection)
{
    images = { fixtures[Undersized] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(visited_images, 1);
}

TEST_F(AsyncioMachODiscovery, RejectsInvalidTable)
{
    images = { fixtures[Invalid] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(visited_images, 1);
}

TEST_F(AsyncioMachODiscovery, SkipsInvalidImagesAndFindsValidTable)
{
    images = { fixtures[Missing], fixtures[Undersized], fixtures[Invalid], fixtures[Valid] };
    expect_valid_offsets();
    EXPECT_EQ(visited_images, 4);
}

TEST_F(AsyncioMachODiscovery, RetriesAfterAnEarlierMiss)
{
    images = { fixtures[Missing] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    images.push_back(fixtures[Valid]);
    expect_valid_offsets();
    EXPECT_EQ(visited_images, 3);
}

class AsyncioMachOSnapshot : public ::testing::Test
{
  protected:
    struct Metadata
    {
        mach_header_64 header;
        segment_command_64 text;
        segment_command_64 data;
        section_64 section;
    };

    size_t page_size = 0;
    void* mapping = MAP_FAILED;
    Metadata* metadata = nullptr;

    void SetUp() override
    {
        images.clear();
        visited_images = 0;
        memory_reads = 0;
        after_header_lookup = {};
        before_memory_read = {};
        page_size = static_cast<size_t>(getpagesize());
        ASSERT_GE(page_size, sizeof(Metadata));
        mapping = mmap(nullptr, 2 * page_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        ASSERT_NE(mapping, MAP_FAILED);
        metadata = new (mapping) Metadata{};
        metadata->header.magic = MH_MAGIC_64;
        metadata->header.filetype = MH_DYLIB;
        metadata->header.ncmds = 2;
        metadata->header.sizeofcmds = sizeof(Metadata) - sizeof(mach_header_64);
        metadata->text.cmd = LC_SEGMENT_64;
        metadata->text.cmdsize = sizeof(segment_command_64);
        std::strcpy(metadata->text.segname, SEG_TEXT);
        // A nonzero preferred address exercises slide calculation rather than treating section.addr as an offset.
        metadata->text.vmaddr = 0x100000000ULL;
        metadata->text.vmsize = metadata->text.filesize = page_size;
        metadata->text.initprot = VM_PROT_READ;
        metadata->data.cmd = LC_SEGMENT_64;
        metadata->data.cmdsize = sizeof(segment_command_64) + sizeof(section_64);
        std::strcpy(metadata->data.segname, SEG_DATA);
        metadata->data.vmaddr = metadata->text.vmaddr + page_size;
        metadata->data.vmsize = metadata->data.filesize = metadata->data.fileoff = page_size;
        metadata->data.initprot = VM_PROT_READ | VM_PROT_WRITE;
        metadata->data.nsects = 1;
        std::strcpy(metadata->section.sectname, "AsyncioDebug");
        std::strcpy(metadata->section.segname, SEG_DATA);
        metadata->section.addr = metadata->data.vmaddr;
        metadata->section.size = sizeof(PyAsyncioDebugOffsets);
        const PyAsyncioDebugOffsets table{ { 512, 8, 16, 24, 25, 32, 40 }, { 4096, 128 }, { 1024, 8, 16, 256 } };
        std::memcpy(static_cast<char*>(mapping) + page_size, &table, sizeof(table));
        images = { reinterpret_cast<const mach_header*>(mapping) };
    }

    void TearDown() override
    {
        after_header_lookup = {};
        before_memory_read = {};
        images.clear();
        if (mapping != MAP_FAILED) {
            EXPECT_EQ(munmap(mapping, 2 * page_size), 0);
        }
    }
};

TEST_F(AsyncioMachOSnapshot, ReadsTableWithNonzeroPreferredAddress)
{
    auto offsets = find_asyncio_debug_offsets();
    ASSERT_TRUE(offsets);
    EXPECT_EQ(offsets->interpreter_tasks_head, 128);
    EXPECT_EQ(offsets->thread_tasks_head, 256);
    EXPECT_EQ(memory_reads, 3);
}

TEST_F(AsyncioMachOSnapshot, HeaderDisappearsAfterEnumeration)
{
    after_header_lookup = [&] {
        // Keep the address reserved but unreadable so reuse cannot make the simulated unload nondeterministic.
        EXPECT_EQ(mprotect(mapping, page_size, PROT_NONE), 0);
    };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(memory_reads, 1);
}

TEST_F(AsyncioMachOSnapshot, CommandsDisappearAfterHeaderRead)
{
    before_memory_read = [&](mach_vm_address_t, mach_vm_size_t size) {
        if (size > sizeof(mach_header_64)) {
            EXPECT_EQ(mprotect(mapping, page_size, PROT_NONE), 0);
        }
    };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(memory_reads, 2);
}

TEST_F(AsyncioMachOSnapshot, TableDisappearsAfterMetadataSnapshot)
{
    before_memory_read = [&](mach_vm_address_t address, mach_vm_size_t) {
        if (address == reinterpret_cast<uintptr_t>(mapping) + page_size) {
            EXPECT_EQ(mprotect(static_cast<char*>(mapping) + page_size, page_size, PROT_NONE), 0);
        }
    };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(memory_reads, 3);
}

TEST_F(AsyncioMachOSnapshot, RejectsNullHeader)
{
    images = { nullptr };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(memory_reads, 0);
}

TEST_F(AsyncioMachOSnapshot, RejectsChangedHeader)
{
    before_memory_read = [&](mach_vm_address_t, mach_vm_size_t size) {
        if (size > sizeof(mach_header_64)) {
            ++metadata->header.flags;
        }
    };
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsExcessiveCommands)
{
    metadata->header.ncmds = 257;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsOversizedMetadata)
{
    metadata->header.sizeofcmds = 64 * 1024 + 1;
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(memory_reads, 1);
}

TEST_F(AsyncioMachOSnapshot, RejectsUndersizedCommand)
{
    metadata->text.cmdsize = 0;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsCommandOutsideSnapshot)
{
    metadata->text.cmdsize = metadata->header.sizeofcmds + 8;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsSectionsOutsideCommand)
{
    metadata->data.nsects = 2;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsSectionOutsideSegment)
{
    metadata->section.addr += page_size;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsUnreadableSegment)
{
    metadata->data.initprot = VM_PROT_WRITE;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsMissingHeaderSegment)
{
    metadata->text.fileoff = page_size;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}

TEST_F(AsyncioMachOSnapshot, RejectsOverflowingTableAddress)
{
    metadata->section.addr = metadata->data.vmaddr = std::numeric_limits<uint64_t>::max() - page_size + 1;
    EXPECT_FALSE(find_asyncio_debug_offsets());
}
