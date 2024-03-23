#include "crash_tracker.hpp"

void
Datadog::Crashtracker::set_collect_stacktrace(bool _collect_stacktrace)
{
    collect_stacktrace = _collect_stacktrace;
}

void
Datadog::Crashtracker::set_create_alt_stack(bool _create_alt_stack)
{
    create_alt_stack = _create_alt_stack;
}

void
Datadog::Crashtracker::set_url(const std::string &_url)
{
    url = _url;
}

void
Datadog::Crashtracker::set_stderr_filename(const std::string &_stderr_filename)
{
    stderr_filename = _stderr_filename;
}

void
Datadog::Crashtracker::set_path_to_receiver_binary(const std::string &_path_to_receiver_binary)
{
    path_to_receiver_binary = _path_to_receiver_binary;
}

void
Datadog::Crashtracker::set_resolve_frames(ddog_prof_CrashtrackerResolveFrames _resolve_frames)
{
    resolve_frames = _resolve_frames;
}

void
Datadog::Crashtracker::set_library_name(const std::string &_library_name)
{
    library_name = _library_name;
}

void
Datadog::Crashtracker::set_library_version(const std::string &_library_version)
{
    library_version = _library_version;
}

void
Datadog::Crashtracker::set_family(const std::string &_family)
{
    family = _family;
}

bool
Datadog::Crashtracker::init()
{
  ddog_prof_CrashtrackerConfiguration config;
  config.collect_stacktrace = collect_stacktrace;
  config.create_alt_stack = create_alt_stack;
  config.endpoint = ddog_prof_Endpoint_agent(to_slice(url)),
  config.path_to_receiver_binary = to_slice(path_to_receiver_binary);
  config.resolve_frames = resolve_frames;

  if (stderr_filename.has_value()) {
    config.optional_stderr_filename = to_slice(stderr_filename.value());
  }

  if (stdout_filename.has_value()) {
    config.optional_stdout_filename = to_slice(stdout_filename.value());
  }

  ddog_prof_CrashtrackerMetadata metadata;
  metadata.profiling_library_name = to_slice(library_name);
  metadata.profiling_library_version = to_slice(library_version);
  metadata.family = to_slice(family);
  metadata.tags = nullptr; // tags like service etc, TBD

  auto result = ddog_prof_Crashtracker_init(config, metadata);
  if (result.tag != DDOG_PROF_PROFILE_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
    auto err = result.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
    std::string errmsg = err_to_msg(&err, "Error initializing crash tracker");
    std::cerr << errmsg << std::endl;
    ddog_Error_drop(&err);
    return false;
  }

  return true;
}
