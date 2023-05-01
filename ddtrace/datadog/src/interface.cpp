// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

// High-level skip for invalid architectures
#ifndef __linux__
#elif __aarch64__
#elif __i386__
#else

#include "exporter.hpp"

#include <iostream>
#include <thread>

// C interface
bool is_initialized = false;
Datadog::Uploader *g_uploader;
Datadog::Profile *g_profile;
Datadog::Profile *g_profile_real[2];
bool g_prof_flag = true;

void ddup_uploader_init(const char *service, const char *env, const char *version, const char *runtime, const char *runtime_version, const char *profiler_version) {
  if (!is_initialized) {
    g_profile_real[0] = new Datadog::Profile(Datadog::Profile::ProfileType::All);
    g_profile_real[1] = new Datadog::Profile(Datadog::Profile::ProfileType::All);
    g_profile = g_profile_real[g_prof_flag];
    g_uploader = new Datadog::Uploader(env, service, version, runtime, runtime_version, profiler_version);
    is_initialized = true;
  }
}

void ddup_start_sample() {
  g_profile->start_sample();
}

void ddup_push_walltime(int64_t walltime, int64_t count){
  g_profile->push_walltime(walltime, count);
}

void ddup_push_cputime(int64_t cputime, int64_t count){
  g_profile->push_cputime(cputime, count);
}

void ddup_push_acquire(int64_t acquire_time, int64_t count) {
  g_profile->push_acquire(acquire_time, count);
}

void ddup_push_release(int64_t release_time, int64_t count) {
  g_profile->push_release(release_time, count);
}

void ddup_push_alloc(int64_t alloc_size, int64_t count) {
  g_profile->push_alloc(alloc_size, count);
}

void ddup_push_heap(int64_t heap_size) {
  g_profile->push_heap(heap_size);
}

void ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char *thread_name){
  if (!thread_name)
    return;
  g_profile->push_threadinfo(thread_id, thread_native_id, thread_name);
}

void ddup_push_taskinfo(int64_t task_id, const char *task_name){
  if (!task_name)
    return;
  g_profile->push_taskinfo(task_id, task_name);
}

void ddup_push_spaninfo(int64_t span_id, int64_t local_root_span_id){
  g_profile->push_spaninfo(span_id, local_root_span_id);
}

void ddup_push_traceinfo(const char *trace_type, const char *trace_resource_container){
  if (!trace_type || !trace_resource_container)
    return;
  g_profile->push_traceinfo(trace_type, trace_resource_container);
}

void ddup_push_exceptioninfo(const char *exception_type, int64_t count) {
  if (!exception_type || !count)
    return;
  return;
  g_profile->push_exceptioninfo(exception_type, count);
}

void ddup_push_classinfo(const char *class_name) {
  if (!class_name)
    return;
  g_profile->push_classinfo(class_name);

}

void ddup_push_frame(const char *name, const char *fname, uint64_t address, int64_t line) {
  auto insert_or_get = [&](const char *str) -> const std::string & {
    auto [it, _] = g_profile->strings.insert(str ? str : "");
    return *it;
  };

  g_profile->push_frame(insert_or_get(name), insert_or_get(fname), address, line);
}

void ddup_flush_sample() {
  g_profile->flush_sample();
}

void ddup_set_runtime_id(const char *id) {
  g_uploader->set_runtime_id(id);
}

void ddup_upload_impl(Datadog::Profile *prof) {
  g_uploader->upload(prof);
}

void ddup_upload() {
  if (!is_initialized)
    std::cout << "WHOA NOT INITIALIZED" << std::endl;
  new std::thread(ddup_upload_impl, g_profile); // set it and forget it
  g_prof_flag ^= true;
  g_profile = g_profile_real[g_prof_flag];
  g_profile->reset();
}

#endif
