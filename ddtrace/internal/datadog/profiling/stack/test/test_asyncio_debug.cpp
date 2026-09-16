#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <array>
#include <bit>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <limits>
#include <ostream>
#include <string>

#if defined(__linux__)
#include <fcntl.h>
#include <link.h>
#include <unistd.h>

namespace {

enum class ReadFault
{
    None,
    Interrupted,
    IoError,
    TruncateToEof,
    TruncateToShortRead,
};

// Only the fixture descriptor and selected offset are affected. All other reads use the real syscall.
struct ReadInjection
{
    int fd = -1;
    off_t offset = 0;
    ReadFault fault = ReadFault::None;
    unsigned int remaining = 0;
    unsigned int calls = 0;
    unsigned int injected = 0;
    ssize_t truncated_read_result = -1;
    size_t requested_size = 0;
};

thread_local ReadInjection read_injection;

template<typename Read>
ssize_t
inject_read(int fd, size_t size, off_t offset, Read real_read)
{
    auto& injection = read_injection;
    if (injection.fault == ReadFault::None || fd != injection.fd || offset != injection.offset) {
        return real_read();
    }
    ++injection.calls;
    if (injection.remaining == 0) {
        return real_read();
    }
    --injection.remaining;
    ++injection.injected;
    if (injection.fault == ReadFault::Interrupted || injection.fault == ReadFault::IoError) {
        errno = injection.fault == ReadFault::Interrupted ? EINTR : EIO;
        return -1;
    }

    // The reader has already captured the original size with fstat. Truncate immediately before the chosen pread
    // so the test exercises a real short read without racing another thread.
    injection.requested_size = size;
    const off_t available = injection.fault == ReadFault::TruncateToEof ? 0 : static_cast<off_t>(size - 1);
    if (ftruncate(fd, offset + available) != 0) {
        return -1;
    }
    injection.truncated_read_result = real_read();
    return injection.truncated_read_result;
}

} // namespace

extern "C" ssize_t
__real_pread(int fd, void* buffer, size_t size, off_t offset);

extern "C" ssize_t
__wrap_pread(int fd, void* buffer, size_t size, off_t offset)
{
    return inject_read(fd, size, offset, [&] { return __real_pread(fd, buffer, size, offset); });
}

#if defined(__GLIBC__)
// glibc may redirect pread through large-file or fortified entry points depending on the build flags.
extern "C" ssize_t
__real_pread64(int fd, void* buffer, size_t size, off64_t offset);
extern "C" ssize_t
__real___pread_chk(int fd, void* buffer, size_t size, off_t offset, size_t buffer_size);
extern "C" ssize_t
__real___pread64_chk(int fd, void* buffer, size_t size, off64_t offset, size_t buffer_size);

extern "C" ssize_t
__wrap_pread64(int fd, void* buffer, size_t size, off64_t offset)
{
    return inject_read(fd, size, offset, [&] { return __real_pread64(fd, buffer, size, offset); });
}

extern "C" ssize_t
__wrap___pread_chk(int fd, void* buffer, size_t size, off_t offset, size_t buffer_size)
{
    return inject_read(fd, size, offset, [&] { return __real___pread_chk(fd, buffer, size, offset, buffer_size); });
}

extern "C" ssize_t
__wrap___pread64_chk(int fd, void* buffer, size_t size, off64_t offset, size_t buffer_size)
{
    return inject_read(fd, size, offset, [&] { return __real___pread64_chk(fd, buffer, size, offset, buffer_size); });
}
#endif

extern "C"
{
    __attribute__((section(".AsyncioDebug"), used)) PyAsyncioDebugOffsets
      process_asyncio_debug_offsets = { { 512, 8, 16, 24, 25, 32, 40 }, { 4096, 128 }, { 1024, 8, 16, 256 } };
}
#endif

