#include "../../dd_wrapper/test/test_utils.hpp"
#include "sampler.hpp"
#include <gtest/gtest.h>

#include <Python.h>

#include <chrono>
#include <cstdlib>
#include <thread>

TEST(NativeShutdownTest, ExitWithoutStoppingSampler)
{
    // Exiting this test process directly lets CTest validate the process status
    // without introducing a sanitizer-sensitive death-test subprocess.
    Py_Initialize();

    // Register this before profiler state and the sampler so it runs after their
    // native exit handlers and widens the use-after-free window.
    std::atexit([]() { std::this_thread::sleep_for(std::chrono::milliseconds(500)); });

    configure("shutdown-test", "test", "1.0", "http://127.0.0.1:8126", "cpython", "test", "test", 64);

    auto& sampler = Datadog::Sampler::get();
    sampler.set_interval(0.001);
    if (!sampler.start()) {
        std::exit(2);
    }

    for (size_t i = 0; i < 1000 && !sampler.is_running(); i++) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!sampler.is_running()) {
        std::exit(3);
    }

    // Embedders such as uWSGI can begin native process teardown without first
    // invoking the profiler's Python shutdown handler.
    std::exit(0);
}
