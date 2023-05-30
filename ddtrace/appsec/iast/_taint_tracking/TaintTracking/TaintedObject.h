#ifndef _NATIVE_TAINTEDOBJECT_H
#define _NATIVE_TAINTEDOBJECT_H
#include "TaintTracking/TaintRange.h"
#include <Python.h>

class TaintedObject
{
    friend class Initializer;

  private:
    TaintRangeRefs ranges_;
    size_t rc_{};

  public:
    constexpr static int TAINT_RANGE_LIMIT = 100;
    constexpr static int RANGES_INITIAL_RESERVE = 16;

    TaintedObject() { ranges_.reserve(RANGES_INITIAL_RESERVE); };

    TaintedObject& operator=(const TaintedObject&) = delete;

    inline void set_values(TaintRangeRefs ranges)
    {
        // Move back the ranges to the ranges stack
        move_ranges_to_stack();
        ranges_ = move(ranges);
    }

    inline void copy_values(const TaintRangeRefs& ranges)
    {
        // Move back the ranges to the ranges stack
        move_ranges_to_stack();
        ranges_ = ranges;
    }

    [[nodiscard]] const TaintRangeRefs& get_ranges() const { return ranges_; }

    [[nodiscard]] TaintRangeRefs get_ranges_copy() const { return ranges_; }

    void add_ranges_shifted(TaintedObject* tainted_object, long offset);

    explicit operator string();

    void move_ranges_to_stack();

    void reset();

    void incref();

    void decref();

    void release();
};

void
pyexport_taintedobject(py::module& m);

#endif //_NATIVE_TAINTEDOBJECT_H
