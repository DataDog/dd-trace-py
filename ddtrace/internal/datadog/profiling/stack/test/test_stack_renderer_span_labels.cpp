#include "origin_task_links.hpp"
#include "span_links.hpp"
#include "stack_renderer.hpp"

#include "dd_wrapper/include/ddup_interface.hpp"
#include "dd_wrapper/include/libdatadog_helpers.hpp"
#include "dd_wrapper/include/sample_manager.hpp"
#include "dd_wrapper/include/uploader_builder.hpp"

#include <gtest/gtest.h>

#include <cstdint>
#include <optional>
#include <string>

namespace Datadog {

// Checks which span labels StackRenderer puts on thread, task and greenlet samples. The labels are read from the
// sample being built, so each test checks them before render_stack_end flushes the sample.
class StackRendererSpanLabels : public ::testing::Test
{
  protected:
    static constexpr uintptr_t thread_id = 1001;
    static constexpr uint64_t thread_span_id = 1;
    static constexpr uint64_t task_span_id = 2;
    static constexpr uint64_t origin_task_id = 77;

    static void SetUpTestSuite()
    {
        UploaderBuilder::set_service("test_service");
        UploaderBuilder::set_env("test_env");
        UploaderBuilder::set_version("0.0.1");
        UploaderBuilder::set_url("http://localhost:8126");
        SampleManager::set_max_nframes(64);
        ddup_start();
        OriginTaskLinks::get_instance().enable();
    }

    static void TearDownTestSuite() { OriginTaskLinks::get_instance().disable_and_reset(); }

    // The thread has a span and an origin task, as a worker thread running a traced request would.
    void SetUp() override
    {
        auto& links = SpanLinks::get_instance();
        links.close();
        links.open();
        links.link(links.lifecycle(), thread_id, 0, 0, Span(thread_span_id, 10, "thread-type"));
        OriginTaskLinks::get_instance().link_origin_task(thread_id, origin_task_id, "origin");
    }

    void TearDown() override
    {
        renderer.abort_sample();
        SpanLinks::get_instance().close();
        OriginTaskLinks::get_instance().unlink_origin_task(thread_id);
    }

    void begin_thread() { renderer.render_thread_begin(nullptr, "test-thread", 0, thread_id, thread_id); }

    void begin_task(uint64_t task_id, const LogicalSpan& logical_span)
    {
        renderer.render_task_begin("task", false, task_id, std::nullopt, logical_span);
    }

    // Returns the number label with the given key on the sample being built.
    std::optional<int64_t> num_label(ExportLabelKey key) const
    {
        const auto key_id = internal::to_interned_string(key);
        for (const auto& label : renderer.sample->get_labels()) {
            if (key_id && label.key == *key_id) {
                return label.num;
            }
        }
        return std::nullopt;
    }

    // Returns the string label with the given key on the sample being built.
    std::optional<std::string> str_label(ExportLabelKey key) const
    {
        const auto key_id = internal::to_interned_string(key);
        for (const auto& label : renderer.sample->get_labels()) {
            if (key_id && label.key == *key_id) {
                return std::string(label.str.ptr, label.str.len);
            }
        }
        return std::nullopt;
    }

    std::optional<int64_t> span_id() const { return num_label(ExportLabelKey::span_id); }
    std::optional<int64_t> origin_id() const { return num_label(ExportLabelKey::origin_task_id); }

    StackRenderer renderer;
};

namespace {

LogicalSpan
own_span(uint64_t span_id)
{
    return { true, Span(span_id, 20, "task-type") };
}

const LogicalSpan not_attributed{ false, std::nullopt };
const LogicalSpan attributed_no_span{ true, std::nullopt };

} // namespace

TEST_F(StackRendererSpanLabels, SpanLabelsWaitUntilTheSampleKindIsKnown)
{
    begin_thread();
    EXPECT_EQ(span_id(), std::nullopt);
    EXPECT_EQ(origin_id(), std::nullopt);
}

TEST_F(StackRendererSpanLabels, NotAttributedTasksGetTheThreadSpan)
{
    // The first task reuses the thread's sample. The second starts its own.
    begin_thread();
    begin_task(1, not_attributed);
    EXPECT_EQ(span_id(), static_cast<int64_t>(thread_span_id));
    EXPECT_EQ(num_label(ExportLabelKey::local_root_span_id), 10);
    EXPECT_EQ(str_label(ExportLabelKey::trace_type), "thread-type");
    EXPECT_EQ(origin_id(), static_cast<int64_t>(origin_task_id));
    EXPECT_EQ(str_label(ExportLabelKey::origin_task_name), "origin");
    renderer.render_stack_end();

    begin_task(2, not_attributed);
    EXPECT_EQ(span_id(), static_cast<int64_t>(thread_span_id));
    EXPECT_EQ(origin_id(), static_cast<int64_t>(origin_task_id));
    EXPECT_EQ(num_label(ExportLabelKey::task_id), 2);
}

TEST_F(StackRendererSpanLabels, AttributedTasksGetOnlyTheirOwnSpan)
{
    begin_thread();
    begin_task(1, own_span(task_span_id));
    EXPECT_EQ(span_id(), static_cast<int64_t>(task_span_id));
    EXPECT_EQ(num_label(ExportLabelKey::local_root_span_id), 20);
    EXPECT_EQ(str_label(ExportLabelKey::trace_type), "task-type");
    EXPECT_EQ(origin_id(), static_cast<int64_t>(origin_task_id));
    renderer.render_stack_end();

    begin_task(2, own_span(task_span_id + 1));
    EXPECT_EQ(span_id(), static_cast<int64_t>(task_span_id + 1));
    EXPECT_EQ(origin_id(), static_cast<int64_t>(origin_task_id));
}

TEST_F(StackRendererSpanLabels, AttributedTaskWithNoSpanNeverGetsTheThreadSpan)
{
    begin_thread();
    begin_task(1, attributed_no_span);
    EXPECT_EQ(span_id(), std::nullopt);
    EXPECT_EQ(num_label(ExportLabelKey::local_root_span_id), std::nullopt);
    EXPECT_EQ(str_label(ExportLabelKey::trace_type), std::nullopt);
    // The origin task belongs to the thread, so it stays.
    EXPECT_EQ(origin_id(), static_cast<int64_t>(origin_task_id));
    renderer.render_stack_end();

    begin_task(2, attributed_no_span);
    EXPECT_EQ(span_id(), std::nullopt);
    EXPECT_EQ(origin_id(), static_cast<int64_t>(origin_task_id));
}

} // namespace Datadog