namespace {

PyAsyncioDebugOffsets
valid_table()
{
    PyAsyncioDebugOffsets table{};
    table.task.size = 512;
    table.task.task_name = 8;
    table.task.task_awaited_by = 16;
    table.task.task_is_task = 24;
    table.task.task_awaited_by_is_set = 25;
    table.task.task_coro = 32;
    table.task.task_node = 40;
    table.interpreter.size = 4096;
    table.interpreter.asyncio_tasks_head = 128;
    table.thread.size = 1024;
    table.thread.asyncio_running_loop = 8;
    table.thread.asyncio_running_task = 16;
    table.thread.asyncio_tasks_head = 256;
    return table;
}

} // namespace

TEST(AsyncioDebugOffsets, ValidatesAndStoresTaskListHeads)
{
    auto table = valid_table();
    auto offsets = parse_asyncio_debug_offsets(table);
    ASSERT_TRUE(offsets);

    EchionSampler echion;
    echion.set_asyncio_offsets(*offsets);
    EXPECT_EQ(echion.asyncio_interpreter_tasks_head_offset(), 128);
    EXPECT_EQ(echion.asyncio_thread_tasks_head_offset(), 256);
}

TEST(AsyncioDebugOffsets, RejectsTaskNodeOutsideTask)
{
    auto table = valid_table();
    table.task.task_node = table.task.size;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsThreadHeadOutsideThread)
{
    auto table = valid_table();
    table.thread.asyncio_tasks_head = table.thread.size;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsZeroInterpreterHead)
{
    auto table = valid_table();
    table.interpreter.asyncio_tasks_head = 0;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsZeroThreadHead)
{
    auto table = valid_table();
    table.thread.asyncio_tasks_head = 0;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsMisalignedTaskCoroutine)
{
    auto table = valid_table();
    ++table.task.task_coro;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsInterpreterTooSmallForListHead)
{
    auto table = valid_table();
    table.interpreter.size = sizeof(uintptr_t);
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsMisalignedInterpreterHead)
{
    auto table = valid_table();
    ++table.interpreter.asyncio_tasks_head;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsMisalignedThreadHead)
{
    auto table = valid_table();
    ++table.thread.asyncio_tasks_head;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

#if PY_VERSION_HEX >= 0x030e0000
TEST(AsyncioDebugOffsets, DiscoversRuntimeTableAfterAnEarlierAttempt)
{
    Py_Initialize();
    // Shared _asyncio builds have no table yet. Built-in _asyncio may already expose it before import.
    (void)find_asyncio_debug_offsets();
    PyObject* module = PyImport_ImportModule("asyncio");
    ASSERT_NE(module, nullptr);
    auto offsets = find_asyncio_debug_offsets();
    Py_DECREF(module);
    ASSERT_TRUE(offsets);
    EXPECT_GT(offsets->interpreter_tasks_head, 0);
    EXPECT_GT(offsets->thread_tasks_head, 0);
}
#endif

#if defined(__linux__)

class AsyncioElfTest : public ::testing::Test
{
  protected:
    struct Binary
    {
        ElfW(Ehdr) header {};
        std::array<ElfW(Phdr), 2> segments{};
        struct
        {
            ElfW(Nhdr) header { 4, 4, NT_GNU_BUILD_ID };
            char name[4] = "GNU";
            unsigned char id[4] = { 1, 2, 3, 4 };
        } note;
        PyAsyncioDebugOffsets invalid_table{};
        PyAsyncioDebugOffsets table = valid_table();
        char names[sizeof(".AsyncioDebug")] = ".AsyncioDebug";
        std::array<ElfW(Shdr), 4> sections{};
    } loaded, on_disk;
    dl_phdr_info binary{};
    FILE* file = nullptr;

    void SetUp() override
    {
        read_injection = {};
        auto& header = loaded.header;
        std::memcpy(header.e_ident, ELFMAG, SELFMAG);
        header.e_ident[EI_CLASS] = sizeof(void*) == 8 ? ELFCLASS64 : ELFCLASS32;
        header.e_ident[EI_DATA] = std::endian::native == std::endian::little ? ELFDATA2LSB : ELFDATA2MSB;
        header.e_ident[EI_VERSION] = EV_CURRENT;
        header.e_version = EV_CURRENT;
        header.e_type = ET_DYN;
        header.e_ehsize = sizeof(header);
        header.e_phentsize = sizeof(ElfW(Phdr));
        header.e_phnum = loaded.segments.size();
        header.e_phoff = offsetof(Binary, segments);
        header.e_shentsize = sizeof(ElfW(Shdr));
        header.e_shnum = loaded.sections.size();
        header.e_shstrndx = 1;
        header.e_shoff = offsetof(Binary, sections);
        loaded.segments[0].p_type = PT_LOAD;
        loaded.segments[0].p_flags = PF_R;
        loaded.segments[0].p_memsz = sizeof(loaded);
        loaded.segments[1].p_type = PT_NOTE;
        loaded.segments[1].p_offset = loaded.segments[1].p_vaddr = offsetof(Binary, note);
        loaded.segments[1].p_filesz = loaded.segments[1].p_memsz = sizeof(loaded.note);
        loaded.sections[1].sh_type = SHT_STRTAB;
        loaded.sections[1].sh_offset = offsetof(Binary, names);
        loaded.sections[1].sh_size = sizeof(loaded.names);
        loaded.sections[3].sh_type = SHT_PROGBITS;
        loaded.sections[3].sh_flags = SHF_ALLOC;
        loaded.sections[3].sh_addr = loaded.sections[3].sh_offset = offsetof(Binary, table);
        loaded.sections[3].sh_size = sizeof(loaded.table);
        binary.dlpi_addr = reinterpret_cast<ElfW(Addr)>(&loaded);
        binary.dlpi_phdr = loaded.segments.data();
        binary.dlpi_phnum = loaded.segments.size();
        on_disk = loaded;
        file = std::tmpfile();
        ASSERT_NE(file, nullptr);
        read_injection.fd = fileno(file);
    }

    void TearDown() override
    {
        read_injection = {};
        if (file != nullptr) {
            std::fclose(file);
        }
    }

    std::optional<AsyncioOffsets> discover(size_t size = sizeof(Binary))
    {
        const int fd = fileno(file);
        EXPECT_EQ(ftruncate(fd, 0), 0);
        // Write members separately so implicit C++ padding becomes zero-filled file gaps.
        const auto write_field = [fd](const auto& field, off_t offset) {
            EXPECT_EQ(pwrite(fd, &field, sizeof(field), offset), static_cast<ssize_t>(sizeof(field)));
        };
        write_field(on_disk.header, offsetof(Binary, header));
        write_field(on_disk.segments, offsetof(Binary, segments));
        write_field(on_disk.note, offsetof(Binary, note));
        write_field(on_disk.invalid_table, offsetof(Binary, invalid_table));
        write_field(on_disk.table, offsetof(Binary, table));
        write_field(on_disk.names, offsetof(Binary, names));
        write_field(on_disk.sections, offsetof(Binary, sections));
        EXPECT_EQ(ftruncate(fd, static_cast<off_t>(size)), 0);
        return read_asyncio_debug_offsets_from_elf(fd, binary);
    }
};

TEST(AsyncioElfDiscovery, ReadsBuildIdFreeProcessExecutableAndNamedMapping)
{
#if defined(TEST_RUNNING_ON_VALGRIND)
    GTEST_SKIP() << "/proc/self/exe identifies Valgrind rather than the test executable";
#endif
    struct DiscoveryResults
    {
        std::optional<AsyncioOffsets> executable;
        std::optional<AsyncioOffsets> named_mapping;
    } results;
    dl_iterate_phdr(
      [](dl_phdr_info* binary, size_t, void* data) {
          if (binary->dlpi_name != nullptr && binary->dlpi_name[0] != '\0') {
              return 0;
          }
          const int fd = open("/proc/self/exe", O_RDONLY | O_CLOEXEC);
          if (fd >= 0) {
              auto* output = static_cast<DiscoveryResults*>(data);
              output->executable = read_asyncio_debug_offsets_from_elf(fd, *binary);
              dl_phdr_info named_binary = *binary;
              named_binary.dlpi_name = "/proc/self/exe";
              output->named_mapping = read_asyncio_debug_offsets_from_elf(fd, named_binary);
              close(fd);
          }
          return 1;
      },
      &results);

    auto discovered = find_asyncio_debug_offsets();
    for (const auto& offsets : { results.executable, results.named_mapping, discovered }) {
        ASSERT_TRUE(offsets);
        EXPECT_EQ(offsets->interpreter_tasks_head, process_asyncio_debug_offsets.interpreter.asyncio_tasks_head);
        EXPECT_EQ(offsets->thread_tasks_head, process_asyncio_debug_offsets.thread.asyncio_tasks_head);
    }
}

TEST_F(AsyncioElfTest, ReadsOffsetsFromMemoryNotFile)
{
    // Offset values must come from memory, not the file contents.
    on_disk.table = {};
    auto offsets = discover();
    ASSERT_TRUE(offsets);
    EXPECT_EQ(offsets->thread_tasks_head, loaded.table.thread.asyncio_tasks_head);
    EXPECT_EQ(offsets->interpreter_tasks_head, loaded.table.interpreter.asyncio_tasks_head);
}

TEST_F(AsyncioElfTest, SupportsExtendedSectionNumbering)
{
    on_disk.header.e_shnum = 0;
    on_disk.header.e_shstrndx = SHN_XINDEX;
    on_disk.sections[0].sh_size = loaded.sections.size();
    on_disk.sections[0].sh_link = 1;
    EXPECT_TRUE(discover());
}

TEST_F(AsyncioElfTest, SkipsInvalidDuplicateSection)
{
    on_disk.sections[2] = loaded.sections[2] = loaded.sections[3];
    on_disk.sections[2].sh_addr = loaded.sections[2].sh_addr = offsetof(Binary, invalid_table);
    EXPECT_TRUE(discover());
}

TEST_F(AsyncioElfTest, RejectsInvalidFileDescriptor)
{
    EXPECT_FALSE(read_asyncio_debug_offsets_from_elf(-1, binary));
}

TEST_F(AsyncioElfTest, RejectsNonRegularFile)
{
    const int fd = open("/dev/null", O_RDONLY);
    ASSERT_GE(fd, 0);
    EXPECT_FALSE(read_asyncio_debug_offsets_from_elf(fd, binary));
    close(fd);
}

TEST_F(AsyncioElfTest, RejectsWriteOnlyFileDescriptor)
{
    ASSERT_TRUE(discover());
    const auto path = "/proc/self/fd/" + std::to_string(fileno(file));
    const int fd = open(path.c_str(), O_WRONLY);
    ASSERT_GE(fd, 0);
    EXPECT_FALSE(read_asyncio_debug_offsets_from_elf(fd, binary));
    close(fd);
}

TEST_F(AsyncioElfTest, RejectsEmptyFile)
{
    EXPECT_FALSE(discover(0));
}

TEST_F(AsyncioElfTest, RejectsTruncatedElfHeader)
{
    EXPECT_FALSE(discover(sizeof(ElfW(Ehdr)) - 1));
}

TEST_F(AsyncioElfTest, RejectsTruncatedNote)
{
    EXPECT_FALSE(discover(offsetof(Binary, note)));
}

TEST_F(AsyncioElfTest, RejectsTruncatedSectionTable)
{
    EXPECT_FALSE(discover(sizeof(Binary) - 1));
}

TEST_F(AsyncioElfTest, RetriesAfterFileIsRestored)
{
    EXPECT_FALSE(discover(0));
    EXPECT_TRUE(discover());
}

TEST_F(AsyncioElfTest, RejectsMismatchedBuildId)
{
    ++on_disk.note.id[0];
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsMismatchedProgramHeaders)
{
    ++on_disk.segments[0].p_memsz;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsMissingBuildIdWithoutMatchingFileIdentity)
{
    loaded.note.header.n_type = on_disk.note.header.n_type = NT_GNU_ABI_TAG;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsOversizedNoteName)
{
    loaded.note.header.n_namesz = on_disk.note.header.n_namesz = std::numeric_limits<uint32_t>::max();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsOversizedNoteDescriptor)
{
    loaded.note.header.n_descsz = on_disk.note.header.n_descsz = std::numeric_limits<uint32_t>::max();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsOversizedNoteSegment)
{
    loaded.segments[1].p_filesz = on_disk.segments[1].p_filesz = 4097;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsInvalidElfMagic)
{
    on_disk.header.e_ident[EI_MAG0] = 0;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsWrongElfClass)
{
    on_disk.header.e_ident[EI_CLASS] = sizeof(void*) == 8 ? ELFCLASS32 : ELFCLASS64;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsInvalidByteOrder)
{
    on_disk.header.e_ident[EI_DATA] = ELFDATANONE;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsInvalidIdentVersion)
{
    on_disk.header.e_ident[EI_VERSION] = EV_NONE;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsInvalidHeaderVersion)
{
    on_disk.header.e_version = EV_NONE;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsRelocatableElf)
{
    on_disk.header.e_type = ET_REL;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsWrongElfHeaderSize)
{
    --on_disk.header.e_ehsize;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsWrongProgramHeaderSize)
{
    --on_disk.header.e_phentsize;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsMissingProgramHeaders)
{
    on_disk.header.e_phnum = 0;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsTooManyProgramHeaders)
{
    on_disk.header.e_phnum = 257;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsOverflowingProgramHeaderOffset)
{
    on_disk.header.e_phoff = std::numeric_limits<ElfW(Off)>::max();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsWrongSectionHeaderSize)
{
    --on_disk.header.e_shentsize;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsOverflowingSectionHeaderOffset)
{
    on_disk.header.e_shoff = std::numeric_limits<ElfW(Off)>::max();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsExcessiveExtendedSectionCount)
{
    on_disk.header.e_shnum = 0;
    on_disk.sections[0].sh_size = std::numeric_limits<ElfW(Xword)>::max();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsZeroExtendedSectionCount)
{
    on_disk.header.e_shnum = 0;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsNamesIndexOutsideSectionTable)
{
    on_disk.header.e_shstrndx = loaded.sections.size();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsExtendedNamesIndexOutsideSectionTable)
{
    on_disk.header.e_shstrndx = SHN_XINDEX;
    on_disk.sections[0].sh_link = loaded.sections.size();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsWrongStringTableType)
{
    on_disk.sections[1].sh_type = SHT_PROGBITS;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsOverflowingStringTableOffset)
{
    on_disk.sections[1].sh_offset = std::numeric_limits<ElfW(Off)>::max();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsSectionNameOutsideStringTable)
{
    on_disk.sections[3].sh_name = sizeof(loaded.names);
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsUnterminatedSectionName)
{
    on_disk.names[sizeof(loaded.names) - 1] = 'x';
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsOverflowingSectionAddress)
{
    on_disk.sections[3].sh_addr = std::numeric_limits<ElfW(Addr)>::max();
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsNonAllocatedSection)
{
    on_disk.sections[3].sh_flags = 0;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsWrongDebugSectionType)
{
    on_disk.sections[3].sh_type = SHT_NOBITS;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsUndersizedDebugSection)
{
    on_disk.sections[3].sh_size = sizeof(PyAsyncioDebugOffsets) - 1;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsDebugSectionOutsideLoadedSegment)
{
    on_disk.sections[3].sh_addr = sizeof(Binary);
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsDebugSectionCrossingLoadedSegmentEnd)
{
    loaded.segments[0].p_memsz = on_disk.segments[0].p_memsz = offsetof(Binary, table) + sizeof(loaded.table) - 1;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsUnreadableLoadedSegment)
{
    loaded.segments[0].p_flags = on_disk.segments[0].p_flags = PF_W;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsInvalidLoadedTable)
{
    loaded.table.thread.asyncio_tasks_head = 0;
    EXPECT_FALSE(discover());
}

struct ReadStage
{
    const char* name;
    off_t offset;

    friend void PrintTo(const ReadStage& stage, std::ostream* output) { *output << stage.name; }
};

class AsyncioElfReadTest
  : public AsyncioElfTest
  , public ::testing::WithParamInterface<ReadStage>
{
  public:
    static auto stages()
    {
        return ::testing::Values(ReadStage{ "ElfHeader", 0 },
                                 ReadStage{ "LoadSegment", offsetof(Binary, segments) },
                                 ReadStage{ "NoteSegment", offsetof(Binary, segments) + sizeof(ElfW(Phdr)) },
                                 ReadStage{ "BuildId", offsetof(Binary, note) },
                                 ReadStage{ "FirstSection", offsetof(Binary, sections) },
                                 ReadStage{ "StringTableSection", offsetof(Binary, sections) + sizeof(ElfW(Shdr)) },
                                 ReadStage{ "DebugSection", offsetof(Binary, sections) + 3 * sizeof(ElfW(Shdr)) },
                                 ReadStage{ "SectionName", offsetof(Binary, names) });
    }

  protected:
    void inject(ReadFault fault, unsigned int count = 1)
    {
        read_injection.offset = GetParam().offset;
        read_injection.fault = fault;
        read_injection.remaining = count;
    }
};

TEST_P(AsyncioElfReadTest, RetriesInterruptedReads)
{
    inject(ReadFault::Interrupted, 2);
    EXPECT_TRUE(discover());
    EXPECT_EQ(read_injection.injected, 2);
    EXPECT_GE(read_injection.calls, 3);
}

TEST_P(AsyncioElfReadTest, BoundsInterruptedReadRetries)
{
    inject(ReadFault::Interrupted, 4);
    EXPECT_FALSE(discover());
    EXPECT_EQ(read_injection.injected, 3);
    EXPECT_EQ(read_injection.calls, 3);
}

TEST_P(AsyncioElfReadTest, RejectsIoErrorWithoutRetry)
{
    inject(ReadFault::IoError);
    EXPECT_FALSE(discover());
    EXPECT_EQ(read_injection.injected, 1);
    EXPECT_EQ(read_injection.calls, 1);
}

TEST_P(AsyncioElfReadTest, RejectsEofAfterFileSizeWasChecked)
{
    inject(ReadFault::TruncateToEof);
    EXPECT_FALSE(discover());
    EXPECT_EQ(read_injection.injected, 1);
    EXPECT_EQ(read_injection.calls, 1);
    EXPECT_EQ(read_injection.truncated_read_result, 0);
}

TEST_P(AsyncioElfReadTest, RejectsShortReadAfterFileSizeWasChecked)
{
    inject(ReadFault::TruncateToShortRead);
    EXPECT_FALSE(discover());
    EXPECT_EQ(read_injection.injected, 1);
    EXPECT_EQ(read_injection.calls, 1);
    ASSERT_GT(read_injection.requested_size, 1);
    EXPECT_EQ(read_injection.truncated_read_result, static_cast<ssize_t>(read_injection.requested_size - 1));
}

TEST_P(AsyncioElfReadTest, RetriesDiscoveryAfterReadError)
{
    inject(ReadFault::IoError);
    EXPECT_FALSE(discover());
    EXPECT_EQ(read_injection.injected, 1);
    read_injection.fault = ReadFault::None;
    EXPECT_TRUE(discover());
}

INSTANTIATE_TEST_SUITE_P(FileReads,
                         AsyncioElfReadTest,
                         AsyncioElfReadTest::stages(),
                         [](const ::testing::TestParamInfo<ReadStage>& parameter) { return parameter.param.name; });

#endif
