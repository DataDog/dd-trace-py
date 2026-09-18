#include "echion/echion_sampler.h"
#include "sampler.hpp"

#include <gtest/gtest.h>

#include <chrono>
#include <future>
#include <mutex>
#include <thread>

extern "C" PyObject*
PyInit__stack();

TEST(AsyncioLoopRegistration, ContendedLookupDoesNotHoldGIL)
{
    ASSERT_EQ(PyImport_AppendInittab("_stack", PyInit__stack), 0);
    Py_Initialize();
    PyObject* module = PyImport_ImportModule("_stack");
    ASSERT_NE(module, nullptr);

    std::promise<void> locked;
    std::promise<void> python_progress;
    auto progress = python_progress.get_future();
    bool progressed_while_locked = false;
    std::thread sampler([&]() {
        std::lock_guard<std::mutex> guard(Datadog::Sampler::get().get_echion().thread_info_map_lock());
        locked.set_value();
        // Bound the pre-fix deadlock: the application must release the GIL while waiting for this mutex.
        progressed_while_locked = progress.wait_for(std::chrono::seconds(1)) == std::future_status::ready;
    });
    locked.get_future().wait();
    std::thread application([&]() {
        // The main test owns interpreter lifetime and never finalizes it while this thread is alive.
        auto gil = PyGILState_Ensure();
        python_progress.set_value();
        PyGILState_Release(gil);
    });

    const auto begin = std::chrono::steady_clock::now();
    PyObject* result = PyObject_CallMethod(module, "is_asyncio_loop_registered", "K", 123ULL);
    const auto elapsed = std::chrono::steady_clock::now() - begin;
    // Also let the observer exit on the pre-fix path before joining it.
    PyThreadState* state = PyEval_SaveThread();
    sampler.join();
    application.join();
    PyEval_RestoreThread(state);

    RecordProperty("lookup_us", std::chrono::duration_cast<std::chrono::microseconds>(elapsed).count());
    EXPECT_EQ(result, Py_False);
    EXPECT_TRUE(progressed_while_locked);
    Py_XDECREF(result);
    Py_DECREF(module);
}
