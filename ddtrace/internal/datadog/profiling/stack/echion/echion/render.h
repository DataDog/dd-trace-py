// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

#include <echion/config.h>
#include <echion/errors.h>
#include <echion/timing.h>

#include <Python.h>

// Forward declaration
class Frame;

enum MetricType
{
    Time,
    Memory
};

class RendererInterface
{
  public:
    [[nodiscard]] virtual Result<void> open() = 0;
    virtual void close() = 0;
    virtual void header() = 0;
    virtual void metadata(const std::string& label, const std::string& value) = 0;

    // If a renderer has its own caching mechanism for frames, this can be used
    // to store frame information.
    virtual void
    frame(uintptr_t key, uintptr_t filename, uintptr_t name, int line, int line_end, int column, int column_end) = 0;

    // Refers to the frame stored using the renderer's frame function
    virtual void frame_ref(uintptr_t key) = 0;
    virtual void frame_kernel(const std::string& scope) = 0;

    // If a renderer has its own caching mechanism for strings, this can be used
    // to store string information.
    virtual void string(uintptr_t key, const std::string& value) = 0;

    // Refers to the string stored using the renderer's string function
    virtual void string_ref(uintptr_t key) = 0;

    // Called to render a message from the profiler.
    virtual void render_message(std::string_view msg) = 0;

    // Called once for each Thread being sampled.
    // Pushes the Thread state but not its current Stack(s).
    virtual void render_thread_begin(PyThreadState* tstate,
                                     std::string_view name,
                                     microsecond_t cpu_time,
                                     uintptr_t thread_id,
                                     unsigned long native_id) = 0;

    // Called once for each Task being sampled on the Thread.
    // Called after render_thread_begin and before render_stack_begin.
    virtual void render_task_begin(std::string task_name, bool on_cpu) = 0;

    // Called once for each Stack being sampled on the Task.
    // Called after render_task_begin and before render_frame.
    virtual void render_stack_begin(long long pid, long long iid, const std::string& thread_name) = 0;

    // Called once for each Frame being sampled on the Stack.
    // Called after render_stack_begin and before render_stack_end.
    virtual void render_frame(Frame& frame) = 0;

    // Called once for each CPU time being sampled on the Thread.
    // Called after render_frame and before render_stack_end.
    virtual void render_cpu_time(uint64_t cpu_time) = 0;

    // Called once for each Stack being sampled on the Thread.
    // Called after render_stack_begin, all render_frame calls and all render_cpu_time calls.
    virtual void render_stack_end(MetricType metric_type, uint64_t delta) = 0;

    // The validity of the interface is a two-step process
    // 1. If the RendererInterface has been destroyed, obviously it's invalid
    // 2. There might be state behind RendererInterface, and the lifetime of that
    //    state alone may be insufficient to know its usability.  is_valid
    //    should return false in such cases.
    virtual bool is_valid() = 0;
    virtual ~RendererInterface() = default;
};

class NullRenderer : public RendererInterface
{
  public:
    bool is_valid() override { return true; }
    void header() override {}
    void metadata(const std::string&, const std::string&) override {}
    void frame(uintptr_t, uintptr_t, uintptr_t, int, int, int, int) override {}
    void frame_ref(uintptr_t) override {}
    void frame_kernel(const std::string&) override {}

    void string(uintptr_t, const std::string&) override {}
    void string_ref(uintptr_t) override {}
    void render_message(std::string_view) override {}
    void render_thread_begin(PyThreadState*, std::string_view, microsecond_t, uintptr_t, unsigned long) override {}
    void render_task_begin(std::string, bool) override {}
    void render_stack_begin(long long, long long, const std::string&) override {}
    void render_frame(Frame&) override {}
    void render_cpu_time(uint64_t) override {}
    void render_stack_end(MetricType, uint64_t) override {}

    Result<void> open() override { return Result<void>::ok(); }
    void close() override {}
};

class Renderer
{
  private:
    std::shared_ptr<RendererInterface> defaultRenderer = std::make_shared<NullRenderer>();
    std::weak_ptr<RendererInterface> currentRenderer;

    std::shared_ptr<RendererInterface> getActiveRenderer()
    {
        if (auto renderer = currentRenderer.lock()) {
            if (renderer->is_valid()) {
                return renderer;
            }
        }

        return defaultRenderer;
    }

    Renderer() = default;
    ~Renderer() = default;

  public:
    Renderer(const Renderer&) = delete;
    Renderer& operator=(const Renderer&) = delete;

    static Renderer& get()
    {
        static Renderer instance;
        return instance;
    }

    void set_renderer(std::shared_ptr<RendererInterface> renderer) { currentRenderer = renderer; }

    void header() { getActiveRenderer()->header(); }

    void metadata(const std::string& label, const std::string& value) { getActiveRenderer()->metadata(label, value); }

    void string(uintptr_t key, const std::string& value) { getActiveRenderer()->string(key, value); }

    void frame(uintptr_t key, uintptr_t filename, uintptr_t name, int line, int line_end, int column, int column_end)
    {
        getActiveRenderer()->frame(key, filename, name, line, line_end, column, column_end);
    }

    void frame_ref(uintptr_t key) { getActiveRenderer()->frame_ref(key); }

    void frame_kernel(const std::string& scope) { getActiveRenderer()->frame_kernel(scope); }

    void string(uintptr_t key, const char* value) { getActiveRenderer()->string(key, value); }

    void string_ref(uintptr_t key) { getActiveRenderer()->string_ref(key); }

    void render_message(std::string_view msg) { getActiveRenderer()->render_message(msg); }

    [[nodiscard]] Result<void> open() { return getActiveRenderer()->open(); }

    void close() { getActiveRenderer()->close(); }

    void render_thread_begin(PyThreadState* tstate,
                             std::string_view name,
                             microsecond_t cpu_time,
                             uintptr_t thread_id,
                             unsigned long native_id)
    {
        getActiveRenderer()->render_thread_begin(tstate, name, cpu_time, thread_id, native_id);
    }

    void render_task_begin(std::string task_name, bool on_cpu)
    {
        getActiveRenderer()->render_task_begin(task_name, on_cpu);
    }

    void render_stack_begin(long long pid, long long iid, const std::string& thread_name)
    {
        getActiveRenderer()->render_stack_begin(pid, iid, thread_name);
    }

    void render_frame(Frame& frame) { getActiveRenderer()->render_frame(frame); }

    void render_cpu_time(uint64_t cpu_time) { getActiveRenderer()->render_cpu_time(cpu_time); }

    void render_stack_end(MetricType metric_type, uint64_t delta)
    {
        getActiveRenderer()->render_stack_end(metric_type, delta);
    }
};
