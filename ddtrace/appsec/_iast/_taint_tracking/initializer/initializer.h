#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "taint_tracking/taint_range.h"
#include "taint_tracking/tainted_object.h"

#include <mutex>
#include <stack>
#include <unordered_map>

using namespace std;

namespace py = pybind11;

class Initializer
{
  private:
    py::object pyfunc_get_settings;
    py::object pyfunc_get_python_lib;
    static constexpr int TAINTRANGES_STACK_SIZE = 4096;
    static constexpr int TAINTEDOBJECTS_STACK_SIZE = 4096;
    stack<TaintedObjectPtr> available_taintedobjects_stack;
    stack<TaintRangePtr> available_ranges_stack;

  public:
    /**
     * Constructor for the Initializer class.
     */
    Initializer();
    /**
     * Allocates a new tainted object.

     * IMPORTANT: if the returned object is not assigned to the map, you have responsibility of calling
     * release_tainted_object on it or you'll have a leak.
     *
     * IMPORTANT2: allocate_ranges_into_taint_object moves the owner of the ranges, if you know the ranges of the
     original
     * tainted object should be used more times, use instead allocate_ranges_into_taint_object_copy.
     *
     * @return A pointer to the allocated tainted object.
     */
    TaintedObjectPtr allocate_tainted_object();

    /**
     * Allocates taint ranges into new tainted object.
     *
     * @param ranges The taint ranges to assign to the allocated object.
     * @return A pointer to the allocated tainted object.
     */
    TaintedObjectPtr allocate_ranges_into_taint_object(TaintRangeRefs ranges);

    /**
     * Allocates and copy taint ranges into new tainted object.
     *
     * @param ranges The taint ranges to assign to the allocated object.
     * @return A pointer to the allocated tainted object.
     */
    TaintedObjectPtr allocate_ranges_into_taint_object_copy(const TaintRangeRefs& ranges);

    /**
     * Allocates and copy taint ranges from a Tainted Object into new tainted object.
     *
     * @param from The existing tainted object to copy.
     * @return A pointer to the allocated tainted object.
     */
    TaintedObjectPtr allocate_tainted_object_copy(const TaintedObjectPtr& from);

    // FIXME: these should be static functions of TaintRange
    // IMPORTANT: if the returned object is not assigned to the map, you have
    // responsibility of calling release_taint_range on it or you'll have a leak.
    TaintRangePtr allocate_taint_range(RANGE_START start,
                                       RANGE_LENGTH length,
                                       const Source& source,
                                       const SecureMarks secure_marks = 0);

    void release_taint_range(TaintRangePtr rangeptr);
};

extern unique_ptr<Initializer> initializer;
