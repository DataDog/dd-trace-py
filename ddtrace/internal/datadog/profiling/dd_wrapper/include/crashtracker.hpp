#pragma once

#include "constants.hpp"
#include "libdatadog_helpers.hpp"

#include <atomic>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>

#include "datadog/crashtracker.h"

namespace Datadog {

// One of the core intrigues with crashtracker is contextualization of crashes--did a crash occur
// because of some user code, or was it this library?
// It's really hard to rule out knock-on or indirect effects, but at least crashtracker
// can mark whether a Datadog component was on-CPU at the time of the crash, and even
// indicate what it was doing.
//
// Right now the caller is assumed to only tell this system _what_ it is doing.  There's no
// available "profiling, other" state.  Just sampling, unwinding, or serializing.
struct ProfilingState
{
    std::atomic<int> is_sampling{ 0 };
    std::atomic<int> is_unwinding{ 0 };
    std::atomic<int> is_serializing{ 0 };
};

class Crashtracker
{
  private:
    bool create_alt_stack = false;
    bool wait_for_receiver = true;
    std::optional<std::string> stderr_filename{ std::nullopt };
    std::optional<std::string> stdout_filename{ std::nullopt };
    std::string path_to_receiver_binary;
    ddog_crasht_StacktraceCollection resolve_frames = DDOG_CRASHT_STACKTRACE_COLLECTION_WITHOUT_SYMBOLS;
    uint64_t timeout_secs = g_crashtracker_timeout_secs;

    ProfilingState profiling_state;

    std::string env;
    std::string service;
    std::string version;
    std::string runtime{ g_runtime_name };
    std::string runtime_id;
    std::string runtime_version;
    std::string library_version;
    std::string url;

    std::unordered_map<std::string, std::string> user_tags;

    static constexpr std::string_view family{ g_language_name };
    static constexpr std::string_view library_name{ g_library_name };

    // Helpers for initialization/restart
    ddog_Vec_Tag get_tags();
    ddog_crasht_Config get_config();
    ddog_crasht_Metadata get_metadata(ddog_Vec_Tag& tags);
    ddog_crasht_ReceiverConfig get_receiver_config();

  public:
    // Setters
    void set_env(std::string_view _env);
    void set_service(std::string_view _service);
    void set_version(std::string_view _version);
    void set_runtime(std::string_view _runtime);
    void set_runtime_id(std::string_view _runtime_id);
    void set_runtime_version(std::string_view _runtime_version);
    void set_library_version(std::string_view _library_version);
    void set_url(std::string_view _url);
    void set_tag(std::string_view _key, std::string_view _value);
    void set_wait_for_receiver(bool _wait);

    void set_create_alt_stack(bool _create_alt_stack);
    void set_stderr_filename(std::string_view _stderr_filename);
    void set_stdout_filename(std::string_view _stdout_filename);
    bool set_receiver_binary_path(std::string_view _path_to_receiver_binary);

    void set_resolve_frames(ddog_crasht_StacktraceCollection _resolve_frames);

    // Helpers
    bool start();
    bool atfork_child();

    // State transition
    void sampling_start();
    void sampling_stop();
    void unwinding_start();
    void unwinding_stop();
    void serializing_start();
    void serializing_stop();
};

} // namespace Datadog
