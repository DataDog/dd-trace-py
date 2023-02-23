// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.
#include "exporter.hpp"

#include <iostream>
#include <string.h> // for strdup for hack
#include <thread>

using namespace Datadog;

inline ddog_CharSlice to_slice(const std::string_view &str) {
  return {.ptr = str.data(), .len = str.size()};
}

Uploader::Uploader(const std::string &_service,
                   const std::string &_env,
                   const std::string &_version,
                   const std::string &_url) {
  service = _service;
  env = _env;
  version = _version;
  url = _url;

  // If we're this far, let's add some tags
  ddog_Vec_Tag tags = ddog_Vec_Tag_new();
  add_tag(tags, "language", language);
  add_tag(tags, "env", env);
  add_tag(tags, "service", service);
  add_tag(tags, "version", version);
  add_tag(tags, "profiler_version", profiler_version);

  ddog_prof_Exporter_NewResult new_exporter = ddog_prof_Exporter_new(
      to_slice("dd-trace-py"),
      to_slice(profiler_version),
      to_slice(family),
      &tags,
      ddog_Endpoint_agent(to_slice(url)));
  ddog_Vec_Tag_drop(tags);

  if (new_exporter.tag == DDOG_PROF_EXPORTER_NEW_RESULT_OK)
    ddog_exporter = new_exporter.ok;
  else
    std::cout << "ERROR INITIALIZING LIBDATADOG EXPORTER" << std::endl;

//  ddog_prof_Exporter_NewResult_drop(new_exporter);
}

Uploader::~Uploader() {
  ddog_prof_Exporter_drop(ddog_exporter);
}

void Uploader::add_tag(ddog_Vec_Tag &tags, const std::string &key, const std::string &val) {
  ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key), to_slice(val));
  if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
    std::cout << "Error pushing tag '" + key + "'->'" + val + "'" << std::endl;
    std::cout << "  err: " << ddog_Error_message(&res.err).ptr << std::endl;
  }
}

#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

bool Uploader::upload(const Profile *profile) {
  ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(
      profile->ddog_profile,
      nullptr,
      nullptr);
  if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
    std::cout << "Failure serializing pprof" << std::endl;
    return false;
  }

  ddog_prof_EncodedProfile *encoded = &result.ok;

  // Write to disk
//  ddog_prof_Vec_U8 *buffer = &encoded->buffer;
//  int fd = open("/tmp/auto.pprof", O_CREAT | O_RDWR, 0600);
//  int n = -1;
//  if (1 > (n=write(fd, buffer->ptr, buffer->len)))
//    std::cout << "Could not write" << std::endl;
//  else if(buffer->len != n)
//    std::cout << "Wrote pprof (failed)" << std::endl;
//  else
//    std::cout << "Wrote pprof" << std::endl;
//  close(fd);

  ddog_Timespec start = encoded->start;
  ddog_Timespec end = encoded->end;

  // Attach file
  ddog_prof_Exporter_File file[] = {
    {
      .name = to_slice("auto.pprof"),
      .file = {
        .ptr = encoded->buffer.ptr,
        .len = encoded->buffer.len,
      },
    },
  };

  ddog_prof_Exporter_Slice_File files = {.ptr = file, .len = 1};

  ddog_prof_Exporter_Request_BuildResult build_res = ddog_prof_Exporter_Request_build(
      ddog_exporter,
      start,
      end,
      files,
      nullptr,
      nullptr,
      5000);
  if (build_res.tag == DDOG_PROF_EXPORTER_REQUEST_BUILD_RESULT_ERR) {
    std::cout << "Could not build request" << std::endl;
    std::cout << "  " << ddog_Error_message(&build_res.err).ptr << std::endl;
    ddog_Error_drop(&build_res.err);
    return false;
  }

  ddog_prof_Exporter_Request *req = build_res.ok;

  ddog_prof_Exporter_SendResult res = ddog_prof_Exporter_send(ddog_exporter, &req, nullptr);

  // Close out request

  if (res.tag == DDOG_PROF_EXPORTER_SEND_RESULT_ERR) {
    std::cout << "Failed to send result to backend" << std::endl;
    std::cout << "  url: " <<  url << std::endl;
    std::cout << "  " << ddog_Error_message(&res.err).ptr << std::endl;
  }

  // We're done exporting, so reset the profile
  if (!ddog_prof_Profile_reset(profile->ddog_profile, nullptr)) {
    std::cout << "Unable to reset!" << std::endl;
    return false;
  }

//  ddog_prof_Profile_SerializeResult_drop(result);
//  ddog_prof_Exporter_Request_drop(req);
//  ddog_prof_Exporter_SendResult_drop(res);

  return true;
}

