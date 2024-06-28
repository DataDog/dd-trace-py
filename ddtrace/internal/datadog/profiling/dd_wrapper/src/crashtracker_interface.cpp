#include "crashtracker_interface.hpp"
#include "crashtracker.hpp"

#include <pthread.h>
#include <signal.h>

// A global instance of the crashtracker is created here.
Datadog::Crashtracker crashtracker;
bool crashtracker_initialized = false;

void
crashtracker_postfork_child()
{
    crashtracker.atfork_child();
}

void
crashtracker_set_url(std::string_view url) // cppcheck-suppress unusedFunction
{
    crashtracker.set_url(url);
}

void
crashtracker_set_service(std::string_view service) // cppcheck-suppress unusedFunction
{
    crashtracker.set_service(service);
}

void
crashtracker_set_env(std::string_view env) // cppcheck-suppress unusedFunction
{
    crashtracker.set_env(env);
}

void
crashtracker_set_version(std::string_view version) // cppcheck-suppress unusedFunction
{
    crashtracker.set_version(version);
}

void
crashtracker_set_runtime(std::string_view runtime) // cppcheck-suppress unusedFunction
{
    crashtracker.set_runtime(runtime);
}

void
crashtracker_set_runtime_version(std::string_view runtime_version) // cppcheck-suppress unusedFunction
{
    crashtracker.set_runtime_version(runtime_version);
}

void
crashtracker_set_runtime_id(std::string_view runtime_id) // cppcheck-suppress unusedFunction
{
    crashtracker.set_runtime_id(runtime_id);
}

void
crashtracker_set_library_version(std::string_view profiler_version) // cppcheck-suppress unusedFunction
{
    crashtracker.set_library_version(profiler_version);
}

void
crashtracker_set_stdout_filename(std::string_view filename) // cppcheck-suppress unusedFunction
{
    crashtracker.set_stdout_filename(filename);
}

void
crashtracker_set_stderr_filename(std::string_view filename) // cppcheck-suppress unusedFunction
{
    crashtracker.set_stderr_filename(filename);
}

void
crashtracker_set_alt_stack(bool alt_stack) // cppcheck-suppress unusedFunction
{
    crashtracker.set_create_alt_stack(alt_stack);
}

void
crashtracker_set_resolve_frames_disable() // cppcheck-suppress unusedFunction
{
    crashtracker.set_resolve_frames(DDOG_PROF_STACKTRACE_COLLECTION_DISABLED);
}

void
crashtracker_set_resolve_frames_fast() // cppcheck-suppress unusedFunction
{
    crashtracker.set_resolve_frames(DDOG_PROF_STACKTRACE_COLLECTION_WITHOUT_SYMBOLS);
}

void
crashtracker_set_resolve_frames_full() // cppcheck-suppress unusedFunction
{
    crashtracker.set_resolve_frames(DDOG_PROF_STACKTRACE_COLLECTION_ENABLED_WITH_INPROCESS_SYMBOLS);
}

void
crashtracker_set_resolve_frames_safe() // cppcheck-suppress unusedFunction
{
    crashtracker.set_resolve_frames(DDOG_PROF_STACKTRACE_COLLECTION_ENABLED_WITH_SYMBOLS_IN_RECEIVER);
}

bool
crashtracker_set_receiver_binary_path(std::string_view path) // cppcheck-suppress unusedFunction
{
    return crashtracker.set_receiver_binary_path(path);
}

void
crashtracker_start() // cppcheck-suppress unusedFunction
{
    // This is a one-time start pattern to ensure that the crashtracker is only started once.
    const static bool initialized = []() {
        crashtracker.start();

        // Also install the post-fork handler for the child process
        pthread_atfork(nullptr, nullptr, crashtracker_postfork_child);
        return true;
    }();
    (void)initialized;
}

void
crashtracker_profiling_state_sampling_start() // cppcheck-suppress unusedFunction
{
    // These functions may be called by components which have no knowledge of
    // whether the crashtracker was started.  We let them call, but ignore them
    // if the crashtracker was not started.
    // Generally, the goal is to start crashtracker as early as possible if
    // we're going to start it at all, so we shouldn't miss any calls.
    if (crashtracker_initialized) {
        crashtracker.sampling_start();
    }
}

void
crashtracker_profiling_state_sampling_stop() // cppcheck-suppress unusedFunction
{
    if (crashtracker_initialized) {
        crashtracker.sampling_stop();
    }
}

void
crashtracker_profiling_state_unwinding_start() // cppcheck-suppress unusedFunction
{
    if (crashtracker_initialized) {
        crashtracker.unwinding_start();
    }
}

void
crashtracker_profiling_state_unwinding_stop() // cppcheck-suppress unusedFunction
{
    if (crashtracker_initialized) {
        crashtracker.unwinding_stop();
    }
}

void
crashtracker_profiling_state_serializing_start() // cppcheck-suppress unusedFunction
{
    if (crashtracker_initialized) {
        crashtracker.serializing_start();
    }
}

void
crashtracker_profiling_state_serializing_stop() // cppcheck-suppress unusedFunction
{
    if (crashtracker_initialized) {
        crashtracker.serializing_stop();
    }
}

// This handler just throws a segfault in order to summon the crashtracker.
void
throw_segfault(int) // cppcheck-suppress unusedFunction
{
    volatile int* p = nullptr;
    *p = 0;
}

void
crashtracker_chain_sigsegv_to_signum(int signum) // cppcheck-suppress unusedFunction
{
    // This is not advisable and it is simple to refactor crashtracker to handle this case.
    // However, it hasn't been refactored yet, and this is a useful diagnostic operation when everything else
    // has failed.

    // Get the address of the SIGSEGV signal handler, so we have the same overall settings
    struct sigaction sa;
    if (sigaction(SIGSEGV, nullptr, &sa) == -1) {
        return;
    }

    // Set the new signal handler
    sa.sa_handler = throw_segfault;

    // Install it on the specified signal
    if (sigaction(signum, &sa, nullptr) == -1) {
        return;
    }
}
