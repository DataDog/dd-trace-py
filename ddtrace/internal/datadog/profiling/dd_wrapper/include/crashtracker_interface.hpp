#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string_view>

#ifdef __cplusplus
extern "C"
{
#endif
    const char* crashtracker_get_exe_name();
    void crashtracker_set_url(std::string_view url);
    void crashtracker_set_service(std::string_view service);
    void crashtracker_set_env(std::string_view env);
    void crashtracker_set_version(std::string_view version);
    void crashtracker_set_runtime(std::string_view runtime);
    void crashtracker_set_runtime_id(std::string_view runtime_id);
    void crashtracker_set_runtime_version(std::string_view runtime_version);
    void crashtracker_set_library_version(std::string_view profiler_version);
    void crashtracker_set_stdout_filename(std::string_view filename);
    void crashtracker_set_stderr_filename(std::string_view filename);
    void crashtracker_set_alt_stack(bool alt_stack);
    void crashtracker_set_wait_for_receiver(bool wait);
    void crashtracker_set_resolve_frames_disable();
    void crashtracker_set_resolve_frames_fast();
    void crashtracker_set_resolve_frames_full();
    void crashtracker_set_resolve_frames_safe();
    bool crashtracker_set_receiver_binary_path(std::string_view path);
    void crashtracker_set_tag(std::string_view key, std::string_view value);
    void crashtracker_profiling_state_sampling_start();
    void crashtracker_profiling_state_sampling_stop();
    void crashtracker_profiling_state_unwinding_start();
    void crashtracker_profiling_state_unwinding_stop();
    void crashtracker_profiling_state_serializing_start();
    void crashtracker_profiling_state_serializing_stop();
    void crashtracker_start();
    bool crashtracker_is_started();
#ifdef __cplusplus
} // extern "C"
#endif
