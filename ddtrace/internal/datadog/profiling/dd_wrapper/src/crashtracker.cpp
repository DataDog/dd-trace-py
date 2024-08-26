#include "crashtracker.hpp"

#include <iostream>
#include <string>
#include <string_view>
#include <sys/stat.h>
#include <vector>

void
Datadog::Crashtracker::set_create_alt_stack(bool _create_alt_stack)
{
    create_alt_stack = _create_alt_stack;
}

void
Datadog::Crashtracker::set_wait_for_receiver(bool _wait)
{
    wait_for_receiver = _wait;
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
Datadog::Crashtracker::set_runtime_id(std::string_view _runtime_id)
{
    runtime_id = std::string(_runtime_id);
}

void
Datadog::Crashtracker::set_runtime_version(std::string_view _runtime_version)
{
    runtime_version = std::string(_runtime_version);
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
Datadog::Crashtracker::set_resolve_frames(ddog_prof_StacktraceCollection _resolve_frames)
{
    resolve_frames = _resolve_frames;
}

void
Datadog::Crashtracker::set_library_version(std::string_view _library_version)
{
    library_version = std::string(_library_version);
}

void
Datadog::Crashtracker::set_tag(std::string_view key, std::string_view value)
{
    // Maybe this should be called "add tag," but the interface to the uploader is already called "set_tag"
    // and we have another refactor incoming anyway, so let's just kick the can for now
    if (!key.empty() && !value.empty()) {
        user_tags[std::string(key)] = std::string(value);
    }
}

bool
Datadog::Crashtracker::set_receiver_binary_path(std::string_view _path)
{
    // First, check that the path is valid and executable
    // We can't use C++ filesystem here because of limitations in manylinux, so we'll use the C API
    struct stat sa;
    if (stat(_path.data(), &sa) != 0) {
        std::cerr << "Receiver binary path does not exist: " << _path << std::endl;
        return false;
    }
    if (!(sa.st_mode & S_IXUSR)) {
        std::cerr << "Receiver binary path is not executable: " << _path << std::endl;
        return false;
    }
    path_to_receiver_binary = std::string(_path);
    return true;
}

ddog_prof_CrashtrackerConfiguration
Datadog::Crashtracker::get_config()
{
    ddog_prof_CrashtrackerConfiguration config{};
    config.create_alt_stack = create_alt_stack;
    config.endpoint = ddog_prof_Endpoint_agent(to_slice(url));
    config.resolve_frames = resolve_frames;
    config.timeout_secs = timeout_secs;
    config.wait_for_receiver = wait_for_receiver;
    return config;
}

ddog_prof_CrashtrackerReceiverConfig
Datadog::Crashtracker::get_receiver_config()
{
    ddog_prof_CrashtrackerReceiverConfig config{};
    config.path_to_receiver_binary = to_slice(path_to_receiver_binary);

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
        { ExportTagKey::is_crash, g_crashtracker_is_crash },
        { ExportTagKey::severity, g_crashtracker_severity },
    };

    // Add system tags
    std::string errmsg; // Populated, but unused
    for (const auto& [key, value] : tag_data) {
        // NB - keys here are members of an enum; `add_tag()` specialization below will stringify them
        if (!value.empty()) {
            add_tag(tags, key, value, errmsg); // We don't have a good way of handling errors here
        }
    }

    // Add user tags
    for (const auto& [key, value] : user_tags) {
        if (!key.empty() && !value.empty()) {
            add_tag(tags, key, value, errmsg);
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
    auto receiver_config = get_receiver_config();
    auto tags = get_tags();
    auto metadata = get_metadata(tags);

    auto result = ddog_prof_Crashtracker_init_with_receiver(config, receiver_config, metadata);
    ddog_Vec_Tag_drop(tags);
    if (result.tag != DDOG_PROF_CRASHTRACKER_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = result.err;                            // NOLINT (cppcoreguidelines-pro-type-union-access)
        std::string errmsg = err_to_msg(&err, "Error initializing crash tracker");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return false;
    }
    return true;
}

bool
Datadog::Crashtracker::atfork_child()
{
    auto config = get_config();
    auto receiver_config = get_receiver_config();
    auto tags = get_tags();
    auto metadata = get_metadata(tags);

    auto result = ddog_prof_Crashtracker_update_on_fork(config, receiver_config, metadata);
    ddog_Vec_Tag_drop(tags);
    if (result.tag != DDOG_PROF_CRASHTRACKER_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = result.err;                            // NOLINT (cppcoreguidelines-pro-type-union-access)
        std::string errmsg = err_to_msg(&err, "Error initializing crash tracker");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return false;
    }

    // Reset the profiling state
    profiling_state.is_sampling.store(0);
    auto res_sampling = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_COLLECTING_SAMPLE);
    (void)res_sampling;

    profiling_state.is_unwinding.store(0);
    auto res_unwinding = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_UNWINDING);
    (void)res_unwinding;

    profiling_state.is_serializing.store(0);
    auto res_serializing = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_SERIALIZING);
    (void)res_serializing;

    return true;
}

// Profiling state management
void
Datadog::Crashtracker::sampling_stop()
{
    static bool has_errored = false; // cppcheck-suppress threadsafety-threadsafety

    // If this was the last sampling operation, then emit that fact to crashtracker
    auto old_val = profiling_state.is_sampling.fetch_sub(1);
    if (old_val == 1) {
        auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_COLLECTING_SAMPLE);
        (void)res; // ignore for now
    } else if (old_val == 0 && !has_errored) {
        // This is an error condition.  We only emit the error once, since the bug in the state machine
        // may jitter around 0 at high frequency.
        std::cerr << "Profiling sampling state underflow" << std::endl;
        has_errored = true;
    }
}

void
Datadog::Crashtracker::sampling_start()
{
    // If this is the first sampling operation, then emit that fact to crashtracker
    // Just like the stop operation, there may be an invalid count, but we track only at stop time
    auto old_val = profiling_state.is_sampling.fetch_add(1);
    if (old_val == 0) {
        auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_COLLECTING_SAMPLE);
        (void)res; // ignore for now
    }
}

void
Datadog::Crashtracker::unwinding_start()
{
    static bool has_errored = false; // cppcheck-suppress threadsafety-threadsafety
    auto old_val = profiling_state.is_unwinding.fetch_sub(1);
    if (old_val == 1) {
        auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_UNWINDING);
        (void)res;
    } else if (old_val == 0 && !has_errored) {
        std::cerr << "Profiling unwinding state underflow" << std::endl;
        has_errored = true;
    }
}

void
Datadog::Crashtracker::unwinding_stop()
{
    auto old_val = profiling_state.is_unwinding.fetch_add(1);
    if (old_val == 0) {
        auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_UNWINDING);
        (void)res;
    }
}

void
Datadog::Crashtracker::serializing_start()
{
    static bool has_errored = false; // cppcheck-suppress threadsafety-threadsafety
    auto old_val = profiling_state.is_serializing.fetch_sub(1);
    if (old_val == 1) {
        auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_SERIALIZING);
        (void)res;
    } else if (old_val == 0 && !has_errored) {
        std::cerr << "Profiling serializing state underflow" << std::endl;
        has_errored = true;
    }
}

void
Datadog::Crashtracker::serializing_stop()
{
    auto old_val = profiling_state.is_serializing.fetch_add(1);
    if (old_val == 0) {
        auto res = ddog_prof_Crashtracker_end_profiling_op(DDOG_PROF_PROFILING_OP_TYPES_SERIALIZING);
        (void)res;
    }
}
