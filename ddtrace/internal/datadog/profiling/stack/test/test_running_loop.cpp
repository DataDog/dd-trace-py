#include "echion/echion_sampler.h"
#include "echion/greenlets.h"
#include "echion/task_name.h"
#include "echion/threads.h"
#include "sampler.hpp"

#include <gtest/gtest.h>

#include <pthread.h>

#include <cstdint>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <thread>

// A thread has two loop slots. The configured loop is the one set_event_loop (or uvloop.new_event_loop) records. The
// running loop is the plain asyncio loop whose run_forever runs on the thread right now. The sampler reads the
// running loop when one is set, otherwise the configured loop.

namespace {

std::unique_ptr<ThreadInfo>
make_thread(uintptr_t thread_id, unsigned long native_id)
{
#if defined PL_LINUX
    return std::make_unique<ThreadInfo>(thread_id, native_id, "test-thread", CLOCK_THREAD_CPUTIME_ID);
#elif defined PL_DARWIN
    return std::make_unique<ThreadInfo>(thread_id, native_id, "test-thread", mach_thread_self());
#endif
}

uintptr_t
current_pthread_id()
{
#if defined PL_LINUX
    return static_cast<uintptr_t>(pthread_self());
#elif defined PL_DARWIN
    return reinterpret_cast<uintptr_t>(pthread_self());
#endif
}

// A contention hook that fails the test if it runs. The running-loop operations must only call it when
// thread_info_map_lock is already held.
void
unexpected_contention(const std::function<void()>& update)
{
    ADD_FAILURE() << "contention hook called while thread_info_map_lock was free";
    update();
}

// The loop fields of a registered thread. Fails the test, and returns empty fields, if the thread is not registered.
Datadog::AsyncioLoopSlots
registered_slots(Datadog::Sampler& sampler, uintptr_t thread_id)
{
    auto slots = sampler.asyncio_loop_slots(thread_id);
    if (!slots.has_value()) {
        ADD_FAILURE() << "thread " << thread_id << " is not registered";
        return {};
    }
    return *slots;
}

constexpr uintptr_t loop_a = 0x1000;
constexpr uintptr_t loop_b = 0x2000;

} // namespace

TEST(RunningLoop, OwnedEnterReplacesAnyRunningLoop)
{
    auto thread = make_thread(1, 1);

    EXPECT_TRUE(thread->enter_running_loop(loop_a, false));
    EXPECT_EQ(thread->running_asyncio_loop, loop_a);

    // An owned call comes from a hook that already checked that no loop runs on the thread, so A is stale.
    EXPECT_TRUE(thread->enter_running_loop(loop_b, false));
    EXPECT_EQ(thread->running_asyncio_loop, loop_b);
}

TEST(RunningLoop, UnownedEnterOnlyFillsAnEmptySlot)
{
    auto thread = make_thread(1, 1);

    EXPECT_TRUE(thread->enter_running_loop(loop_a, true));
    EXPECT_EQ(thread->running_asyncio_loop, loop_a);

    EXPECT_FALSE(thread->enter_running_loop(loop_b, true));
    EXPECT_EQ(thread->running_asyncio_loop, loop_a);

    // The same loop again changes nothing either.
    EXPECT_FALSE(thread->enter_running_loop(loop_a, true));
    EXPECT_EQ(thread->running_asyncio_loop, loop_a);
}

TEST(RunningLoop, ExitOnlyClearsTheMatchingLoop)
{
    auto thread = make_thread(1, 1);
    ASSERT_TRUE(thread->enter_running_loop(loop_b, false));

    thread->exit_running_loop(loop_a);
    EXPECT_EQ(thread->running_asyncio_loop, loop_b);

    thread->exit_running_loop(loop_b);
    EXPECT_EQ(thread->running_asyncio_loop, 0);

    // Exiting an empty slot is a no-op.
    thread->exit_running_loop(loop_b);
    EXPECT_EQ(thread->running_asyncio_loop, 0);
}

TEST(RunningLoop, EffectiveLoopPrefersTheRunningLoopWithUvloopModeOff)
{
    auto thread = make_thread(1, 1);
    EXPECT_EQ(thread->effective_loop(), 0);
    EXPECT_FALSE(thread->effective_uvloop());

    thread->asyncio_loop = loop_a;
    thread->using_uvloop = true;
    EXPECT_EQ(thread->effective_loop(), loop_a);
    EXPECT_TRUE(thread->effective_uvloop());

    // A plain asyncio loop running on a thread whose configured loop is a uvloop loop is read without uvloop mode.
    ASSERT_TRUE(thread->enter_running_loop(loop_b, false));
    EXPECT_EQ(thread->effective_loop(), loop_b);
    EXPECT_FALSE(thread->effective_uvloop());
    EXPECT_EQ(thread->asyncio_loop, loop_a);
    EXPECT_TRUE(thread->using_uvloop);

    thread->exit_running_loop(loop_b);
    EXPECT_EQ(thread->effective_loop(), loop_a);
    EXPECT_TRUE(thread->effective_uvloop());
}

