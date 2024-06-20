#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"

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
    // This is a map instead of a set so we can change the contents on iteration; otherwise
    // keys and values are the same pointer.
    unordered_map<TaintRangeMapType*, TaintRangeMapTypePtr> active_map_addreses;

  public:
    /**
     * Constructor for the Initializer class.
     */
    Initializer();

    /**
     * Creates a new taint range map.
     *
     * @return A pointer to the created taint range map.
     */
    TaintRangeMapTypePtr create_tainting_map();

    /**
     * Clears a taint range map.
     *
     * @param tx_map The taint range map to be freed.
     */
    void clear_tainting_map(const TaintRangeMapTypePtr& tx_map);

    /**
     * Gets the current taint range map.
     *
     * @return A pointer to the current taint range map.
     */
    static TaintRangeMapTypePtr get_tainting_map();

    /**
     * Clears all active taint maps.
     */
    void clear_tainting_maps();

    /**
     * Gets the number of tainted objects.
     *
     * @return The number of tainted objects.
     */
    static int num_objects_tainted();

    static string debug_taint_map();

    /**
     * Gets the size of the Initializer object.
     *
     * @return The size of the Initializer object.
     */
    int initializer_size() const;

    /**
     * Gets the size of active map addresses.
     *
     * @return The size of active map addresses.
     */
    int active_map_addreses_size() const;

    /**
     * Creates a new taint tracking context.
     */
    void create_context();

    /**
     * Resets the current taint tracking context.
     */
    void reset_context();

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

    void release_tainted_object(TaintedObjectPtr tobj);

    // FIXME: these should be static functions of TaintRange
    // IMPORTANT: if the returned object is not assigned to the map, you have
    // responsibility of calling release_taint_range on it or you'll have a leak.
    TaintRangePtr allocate_taint_range(RANGE_START start, RANGE_LENGTH length, const Source& source);

    void release_taint_range(TaintRangePtr rangeptr);
};

extern unique_ptr<Initializer> initializer;

void
pyexport_initializer(py::module& m);
