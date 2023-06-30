#pragma once

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "Context/Context.h"
#include "Context/GlobalContext.h"
#include "Exceptions/exceptions.h"
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"
#include "absl/container/node_hash_map.h"

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
    unordered_map<size_t, shared_ptr<Context>> contexts;
    static constexpr int TAINTRANGES_STACK_SIZE = 4096;
    static constexpr int TAINTEDOBJECTS_STACK_SIZE = 4096;
    static constexpr int SOURCE_STACK_SIZE = 1024;
    stack<TaintedObjectPtr> available_taintedobjects_stack;
    stack<TaintRangePtr> available_ranges_stack;
    stack<SourcePtr> available_source_stack;
    unordered_set<TaintRangeMapType*> active_map_addreses;
    absl::node_hash_map<size_t, SourcePtr> allocated_sources_map;

  public:
    Initializer();

    TaintRangeMapType* create_tainting_map();

    void free_tainting_map(TaintRangeMapType* tx_map);

    static TaintRangeMapType* get_tainting_map();

    void clear_tainting_maps();

    static int num_objects_tainted();

    shared_ptr<Context> create_context();

    void destroy_context();

    shared_ptr<Context> get_context(size_t tx_id = 0);

    void contexts_reset();

    static size_t context_id();

    // FIXME: these should be static functions of TaintedObject

    // IMPORTANT: if the returned object is not assigned to the map, you have
    // responsibility of calling release_tainted_object on it or you'll have a
    // leak.
    TaintedObjectPtr allocate_tainted_object();

    TaintedObjectPtr allocate_tainted_object(TaintRangeRefs ranges)
    {
        auto toptr = allocate_tainted_object();
        toptr->set_values(move(ranges));
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
        return allocate_tainted_object(move(from->ranges_));
    }

    TaintedObjectPtr allocate_tainted_object_copy(const TaintedObjectPtr& from)
    {
        if (!from) {
            return allocate_tainted_object();
        }
        return allocate_tainted_object_copy(from->ranges_);
    }

    // void release_tainted_object(TaintedObjectPtr tobj);

    // FIXME: these should be static functions of TaintRange
    // IMPORTANT: if the returned object is not assigned to the map, you have
    // responsibility of calling release_taint_range on it or you'll have a leak.
    TaintRangePtr allocate_taint_range(int start, int length, SourcePtr source);
    static SourcePtr reuse_taint_source(SourcePtr source);

    void release_taint_range(TaintRangePtr rangeptr);

    // IMPORTANT: if the returned object is not assigned to a range, you have
    // responsibility of calling release_taint_source on it or you'll have a leak.
    SourcePtr allocate_taint_source(string, string, OriginType);

    void release_taint_source(SourcePtr);
};

extern unique_ptr<Initializer> initializer;

void
pyexport_initializer(py::module& m);
