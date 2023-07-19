#include "Initializer.h"

#include <iostream> // FIXME: debug, remove
#include <mutex>
#include <thread>

using namespace std;
using namespace pybind11::literals;

thread_local struct ThreadContextCache_
{
    size_t tx_id = 0;
    shared_ptr<Context> local_ctx;
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

    // Fill the taint origin stack
    for (int i = 0; i < SOURCE_STACK_SIZE; i++) {
        available_source_stack.push(new Source());
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
    auto it = active_map_addreses.find(tx_map);
    if (it == active_map_addreses.end()) {
        // Map wasn't in the set, do nothing
        return;
    }

    for (auto& kv_taint_map : *tx_map) {
        kv_taint_map.second->decref();
    }
    if (tx_map) {
        tx_map->clear();
        delete tx_map;
    }
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

// TODO: Release tainted objects, where?
// void
// Initializer::release_tainted_object(TaintedObjectPtr tobj)
//{
//    if (!tobj)
//        return;
//
//    tobj->reset();
//    if (available_taintedobjects_stack.size() < TAINTEDOBJECTS_STACK_SIZE) {
//        available_taintedobjects_stack.push(tobj);
//        return;
//    }
//
//    // Stack full, just delete the object (but to a reset before so ranges are
//    // reused or freed)
//    delete tobj;
//}

TaintRangePtr
Initializer::allocate_taint_range(int start, int length, SourcePtr origin)
{
    if (!available_ranges_stack.empty()) {
        auto rptr = available_ranges_stack.top();
        available_ranges_stack.pop();
        rptr->set_values(start, length, reuse_taint_source(origin));
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

    rangeptr->reset();
    if (available_ranges_stack.size() < TAINTRANGES_STACK_SIZE) {
        // Move the range to the allocated ranges stack
        available_ranges_stack.push(rangeptr);
        return;
    }

    // Stack full or initializer already cleared (interpreter finishing), just
    // release the object
    rangeptr.reset();
}

SourcePtr
Initializer::allocate_taint_source(string name, string value, OriginType origin)
{
    auto source_hash = Source::hash(name, value, origin);
    auto it = allocated_sources_map.find(source_hash);
    if (it != allocated_sources_map.end()) {
        // It's already in the map, increase the reference count and return it
        ++(it->second->refcount);
        return it->second;
    }

    // else: not in the map, retrieve from the stack and insert in the map before
    // returning it

    if (!available_source_stack.empty()) {
        auto toptr = available_source_stack.top();
        available_source_stack.pop();
        toptr->set_values(move(name), move(value), origin);
        ++(toptr->refcount);
        allocated_sources_map.insert({ source_hash, toptr });
        return toptr;
    }

    // Stack is empty, create a new object
    auto toptr = new Source(move(name), move(value), origin);
    ++(toptr->refcount);
    allocated_sources_map.insert({ source_hash, toptr });
    return toptr;
}

SourcePtr
Initializer::reuse_taint_source(SourcePtr source)
{
    if (!source)
        return nullptr;

    ++(source->refcount);
    return source;
}

void
Initializer::release_taint_source(SourcePtr sourceptr)
{
    if (!sourceptr)
        return;

    if (--(sourceptr->refcount) == 0) {
        // No more references pointing to this origin; move it back from the map
        // to the stack (or delete it if the stack is full)
        if (available_source_stack.size() < SOURCE_STACK_SIZE) {
            // Move the range to the allocated origins stack
            available_source_stack.push(sourceptr);
            return;
        }

        // Stack full or initializer already cleared (interpreter finishing), just
        // delte the object
        delete sourceptr;
    }

    // else: still references to this origin exist so it remains in the map
}

recursive_mutex contexts_mutex; // NOLINT(cert-err58-cpp)

// TODO: also return the tx_id so it can be reused on aspects or calls
// to get/set_ranges without accessing the ThreadLocal struct
shared_ptr<Context>
Initializer::create_context()
{
    if (ThreadContextCache.tx_id != 0) {
        // Destroy the current context
        destroy_context();
    }

    // Create a new taint_map
    auto map_ptr = create_tainting_map();
    ThreadContextCache.tx_id = (size_t)map_ptr;
    auto ret_ctx = make_shared<Context>();
    contexts[(size_t)map_ptr] = ret_ctx;
    ThreadContextCache.local_ctx = ret_ctx;
    return ret_ctx;
}

void
Initializer::destroy_context()
{
    auto tx_id = ThreadContextCache.tx_id;
    ThreadContextCache.local_ctx.reset();
    ThreadContextCache.tx_id = 0;
    contexts[tx_id].reset();
    contexts.erase(tx_id);
    free_tainting_map((TaintRangeMapType*)tx_id);
}

shared_ptr<Context>
Initializer::get_context(size_t tx_id_)
{
    if (tx_id_ == 0) {
        if (ThreadContextCache.tx_id == 0) {
            throw ContextNotInitializedException("Context is not created");
        }

        assert(ThreadContextCache.local_ctx);
        return ThreadContextCache.local_ctx;
    } else {
        // tx_id was specified, check the cache
        if (ThreadContextCache.tx_id == tx_id_) {
            return ThreadContextCache.local_ctx;
        }
        ThreadContextCache.tx_id = tx_id_;
    }

    // tx_id not  in the cache, search for it in the contexts map
    // ...but first check that the map exists
    auto it = active_map_addreses.find((TaintRangeMapType*)ThreadContextCache.tx_id);
    if (it == active_map_addreses.end()) {
        throw ContextNotInitializedException("Context doesnt have available tainted map allocated");
    }

    shared_ptr<Context> ret_ctx;
    auto ctx_it = contexts.find(ThreadContextCache.tx_id);
    if (ctx_it == contexts.end() or not ctx_it->second) {
        // Context not created (new key or it was empty), create it
        ret_ctx = make_shared<Context>();
        contexts[ThreadContextCache.tx_id] = ret_ctx;
    } else {
        ret_ctx = ctx_it->second;
    }

    ThreadContextCache.local_ctx = ret_ctx;
    return ret_ctx;
}

size_t
Initializer::context_id()
{
    return ThreadContextCache.tx_id;
}

void
Initializer::contexts_reset()
{
    //    lock_guard<recursive_mutex> lock(contexts_mutex);
    if (contexts[ThreadContextCache.tx_id]) {
        contexts[ThreadContextCache.tx_id]->reset_blocking_vulnerability_hashes();
    }

    contexts[ThreadContextCache.tx_id].reset();
    ThreadContextCache.tx_id = 0;
    ThreadContextCache.local_ctx.reset();

    contexts.clear();
    clear_tainting_maps();
}

// Created in the PYBIND11_MODULE in _native.cpp
unique_ptr<Initializer> initializer;

void
pyexport_initializer(py::module& m)
{
    m.def("clear_tainting_maps", [] { initializer->clear_tainting_maps(); });

    m.def("num_objects_tainted", [] { return initializer->num_objects_tainted(); });

    m.def(
      "create_context", []() { return initializer->create_context(); }, py::return_value_policy::reference);
    m.def(
      "get_context",
      [](const size_t tx_id) { return initializer->get_context(tx_id); },
      py::return_value_policy::reference,
      "tx_id"_a = 0);
    m.def("contexts_reset", [] { initializer->contexts_reset(); });
    m.def("destroy_context", [] { initializer->destroy_context(); });
}
