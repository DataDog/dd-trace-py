#include "Initializer.h"

#include <thread>

using namespace std;
using namespace pybind11::literals;

thread_local struct ThreadContextCache_
{
    size_t tx_id = 0;
} ThreadContextCache;

Initializer::Initializer()
{
    // Fill the taintedobjects stack
    for (int i = 0; i < TAINTEDOBJECTS_STACK_SIZE; i++) {
        available_taintedobjects_stack.push(new TaintedObject());
    }

    // Fill the ranges stack
    for (int i = 0; i < TAINTRANGES_STACK_SIZE; i++) {
        available_ranges_stack.push(make_shared<TaintRange>());
    }
}

TaintRangeMapType*
Initializer::create_tainting_map()
{
    auto map_ptr = new TaintRangeMapType();
    active_map_addreses.insert(map_ptr);
    return map_ptr;
}

void
Initializer::free_tainting_map(TaintRangeMapType* tx_map)
{
    if (not tx_map)
        return;

    auto it = active_map_addreses.find(tx_map);
    if (it == active_map_addreses.end()) {
        // Map wasn't in the set, do nothing
        return;
    }

    for (auto& kv_taint_map : *tx_map) {
        kv_taint_map.second->decref();
    }

    tx_map->clear();
    delete tx_map;
    active_map_addreses.erase(it);
}

// User must check for nullptr return
TaintRangeMapType*
Initializer::get_tainting_map()
{
    return (TaintRangeMapType*)ThreadContextCache.tx_id;
}

void
Initializer::clear_tainting_maps()
{
    // Need to copy because free_tainting_map changes the set inside the iteration
    auto map_addresses_copy = initializer->active_map_addreses;
    for (auto map_ptr : map_addresses_copy) {
        free_tainting_map((TaintRangeMapType*)map_ptr);
    }
    active_map_addreses.clear();
}

int
Initializer::num_objects_tainted()
{
    auto ctx_map = initializer->get_tainting_map();
    if (ctx_map) {
        return ctx_map->size();
    }
    return 0;
}

int
Initializer::initializer_size()
{
    return sizeof(*this);
}

int
Initializer::active_map_addreses_size()
{
    return active_map_addreses.size();
}

TaintedObjectPtr
Initializer::allocate_tainted_object()
{
    if (!available_taintedobjects_stack.empty()) {
        const auto& toptr = available_taintedobjects_stack.top();
        available_taintedobjects_stack.pop();
        return toptr;
    }
    // Stack is empty, create new object
    return new TaintedObject();
}

void
Initializer::release_tainted_object(TaintedObjectPtr tobj)
{
    if (!tobj) {
        return;
    }

    tobj->reset();
    if (available_taintedobjects_stack.size() < TAINTEDOBJECTS_STACK_SIZE) {
        available_taintedobjects_stack.push(tobj);
        return;
    }

    // Stack full, just delete the object (but to a reset before so ranges are
    // reused or freed)
    delete tobj;
}

TaintRangePtr
Initializer::allocate_taint_range(int start, int length, Source origin)
{
    if (!available_ranges_stack.empty()) {
        auto rptr = available_ranges_stack.top();
        available_ranges_stack.pop();
        rptr->set_values(start, length, origin);
        return rptr;
    }

    // Stack is empty, create new object
    return make_shared<TaintRange>(start, length, origin);
}

void
Initializer::release_taint_range(TaintRangePtr rangeptr)
{
    if (!rangeptr)
        return;

    if (rangeptr.use_count() == 1) {
        rangeptr->reset();
        if (available_ranges_stack.size() < TAINTRANGES_STACK_SIZE) {
            // Move the range to the allocated ranges stack
            available_ranges_stack.push(rangeptr);
            return;
        }

        // Stack full or initializer already cleared (interpreter finishing), just
        // release the object
        rangeptr.reset(); // Not duplicated or typo, calling reset on the shared_ptr, not the TaintRange
    }
}

void
Initializer::create_context()
{
    if (ThreadContextCache.tx_id != 0) {
        // Destroy the current context
        destroy_context();
    }

    // Create a new taint_map
    auto map_ptr = create_tainting_map();
    ThreadContextCache.tx_id = (size_t)map_ptr;
}

void
Initializer::destroy_context()
{
    free_tainting_map((TaintRangeMapType*)ThreadContextCache.tx_id);
    ThreadContextCache.tx_id = 0;
}

size_t
Initializer::context_id()
{
    return ThreadContextCache.tx_id;
}

void
Initializer::reset_context()
{
    //    lock_guard<recursive_mutex> lock(contexts_mutex);
    ThreadContextCache.tx_id = 0;
    clear_tainting_maps();
}

// Created in the PYBIND11_MODULE in _native.cpp
unique_ptr<Initializer> initializer;

void
pyexport_initializer(py::module& m)
{
    m.def("clear_tainting_maps", [] { initializer->clear_tainting_maps(); });

    m.def("num_objects_tainted", [] { return initializer->num_objects_tainted(); });
    m.def("initializer_size", [] { return initializer->initializer_size(); });
    m.def("active_map_addreses_size", [] { return initializer->active_map_addreses_size(); });

    m.def(
      "create_context", []() { return initializer->create_context(); }, py::return_value_policy::reference);
    m.def("reset_context", [] { initializer->reset_context(); });
    m.def("destroy_context", [] { initializer->destroy_context(); });
}
