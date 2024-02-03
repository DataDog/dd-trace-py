// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#include "interface.hpp"
#include "global_cache.hpp"
#include "libdatadog_helpers.hpp"
#include "profile.hpp"
#include "profile_shared.hpp"
#include "uploader.hpp"

#include <cstdlib>
#include <iostream>
#include <unistd.h>

// State
bool is_ddup_initialized = false;
bool g_prof_flag = true;

// Store a pointer to the current profile.  This can be assumed set in inner
// functions, but any function that may be called by a new thread should check
thread_local Datadog::Profile* g_profile;

// When a fork is detected, we need to reinitialize this state.
// This handler will be called in the single thread of the child process after the fork
void
ddup_fork_handler()
{
    Datadog::ProfileSharedState::mark_dirty();
    Datadog::ProfileGlobalStorage::clear();
}

// Configuration
void
ddup_config_env(const char* env)
{
    if (!env || !*env)
        return;

    Datadog::ProfileGlobalStorage::uploader_builder.set_env(env);
}
void
ddup_config_service(const char* service)
{
    if (!service || !*service) {
        return;
    }

    Datadog::ProfileGlobalStorage::uploader_builder.set_service(service);
}
void
ddup_config_version(const char* version)
{
    if (!version || !*version)
        return;
    Datadog::ProfileGlobalStorage::uploader_builder.set_version(version);
}
void
ddup_config_runtime(const char* runtime)
{
    if (!runtime || !*runtime)
        return;
    Datadog::ProfileGlobalStorage::uploader_builder.set_runtime(runtime);
}
void
ddup_config_runtime_version(const char* runtime_version)
{
    if (!runtime_version || !*runtime_version)
        return;
    Datadog::ProfileGlobalStorage::uploader_builder.set_runtime_version(runtime_version);
}
void
ddup_config_profiler_version(const char* profiler_version)
{
    if (!profiler_version || !*profiler_version)
        return;
    Datadog::ProfileGlobalStorage::uploader_builder.set_profiler_version(profiler_version);
}
void
ddup_config_url(const char* url)
{
    if (!url || !*url)
        return;
    Datadog::ProfileGlobalStorage::uploader_builder.set_url(url);
}
void
ddup_config_user_tag(const char* key, const char* val)
{
    if (!key || !*key || !val || !*val)
        return;
    Datadog::ProfileGlobalStorage::uploader_builder.set_tag(key, val);
}
void
ddup_config_sample_type(unsigned int type)
{
    Datadog::ProfileGlobalStorage::profile_builder.add_type(type);
}
void
ddup_config_max_nframes(int max_nframes)
{
    if (max_nframes > 0)
        Datadog::ProfileGlobalStorage::profile_builder.set_max_nframes(max_nframes);
}

#if DDUP_BACKTRACE_ENABLE
#include <csignal>
#include <cxxabi.h>
#include <execinfo.h>

inline static void
print_backtrace()
{
    constexpr int max_frames = 128;
    void* frames[max_frames];
    int num_frames = backtrace(frames, max_frames);
    char** symbols = backtrace_symbols(frames, num_frames);

    std::cerr << "Backtrace:\n";
    for (int i = 0; i < num_frames; ++i) {
        std::string symbol(symbols[i]);
        std::size_t start = symbol.find_first_of('_');
        std::size_t end = symbol.find_first_of(' ', start);

        if (start != std::string::npos && end != std::string::npos) {
            std::string mangled_name = symbol.substr(start, end - start);
            int status = -1;
            char* demangled_name = abi::__cxa_demangle(mangled_name.c_str(), nullptr, nullptr, &status);
            if (status == 0) {
                symbol.replace(start, end - start, demangled_name);
                free(demangled_name);
            }
        }
        std::cerr << symbol << std::endl;
    }
    std::cerr << std::endl;

    free(symbols);
}

static void
sigsegv_handler(int sig, siginfo_t* si, void* uc)
{
    (void)uc;
    print_backtrace();
    exit(-1);
}
#endif

bool
ddup_is_initialized()
{
    return is_ddup_initialized;
}

