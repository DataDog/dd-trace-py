// AsyncioDebug table tests: validation against fake memory on every Python version, and discovery through the real
// _asyncio module state on Python 3.14+.

#include "asyncio_debug_test_table.h"

#include "echion/cpython/asyncio_discovery.h"
#include "echion/echion_sampler.h"
#include "echion/vm.h"

#include <gtest/gtest.h>

#include <sys/mman.h>
#include <unistd.h>

#include <cstring>
#include <utility>
#include <vector>

using asyncio_debug_test::valid_table;

namespace {

// Readable regions of the fake address space. The fake reader copies only from inside one of them, so tests can make
// any address unreadable by not registering it.
std::vector<std::pair<uintptr_t, size_t>> g_readable;

bool
fake_read(uintptr_t start, void* buffer, size_t size)
{
    for (const auto& [base, length] : g_readable) {
        if (start >= base && size <= length && start - base <= length - size) {
            std::memcpy(buffer, reinterpret_cast<const void*>(start), size);
            return true;
        }
    }
    return false;
}

template<typename T>
void
make_readable(const T& object)
{
    g_readable.emplace_back(reinterpret_cast<uintptr_t>(&object), sizeof(object));
}

AsyncioTaskLayout
layout_of(const PyAsyncioDebugOffsets& table)
{
    return AsyncioTaskLayout{ table.task.size,
                              table.task.task_name,
                              table.task.task_awaited_by,
                              table.task.task_is_task,
                              table.task.task_awaited_by_is_set,
                              table.task.task_coro,
                              table.task.task_node };
}

struct Node
{
    uintptr_t next;
    uintptr_t prev;
};

uintptr_t
address_of(const Node& node)
{
    return reinterpret_cast<uintptr_t>(&node);
}

class AsyncioFakeMemory : public ::testing::Test
{
  protected:
    void SetUp() override { g_readable.clear(); }
    void TearDown() override { g_readable.clear(); }
};

} // namespace

// ----------------------------------------------------------------------------
// Table validation (parse_asyncio_debug_offsets).
// ----------------------------------------------------------------------------

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

