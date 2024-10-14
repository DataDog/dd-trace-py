#include "sampler.hpp"

#include "thread_span_links.hpp"

#include "echion/interp.h"
#include "echion/tasks.h"
#include "echion/threads.h"

#include <pthread.h>

using namespace Datadog;

void
Sampler::sampling_thread(const uint64_t seq_num)
{
    using namespace std::chrono;
    auto sample_time_prev = steady_clock::now();

    while (seq_num == thread_seq_num.load()) {
        auto sample_time_now = steady_clock::now();
        auto wall_time_us = duration_cast<microseconds>(sample_time_now - sample_time_prev).count();
        sample_time_prev = sample_time_now;

        // Perform the sample
        for_each_interp([&](PyInterpreterState* interp) -> void {
            for_each_thread(interp, [&](PyThreadState* tstate, ThreadInfo& thread) {
                thread.sample(interp->id, tstate, wall_time_us);
            });
        });

        // Before sleeping, check whether the user has called for this thread to die.
        if (seq_num != thread_seq_num.load()) {
            break;
        }

        // Sleep for the remainder of the interval, get it atomically
        // Generally speaking system "sleep" times will wait _at least_ as long as the specified time, so
        // in actual fact the duration may be more than we indicated.  This tends to be more true on busy
        // systems.
        std::this_thread::sleep_until(sample_time_now + microseconds(sample_interval_us.load()));
    }
}

void
Sampler::set_interval(double new_interval_s)
{
    microsecond_t new_interval_us = static_cast<microsecond_t>(new_interval_s * 1e6);
    sample_interval_us.store(new_interval_us);
}

Sampler::Sampler()
  : renderer_ptr{ std::make_shared<StackRenderer>() }
{
}

Sampler&
Sampler::get()
{
    static Sampler instance;
    return instance;
}

void
_stack_v2_atfork_child()
{
    // The only thing we need to do at fork is to propagate the PID to echion
    // so we don't even reveal this function to the user
    _set_pid(getpid());
    ThreadSpanLinks::postfork_child();
}

__attribute__((constructor)) void
_stack_v2_init()
{
    _stack_v2_atfork_child();
}

void
Sampler::one_time_setup()
{
    _set_cpu(true);
    init_frame_cache(echion_frame_cache_size);

    // It is unlikely, but possible, that the caller has forked since application startup, but before starting echion.
    // Run the atfork handler to ensure that we're tracking the correct process
    _stack_v2_atfork_child();
    pthread_atfork(nullptr, nullptr, _stack_v2_atfork_child);

    // Register our rendering callbacks with echion's Renderer singleton
    Renderer::get().set_renderer(renderer_ptr);
}

void
Sampler::register_thread(uint64_t id, uint64_t native_id, const char* name)
{
    // Registering threads requires coordinating with one of echion's global locks, which we take here.
    const std::lock_guard<std::mutex> thread_info_guard{ thread_info_map_lock };

    static bool has_errored = false;
    auto it = thread_info_map.find(id);
    if (it == thread_info_map.end()) {
        try {
            thread_info_map.emplace(id, std::make_unique<ThreadInfo>(id, native_id, name));
        } catch (const ThreadInfo::Error& e) {
            if (!has_errored) {
                has_errored = true;
                std::cerr << "Failed to register thread: " << std::hex << id << std::dec << " (" << native_id << ") "
                          << name << std::endl;
            }
        }
    } else {
        try {
            it->second = std::make_unique<ThreadInfo>(id, native_id, name);
        } catch (const ThreadInfo::Error& e) {
            if (!has_errored) {
                has_errored = true;
                std::cerr << "Failed to register thread: " << std::hex << id << std::dec << " (" << native_id << ") "
                          << name << std::endl;
            }
        }
    }
}

void
Sampler::unregister_thread(uint64_t id)
{
    // unregistering threads requires coordinating with one of echion's global locks, which we take here.
    const std::lock_guard<std::mutex> thread_info_guard{ thread_info_map_lock };
    thread_info_map.erase(id);
}

void
Sampler::start()
{
    static std::once_flag once;
    std::call_once(once, [this]() { this->one_time_setup(); });

    // Launch the sampling thread.
    // Thread lifetime is bounded by the value of the sequence number.  When it is changed from the value the thread was
    // launched with, the thread will exit.
    std::thread t(&Sampler::sampling_thread, this, ++thread_seq_num);
    t.detach();
}

void
Sampler::stop()
{
    // Modifying the thread sequence number will cause the sampling thread to exit when it completes
    // a sampling loop.  Currently there is no mechanism to force stuck threads, should they get locked.
    ++thread_seq_num;
}

void
Sampler::track_asyncio_loop(uintptr_t thread_id, PyObject* loop)
{
    // Holds echion's global lock
    std::lock_guard<std::mutex> guard(thread_info_map_lock);
    if (thread_info_map.find(thread_id) != thread_info_map.end()) {
        thread_info_map.find(thread_id)->second->asyncio_loop =
          (loop != Py_None) ? reinterpret_cast<uintptr_t>(loop) : 0;
    }
}

void
Sampler::init_asyncio(PyObject* _asyncio_current_tasks,
                      PyObject* _asyncio_scheduled_tasks,
                      PyObject* _asyncio_eager_tasks)
{
    asyncio_current_tasks = _asyncio_current_tasks;
    asyncio_scheduled_tasks = _asyncio_scheduled_tasks;
    asyncio_eager_tasks = _asyncio_eager_tasks;
    if (asyncio_eager_tasks == Py_None) {
        asyncio_eager_tasks = NULL;
    }
}

void
Sampler::link_tasks(PyObject* parent, PyObject* child)
{
    std::lock_guard<std::mutex> guard(task_link_map_lock);
    task_link_map[child] = parent;
}
