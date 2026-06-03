#pragma once
#include "taint_tracking/taint_range.h"
#include <Python.h>

// Helper function to get max range count from environment variable
int
get_taint_range_limit();

// Reset the cached taint range limit (for testing purposes only)
void
reset_taint_range_limit_cache();

class TaintedObject
{
    friend class Initializer;

  private:
    TaintRangeRefs ranges_;

  public:
    constexpr static int RANGES_INITIAL_RESERVE = 16;

    TaintedObject() { ranges_.reserve(RANGES_INITIAL_RESERVE); };

    TaintedObject& operator=(const TaintedObject&) = delete;

    inline void set_values(TaintRangeRefs ranges)
    {
        // Move back the ranges to the ranges stack
        move_ranges_to_stack();
        ranges_ = std::move(ranges);
    }

    inline void copy_values(const TaintRangeRefs& ranges)
    {
        // Move back the ranges to the ranges stack
        move_ranges_to_stack();
        ranges_ = ranges;
    }

    [[nodiscard]] const TaintRangeRefs& get_ranges() const { return ranges_; }

    [[nodiscard]] TaintRangeRefs get_ranges_copy() const { return ranges_; }

    [[nodiscard]] bool has_free_tainted_ranges_space() const
    {
        const int range_limit = get_taint_range_limit();
        return ranges_.size() < static_cast<size_t>(range_limit);
    }

    [[nodiscard]] size_t get_free_tainted_ranges_space() const
    {
        const int range_limit = get_taint_range_limit();
        if (ranges_.size() >= static_cast<size_t>(range_limit)) {
            return 0;
        }
        return static_cast<size_t>(range_limit) - ranges_.size();
    }

    void add_ranges_shifted(TaintedObjectPtr tainted_object,
                            RANGE_START offset,
                            RANGE_LENGTH max_length = -1,
                            RANGE_START orig_offset = -1);

    void add_ranges_shifted(TaintRangeRefs ranges,
                            RANGE_START offset,
                            RANGE_LENGTH max_length = -1,
                            RANGE_START orig_offset = -1);

    std::string toString() const;

    explicit operator string() const;

    void move_ranges_to_stack();

    void reset();
};
