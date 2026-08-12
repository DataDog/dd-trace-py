#include "../../dd_wrapper/test/test_utils.hpp"
#include "dd_wrapper/include/profiler_state.hpp"
#include "sampler.hpp"
#include <gtest/gtest.h>

#include <Python.h>

#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <mutex>
#include <thread>

namespace {

std::mutex thread_start_mutex;
std::condition_variable thread_start_cv;
bool thread_started = false;
bool release_thread = false;

void
block_sampling_thread()
{
    std::unique_lock<std::mutex> lock(thread_start_mutex);
    thread_started = true;
    thread_start_cv.notify_all();
    thread_start_cv.wait(lock, []() { return release_thread; });
}

void
verify_process_lifetime_state()
{
    if (!Datadog::ProfilerState::get().get_profiles_dictionary()) {
        std::_Exit(4);
    }

    {
        std::lock_guard<std::mutex> lock(thread_start_mutex);
        release_thread = true;
    }
    thread_start_cv.notify_all();

    // Give the released sampler time to access process-wide state after all
    // later-registered native exit handlers have returned.
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
}

enum class BlockPoint
{
    BeforeRunning,
    AfterRunning,
};

[[noreturn]] void
exit_with_blocked_sampler(BlockPoint block_point)
{
    Py_Initialize();

    // This runs after profiler and sampler exit handlers because native exit
    // callbacks run in reverse registration order.
    std::atexit(verify_process_lifetime_state);

    configure("shutdown-test", "test", "1.0", "http://127.0.0.1:8126", "cpython", "test", "test", 64);

    auto& sampler = Datadog::Sampler::get();
    if (block_point == BlockPoint::BeforeRunning) {
        sampler.set_thread_start_hook_for_testing(block_sampling_thread);
    } else {
        sampler.set_thread_running_hook_for_testing(block_sampling_thread);
    }
    sampler.set_interval(0.001);
    if (!sampler.start()) {
        std::exit(2);
    }

    {
        std::unique_lock<std::mutex> lock(thread_start_mutex);
        if (!thread_start_cv.wait_for(lock, std::chrono::seconds(1), []() { return thread_started; })) {
            std::exit(3);
        }
    }

    std::exit(0);
}

}

TEST(NativeShutdownTest, ExitWhileSamplerThreadIsStarting)
{
    exit_with_blocked_sampler(BlockPoint::BeforeRunning);
}

TEST(NativeShutdownTest, RetainProfilerStateAfterSamplerStopTimeout)
{
    exit_with_blocked_sampler(BlockPoint::AfterRunning);
}
