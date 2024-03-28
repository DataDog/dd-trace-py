#include "crash_tracker.hpp"

#include <filesystem>
#include <iostream>
#include <vector>

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
Datadog::Crashtracker::set_env(std::string_view _env)
{
    env = std::string(_env);
}

void
Datadog::Crashtracker::set_service(std::string_view _service)
{
    service = std::string(_service);
}

void
Datadog::Crashtracker::set_version(std::string_view _version)
{
    version = std::string(_version);
}

void
Datadog::Crashtracker::set_runtime(std::string_view _runtime)
{
    runtime = std::string(_runtime);
}

void
Datadog::Crashtracker::set_runtime_version(std::string_view _runtime_version)
{
    runtime_version = std::string(_runtime_version);
}

void
Datadog::Crashtracker::set_runtime_id(std::string_view _runtime_id)
{
    runtime_id = std::string(_runtime_id);
}

void
Datadog::Crashtracker::set_url(std::string_view _url)
{
    url = std::string(_url);
}

void
Datadog::Crashtracker::set_stderr_filename(std::string_view _stderr_filename)
{
    if (_stderr_filename.empty()) {
        stderr_filename.reset();
    } else {
        stderr_filename = std::string(_stderr_filename);
    }
}

void
Datadog::Crashtracker::set_stdout_filename(std::string_view _stdout_filename)
{
    if (_stdout_filename.empty()) {
        stdout_filename.reset();
    } else {
        stdout_filename = std::string(_stdout_filename);
    }
}

void
Datadog::Crashtracker::set_resolve_frames(ddog_prof_CrashtrackerResolveFrames _resolve_frames)
{
    resolve_frames = _resolve_frames;
}

void
Datadog::Crashtracker::set_library_version(std::string_view _library_version)
{
    library_version = std::string(_library_version);
}

bool
Datadog::Crashtracker::set_receiver_binary_path(std::string_view _path)
{
    // First, check that the path is valid
    if (!std::filesystem::exists(_path)) {
        // TODO in the future, we could verify that this object has executable permissions
        // and possibly check the header or run the binary in some kind of diagnostic mode to get
        // output, but existence is fine for now.
        std::cerr << "Receiver binary path does not exist: " << _path << std::endl;
        return false;
    }
    path_to_receiver_binary = std::string(_path);
    return true;
}

ddog_prof_CrashtrackerConfiguration
Datadog::Crashtracker::get_config()
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

    return config;
}

ddog_Vec_Tag
Datadog::Crashtracker::get_tags()
{
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, env },
        { ExportTagKey::service, service },
        { ExportTagKey::version, version },
        { ExportTagKey::language, family }, // Slight conflation of terms, but should be OK
        { ExportTagKey::runtime, runtime },
        { ExportTagKey::runtime_version, runtime_version },
        { ExportTagKey::library_version, library_version },
    };

    std::string errmsg; // Populated, but unused
    for (const auto& [tag, data] : tag_data) {
        if (!data.empty()) {
            add_tag(tags, tag, data, errmsg); // We don't have a good way of handling errors here
        }
    }

    return tags;
}

ddog_prof_CrashtrackerMetadata
Datadog::Crashtracker::get_metadata(ddog_Vec_Tag& tags)
{
    ddog_prof_CrashtrackerMetadata metadata;
    metadata.profiling_library_name = to_slice(library_name);
    metadata.profiling_library_version = to_slice(library_version);
    metadata.family = to_slice(family);
    metadata.tags = &tags;

    return metadata;
}

bool
Datadog::Crashtracker::start()
{
  auto config = get_config();
  auto tags = get_tags();
  auto metadata = get_metadata(tags);

  auto result = ddog_prof_Crashtracker_init(config, metadata);
  ddog_Vec_Tag_drop(tags);
  if (result.tag != DDOG_PROF_PROFILE_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
    auto err = result.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
    std::string errmsg = err_to_msg(&err, "Error initializing crash tracker");
    std::cerr << errmsg << std::endl;
    ddog_Error_drop(&err);
    return false;
  }

  // Set the base state for profiling ops
  start_not_profiling();

  return true;
}

bool
Datadog::Crashtracker::atfork_child()
{
  auto config = get_config();
  auto tags = get_tags();
  auto metadata = get_metadata(tags);

  auto result = ddog_prof_Crashtracker_update_on_fork(config, metadata);
  ddog_Vec_Tag_drop(tags);
  if (result.tag != DDOG_PROF_PROFILE_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
    auto err = result.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
    std::string errmsg = err_to_msg(&err, "Error initializing crash tracker");
    std::cerr << errmsg << std::endl;
    ddog_Error_drop(&err);
    return false;
  }

  // Set the base state for profiling ops
  start_not_profiling();

  return true;
}

// Profiling state management
void
Datadog::Crashtracker::start_not_profiling()
{
    auto res = ddog_prof_Crashtracker_begin_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_NOT_PROFILING);
    (void)res; // ignore for now
}

void
Datadog::Crashtracker::stop_not_profiling()
{
    auto res = ddog_prof_Crashtracker_begin_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_NOT_PROFILING);
    (void)res; // ignore for now
}

void
Datadog::Crashtracker::start_sampling()
{
    auto res = ddog_prof_Crashtracker_begin_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_COLLECTING_SAMPLE);
    (void)res; // ignore for now
}

void
Datadog::Crashtracker::stop_sampling()
{
    auto res = ddog_prof_Crashtracker_begin_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_NOT_PROFILING);
    (void)res; // ignore for now
}

void
Datadog::Crashtracker::start_unwinding()
{
    auto res = ddog_prof_Crashtracker_begin_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_UNWINDING);
    (void)res; // ignore for now
}

void
Datadog::Crashtracker::stop_unwinding()
{
    auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_UNWINDING);
    (void)res; // ignore for now
}

void
Datadog::Crashtracker::start_serializing()
{
    auto res = ddog_prof_Crashtracker_begin_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_SERIALIZING);
    (void)res; // ignore for now
}

void
Datadog::Crashtracker::stop_serializing()
{
    auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_SERIALIZING);
    (void)res; // ignore for now
}
