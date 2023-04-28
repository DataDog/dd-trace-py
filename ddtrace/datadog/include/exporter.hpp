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

// There's currently no need to offer custom tags, so there's no interface for
// it.  Instead, tags are keyed and populated based on this table, then
// referenced in `add_tag()`.
// There are two columns because runtime-id has a dash.
#define EXPORTER_TAGS(X) \
  X(language,         language) \
  X(env,              env) \
  X(service,          service) \
  X(version,          version) \
  X(runtime_version,  runtime_version) \
  X(runtime,          runtime) \
  X(runtime_id,       runtime-id) \
  X(profiler_version, profiler_version) \
  X(profile_seq,      profile_seq)

#define X_ENUM(a, b) a,
enum class ExportTagKey {
  EXPORTER_TAGS(X_ENUM)
  _Length
};
#undef X_ENUM

class DdogProfExporter {
public:
  void add_tag(ddog_Vec_Tag &tags, const ExportTagKey key, std::string_view val);

  static constexpr std::string_view language = "python";
  static constexpr std::string_view family = "python";

  DdogProfExporter(std::string_view env,
                   std::string_view service,
                   std::string_view version,
                   std::string_view runtime,
                   std::string_view runtime_version,
                   std::string_view profiler_version,
                   std::string_view url);
  ~DdogProfExporter();

  ddog_prof_Exporter *ptr;
};

class Uploader {
  std::string env;
  std::string service;
  std::string version;
  std::string url;
  std::string api_key; // Datadog api key
  bool agentless; // Whether or not to actually use API key/intake
  size_t profile_seq = 0;

  std::string runtime_id;
  std::unique_ptr<DdogProfExporter> ddog_exporter;

public:
  Uploader(std::string_view _env = "prod",
           std::string_view _service = "py_libdatadog",
           std::string_view _version = "",
           std::string_view runtime = "cython",
           std::string_view runtime_version = "???",
           std::string_view profiler_version = "???",
           std::string_view _url = "http://localhost:8126");

  void set_runtime_id(const std::string &id);
  bool upload(const Profile *profile);

};

class Profile {
public:
  enum ProfileType : unsigned int {
    CPU         = 1<<0,
    Wall        = 1<<1,
    Exception   = 1<<2,
    LockAcquire = 1<<3,
    LockRelease = 1<<4,
    Allocation  = 1<<5,
    Heap        = 1<<6,
    All = CPU | Wall | Exception | LockAcquire | LockRelease | Allocation | Heap
  };

private:
  bool is_valid = false;

  unsigned int type_mask;

  std::vector<ddog_prof_Location> locations;
  std::vector<ddog_prof_Line> lines;

  // Storage for labels
  ddog_prof_Label labels[8];
  size_t cur_label = 0;
  
  // Storage for values
  std::vector<int64_t> values = {};
  struct {
    unsigned short cpu_time;
    unsigned short cpu_count;
    unsigned short wall_time;
    unsigned short wall_count;
    unsigned short exception_count;
    unsigned short lock_acquire_time;
    unsigned short lock_acquire_count;
    unsigned short lock_release_time;
    unsigned short lock_release_count;
    unsigned short alloc_space;
    unsigned short alloc_count;
    unsigned short heap_space;
  } val_idx;

  // Helpers
  void push_label(const std::string_view &key, const std::string_view &val);
  void push_label(const std::string_view &key, int64_t val);

public:
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

  // Make the profile reusable
  void reset();

  Profile(ProfileType type);
  ~Profile();
};

} // namespace Datadog

extern "C" {
  void ddup_uploader_init(const char *_service, const char *_env, const char *_version, const char *_runtime, const char *_runtime_version, const char *_profiler_version);
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
  void ddup_set_runtime_id(const char *_id);
  void ddup_upload();
} // extern "C"