void
ddup_init()
{
    static int initialized_count = 0;
    static bool initialized = []() {
#if DDUP_BACKTRACE_ENABLE
        // Install segfault handler
        struct sigaction sigaction_handlers = {};
        sigaction_handlers.sa_sigaction = sigsegv_handler;
        sigaction_handlers.sa_flags = SA_SIGINFO;
        sigaction(SIGSEGV, &(sigaction_handlers), NULL);
#endif

        // install the ddup_fork_handler for pthread_atfork
        // Right now, only do things in the child _after_ fork
        pthread_atfork(nullptr, nullptr, ddup_fork_handler);

        // Set the global initialization flag
        is_ddup_initialized = true;
        return true;
    }();

    if (initialized)
      ++initialized_count;
    if (initialized_count > 1) {
        std::cerr << "ddup_init() called " << initialized_count << " times" << std::endl;
    }
}

void
ddup_start_sample()
{
    g_profile = &Datadog::Profile::start_sample();
}

void
ddup_push_walltime(int64_t walltime, int64_t count)
{
    g_profile->push_walltime(walltime, count);
}

void
ddup_push_cputime(int64_t cputime, int64_t count)
{
    g_profile->push_cputime(cputime, count);
}

void
ddup_push_acquire(int64_t acquire_time, int64_t count)
{
    g_profile->push_acquire(acquire_time, count);
}

void
ddup_push_release(int64_t release_time, int64_t count)
{
    g_profile->push_release(release_time, count);
}

void
ddup_push_alloc(uint64_t size, uint64_t count)
{
    g_profile->push_alloc(size, count);
}

void
ddup_push_heap(uint64_t size)
{
    g_profile->push_heap(size);
}

void
ddup_push_lock_name(const char* lock_name)
{
    if (!lock_name)
        return;
    g_profile->push_lock_name(lock_name);
}

void
ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char* thread_name)
{
    g_profile->push_threadinfo(thread_id, thread_native_id, thread_name);
}

void
ddup_push_task_id(int64_t task_id)
{
    if (task_id > 0)
        g_profile->push_task_id(task_id);
}

void
ddup_push_task_name(const char* task_name)
{
    if (task_name && *task_name)
        g_profile->push_task_name(task_name);
}

void
ddup_push_span_id(int64_t span_id)
{
    g_profile->push_span_id(span_id);
}

void
ddup_push_local_root_span_id(int64_t local_root_span_id)
{
    g_profile->push_local_root_span_id(local_root_span_id);
}

void
ddup_push_trace_type(const char* trace_type)
{
    if (!trace_type || !*trace_type)
        return;
    g_profile->push_trace_type(trace_type);
}

void
ddup_push_trace_resource_container(const char* trace_resource_container)
{
    if (!trace_resource_container || !*trace_resource_container)
        return;
    g_profile->push_trace_resource_container(trace_resource_container);
}

void
ddup_push_exceptioninfo(const char* exception_type, int64_t count)
{
    if (!exception_type || !count)
        return;
    g_profile->push_exceptioninfo(exception_type, count);
}

void
ddup_push_class_name(const char* class_name)
{
    if (!class_name)
        return;
    g_profile->push_class_name(class_name);
}

void
ddup_push_frame(const char* name, const char* fname, uint64_t address, int64_t line)
{
    g_profile->push_frame(name, fname, address, line);
}

void
ddup_flush_sample()
{
    g_profile->flush_sample();
}

void
ddup_set_runtime_id(const char* id, size_t sz)
{
    if (id && *id)
        Datadog::ProfileGlobalStorage::uploader_builder.set_runtime_id(std::string_view(id, sz));
}

bool
ddup_upload()
{
    if (!is_ddup_initialized) {
        std::cerr << "ddup_upload() called before ddup_init()" << std::endl;
        return false;
    }

    ddog_prof_Profile upload_profile = Datadog::Profile::get_ddog_profile();
    Datadog::ProfileSharedState::mark_dirty();

    // We create a new uploader just for this operation
    auto uploader = Datadog::ProfileGlobalStorage::uploader_builder.build_ptr();
    if (!uploader) {
        std::cerr << "Failed to create uploader" << std::endl;
        return false;
    }
    bool success = uploader->upload(upload_profile);
    return success;
}
