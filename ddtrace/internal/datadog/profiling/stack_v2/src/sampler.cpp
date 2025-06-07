#include "sampler.hpp"

#include "thread_span_links.hpp"

#include "echion/config.h"
#include "echion/interp.h"
#include "echion/tasks.h"
#include "echion/threads.h"

#include <pthread.h>

using namespace Datadog;

// Helper class for spawning a std::thread with control over its default stack size
#ifdef __linux__
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

struct ThreadArgs
{
    Sampler* sampler;
    uint64_t seq_num;
};

void*
call_sampling_thread(void* args)
{
    ThreadArgs thread_args = *static_cast<ThreadArgs*>(args);
    delete static_cast<ThreadArgs*>(args); // no longer needed, dynamic alloc
    Sampler* sampler = thread_args.sampler;
    sampler->sampling_thread(thread_args.seq_num);
    return nullptr;
}

pthread_t
create_thread_with_stack(size_t stack_size, Sampler* sampler, uint64_t seq_num)
{
    pthread_attr_t attr;
    if (pthread_attr_init(&attr) != 0) {
        return 0;
    }
    if (stack_size > 0) {
        pthread_attr_setstacksize(&attr, stack_size);
    }

    pthread_t thread_id;
    ThreadArgs* thread_args = new ThreadArgs{ sampler, seq_num };
    int ret = pthread_create(&thread_id, &attr, call_sampling_thread, thread_args);

    pthread_attr_destroy(&attr);

    if (ret != 0) {
        delete thread_args; // usually deleted in the thread, but need to clean it up here
        return 0;
    }
    return thread_id;
}
#elif defined(__MACH__)
#include <mach/mach.h>
#endif

void
Sampler::adapt_sampling_interval()
{
#if defined(__linux__)
    struct timespec ts;

    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
    auto new_process_count = static_cast<uint64_t>(ts.tv_sec * 1000000ULL + ts.tv_nsec / 1000);

    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    auto new_sampler_thread_count = static_cast<uint64_t>(ts.tv_sec * 1000000ULL + ts.tv_nsec / 1000);
#elif defined(__MACH__)
    // Get the process CPU time
    task_thread_times_info_data_t task_info_data;
    mach_msg_type_number_t task_info_count = TASK_THREAD_TIMES_INFO_COUNT;

    if (task_info(
          mach_task_self(), TASK_THREAD_TIMES_INFO, reinterpret_cast<task_info_t>(&task_info_data), &task_info_count) !=
        KERN_SUCCESS) {
        return;
    }

    auto new_process_count =
      static_cast<uint64_t>(task_info_data.user_time.seconds * 1e6 + task_info_data.user_time.microseconds +
                            task_info_data.system_time.seconds * 1e6 + task_info_data.system_time.microseconds);

    // Get the sampling thread CPU time
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    thread_basic_info_data_t info;

    thread_port_t thread = mach_thread_self(); // perf: call once
    int kr = thread_info(thread, THREAD_BASIC_INFO, reinterpret_cast<thread_info_t>(&info), &count);
    mach_port_deallocate(mach_task_self(), thread);
    if (kr != KERN_SUCCESS) {
        return;
    }

    auto new_sampler_thread_count =
      static_cast<uint64_t>(info.user_time.seconds * 1e6 + info.user_time.microseconds +
                            info.system_time.seconds * 1e6 + info.system_time.microseconds);
#endif
    auto sampler_thread_delta = static_cast<double>(new_sampler_thread_count - sampler_thread_count);
    auto process_delta = static_cast<double>(new_process_count - process_count - sampler_thread_delta);
    if (process_delta <= 0) {
        process_delta = 1; // Avoid division by zero or negative values
    }

    auto current_interval = static_cast<double>(sample_interval_us.load());

    // We assume that every sampling operation contributes a fixed amount of
    // overhead, while the application consumes an average amount of CPU over
    // time. With:
    //    s - sampler time
    //    p - process time
    //    o - overhead threshold
    //    I - interval
    //    I'- interval after adjustment
    // we use the following formula to adapt the sampling interval
    //    I' = I * [(s / p) / o]
    // As the value could be small when the process is idle, we use a lower
    // bound of the sampling interval to avoid CPU spikes from the sampler.
    auto new_interval =
      static_cast<microsecond_t>(current_interval * ((sampler_thread_delta / process_delta) / g_target_overhead));

    // Cap the new interval to the min/max sampling period
    if (new_interval < g_min_sampling_period_us) {
        new_interval = g_min_sampling_period_us;
    } else if (new_interval > g_max_sampling_period_us) {
        new_interval = g_max_sampling_period_us;
    }

    sample_interval_us.store(new_interval);

    // Update the counters for the next iteration
    process_count = new_process_count;
    sampler_thread_count = new_sampler_thread_count;
}

void
Sampler::sampling_thread(const uint64_t seq_num)
{
    using namespace std::chrono;
    auto sample_time_prev = steady_clock::now();
    auto interval_adjust_time_prev = sample_time_prev;

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

        if (do_adaptive_sampling) {
            // Adjust the sampling interval at most every second
            if (sample_time_now - interval_adjust_time_prev > microseconds(g_adaptive_sampling_interval_us)) {
                adapt_sampling_interval();
                interval_adjust_time_prev = sample_time_now;
            }
        }

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

    // `thread_info_map_lock` and `task_link_map_lock` are global locks held in echion
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&thread_info_map_lock) std::mutex;
    new (&task_link_map_lock) std::mutex;
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
    // By default echion will ignore thread that are not running. We still want
    // to track them and set cpu time 0, so we disable this behavior.
    _set_ignore_non_running_threads(false);
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

bool
Sampler::start()
{
    static std::once_flag once;
    std::call_once(once, [this]() { this->one_time_setup(); });

    // Launch the sampling thread.
    // Thread lifetime is bounded by the value of the sequence number.  When it is changed from the value the thread was
    // launched with, the thread will exit.
#ifdef __linux__
    // We might as well get the default stack size and use that
    rlimit stack_sz = {};
    getrlimit(RLIMIT_STACK, &stack_sz);
    if (create_thread_with_stack(stack_sz.rlim_cur, this, ++thread_seq_num) == 0) {
        return false;
    }
#else
    try {
        std::thread t(&Sampler::sampling_thread, this, ++thread_seq_num);
        t.detach();
    } catch (const std::exception& e) {
        return false;
    }
#endif
    return true;
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

void
Sampler::set_max_nframes(int new_max_frames)
{
    // max_frames is a global variable that is used to limit the number of frames
    // that are unwound for a single thread/task.
    if (new_max_frames > 0) {
        max_frames = new_max_frames;
    }
    if (max_frames > g_backend_max_nframes) {
        max_frames = g_backend_max_nframes;
    }
}
