#include "_memalloc_heap_map.hpp"
#include "_memalloc_debug.h"

/* Note that the HeapSample tables will, in general, never free their backing
 * memory unless we completely clear them. The table takes 17 bytes per entry: 8
 * for the void* keys, 8 for the traceback* values, and 1 byte per entry for
 * control metadata. Assuming a load factor target of ~50%, meaning our table
 * has roughly twice as many slots as actual entries, then for our default
 * maximum of 2^16 entries the table will be about 2MiB. A table this large
 * would correspond to a program with a ~65GiB live heap with a 1MiB default
 * sampling interval. Most of the memory usage of the profiler will come from
 * the tracebacks themselves, which we _do_ free when we're done with them.
 */

// memalloc_heap_map implementation
memalloc_heap_map::memalloc_heap_map()
  : map(HeapSamples_new(0))
{
}

memalloc_heap_map::~memalloc_heap_map()
{
    HeapSamples_CIter it = HeapSamples_citer(&map);
    for (const HeapSamples_Entry* e = HeapSamples_CIter_get(&it); e != nullptr; e = HeapSamples_CIter_next(&it)) {
        delete e->val;
    }
    HeapSamples_destroy(&map);
}

size_t
memalloc_heap_map::size() const
{
    return HeapSamples_size(&map);
}

void
memalloc_heap_map::insert(void* key, traceback_t* value)
{
    HeapSamples_Entry k = { .key = key, .val = value };
    HeapSamples_Insert res = HeapSamples_insert(&map, &k);
    if (!res.inserted) {
        /* This should not happen. It means we did not properly remove a previously-tracked
         * allocation from the map. This should probably be an assertion. Delete the previous
         * entry and replace it with the new value. */
        HeapSamples_Entry* e = HeapSamples_Iter_get(&res.iter);
        traceback_t* prev = e->val;
        e->val = value;
        delete prev;
    }
}

bool
memalloc_heap_map::contains(void* key) const
{
    return HeapSamples_contains(&map, &key);
}

traceback_t*
memalloc_heap_map::remove(void* key)
{
    traceback_t* res = nullptr;
    HeapSamples_Iter it = HeapSamples_find(&map, &key);
    HeapSamples_Entry* e = HeapSamples_Iter_get(&it);
    if (e != nullptr) {
        res = e->val;
        /* This erases the entry but won't shrink the table. */
        HeapSamples_erase_at(it);
    }
    return res;
}

void
memalloc_heap_map::destructive_copy_from(memalloc_heap_map& src)
{
    HeapSamples_Iter it = HeapSamples_iter(&src.map);
    for (const HeapSamples_Entry* e = HeapSamples_Iter_get(&it); e != nullptr; e = HeapSamples_Iter_next(&it)) {
        HeapSamples_insert(&map, e);
    }
    /* Can't erase inside the loop or the iterator is invalidated */
    HeapSamples_clear(&src.map);
}

// Iterator implementation
memalloc_heap_map::iterator::iterator()
  : iter{}
{
}

memalloc_heap_map::iterator::iterator(const memalloc_heap_map& map)
  : iter(HeapSamples_citer(&map.map))
{
}

memalloc_heap_map::iterator&
memalloc_heap_map::iterator::operator++()
{
    const HeapSamples_Entry* e = HeapSamples_CIter_get(&iter);
    if (!e) {
        return *this;
    }
    HeapSamples_CIter_next(&iter);
    return *this;
}

memalloc_heap_map::iterator
memalloc_heap_map::iterator::operator++(int)
{
    iterator tmp = *this;
    ++(*this);
    return tmp;
}

memalloc_heap_map::iterator::value_type
memalloc_heap_map::iterator::operator*() const
{
    const HeapSamples_Entry* e = HeapSamples_CIter_get(&iter);
    if (!e) {
        return { nullptr, nullptr };
    }
    return { e->key, e->val };
}

bool
memalloc_heap_map::iterator::operator==(const iterator& other) const
{
    // Compare underlying iterators by their current entry pointers
    // Note: HeapSamples_CIter doesn't have equality comparison, so we compare
    // the current entry pointers. Both end iterators will have nullptr entries.
    const HeapSamples_Entry* e1 = HeapSamples_CIter_get(&iter);
    const HeapSamples_Entry* e2 = HeapSamples_CIter_get(&other.iter);
    return e1 == e2;
}

bool
memalloc_heap_map::iterator::operator!=(const iterator& other) const
{
    return !(*this == other);
}

memalloc_heap_map::iterator
memalloc_heap_map::begin() const
{
    return iterator(*this);
}

memalloc_heap_map::iterator
memalloc_heap_map::end() const
{
    return iterator();
}
