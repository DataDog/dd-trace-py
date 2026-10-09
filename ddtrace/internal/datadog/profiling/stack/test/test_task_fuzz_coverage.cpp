#include <echion/cpython/tasks.h>

#include "fuzz_memory_image.h"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <array>
#include <cstring>
#include <vector>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size);

namespace {

std::vector<uintptr_t> list_reads;

} // namespace

void
echion_fuzz_on_read(const void* address, ssize_t size)
{
    if (size == static_cast<ssize_t>(sizeof(llist_node))) {
        list_reads.push_back(reinterpret_cast<uintptr_t>(address));
    }
}

class TaskFuzzCoverage : public ::testing::Test
{
  protected:
    // Match the harness's compact synthetic state layout, not CPython's full private structs.
    static constexpr size_t thread_base = 64;
    static constexpr size_t interpreter_base = 112;
    static constexpr size_t thread_head = thread_base + sizeof(uintptr_t);
    static constexpr size_t interpreter_head = interpreter_base + 2 * sizeof(uintptr_t);
    static constexpr size_t thread_node = 256;
    static constexpr size_t interpreter_node = 288;
    std::array<uint8_t, 512> seed{};

    template<typename T>
    void write(size_t offset, const T& value)
    {
        ASSERT_LE(offset + sizeof(value), seed.size());
        std::memcpy(seed.data() + offset, &value, sizeof(value));
    }

    static llist_node* node_at(size_t offset)
    {
        // NOLINTNEXTLINE(performance-no-int-to-ptr)
        return reinterpret_cast<llist_node*>(kRemoteBase + offset);
    }

    void SetUp() override
    {
        list_reads.clear();
        write(sizeof(uintptr_t), uint64_t{ thread_base });
        // Keep the unrelated Python task-set source unreadable.
        write(2 * sizeof(uintptr_t), uint64_t{ seed.size() - 1 });
        write(3 * sizeof(uintptr_t), uint64_t{ interpreter_base });
        write(thread_head, llist_node{ node_at(thread_head), node_at(thread_head) });
        write(interpreter_head, llist_node{ node_at(interpreter_head), node_at(interpreter_head) });
    }
};

TEST_F(TaskFuzzCoverage, ReadsBothNativeListHeads)
{
    EXPECT_EQ(LLVMFuzzerTestOneInput(seed.data(), seed.size()), 0);
    EXPECT_THAT(list_reads, ::testing::Contains(kRemoteBase + thread_head));
    EXPECT_THAT(list_reads, ::testing::Contains(kRemoteBase + interpreter_head));
}

TEST_F(TaskFuzzCoverage, ReachesNodesInBothNativeLists)
{
    write(thread_head, llist_node{ node_at(thread_node), node_at(thread_node) });
    write(interpreter_head, llist_node{ node_at(interpreter_node), node_at(interpreter_node) });
    // The zeroed nodes deliberately break the backward links. Each source must reach its node before rejecting it.
    EXPECT_EQ(LLVMFuzzerTestOneInput(seed.data(), seed.size()), 0);
    EXPECT_THAT(list_reads, ::testing::Contains(kRemoteBase + thread_node));
    EXPECT_THAT(list_reads, ::testing::Contains(kRemoteBase + interpreter_node));
}
