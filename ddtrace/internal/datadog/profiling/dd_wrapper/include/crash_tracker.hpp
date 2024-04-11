#pragma once

#include "libdatadog_helpers.hpp"

#include <optional>
#include <string>
#include <string_view>

namespace Datadog {
class Crashtracker {
  private:
    bool create_alt_stack = false;
    std::optional<std::string> stderr_filename{std::nullopt};
    std::optional<std::string> stdout_filename{std::nullopt};
    std::string path_to_receiver_binary;
    ddog_prof_CrashtrackerResolveFrames resolve_frames;

    std::string env;
    std::string service;
    std::string version;
    std::string runtime;
    std::string runtime_version;
    const std::string library_name{"dd-trace-py"};
    const std::string family{"CPython"}; // This duplicates "language" from ddup?
    std::string library_version;
    std::string url;
    std::string runtime_id;

    // Helpers for initialization/restart
    ddog_Vec_Tag get_tags();
    ddog_prof_CrashtrackerConfiguration get_config();
    ddog_prof_CrashtrackerMetadata get_metadata(ddog_Vec_Tag &tags);

  public:

    // Setters
    void set_env(std::string_view _env);
    void set_service(std::string_view _service);
    void set_version(std::string_view _version);
    void set_runtime(std::string_view _runtime);
    void set_runtime_version(std::string_view _runtime_version);
    void set_library_version(std::string_view _library_version);
    void set_url(std::string_view _url);
    void set_runtime_id(std::string_view _runtime_id);

    void set_create_alt_stack(bool _create_alt_stack);
    void set_stderr_filename(std::string_view _stderr_filename);
    void set_stdout_filename(std::string_view _stdout_filename);
    bool set_receiver_binary_path(std::string_view _path_to_receiver_binary);

    void set_resolve_frames(ddog_prof_CrashtrackerResolveFrames _resolve_frames);

    // Helpers
    bool start();
    bool atfork_child();

    // State transition
    void start_not_profiling();
    void stop_not_profiling();
    void start_sampling();
    void stop_sampling();
    void start_unwinding();
    void stop_unwinding();
    void start_serializing();
    void stop_serializing();
};

} // namespace Datadog
