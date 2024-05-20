#include "Initializer.h"

#include <thread>

using namespace std;
using namespace pybind11::literals;

thread_local struct ThreadContextCache_
{
    TaintRangeMapTypePtr tx_map = nullptr;
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

TaintRangeMapTypePtr
Initializer::create_tainting_map()
{
    auto map_ptr = make_shared<TaintRangeMapType>();
    active_map_addreses[map_ptr.get()] = map_ptr;
    return map_ptr;
}

void
Initializer::clear_tainting_map(const TaintRangeMapTypePtr& tx_map)
{
    if (not tx_map or tx_map->empty())
        return;

    if (const auto it = active_map_addreses.find(tx_map.get()); it == active_map_addreses.end()) {
        // Map wasn't in the active addresses, do nothing
        return;
    }

    for (const auto& [fst, snd] : *tx_map) {
        snd.second->decref();
    }

    tx_map->clear();
}

// User must check for nullptr return
TaintRangeMapTypePtr
Initializer::get_tainting_map()
{
    return ThreadContextCache.tx_map;
}

void
Initializer::clear_tainting_maps()
{
    // Need to copy because free_tainting_map changes the set inside the iteration
    for (auto& [fst, snd] : initializer->active_map_addreses) {
        clear_tainting_map(snd);
        snd = nullptr;
    }
    active_map_addreses.clear();
}

int
Initializer::num_objects_tainted()
{
    if (const auto ctx_map = initializer->get_tainting_map()) {
        return static_cast<int>(ctx_map->size());
    }
    return 0;
}

string
Initializer::debug_taint_map()
{
    const auto ctx_map = initializer->get_tainting_map();
    if (!ctx_map) {
        return ("[]");
    }

    std::stringstream output;
    output << "[";
    for (const auto& [fst, snd] : *ctx_map) {
        output << "{ 'Id-Key': " << fst << ",";
        output << "'Value': { 'Hash': " << snd.first << ", 'TaintedObject': '" << snd.second->toString() << "'}},";
    }
    output << "]";
    return output.str();
}

int
Initializer::initializer_size() const
{
    return sizeof(*this);
}

int
Initializer::active_map_addreses_size() const
{
    return static_cast<int>(active_map_addreses.size());
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

TaintedObjectPtr
Initializer::allocate_ranges_into_taint_object(TaintRangeRefs ranges)
{
    const auto toptr = allocate_tainted_object();
    toptr->set_values(std::move(ranges));
    return toptr;
}

TaintedObjectPtr
Initializer::allocate_ranges_into_taint_object_copy(const TaintRangeRefs& ranges)
{
    const auto toptr = allocate_tainted_object();
    toptr->copy_values(ranges);
    return toptr;
}

TaintedObjectPtr
Initializer::allocate_tainted_object_copy(const TaintedObjectPtr& from)
{
    if (!from) {
        return allocate_tainted_object();
    }
    return allocate_ranges_into_taint_object_copy(from->ranges_);
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
Initializer::allocate_taint_range(const RANGE_START start, const RANGE_LENGTH length, const Source& origin)
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
    if (ThreadContextCache.tx_map != nullptr) {
        // Reset the current context
        reset_context();
    }

    // Create a new taint_map
    auto map_ptr = create_tainting_map();
    ThreadContextCache.tx_map = map_ptr;
}

void
Initializer::reset_context()
{
    clear_tainting_maps();
    ThreadContextCache.tx_map = nullptr;
}

// Created in the PYBIND11_MODULE in _native.cpp
unique_ptr<Initializer> initializer;

void
pyexport_initializer(py::module& m)
{
    m.def("clear_tainting_maps", [] { initializer->clear_tainting_maps(); });
    m.def("debug_taint_map", [] { return initializer->debug_taint_map(); });

    m.def("num_objects_tainted", [] { return initializer->num_objects_tainted(); });
    m.def("initializer_size", [] { return initializer->initializer_size(); });
    m.def("active_map_addreses_size", [] { return initializer->active_map_addreses_size(); });

    m.def(
      "create_context", []() { return initializer->create_context(); }, py::return_value_policy::reference);
    m.def("reset_context", [] { initializer->reset_context(); });
}
