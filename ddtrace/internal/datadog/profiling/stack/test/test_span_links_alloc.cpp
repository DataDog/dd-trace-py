#include "span_links.hpp"

#include <gtest/gtest.h>

#include <cstdlib>
#include <new>

namespace {
// Isolate allocation injection in this executable, and disable it before any assertion allocates.
thread_local int allocations_before_failure = -1;
}

void*
operator new(std::size_t size)
{
    if (allocations_before_failure == 0) {
        allocations_before_failure = -1;
        throw std::bad_alloc();
    }
    if (allocations_before_failure > 0) {
        --allocations_before_failure;
    }
    if (void* memory = std::malloc(size == 0 ? 1 : size)) {
        return memory;
    }
    throw std::bad_alloc();
}

void
operator delete(void* memory) noexcept
{
    std::free(memory);
}

void
operator delete(void* memory, std::size_t) noexcept
{
    std::free(memory);
}

TEST(SpanLinksAllocation, FailedTaskPublicationLeavesNoOrphan)
{
    auto& links = Datadog::SpanLinks::get_instance();
    bool succeeded = false;
    int failures = 0;
    for (int allocation = 0; allocation < 16; ++allocation) {
        links.reset();
        // The new task shares a span with an existing task; rollback must preserve that task's reverse index.
        links.link_task_span(10, 100, 100, "web");
        allocations_before_failure = allocation;
        try {
            links.link_task_span(20, 100, 100, "web");
            succeeded = true;
        } catch (const std::bad_alloc&) {
            ++failures;
        }
        allocations_before_failure = -1;
        if (!succeeded) {
            EXPECT_FALSE(links.get_active_span_from_task_id(20).has_value());
        }
        EXPECT_TRUE(links.get_active_span_from_task_id(10).has_value());
        links.unlink_finished_span(100);
        EXPECT_FALSE(links.get_active_span_from_task_id(10).has_value());
        EXPECT_FALSE(links.get_active_span_from_task_id(20).has_value());
        if (succeeded) {
            break;
        }
    }
    EXPECT_TRUE(succeeded);
    EXPECT_GE(failures, 2);
    links.reset();
}
