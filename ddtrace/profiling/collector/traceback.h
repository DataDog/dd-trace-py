#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <cstdio>

namespace ddtrace {
namespace profiling {

/// Traceback represents a captured stack trace.
class Traceback {
  public:
    Traceback() : frame_count(0), description("default traceback") {}
    explicit Traceback(size_t initial_count) 
        : frame_count(initial_count), description("traceback with " + std::to_string(initial_count) + " frames") {}
    
    void increment_frames() {
        frame_count++;
    }
    
    size_t size() const {
        return frame_count;
    }
    
    /// Print the traceback description to stdout
    void print_description() const {
        printf("[C++ Traceback] %s (frame_count: %zu)\n", description.c_str(), frame_count);
    }
    
  private:
    size_t frame_count;
    std::string description;
};

/// Factory function to create a new Traceback as a unique_ptr
inline std::unique_ptr<Traceback> new_traceback(size_t initial_count) {
    return std::make_unique<Traceback>(initial_count);
}

} // namespace profiling
} // namespace ddtrace