TEST(RunningLoopSampler, UnregisteredThreadIsIgnored)
{
    Datadog::Sampler& sampler = Datadog::Sampler::get();
    constexpr uintptr_t unregistered_thread_id = 0xdead;

    EXPECT_FALSE(sampler.enter_running_loop(unregistered_thread_id, loop_a, false, unexpected_contention));
    EXPECT_FALSE(sampler.enter_running_loop(unregistered_thread_id, loop_a, true, unexpected_contention));
    sampler.exit_running_loop(unregistered_thread_id, loop_a, unexpected_contention);
    EXPECT_FALSE(sampler.asyncio_loop_slots(unregistered_thread_id).has_value());
}

TEST(RunningLoopSampler, EnterAndExitUpdateARegisteredThread)
{
    Datadog::Sampler& sampler = Datadog::Sampler::get();
    const uintptr_t thread_id = current_pthread_id();
    sampler.register_thread(thread_id, 1, "test-thread");

    EXPECT_TRUE(sampler.enter_running_loop(thread_id, loop_a, false, unexpected_contention));
    EXPECT_FALSE(sampler.enter_running_loop(thread_id, loop_b, true, unexpected_contention));
    auto slots = registered_slots(sampler, thread_id);
    EXPECT_EQ(slots.running_loop, loop_a);
    EXPECT_EQ(slots.effective_loop, loop_a);
    EXPECT_EQ(slots.configured_loop, 0);

    sampler.exit_running_loop(thread_id, loop_a, unexpected_contention);
    EXPECT_EQ(registered_slots(sampler, thread_id).running_loop, 0);

    sampler.unregister_thread(thread_id);
}

TEST(RunningLoopSampler, ReRegisteringALiveThreadKeepsTheRunningLoop)
{
    Datadog::Sampler& sampler = Datadog::Sampler::get();
    const uintptr_t thread_id = current_pthread_id();
    sampler.register_thread(thread_id, 1, "test-thread");
    ASSERT_TRUE(sampler.enter_running_loop(thread_id, loop_a, false, unexpected_contention));

    // Profiler start registers living threads again. The same native id means the same thread, so its loop stays.
    sampler.register_thread(thread_id, 1, "test-thread");
    EXPECT_EQ(registered_slots(sampler, thread_id).running_loop, loop_a);

    // A different native id means the thread id now belongs to another thread, so the old loop is dropped.
    sampler.register_thread(thread_id, 2, "test-thread");
    EXPECT_EQ(registered_slots(sampler, thread_id).running_loop, 0);

    sampler.unregister_thread(thread_id);
}

namespace {

// Runs operation while a helper thread holds mtx, as the sampling thread does during an unwind. operation receives
// the contention hook, which the running-loop operations pass to run_locked (span_links.hpp), the helper SpanLinks
// uses too. The hook lets the helper go, then runs the function run_locked gave it, which blocks until the helper
// unlocks. Promises force this order, so the test never needs two threads to run at the same time (CI runs native
// tests under valgrind, which runs one thread at a time). Returns how many times the hook ran.
template<typename Operation>
int
run_with_lock_held_by_another_thread(std::mutex& mtx, Operation&& operation)
{
    std::promise<void> held;
    std::promise<void> release;
    std::future<void> release_future = release.get_future();
    std::thread holder([&mtx, &held, &release_future] {
        const std::lock_guard<std::mutex> guard(mtx);
        held.set_value();
        release_future.wait();
    });
    held.get_future().wait();

    int hook_calls = 0;
    operation([&](const std::function<void()>& update) {
        ++hook_calls;
        release.set_value();
        update();
    });
    if (hook_calls == 0) {
        // The operation took the lock without the hook, which is the bug under test. Let the helper go so join returns.
        release.set_value();
    }
    holder.join();
    // The function the hook got released mtx before returning.
    if (mtx.try_lock()) {
        mtx.unlock();
    } else {
        ADD_FAILURE() << "thread_info_map_lock is still held after the operation";
    }
    return hook_calls;
}

} // namespace

