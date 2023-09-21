#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"

#include <stack>
#include <unordered_map>
#include <unordered_set>

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
    unordered_set<TaintRangeMapType*> active_map_addreses;

  public:
    Initializer();

    TaintRangeMapType* create_tainting_map();

    void free_tainting_map(TaintRangeMapType* tx_map);

    static TaintRangeMapType* get_tainting_map();

    void clear_tainting_maps();

    static int num_objects_tainted();

    int initializer_size();

    int active_map_addreses_size();

    void create_context();

    void destroy_context();

    void reset_context();

    static size_t context_id();

    // FIXME: these should be static functions of TaintedObject

    // IMPORTANT: if the returned object is not assigned to the map, you have
    // responsibility of calling release_tainted_object on it or you'll have a
    // leak.
    TaintedObjectPtr allocate_tainted_object();

    TaintedObjectPtr allocate_tainted_object(TaintRangeRefs ranges)
    {
        auto toptr = allocate_tainted_object();
        toptr->set_values(std::move(ranges));
        return toptr;
    }

    TaintedObjectPtr allocate_tainted_object_copy(const TaintRangeRefs& ranges)
    {
        auto toptr = allocate_tainted_object();
        toptr->copy_values(ranges);
        return toptr;
    }

    TaintedObjectPtr allocate_tainted_object(TaintedObjectPtr from)
    {
        if (!from) {
            return allocate_tainted_object();
        }
        return allocate_tainted_object(std::move(from->ranges_));
    }

    TaintedObjectPtr allocate_tainted_object_copy(const TaintedObjectPtr& from)
    {
        if (!from) {
            return allocate_tainted_object();
        }
        return allocate_tainted_object_copy(from->ranges_);
    }

    void release_tainted_object(TaintedObjectPtr tobj);

    // FIXME: these should be static functions of TaintRange
    // IMPORTANT: if the returned object is not assigned to the map, you have
    // responsibility of calling release_taint_range on it or you'll have a leak.
    TaintRangePtr allocate_taint_range(int start, int length, Source source);

    void release_taint_range(TaintRangePtr rangeptr);
};

extern unique_ptr<Initializer> initializer;

void
pyexport_initializer(py::module& m);
