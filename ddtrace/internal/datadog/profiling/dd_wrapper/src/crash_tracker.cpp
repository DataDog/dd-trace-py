#include "crash_tracker.hpp"

void
CrashTracker::set_collect_stacktrace(bool _collect_stacktrace)
{
    collect_stacktrace = _collect_stacktrace;
}

void
CrashTracker::set_create_alt_stack(bool _create_alt_stack)
{
    create_alt_stack = _create_alt_stack;
}

void
CrashTracker::set_url(const std::string &_url)
{
    url = _url;
}

void
CrashTracker::set_stderr_filename(const std::string &_stderr_filename)
{
    stderr_filename = _stderr_filename;
}

void
CrashTracker::set_path_to_receiver_binary(const std::string &_path_to_receiver_binary)
{
    path_to_receiver_binary = _path_to_receiver_binary;
}

void
CrashTracker::set_resolve_frames(ddog_prof_CrashTrackerResolveFrames _resolve_frames)
{
    resolve_frames = _resolve_frames;
}

void
CrashTracker::set_library_name(const std::string &_library_name)
{
    library_name = _library_name;
}

void
CrashTracker::set_library_version(const std::string &_library_version)
{
    library_version = _library_version;
}

void
CrashTracker::set_family(const std::string &_family)
{
    family = _family;
}

bool
CrashTracker::init()
{
  ddog_prof_CrashTrackerConfiguration config{
    collect_stacktrace,
    create_alt_stack,
    ddog_prof_Endpoint_agent(to_slice(url)),
    to_slice(stderr_filename),
    to_slice(stdout_filename),
    to_slice(path_to_receiver_binary),
    resolve_frames,
    to_slice(library_name),
    to_slice(library_version),
    to_slice(family)
  };

  ddog_prof_CrashTrackerMetadata metadata{
    to_slice(library_name),
    to_slice(library_version),
    to_slice(family),
    nullptr // tags like service etc, TBD
  };
}
