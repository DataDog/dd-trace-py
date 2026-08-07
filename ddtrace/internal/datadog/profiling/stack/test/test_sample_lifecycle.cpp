#include "stack_renderer.hpp"

#include "dd_wrapper/include/ddup_interface.hpp"
#include "dd_wrapper/include/sample_manager.hpp"
#include "dd_wrapper/include/static_sample_pool.hpp"

#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

using namespace Datadog;

static_assert(!std::is_copy_constructible_v<StackRenderer>);
static_assert(!std::is_move_constructible_v<StackRenderer>);
static_assert(!std::is_copy_constructible_v<StackRenderer::RenderCycle>);
static_assert(std::is_move_constructible_v<StackRenderer::RenderCycle>);

namespace {

void
fill_sample_pool()
{
    std::vector<Sample*> samples;
    samples.reserve(StaticSamplePool::CAPACITY);
    for (size_t i = 0; i < StaticSamplePool::CAPACITY; ++i) {
        samples.push_back(SampleManager::start_sample());
    }
    for (auto* sample : samples) {
        SampleManager::drop_sample(sample);
    }
}

size_t
count_available_samples()
{
    std::vector<Sample*> samples;
    while (auto sample = StaticSamplePool::take_sample()) {
        samples.push_back(*sample);
    }

    for (auto* sample : samples) {
        auto leftover = StaticSamplePool::return_sample(sample);
        EXPECT_FALSE(leftover.has_value());
        if (leftover.has_value()) {
            delete *leftover; // NOLINT(cppcoreguidelines-owning-memory)
        }
    }
    return samples.size();
}

class StackRendererSampleLifecycle : public ::testing::Test
{
  protected:
    static void SetUpTestSuite()
    {
        ddup_config_service("test_service");
        ddup_config_env("test_env");
        ddup_config_version("0.0.1");
        ddup_config_url("http://localhost:8126");
        ddup_config_max_nframes(64);
        ddup_start();
    }

    void SetUp() override
    {
        fill_sample_pool();
        ASSERT_EQ(count_available_samples(), StaticSamplePool::CAPACITY);
    }
};

void
abandon_render_cycle(StackRenderer& renderer, std::uint64_t id)
{
    [[maybe_unused]] auto cycle = renderer.render_thread_begin(nullptr, "test-thread", 0, id, id);
}

void
throw_during_render_cycle(StackRenderer& renderer)
{
    [[maybe_unused]] auto cycle = renderer.render_thread_begin(nullptr, "test-thread", 0, 1, 1);
    throw std::runtime_error("injected render failure");
}

} // namespace

TEST_F(StackRendererSampleLifecycle, ScopeExitRecoversIncompleteSample)
{
    StackRenderer renderer;

    // Run beyond the pool capacity so a missing guard cleanup would drain every reusable Sample.
    for (size_t i = 0; i < StaticSamplePool::CAPACITY * 4; ++i) {
        abandon_render_cycle(renderer, i + 1);
    }

    EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY)
      << "render-cycle scope exit leaked an incomplete Sample";
}

TEST_F(StackRendererSampleLifecycle, ExceptionUnwindRecoversIncompleteSample)
{
    StackRenderer renderer;

    EXPECT_THROW(throw_during_render_cycle(renderer), std::runtime_error);
    EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY)
      << "exception unwinding leaked an incomplete Sample";
}

TEST_F(StackRendererSampleLifecycle, OlderGuardCannotAbortNewerCycle)
{
    StackRenderer renderer;
    std::optional<StackRenderer::RenderCycle> older_cycle;
    older_cycle.emplace(renderer.render_thread_begin(nullptr, "first-thread", 0, 1, 1));

    {
        [[maybe_unused]] auto newer_cycle = renderer.render_thread_begin(nullptr, "second-thread", 0, 2, 2);
        older_cycle.reset();

        EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY - 1)
          << "an invalidated guard aborted the Sample owned by a newer render cycle";
    }

    EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY)
      << "the newer render-cycle guard did not return its Sample";
}

TEST_F(StackRendererSampleLifecycle, MovingCycleGuardTransfersCleanup)
{
    StackRenderer renderer;

    {
        auto first_guard = renderer.render_thread_begin(nullptr, "test-thread", 0, 1, 1);
        [[maybe_unused]] auto moved_guard = std::move(first_guard);
    }

    EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY)
      << "moving the render-cycle guard lost or duplicated Sample ownership";
}

TEST_F(StackRendererSampleLifecycle, TaskCannotAcquireSampleWithoutCycleGuard)
{
    StackRenderer renderer;

    testing::internal::CaptureStderr();
    renderer.render_task_begin("orphan-task", true, 1);
    const auto captured_error = testing::internal::GetCapturedStderr();

    EXPECT_NE(captured_error.find("without an active render cycle"), std::string::npos);
    EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY)
      << "render_task_begin acquired a Sample without an active render cycle";
}
