// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <ostream>
#include <string_view>

#include <echion/config.h>
#include <echion/mojo.h>
#include <echion/timing.h>
#include <echion/errors.h>

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
    virtual void frame(mojo_ref_t key, mojo_ref_t filename, mojo_ref_t name, mojo_int_t line,
                       mojo_int_t line_end, mojo_int_t column, mojo_int_t column_end) = 0;
    // Refers to the frame stored using above function
    virtual void frame_ref(mojo_ref_t key) = 0;
    virtual void frame_kernel(const std::string& scope) = 0;
    // Simlar to frame/frame_ref functions, helpers for string tables
    virtual void string(mojo_ref_t key, const std::string& value) = 0;
    virtual void string_ref(mojo_ref_t key) = 0;

    virtual void render_message(std::string_view msg) = 0;
    virtual void render_thread_begin(PyThreadState* tstate, std::string_view name,
                                     microsecond_t cpu_time, uintptr_t thread_id,
                                     unsigned long native_id) = 0;
    virtual void render_task_begin(std::string task_name, bool on_cpu) = 0;
    virtual void render_stack_begin(long long pid, long long iid,
                                    const std::string& thread_name) = 0;
    virtual void render_frame(Frame& frame) = 0;
    virtual void render_cpu_time(uint64_t cpu_time) = 0;
    virtual void render_stack_end(MetricType metric_type, uint64_t delta) = 0;

    // The validity of the interface is a two-step process
    // 1. If the RendererInterface has been destroyed, obviously it's invalid
    // 2. There might be state behind RendererInterface, and the lifetime of that
    //    state alone may be insufficient to know its usability.  is_valid
    //    should return false in such cases.
    virtual bool is_valid() = 0;
    virtual ~RendererInterface() = default;
};

class WhereRenderer : public RendererInterface
{
private:
    std::ostream* output;
    std::ofstream file_stream;

    WhereRenderer() {}
    ~WhereRenderer() {}

public:
    static WhereRenderer& get()
    {
        static WhereRenderer instance;
        return instance;
    }

    WhereRenderer(WhereRenderer&) = delete;
    WhereRenderer(WhereRenderer&&) = delete;
    void operator=(const WhereRenderer&) = delete;

    bool set_output(std::string_view file_name)
    {
        file_stream.close();
        file_stream.open(file_name.data(), std::ios::out);
        if (file_stream.is_open())
        {
            output = &file_stream;
            return true;
        }
        return false;
    }

    bool set_output(std::ostream& new_output)
    {
        file_stream.close();
        output = &new_output;
        return true;
    }

    [[nodiscard]] Result<void> open() override { return Result<void>::ok(); };
    void close() override {};
    void header() override {};
    void metadata(const std::string&, const std::string&) override {};
    void frame(mojo_ref_t, mojo_ref_t, mojo_ref_t, mojo_int_t, mojo_int_t, mojo_int_t, mojo_int_t) override {};
    void frame_ref(mojo_ref_t) override {};
    void frame_kernel(const std::string&) override {};
    void string(mojo_ref_t, const std::string&) override {};
    void string_ref(mojo_ref_t) override {};

    void render_thread_begin(PyThreadState*, std::string_view name, microsecond_t, uintptr_t, unsigned long) override
    {
        *output << "    🧵 " << name << ":" << std::endl;
    }
    void render_task_begin(std::string, bool) override {}
    void render_stack_begin(long long, long long, const std::string&) override {}
    void render_message(std::string_view msg) override
    {
        *output << msg << std::endl;
    }
    void render_frame(Frame&) override;
    void render_stack_end(MetricType, uint64_t) override {}
    void render_cpu_time(uint64_t) override {}

    bool is_valid() override
    {
        return true;
    }
};

class MojoRenderer : public RendererInterface
{
    std::ofstream output;
    std::mutex lock;
    uint64_t metric = 0;

    void inline event(MojoEvent event)
    {
        output.put((char)event);
    }
    void inline string(const std::string& string)
    {
        output << string << '\0';
    }
    void inline string(const char* string)
    {
        output << string << '\0';
    }
    void inline ref(mojo_ref_t value)
    {
        integer(value);
    }
    void inline integer(mojo_int_t n)
    {
        mojo_uint_t integer = n < 0 ? -n : n;
        bool sign = n < 0;

        unsigned char byte = integer & 0x3f;
        if (sign)
            byte |= 0x40;

        integer >>= 6;
        if (integer)
            byte |= 0x80;

        output.put(byte);

        while (integer)
        {
            byte = integer & 0x7f;
            integer >>= 7;
            if (integer)
                byte |= 0x80;
            output.put(byte);
        }
    }

public:
    MojoRenderer() = default;

    [[nodiscard]] Result<void> open() override
    {
        output.open(std::getenv("ECHION_OUTPUT"));
        if (!output.is_open())
        {
            std::cerr << "Failed to open output file " << std::getenv("ECHION_OUTPUT") << std::endl;
            return ErrorKind::RendererError;
        }

        return Result<void>::ok();
    }

    // ------------------------------------------------------------------------
    void close() override
    {
        std::lock_guard<std::mutex> guard(lock);

        output.flush();
        output.close();
    }

