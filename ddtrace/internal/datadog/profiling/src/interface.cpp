// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#include "interface.hpp"
#include "exporter.hpp"

#include <csignal>
#include <cstdlib>
#include <cxxabi.h>
#include <execinfo.h>
#include <thread>
#include <unistd.h>

// State
bool is_initialized = false;
Datadog::Uploader* g_uploader;
Datadog::Profile* g_profile;
Datadog::Profile* g_profile_real[2];
bool g_prof_flag = true;

// State used only for one-time configuration
Datadog::UploaderBuilder uploader_builder;
Datadog::ProfileBuilder profile_builder;

// Configuration
void
ddup_config_env(const char* env)
{
    if (!env || !*env)
        return;
    uploader_builder.set_env(env);
}
void
ddup_config_service(const char* service)
{
    if (!service || !*service) {
        return;
    }

    uploader_builder.set_service(service);
}
void
ddup_config_version(const char* version)
{
    if (!version || !*version)
        return;
    uploader_builder.set_version(version);
}
void
ddup_config_runtime(const char* runtime)
{
    uploader_builder.set_runtime(runtime);
}
void
ddup_config_runtime_version(const char* runtime_version)
{
    uploader_builder.set_runtime_version(runtime_version);
}
void
ddup_config_profiler_version(const char* profiler_version)
{
    uploader_builder.set_profiler_version(profiler_version);
}
void
ddup_config_url(const char* url)
{
    if (url && *url)
        uploader_builder.set_url(url);
}
void
ddup_config_user_tag(const char* key, const char* val)
{
    uploader_builder.set_tag(key, val);
}
void
ddup_config_sample_type(unsigned int type)
{
    profile_builder.add_type(type);
}
void
ddup_config_max_nframes(int max_nframes)
{
    if (max_nframes > 0)
        profile_builder.set_max_nframes(max_nframes);
}

#if DDUP_BACKTRACE_ENABLE
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
char *ddup_errmsg = nullptr;
char *get_err() { return ddup_errmsg; }

bool
ddup_init()
{
    if (!is_initialized) {
#if DDUP_BACKTRACE_ENABLE
        // Install segfault handler
        struct sigaction sigaction_handlers = {};
        sigaction_handlers.sa_sigaction = sigsegv_handler;
        sigaction_handlers.sa_flags = SA_SIGINFO;
        sigaction(SIGSEGV, &(sigaction_handlers), NULL);
#endif

        g_profile_real[0] = profile_builder.build_ptr();
        g_profile_real[1] = profile_builder.build_ptr();
        g_profile = g_profile_real[g_prof_flag];
        g_uploader = uploader_builder.build_ptr();
        if (!g_uploader) {
          ddup_errmsg = const_cast<char *>(uploader_builder.errmsg.c_str());
          return false;
        }
        is_initialized = true;
    }

    return true;
}

bool set_err(bool value, std::string &str) {
  ddup_errmsg = value ? const_cast<char *>(str.c_str()) : nullptr;
  return value;
}

bool
ddup_start_sample(unsigned int nframes)
{
    return set_err(g_profile->start_sample(nframes), g_profile->errmsg);
}

bool
ddup_push_walltime(int64_t walltime, int64_t count)
{
    return set_err(g_profile->push_walltime(walltime, count), g_profile->errmsg);
}

bool
ddup_push_cputime(int64_t cputime, int64_t count)
{
    return set_err(g_profile->push_cputime(cputime, count), g_profile->errmsg);
}

bool
ddup_push_acquire(int64_t acquire_time, int64_t count)
{
    return set_err(g_profile->push_acquire(acquire_time, count), g_profile->errmsg);
}

bool
ddup_push_release(int64_t release_time, int64_t count)
{
    return set_err(g_profile->push_release(release_time, count), g_profile->errmsg);
}

bool
ddup_push_alloc(uint64_t size, uint64_t count)
{
    return set_err(g_profile->push_alloc(size, count), g_profile->errmsg);
}

bool
ddup_push_heap(uint64_t size)
{
    return set_err(g_profile->push_heap(size), g_profile->errmsg);
}

bool
ddup_push_lock_name(const char* lock_name)
{
    return set_err(g_profile->push_lock_name(lock_name), g_profile->errmsg);
}

bool
ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char* thread_name)
{
    return set_err(g_profile->push_threadinfo(thread_id, thread_native_id, thread_name), g_profile->errmsg);
}

bool
ddup_push_task_id(int64_t task_id)
{
    return set_err(g_profile->push_task_id(task_id), g_profile->errmsg);
}

bool
ddup_push_task_name(const char* task_name)
{
    return set_err(g_profile->push_task_name(task_name), g_profile->errmsg);
}

bool
ddup_push_span_id(int64_t span_id)
{
    return set_err(g_profile->push_span_id(span_id), g_profile->errmsg);
}

bool
ddup_push_local_root_span_id(int64_t local_root_span_id)
{
    return set_err(g_profile->push_local_root_span_id(local_root_span_id), g_profile->errmsg);
}

bool
ddup_push_trace_type(const char* trace_type)
{
    return set_err(g_profile->push_trace_type(trace_type), g_profile->errmsg);
}

bool
ddup_push_trace_resource_container(const char* trace_resource_container)
{
    return set_err(g_profile->push_trace_resource_container(trace_resource_container), g_profile->errmsg);
}

bool
ddup_push_exceptioninfo(const char* exception_type, int64_t count)
{
    return set_err(g_profile->push_exceptioninfo(exception_type, count), g_profile->errmsg);
}

bool
ddup_push_class_name(const char* class_name)
{
    return set_err(g_profile->push_class_name(class_name), g_profile->errmsg);
}

void
ddup_push_frame(const char* name, const char* fname, uint64_t address, int64_t line)
{
    g_profile->push_frame(name, fname, address, line);
}

bool
ddup_flush_sample()
{
    return set_err(g_profile->flush_sample(), g_profile->errmsg);
}

bool
ddup_set_runtime_id(const char* id, size_t sz)
{
    return set_err(g_uploader->set_runtime_id(std::string_view(id, sz)), g_profile->errmsg);
}

bool
ddup_upload()
{

    if (!is_initialized) {
        // Rationalize return for interface
        static char err_uninitialized[] = "Error: not initialized";
        ddup_errmsg = err_uninitialized;
        return false;
    }

    // NB., this function strongly assumes single-threaded access in the
    // caller; otherwise the collection will be serialized as it is being
    // written to, which is undefined behavior for libdatadog.
    auto upload_profile = g_profile;
    g_prof_flag ^= true;
    g_profile = g_profile_real[g_prof_flag];
    g_profile->reset();
    return g_uploader->upload(upload_profile);
}
