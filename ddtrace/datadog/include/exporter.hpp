// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.
#pragma once

#include <array>
#include <chrono>
#include <string>
#include <string_view>
#include <memory>
#include <vector>

extern "C" {
#include "datadog/profiling.h"
};

namespace Datadog {

// Forward
class Profile;

class DdogProfExporter {
  void add_tag(ddog_Vec_Tag &tags, std::string_view key, std::string_view val);

public:
  static constexpr std::string_view language = "python";
  static constexpr std::string_view family = "python";
  static constexpr std::string_view profiler_version = "1.8.0rc2_libdatadog";

  DdogProfExporter(std::string_view env, std::string_view service, std::string_view version, std::string_view url);
  ~DdogProfExporter();

  ddog_prof_Exporter *ptr;
};

class Uploader {
  std::string env;     // ex: staging / local / prod
  std::string service; // service name (ex:prof-probe-native)
  std::string version; // appended to tags (example: 1.2.1)
  std::string url;     // host:port
  std::string api_key; // Datadog api key
  bool agentless; // Whether or not to actually use API key/intake

  std::unique_ptr<DdogProfExporter> ddog_exporter;

public:
  Uploader(std::string_view _env = "prod",
           std::string_view _service = "py_libdatadog",
           std::string_view _version = "",
           std::string_view _url = "http://localhost:8126");

  bool upload(const Profile *profile);

};

class Profile {
  bool is_valid = false;

  std::vector<ddog_prof_Location> locations;
  std::vector<ddog_prof_Line> lines;

  // Storage for labels
  ddog_prof_Label labels[8];
  size_t cur_label = 0;
  
  // Storage for values
  std::array<int64_t, 12> values = {};

  // Helpers
  void push_label(const std::string_view &key, const std::string_view &val);
  void push_label(const std::string_view &key, int64_t val);

public:
  // HACKY BAD STUFF
  std::vector<std::string> strings;

  uint64_t samples = 0;
  uint64_t frames = 0;
  ddog_prof_Profile *ddog_profile;

  // Clears the current sample without flushing and starts a new one
  void start_sample();

  // Add values
  void push_walltime(int64_t walltime, int64_t count);
  void push_cputime(int64_t cputime, int64_t count);
  void push_acquire(int64_t acquire_time, int64_t count);
  void push_release(int64_t lock_time, int64_t count);
  void push_alloc(int64_t alloc_size, int64_t count);
  void push_heap(int64_t heap_size);

  // Adds metadata to sample
  void push_threadinfo(
        int64_t thread_id,
        int64_t thread_native_id,
        const std::string_view &thread_name
      );
  void push_taskinfo(
        int64_t task_id,
        const std::string_view &task_name
      );
  void push_spaninfo(
      int64_t span_id,
      int64_t local_root_span_id
      );
  void push_traceinfo(
        const std::string_view &trace_type,
        const std::string_view &trace_resource_container
      );

  void push_exceptioninfo(
        const std::string_view &exception_type,
        int64_t count
      );

  void push_classinfo(
        const std::string_view &class_name
      );

  // Assumes frames are pushed in leaf-order
  void push_frame(
        const std::string_view &name,      // for ddog_prof_Function
        const std::string_view &filename,  // for ddog_prof_Function
        uint64_t address,                  // for ddog_prof_Location
        int64_t line                       // for ddog_prof_Line
      );
    

  // Flushes the current buffer, clearing it
  bool flush_sample();

  // Clears temporary things
  void clear_buffers();

  // Zero out stats
  void zero_stats();

  Profile();
  ~Profile();
};

} // namespace Datadog

extern "C" {
  void ddup_uploader_init(const char *_service, const char *_env, const char *_version);
  void ddup_start_sample();
  void ddup_push_walltime(int64_t walltime, int64_t count);
  void ddup_push_cputime(int64_t cputime, int64_t count);
  void ddup_push_acquire(int64_t acquire_time, int64_t count);
  void ddup_push_release(int64_t release_time, int64_t count);
  void ddup_push_alloc(int64_t alloc_size, int64_t count);
  void ddup_push_heap(int64_t heap_size);
  void ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char *thread_name);
  void ddup_push_taskinfo(int64_t task_id, const char *task_name);
  void ddup_push_spaninfo(int64_t span_id, int64_t local_root_span_id);
  void ddup_push_traceinfo(const char *trace_type, const char *trace_resource_container);
  void ddup_push_exceptioninfo(const char *exception_type, int64_t count);
  void ddup_push_classinfo(const char *class_name);
  void ddup_push_frame(const char *_name, const char *_filename, uint64_t address, int64_t line);
  void ddup_flush_sample();
  void ddup_upload();
} // extern "C"