TEST(AsyncioDebugOffsets, ReportsTaskLayout)
{
    auto table = valid_table();
    auto offsets = parse_asyncio_debug_offsets(table);
    ASSERT_TRUE(offsets);
    ASSERT_TRUE(offsets->task_layout);
    EXPECT_EQ(*offsets->task_layout, layout_of(table));
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

// ----------------------------------------------------------------------------
// Reading the table (read_asyncio_debug_offsets_at), against fake memory.
// ----------------------------------------------------------------------------

TEST_F(AsyncioFakeMemory, ReadsValidTable)
{
    const auto table = valid_table();
    make_readable(table);

    auto offsets = read_asyncio_debug_offsets_at(reinterpret_cast<uintptr_t>(&table), layout_of(table), fake_read);
    ASSERT_TRUE(offsets);
    EXPECT_EQ(offsets->interpreter_tasks_head, table.interpreter.asyncio_tasks_head);
    EXPECT_EQ(offsets->thread_tasks_head, table.thread.asyncio_tasks_head);
}

TEST_F(AsyncioFakeMemory, RejectsNullMisalignedOrUnreadableTableAddress)
{
    // Room for a misaligned copy one byte in, so only the alignment check can reject it.
    alignas(uint64_t) unsigned char bytes[sizeof(PyAsyncioDebugOffsets) + 1] = {};
    const auto table = valid_table();
    std::memcpy(bytes + 1, &table, sizeof(table));
    make_readable(bytes);
    const auto layout = layout_of(table);

    EXPECT_FALSE(read_asyncio_debug_offsets_at(0, layout, fake_read));
    EXPECT_FALSE(read_asyncio_debug_offsets_at(reinterpret_cast<uintptr_t>(bytes + 1), layout, fake_read));

    const auto unreadable = valid_table();
    EXPECT_FALSE(read_asyncio_debug_offsets_at(reinterpret_cast<uintptr_t>(&unreadable), layout, fake_read));
}

TEST_F(AsyncioFakeMemory, RejectsTableThatFailsValidation)
{
    auto table = valid_table();
    table.thread.asyncio_tasks_head = 0;
    make_readable(table);

    EXPECT_FALSE(read_asyncio_debug_offsets_at(reinterpret_cast<uintptr_t>(&table), layout_of(table), fake_read));
}

TEST_F(AsyncioFakeMemory, RejectsTaskLayoutDifferingByOneField)
{
    const auto table = valid_table();
    make_readable(table);

    auto layout = layout_of(table);
    layout.task_node += sizeof(uintptr_t);
    EXPECT_FALSE(read_asyncio_debug_offsets_at(reinterpret_cast<uintptr_t>(&table), layout, fake_read));

    layout = layout_of(table);
    layout.size += sizeof(uintptr_t);
    EXPECT_FALSE(read_asyncio_debug_offsets_at(reinterpret_cast<uintptr_t>(&table), layout, fake_read));
}

// ----------------------------------------------------------------------------
// Live list-head checks, against fake memory.
// ----------------------------------------------------------------------------

TEST_F(AsyncioFakeMemory, AcceptsEmptyAndTwoNodeLists)
{
    Node empty{};
    empty.next = empty.prev = address_of(empty);
    make_readable(empty);
    EXPECT_TRUE(asyncio_list_head_is_consistent(address_of(empty), fake_read));

    Node head{}, first{}, second{};
    head.next = address_of(first);
    head.prev = address_of(second);
    first.next = address_of(second);
    first.prev = address_of(head);
    second.next = address_of(head);
    second.prev = address_of(first);
    make_readable(head);
    make_readable(first);
    make_readable(second);
    EXPECT_TRUE(asyncio_list_head_is_consistent(address_of(head), fake_read));
}

TEST_F(AsyncioFakeMemory, RejectsInconsistentOrUnreadableHeads)
{
    Node head{}, neighbor{};
    make_readable(head);
    make_readable(neighbor);
    const auto link = [&]() {
        head.next = head.prev = address_of(neighbor);
        neighbor.next = neighbor.prev = address_of(head);
    };

    link();
    ASSERT_TRUE(asyncio_list_head_is_consistent(address_of(head), fake_read));

    link();
    head.next = 0;
    EXPECT_FALSE(asyncio_list_head_is_consistent(address_of(head), fake_read));

    link();
    head.prev = 0;
    EXPECT_FALSE(asyncio_list_head_is_consistent(address_of(head), fake_read));

    link();
    neighbor.prev = address_of(neighbor);
    EXPECT_FALSE(asyncio_list_head_is_consistent(address_of(head), fake_read));

    link();
    neighbor.next = address_of(neighbor);
    EXPECT_FALSE(asyncio_list_head_is_consistent(address_of(head), fake_read));

    Node unreadable_head{};
    unreadable_head.next = unreadable_head.prev = address_of(unreadable_head);
    EXPECT_FALSE(asyncio_list_head_is_consistent(address_of(unreadable_head), fake_read));

    Node unreadable_neighbor{};
    unreadable_neighbor.next = unreadable_neighbor.prev = address_of(head);
    link();
    head.next = head.prev = address_of(unreadable_neighbor);
    EXPECT_FALSE(asyncio_list_head_is_consistent(address_of(head), fake_read));
}

TEST_F(AsyncioFakeMemory, ChecksThreadHeadAlwaysAndInterpreterHeadWhenGiven)
{
    // Each fake struct is one list head preceded by 16 bytes, so the head offset is 16 for both.
    struct FakeState
    {
        unsigned char padding[16];
        Node head;
    };
    FakeState thread{}, interp{};
    make_readable(thread);
    make_readable(interp);
    thread.head.next = thread.head.prev = address_of(thread.head);
    interp.head.next = interp.head.prev = address_of(interp.head);
    const AsyncioOffsets offsets{ .interpreter_tasks_head = offsetof(FakeState, head),
                                  .thread_tasks_head = offsetof(FakeState, head) };
    const auto thread_base = reinterpret_cast<uintptr_t>(&thread);
    const auto interp_base = reinterpret_cast<uintptr_t>(&interp);

    EXPECT_TRUE(asyncio_offsets_match_live_heads(offsets, thread_base, interp_base, fake_read));

    interp.head.next = 0;
    EXPECT_FALSE(asyncio_offsets_match_live_heads(offsets, thread_base, interp_base, fake_read));
    // The free-threaded path passes no interpreter base and must not look at that head.
    EXPECT_TRUE(asyncio_offsets_match_live_heads(offsets, thread_base, 0, fake_read));

    thread.head.next = 0;
    EXPECT_FALSE(asyncio_offsets_match_live_heads(offsets, thread_base, 0, fake_read));
    EXPECT_FALSE(asyncio_offsets_match_live_heads(offsets, 0, 0, fake_read));

    const AsyncioOffsets overflowing{ .interpreter_tasks_head = 16, .thread_tasks_head = SIZE_MAX };
    EXPECT_FALSE(asyncio_offsets_match_live_heads(overflowing, thread_base, 0, fake_read));
}

// ----------------------------------------------------------------------------
// read_own_memory, against real memory.
// ----------------------------------------------------------------------------

TEST(AsyncioReadOwnMemory, ReadsReadableMemoryAndRejectsUnreadableMemory)
{
    const uint64_t source[4] = { 1, 2, 3, 4 };
    uint64_t copy[4] = {};
    ASSERT_TRUE(read_own_memory(reinterpret_cast<uintptr_t>(source), copy, sizeof(source)));
    EXPECT_EQ(std::memcmp(source, copy, sizeof(source)), 0);

    EXPECT_FALSE(read_own_memory(0, copy, sizeof(copy)));

    // Two pages, the second inaccessible: a read entirely in it fails, and so does one that starts readable and runs
    // into it.
    const size_t page = static_cast<size_t>(getpagesize());
    auto* pages =
      static_cast<unsigned char*>(mmap(nullptr, 2 * page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    ASSERT_NE(pages, MAP_FAILED);
    ASSERT_EQ(mprotect(pages + page, page, PROT_NONE), 0);

    const auto base = reinterpret_cast<uintptr_t>(pages);
    EXPECT_FALSE(read_own_memory(base + page, copy, sizeof(copy)));
    EXPECT_FALSE(read_own_memory(base + page - sizeof(uint64_t), copy, sizeof(copy)));
    EXPECT_TRUE(read_own_memory(base + page - sizeof(copy), copy, sizeof(copy)));

    munmap(pages, 2 * page);
}

// ----------------------------------------------------------------------------
// Discovery through the real _asyncio module state (Python 3.14+).
// ----------------------------------------------------------------------------

#if PY_VERSION_HEX >= 0x030e0000
namespace {

class AsyncioDiscovery : public ::testing::Test
{
  protected:
    static void SetUpTestSuite()
    {
        if (!Py_IsInitialized()) {
            Py_Initialize();
        }
    }

    void SetUp() override
    {
        asyncio_c = PyImport_ImportModule("_asyncio");
        ASSERT_NE(asyncio_c, nullptr);
    }

    void TearDown() override
    {
        Py_XDECREF(asyncio_c);
        EXPECT_FALSE(PyErr_Occurred());
        PyErr_Clear();
    }

    PyObject* asyncio_c = nullptr;
};

AsyncioStateMirror*
state_of(PyObject* module)
{
    return static_cast<AsyncioStateMirror*>(PyModule_GetState(module));
}

// A multi-phase module named _asyncio whose state has the given size, built without touching sys.modules.
struct FakeAsyncioModule
{
    PyModuleDef_Slot slots[1] = { { 0, nullptr } };
    PyModuleDef def;
    PyObject* module = nullptr;

    explicit FakeAsyncioModule(Py_ssize_t state_size)
      : def{ PyModuleDef_HEAD_INIT, "_asyncio", nullptr, state_size, nullptr, slots, nullptr, nullptr, nullptr }
    {
        PyObject* machinery = PyImport_ImportModule("importlib.machinery");
        PyObject* spec = machinery ? PyObject_CallMethod(machinery, "ModuleSpec", "sO", "_asyncio", Py_None) : nullptr;
        if (spec != nullptr) {
            module = PyModule_FromDefAndSpec(&def, spec);
            if (module != nullptr && PyModule_ExecDef(module, &def) != 0) {
                Py_CLEAR(module);
            }
        }
        Py_XDECREF(spec);
        Py_XDECREF(machinery);
    }

    ~FakeAsyncioModule() { Py_XDECREF(module); }
};

} // namespace

TEST_F(AsyncioDiscovery, FindsTheRuntimeTable)
{
    auto offsets = discover_asyncio_offsets(asyncio_c);
    ASSERT_TRUE(offsets);
    // Native tests build and run on the same release, so the compile-time offsets are the right answer here.
    EXPECT_EQ(offsets->interpreter_tasks_head, offsetof(PyInterpreterState, asyncio_tasks_head));
    EXPECT_EQ(offsets->thread_tasks_head, offsetof(_PyThreadStateImpl, asyncio_tasks_head));
    ASSERT_TRUE(offsets->task_layout);
    EXPECT_EQ(*offsets->task_layout, EchionSampler::asyncio_mirror_task_layout());
}

// Fails first when a CPython release changes asyncio_state: update AsyncioStateMirror from Modules/_asynciomodule.c.
TEST_F(AsyncioDiscovery, StateMirrorMatchesTheRuntimeModule)
{
    PyModuleDef* def = PyModule_GetDef(asyncio_c);
    ASSERT_NE(def, nullptr);
    EXPECT_EQ(def->m_size, static_cast<Py_ssize_t>(sizeof(AsyncioStateMirror)));
    EXPECT_NE(state_of(asyncio_c)->debug_offsets, nullptr);
}

TEST_F(AsyncioDiscovery, RejectsObjectsThatAreNotTheAsyncioModule)
{
    EXPECT_FALSE(discover_asyncio_offsets(Py_None));

    PyObject* number = PyLong_FromLong(42);
    ASSERT_NE(number, nullptr);
    EXPECT_FALSE(discover_asyncio_offsets(number));
    Py_DECREF(number);

    // A different extension module and a Python module.
    for (const char* name : { "_json", "asyncio" }) {
        PyObject* module = PyImport_ImportModule(name);
        ASSERT_NE(module, nullptr) << name;
        EXPECT_FALSE(discover_asyncio_offsets(module)) << name;
        EXPECT_FALSE(PyErr_Occurred()) << name;
        Py_DECREF(module);
    }
}

TEST_F(AsyncioDiscovery, RejectsStateOfTheWrongSize)
{
    const void* table = state_of(asyncio_c)->debug_offsets;
    for (Py_ssize_t size : { static_cast<Py_ssize_t>(sizeof(AsyncioStateMirror) - sizeof(void*)),
                             static_cast<Py_ssize_t>(sizeof(AsyncioStateMirror) + sizeof(void*)) }) {
        FakeAsyncioModule fake(size);
        ASSERT_NE(fake.module, nullptr) << size;
        // Same slot as the real module's, holding the real table, so only the size check can reject it.
        std::memcpy(static_cast<char*>(PyModule_GetState(fake.module)) + offsetof(AsyncioStateMirror, debug_offsets),
                    &table,
                    sizeof(table));
        EXPECT_FALSE(discover_asyncio_offsets(fake.module)) << size;
    }
}

TEST_F(AsyncioDiscovery, ChecksWhatTheStateSlotPointsTo)
{
    FakeAsyncioModule fake(sizeof(AsyncioStateMirror));
    ASSERT_NE(fake.module, nullptr);
    AsyncioStateMirror* state = state_of(fake.module);

    // The fixture is accepted with the real table, so the rejections below come from the slot's value.
    state->debug_offsets = state_of(asyncio_c)->debug_offsets;
    EXPECT_TRUE(discover_asyncio_offsets(fake.module));

    state->debug_offsets = nullptr;
    EXPECT_FALSE(discover_asyncio_offsets(fake.module));

    // A counter moved into the slot by a same-size reorder.
    state->debug_offsets = reinterpret_cast<void*>(uintptr_t{ 42 });
    EXPECT_FALSE(discover_asyncio_offsets(fake.module));

    // Another object, for example a PyObject* moved into the slot.
    state->debug_offsets = asyncio_c;
    EXPECT_FALSE(discover_asyncio_offsets(fake.module));

    // A well-formed table whose task layout is not Echion's TaskObj mirror.
    static const PyAsyncioDebugOffsets other_layout = valid_table();
    state->debug_offsets = const_cast<PyAsyncioDebugOffsets*>(&other_layout);
    EXPECT_FALSE(discover_asyncio_offsets(fake.module));
}
#endif