TEST(RunningLoopSampler, ContendedLockGoesThroughTheContentionHook)
{
    Datadog::Sampler& sampler = Datadog::Sampler::get();
    const uintptr_t thread_id = current_pthread_id();
    sampler.register_thread(thread_id, 1, "test-thread");
    std::mutex& lock = sampler.get_echion().thread_info_map_lock();

    bool entered = false;
    EXPECT_EQ(run_with_lock_held_by_another_thread(lock,
                                                   [&](const Datadog::Sampler::OnLockContention& hook) {
                                                       entered =
                                                         sampler.enter_running_loop(thread_id, loop_a, false, hook);
                                                   }),
              1);
    EXPECT_TRUE(entered);
    EXPECT_EQ(registered_slots(sampler, thread_id).running_loop, loop_a);

    EXPECT_EQ(run_with_lock_held_by_another_thread(lock,
                                                   [&](const Datadog::Sampler::OnLockContention& hook) {
                                                       sampler.exit_running_loop(thread_id, loop_a, hook);
                                                   }),
              1);
    EXPECT_EQ(registered_slots(sampler, thread_id).running_loop, 0);

    sampler.unregister_thread(thread_id);
}

namespace {

constexpr char UNWIND_CONTEXT[] = "UnwindContext";

struct UnwindContext
{
    EchionSampler* echion;
    ThreadInfo* thread;
    bool unwound = false;
};

// Unwinds the thread from inside Python, so the thread state has real frames to read.
PyObject*
unwind_current_thread(PyObject* capsule, PyObject* Py_UNUSED(args))
{
    auto* context = static_cast<UnwindContext*>(PyCapsule_GetPointer(capsule, UNWIND_CONTEXT));
    if (context == nullptr) {
        return nullptr;
    }
    context->unwound = static_cast<bool>(context->thread->unwind(*context->echion, PyThreadState_Get(), 0));
    Py_RETURN_NONE;
}

void
run_unwind_in_python(UnwindContext& context)
{
    PyObject* capsule = PyCapsule_New(&context, UNWIND_CONTEXT, nullptr);
    ASSERT_NE(capsule, nullptr);
    static PyMethodDef unwind_method = { "unwind_current_thread", unwind_current_thread, METH_NOARGS, nullptr };
    PyObject* unwind = PyCFunction_NewEx(&unwind_method, capsule, nullptr);
    ASSERT_NE(unwind, nullptr);
    PyObject* globals = PyDict_New();
    ASSERT_NE(globals, nullptr);
    ASSERT_EQ(PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()), 0);
    ASSERT_EQ(PyDict_SetItemString(globals, "unwind_current_thread", unwind), 0);

    PyObject* result =
      PyRun_String("def outer():\n    unwind_current_thread()\nouter()\n", Py_file_input, globals, globals);
    if (result == nullptr) {
        PyErr_Print();
    }
    EXPECT_NE(result, nullptr);
    Py_XDECREF(result);
    Py_DECREF(globals);
    Py_DECREF(unwind);
    Py_DECREF(capsule);
}

} // namespace

TEST(RunningLoopUnwind, ThreadWithOnlyARunningLoopTakesTheTaskPath)
{
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
    _set_pid(getpid());

    constexpr unsigned long native_id = 4242;
    constexpr GreenletInfo::ID greenlet_id = 7;
    EchionSampler echion;
    auto thread = make_thread(1, native_id);

    // An empty task set, so the task path finds no tasks instead of reading an unset pointer.
    PyObject* scheduled_tasks = PySet_New(nullptr);
    ASSERT_NE(scheduled_tasks, nullptr);
    echion.init_asyncio(scheduled_tasks, Py_None);

    // The thread also runs a greenlet, so the greenlet path would render it if the sampler took that path.
    {
        const std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
        echion.greenlet_thread_map().emplace(native_id, greenlet_id);
        echion.greenlet_info_map().emplace(
          greenlet_id, std::make_unique<GreenletInfo>(greenlet_id, Py_None, TaskName::from_literal("greenlet")));
    }

    UnwindContext context{ &echion, thread.get() };

    // Without any loop the sampler takes the greenlet path.
    run_unwind_in_python(context);
    ASSERT_TRUE(context.unwound);
    EXPECT_EQ(thread->current_greenlets.size(), 1);

    // With only a running loop, and no configured loop, it takes the task path and renders no greenlet.
    ASSERT_TRUE(thread->enter_running_loop(loop_a, false));
    ASSERT_EQ(thread->asyncio_loop, 0);
    run_unwind_in_python(context);
    ASSERT_TRUE(context.unwound);
    EXPECT_TRUE(thread->current_greenlets.empty());
    EXPECT_TRUE(thread->current_tasks.empty());

    Py_DECREF(scheduled_tasks);
}