Profile::Profile() {
  // Fill in the profiling bits
  ddog_prof_ValueType samplers[] = {
    {to_slice("cpu-time"), to_slice("nanoseconds")},
    {to_slice("cpu-samples"), to_slice("count")},
    {to_slice("wall-time"), to_slice("nanoseconds")},
    {to_slice("wall-samples"), to_slice("count")},
    {to_slice("exception-samples"), to_slice("count")},
    {to_slice("lock-acquire"), to_slice("count")},
    {to_slice("lock-acquire-wait"), to_slice("nanoseconds")},
    {to_slice("lock-release"), to_slice("count")},
    {to_slice("lock-release-hold"), to_slice("nanoseconds")},
    {to_slice("alloc-samples"), to_slice("count")},
    {to_slice("alloc-space"), to_slice("bytes")},
    {to_slice("heap-space"), to_slice("bytes")},
  };

  ddog_prof_Period cpu_period = {samplers[0], 1}; // Is this even used?
  ddog_profile = ddog_prof_Profile_new(
      {samplers, std::size(samplers)},
      &cpu_period,
      nullptr);

  // Initialize the size for buffers
  locations.reserve(2048);
  lines.reserve(2048);
  strings.reserve(8192);
}

Profile::~Profile() {
  ddog_prof_Profile_drop(ddog_profile);
}

void Profile::zero_stats() {
  frames = 0;
  samples = 0;
}

void Profile::start_sample() {
  strings.clear();
  clear_buffers();
}

// TODO
// It's possible that the underlying string table references can be moved by the runtime over the course of
// processing, invalidating what we saved by the time we come back to it
// It may be possible to avoid intermediate copies by writing down indices or referring to other elements
// of the Python frame object
void Profile::push_frame(
    const std::string_view &name,
    const std::string_view &filename,
    uint64_t address,
    int64_t line) {

  lines.push_back({
          .function = {
            .name = to_slice(name),
            .system_name = {},
            .filename = to_slice(filename),
            .start_line = -1,
            },
          .line = line,
  });

  locations.push_back({
       {},
       address,
       {&lines.back(), 1},
       false,
  });

  ++frames;
}

void Profile::push_label(const std::string_view &key, const std::string_view &val) {
  labels[cur_label].key = to_slice(key);

  // Label may not persist, so it needs to be saved
  strings.push_back(std::string{val});
  labels[cur_label].str = to_slice(strings.back());
  cur_label++;
}

void Profile::push_label(const std::string_view &key, int64_t val) {
  labels[cur_label].key = to_slice(key);
  labels[cur_label].num = val;
  cur_label++;
}

void Profile::clear_buffers() {
  locations.clear();
  lines.clear();
  values = {};
  std::fill(std::begin(labels), std::end(labels), ddog_prof_Label{});
  cur_label = 0;
}

bool Profile::flush_sample() {
  ddog_prof_Sample sample = {
    .locations = {&locations[0], locations.size()},
    .values = {&values[0], std::size(values)},
    .labels = {labels, cur_label},
  };

  ddog_prof_Profile_AddResult address = ddog_prof_Profile_add(ddog_profile, sample);

  if (address.tag == DDOG_PROF_PROFILE_ADD_RESULT_ERR) {
    ddog_CharSlice message = ddog_Error_message(&address.err);
    fprintf(stderr, "%*s", (int)message.len, message.ptr);
    ddog_Error_drop(&address.err);

    std::cout << "Problem flushing sample to profile" << std::endl;
    for (auto el : values) {
      std::cout << el << ", ";
    }
    std::cout << std::endl;
    for (auto el : locations) {
      for (size_t i = 0; i < el.lines.len; ++i) {
        std::cout << "Pulling location " << i + 1 << " of " << el.lines.len << std::endl;
        const ddog_prof_Line *ell = &el.lines.ptr[i];
        std::cout << "  line is " << ell->line << std::endl;
        if (!ell) {
          std::cout << "  Location is null" << std::endl;
        }
        const char *name = ell->function.name.ptr;
        if (!name)
          std::cout << "  name is NULL" << std::endl;
        else
          std::cout << "  name: " << std::string(name) << std::endl;

        const char *sname = ell->function.system_name.ptr;
        if (!sname)
          std::cout << "  sname is NULL" << std::endl;
        else
          std::cout << "  sname: " << std::string() << std::endl;

        const char *filename = ell->function.filename.ptr;
        if (!filename)
          std::cout << "  file is NULL" << std::endl;
        else
          std::cout << "  file: " << std::string(filename) << std::endl;

        std::cout << "  fline: " << ell->function.start_line << std::endl;
        std::cout << std::endl;
      }
    }

    std::cout << "  labels: " << std::endl;
    for (size_t i = 0; i < cur_label; i++) {
      auto &el = labels[i];
      std::cout << "  key: `" << el.key.ptr << "`" << std::endl;
      if (el.str.ptr)
        std::cout << "  val: `" << el.str.ptr << "`" << std::endl;
      else
        std::cout << "  num: `" << el.num << "`" << std::endl;
      std::cout << std::endl;
    }
    clear_buffers();
    std::exit(-1);
    return false;
  }
  clear_buffers();
  ++samples;

  return true;
}

