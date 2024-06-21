#include "crashtracker_interface.hpp"
#include "crashtracker.hpp"

// A global instance of the crashtracker is created here.
Datadog::Crashtracker crashtracker;

void
crashtracker_postfork_child()
{
    crashtracker.atfork_child();
}

void
crashtracker_set_url(std::string_view url)
{
    crashtracker.set_url(url);
}

void
crashtracker_set_service(std::string_view service)
{
    crashtracker.set_service(service);
}

void
crashtracker_set_env(std::string_view env)
{
    crashtracker.set_env(env);
}

void
crashtracker_set_version(std::string_view version)
{
    crashtracker.set_version(version);
}

void
crashtracker_set_runtime(std::string_view runtime)
{
    crashtracker.set_runtime(runtime);
}

void
crashtracker_set_runtime_version(std::string_view runtime_version)
{
    crashtracker.set_runtime_version(runtime_version);
}

void
crashtracker_set_runtime_id(std::string_view runtime_id)
{
    crashtracker.set_runtime_id(runtime_id);
}

void
crashtracker_set_library_version(std::string_view profiler_version)
{
    crashtracker.set_library_version(profiler_version);
}

void
crashtracker_set_stdout_filename(std::string_view filename)
{
    crashtracker.set_stdout_filename(filename);
}

void
crashtracker_set_stderr_filename(std::string_view filename)
{
    crashtracker.set_stderr_filename(filename);
}

void
crashtracker_set_alt_stack(bool alt_stack)
{
    crashtracker.set_create_alt_stack(alt_stack);
}

void
crashtracker_set_resolve_frames_disable()
{
    crashtracker.set_resolve_frames(DDOG_PROF_STACKTRACE_COLLECTION_DISABLED);
}

void
crashtracker_set_resolve_frames_fast()
{
    crashtracker.set_resolve_frames(DDOG_PROF_STACKTRACE_COLLECTION_WITHOUT_SYMBOLS);
}

void
crashtracker_set_resolve_frames_full()
{
    crashtracker.set_resolve_frames(DDOG_PROF_STACKTRACE_COLLECTION_ENABLED);
}

bool
crashtracker_set_receiver_binary_path(std::string_view path)
{
    return crashtracker.set_receiver_binary_path(path);
}

void
crashtracker_start()
{
    // This is a one-time start pattern to ensure that the crashtracker is only started once.
    const static bool initialized = []() {
        crashtracker.start();
        return true;
    }();
    (void)initialized;
}