    // ------------------------------------------------------------------------
    void inline header() override
    {
        std::lock_guard<std::mutex> guard(lock);

        output << "MOJ";
        integer(MOJO_VERSION);
    }

    // ------------------------------------------------------------------------
    void inline metadata(const std::string& label, const std::string& value) override
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_METADATA);
        string(label);
        string(value);
    }

    // ------------------------------------------------------------------------
    void inline stack(mojo_int_t pid, mojo_int_t iid, const std::string& thread_name)
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_STACK);
        integer(pid);
        integer(iid);
        string(thread_name);
    }

    // ------------------------------------------------------------------------
    void inline frame(mojo_ref_t key, mojo_ref_t filename, mojo_ref_t name, mojo_int_t line,
                      mojo_int_t line_end, mojo_int_t column, mojo_int_t column_end) override
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_FRAME);
        ref(key);
        ref(filename);
        ref(name);
        integer(line);
        integer(line_end);
        integer(column);
        integer(column_end);
    }

    // ------------------------------------------------------------------------
    void inline frame_ref(mojo_ref_t key) override
    {
        std::lock_guard<std::mutex> guard(lock);

        if (key == 0)
        {
            event(MOJO_FRAME_INVALID);
        }
        else
        {
            event(MOJO_FRAME_REF);
            ref(key);
        }
    }

    // ------------------------------------------------------------------------
    void inline frame_kernel(const std::string& scope) override
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_FRAME_KERNEL);
        string(scope);
    }

    // ------------------------------------------------------------------------
    void inline metric_time(mojo_int_t value)
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_METRIC_TIME);
        integer(value);
    }

    // ------------------------------------------------------------------------
    void inline metric_memory(mojo_int_t value)
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_METRIC_MEMORY);
        integer(value);
    }

    // ------------------------------------------------------------------------
    void inline string(mojo_ref_t key, const std::string& value) override
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_STRING);
        ref(key);
        string(value);
    }

    // ------------------------------------------------------------------------
    void inline string_ref(mojo_ref_t key) override
    {
        std::lock_guard<std::mutex> guard(lock);

        event(MOJO_STRING_REF);
        ref(key);
    }

    void render_message(std::string_view) override {};
    void render_thread_begin(PyThreadState*, std::string_view, microsecond_t, uintptr_t, unsigned long) override {};
    void render_task_begin(std::string, bool) override {};
    void render_stack_begin(long long pid, long long iid, const std::string& name) override
    {
        stack(pid, iid, name);
    };
    void render_frame(Frame& frame) override;
    void render_cpu_time(uint64_t cpu_time) override
    {
        metric = cpu_time;
    };
    void render_stack_end(MetricType metric_type, uint64_t delta) override
    {
        if (metric_type == MetricType::Time)
        {
            metric_time(cpu ? metric : delta);
        }
        else if (metric_type == MetricType::Memory)
        {
            metric_memory(delta);
        }
    };
    bool is_valid() override
    {
        return true;
    }
};

class Renderer
{
private:
    std::shared_ptr<RendererInterface> default_renderer = std::make_shared<MojoRenderer>();
    std::weak_ptr<RendererInterface> currentRenderer;

    std::shared_ptr<RendererInterface> getActiveRenderer()
    {
        if (auto renderer = currentRenderer.lock())
        {
            if (renderer->is_valid())
            {
                return renderer;
            }
        }
        return default_renderer;
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

    void set_renderer(std::shared_ptr<RendererInterface> renderer)
    {
        currentRenderer = renderer;
    }

    void header()
    {
        getActiveRenderer()->header();
    }

    void metadata(const std::string& label, const std::string& value)
    {
        getActiveRenderer()->metadata(label, value);
    }

    void string(mojo_ref_t key, const std::string& value)
    {
        getActiveRenderer()->string(key, value);
    }

    void frame(mojo_ref_t key, mojo_ref_t filename, mojo_ref_t name, mojo_int_t line,
               mojo_int_t line_end, mojo_int_t column, mojo_int_t column_end)
    {
        getActiveRenderer()->frame(key, filename, name, line, line_end, column, column_end);
    }

    void frame_ref(mojo_ref_t key)
    {
        getActiveRenderer()->frame_ref(key);
    }

    void frame_kernel(const std::string& scope)
    {
        getActiveRenderer()->frame_kernel(scope);
    }

    void string(mojo_ref_t key, const char* value)
    {
        getActiveRenderer()->string(key, value);
    }

    void string_ref(mojo_ref_t key)
    {
        getActiveRenderer()->string_ref(key);
    }

    void render_message(std::string_view msg)
    {
        getActiveRenderer()->render_message(msg);
    }

    [[nodiscard]] Result<void> open()
    {
        return getActiveRenderer()->open();
    }

    void close()
    {
        getActiveRenderer()->close();
    }

    void render_thread_begin(PyThreadState* tstate, std::string_view name, microsecond_t cpu_time,
                             uintptr_t thread_id, unsigned long native_id)
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

    void render_frame(Frame& frame)
    {
        getActiveRenderer()->render_frame(frame);
    }

    void render_cpu_time(uint64_t cpu_time)
    {
        getActiveRenderer()->render_cpu_time(cpu_time);
    }

    void render_stack_end(MetricType metric_type, uint64_t delta)
    {
        getActiveRenderer()->render_stack_end(metric_type, delta);
    }
};