void Datadog::Profile::push_walltime(int64_t walltime, int64_t count) {
  values[2] += walltime * count;
  values[3] += count;
}

void Datadog::Profile::push_cputime(int64_t cputime, int64_t count) {
  values[0] += cputime * count;
  values[1] += count;
}

void Datadog::Profile::push_threadinfo(int64_t thread_id, int64_t thread_native_id, const std::string_view &thread_name) {
  push_label("thread id", thread_id);
  push_label("thread native id", thread_native_id);
  push_label("thread name", thread_name);
}

void Datadog::Profile::push_taskinfo(int64_t task_id, const std::string_view &task_name) {
  push_label("task id", task_id);
  push_label("task name", task_name);
}

void Datadog::Profile::push_spaninfo(int64_t span_id, int64_t local_root_span_id) {
  push_label("span id", span_id);
  push_label("local root span id", local_root_span_id);
}

void Datadog::Profile::push_traceinfo(const std::string_view &trace_type, const std::string_view &trace_resource_container) {
  push_label("trace type", trace_type);
  push_label("trace resource container", trace_resource_container);
}

void Datadog::Profile::push_exceptioninfo(const std::string_view &exception_type, int64_t count) {
  push_label("exception type", exception_type);
  values[4] += count;
}

void Datadog::Profile::push_classinfo(const std::string_view &class_name) {
  push_label("class name", class_name);
}

// C interface
bool is_initialized = false;
Datadog::Uploader *g_uploader;
Datadog::Profile *g_profile;
Datadog::Profile *g_profile_real[2];
bool g_prof_flag;

void uploader_init(const char *service, const char *env, const char *version) {
  if (!is_initialized) {
    g_profile_real[0] = new Datadog::Profile();
    g_profile_real[1] = new Datadog::Profile();
    g_profile = g_profile_real[0];
    g_uploader = new Datadog::Uploader(service, env, version);
    is_initialized = true;
  }
}

void start_sample() {
  g_profile->start_sample();
}

void push_walltime(int64_t walltime, int64_t count){
  g_profile->push_walltime(walltime, count);
}

void push_cputime(int64_t cputime, int64_t count){
  g_profile->push_cputime(cputime, count);
}

void push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char *thread_name){
  if (!thread_name)
    return;
  g_profile->push_threadinfo(thread_id, thread_native_id, thread_name);
}

void push_taskinfo(int64_t task_id, const char *task_name){
  if (!task_name)
    return;
  g_profile->push_taskinfo(task_id, task_name);
}

void push_spaninfo(int64_t span_id, int64_t local_root_span_id){
  g_profile->push_spaninfo(span_id, local_root_span_id);
}

void push_traceinfo(const char *trace_type, const char *trace_resource_container){
  if (!trace_type || !trace_resource_container)
    return;
  g_profile->push_traceinfo(trace_type, trace_resource_container);
}

void push_exceptioninfo(const char *exception_type, int64_t count) {
  if (!exception_type || !count)
    return;
  return;
  g_profile->push_exceptioninfo(exception_type, count);
}

void push_classinfo(const char *class_name) {
  if (!class_name)
    return;
  g_profile->push_classinfo(class_name);

}

void push_frame(const char *_name, const char *_fname, uint64_t address, int64_t line) {
  if (!_name || !*_name)
    _name = "NONAME";

  if (!_fname || !*_fname)
    _fname = "NOFILE";

  // Stash the name
  g_profile->strings.push_back(_name);
  const auto &name = g_profile->strings.back();

  // Stash the filename
  g_profile->strings.push_back(_fname);
  const auto &fname = g_profile->strings.back();

  g_profile->push_frame(name, fname, address, line);
}

void flush_sample() {
  g_profile->flush_sample();
}

void upload_impl(Datadog::Profile *prof) {
  g_uploader->upload(prof);
}

void upload() {
  new std::thread(upload_impl, g_profile); // set it and forget it
  g_profile = g_profile_real[g_prof_flag];
  g_prof_flag ^= true;
  g_profile->zero_stats();
}
