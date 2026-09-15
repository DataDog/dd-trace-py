#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <array>
#include <bit>
#include <cstdio>
#include <cstring>
#include <limits>

#if defined(__linux__)
#include <fcntl.h>
#include <link.h>
#include <unistd.h>

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
    auto offsets = parse_asyncio_debug_offsets(&table);
    ASSERT_TRUE(offsets);

    EchionSampler echion;
    echion.set_asyncio_offsets(*offsets);
    EXPECT_EQ(echion.asyncio_interpreter_tasks_head_offset(), 128);
    EXPECT_EQ(echion.asyncio_thread_tasks_head_offset(), 256);

    EXPECT_FALSE(parse_asyncio_debug_offsets(nullptr));
    table.task.task_node = table.task.size;
    EXPECT_FALSE(parse_asyncio_debug_offsets(&table));
    table = valid_table();
    table.thread.asyncio_tasks_head = table.thread.size;
    EXPECT_FALSE(parse_asyncio_debug_offsets(&table));
    table = valid_table();
    table.interpreter.asyncio_tasks_head = 0;
    EXPECT_FALSE(parse_asyncio_debug_offsets(&table));
    table = valid_table();
    table.thread.asyncio_tasks_head = 0;
    EXPECT_FALSE(parse_asyncio_debug_offsets(&table));
    table = valid_table();
    ++table.task.task_coro;
    EXPECT_FALSE(parse_asyncio_debug_offsets(&table));
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
    }

    void TearDown() override
    {
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

TEST_F(AsyncioElfTest, ReadsLoadedTableAndSupportsExtendedSectionNumbering)
{
    // Offset values must come from memory, not the file contents.
    on_disk.table = {};
    auto offsets = discover();
    ASSERT_TRUE(offsets);
    EXPECT_EQ(offsets->thread_tasks_head, loaded.table.thread.asyncio_tasks_head);
    EXPECT_EQ(offsets->interpreter_tasks_head, loaded.table.interpreter.asyncio_tasks_head);
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

TEST_F(AsyncioElfTest, RejectsUnavailableOrTruncatedFilesAndRetries)
{
    EXPECT_FALSE(read_asyncio_debug_offsets_from_elf(-1, binary));
    for (size_t size : { size_t{ 0 }, sizeof(ElfW(Ehdr)) - 1, offsetof(Binary, note), sizeof(Binary) - 1 }) {
        EXPECT_FALSE(discover(size));
    }
    EXPECT_TRUE(discover());
    const int fd = open("/dev/null", O_RDONLY);
    ASSERT_GE(fd, 0);
    EXPECT_FALSE(read_asyncio_debug_offsets_from_elf(fd, binary));
    close(fd);
}

TEST_F(AsyncioElfTest, RejectsReplacedBinaryAndMissingBuildId)
{
    ++on_disk.note.id[0];
    EXPECT_FALSE(discover());
    on_disk = loaded;
    ++on_disk.segments[0].p_memsz;
    EXPECT_FALSE(discover());
    on_disk = loaded;
    loaded.note.header.n_type = on_disk.note.header.n_type = NT_GNU_ABI_TAG;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, BoundsNoteParsing)
{
    loaded.note.header.n_namesz = on_disk.note.header.n_namesz = std::numeric_limits<uint32_t>::max();
    EXPECT_FALSE(discover());
    loaded.note.header.n_namesz = on_disk.note.header.n_namesz = 4;
    loaded.note.header.n_descsz = on_disk.note.header.n_descsz = std::numeric_limits<uint32_t>::max();
    EXPECT_FALSE(discover());
    loaded.segments[1].p_filesz = on_disk.segments[1].p_filesz = 4097;
    EXPECT_FALSE(discover());
}

TEST_F(AsyncioElfTest, RejectsMalformedMetadata)
{
    on_disk.header.e_ident[EI_DATA] = ELFDATANONE;
    EXPECT_FALSE(discover());
    on_disk = loaded;
    on_disk.header.e_shoff = std::numeric_limits<ElfW(Off)>::max();
    EXPECT_FALSE(discover());
    on_disk = loaded;
    on_disk.header.e_shnum = 0;
    on_disk.sections[0].sh_size = std::numeric_limits<ElfW(Xword)>::max();
    EXPECT_FALSE(discover());
    on_disk = loaded;
    on_disk.sections[1].sh_offset = std::numeric_limits<ElfW(Off)>::max();
    EXPECT_FALSE(discover());
    on_disk = loaded;
    on_disk.sections[3].sh_name = sizeof(loaded.names);
    EXPECT_FALSE(discover());
    on_disk = loaded;
    on_disk.names[sizeof(loaded.names) - 1] = 'x';
    EXPECT_FALSE(discover());
    on_disk = loaded;
    on_disk.sections[3].sh_addr = std::numeric_limits<ElfW(Addr)>::max();
    EXPECT_FALSE(discover());
    on_disk = loaded;
    on_disk.sections[3].sh_flags = 0;
    EXPECT_FALSE(discover());
    on_disk = loaded;
    loaded.table.thread.asyncio_tasks_head = 0;
    EXPECT_FALSE(discover());
}

#endif
